"""
Service da OP2 ImprimirEvolucao (rotina 'clmf_imprimir_evolucao').

Responsabilidades:
  - parse da planilha modelo de evolucoes (openpyxl read_only)
  - agrupamento por (idPaciente, DataExec) -> 1 job por par, encadeando itens[guia/ID_prof](horas)
  - criacao dos jobs pendentes + itens PENDENTE em evolucao_itens
  - geracao do xlsx de status (compartilhado entre rota e script CLI)

Planilha modelo (aba ativa; cabecalho na primeira linha):
  idPaciente, nomePaciente, Guia, DataExec, Profissional, CodTerapia, Terapia,
  Sessoes, HoraInicial, Lote, status, tipo, ID_prof

Do JSON do job participam apenas: idPaciente, nomePaciente, Guia, DataExec, Terapia,
ID_prof, HoraInicial (+"login"/"senha" quando configurados no env do backend).
CodTerapia e Profissional NAO entram (profissao_id vem do select do portal pela
Terapia; nome do profissional e resolvido no select pelo ID_prof).
"""
import io
import os
import re
import threading
from datetime import date, datetime, timedelta

from openpyxl import Workbook, load_workbook
from sqlalchemy.orm import Session

from models import Carteirinha, EvolucaoItem, Job

ROTINA_EVOLUCAO = "clmf_imprimir_evolucao"

# Carteirinha ancora criada pela migration 0032: o dispatcher exige carteirinha_id
# valido (dispatcher.py le job.carteirinha_rel.carteirinha). Evolucoes nao tem
# carteirinha no dominio — todos os jobs da rotina apontam para esta unica linha.
CARTEIRINHA_ANCORA = "EVOLUCOES-CLMF"

# Colunas obrigatorias da planilha (subset que segue no job)
REQUIRED_HEADERS = ["idPaciente", "nomePaciente", "Guia", "DataExec", "Terapia", "HoraInicial", "ID_prof"]

# Janela do filtro no portal: dataExec +/- 30 dias
JANELA_DIAS = 30

# Limites do relatorio de erros retornado pelo upload
MAX_ERROS_RETORNO = 50

# Criacao de jobs em LOTES: 1 commit a cada N jobs (commit-por-job via WAN ao
# Supabase custa ~1 job/s; com lote de 200, 10k jobs saem em ~1 min).
CRIACAO_BATCH_JOBS = 200


# ─── Parsing de celulas ─────────────────────────────────────────────────────

def _norm_header(h) -> str:
    return str(h or "").strip()


def _parse_data(v):
    """Valor de celula DataExec -> date | None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_hora_hhmm(v) -> str | None:
    """'07:00:00' / '07:00' / time -> '0700' (formato do JSON do job)."""
    s = str(v or "").strip()
    m = re.match(r"^(\d{1,2})[:h]?(\d{2})", s)
    if m:
        hh, mm = int(m.group(1)), m.group(2)
        if 0 <= hh <= 23:
            return f"{hh:02d}{mm}"
    return None


# ─── Parse da planilha → payloads de job ────────────────────────────────────

def parse_planilha(contents: bytes) -> tuple[list[dict], list[str]]:
    """Retorna (jobs_payload, erros).

    jobs_payload: lista ordenada por (idPaciente, DataExec) — jobs do mesmo paciente
    ficam com ids consecutivos, favorecendo a estrategia de afinidade do worker.
    Cada payload segue o contrato de params do job (ver docstring do modulo).
    """
    wb = load_workbook(filename=io.BytesIO(contents), read_only=True, data_only=True)
    ws = wb.active

    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        return [], ["Planilha vazia (sem cabecalho)."]

    header = {col: _norm_header(h) for col, h in enumerate(header_row) if h is not None}
    faltando = [c for c in REQUIRED_HEADERS if c not in header.values()]
    if faltando:
        raise ValueError(f"Cabecalho invalido. Colunas obrigatorias ausentes: {', '.join(faltando)}. "
                         f"Esperado: {', '.join(REQUIRED_HEADERS)}")

    col = {name: idx for idx, name in header.items()}
    erros: list[str] = []
    grupos: dict[tuple[int, date], dict] = {}
    nome_por_paciente: dict[int, str] = {}

    for lin, row in enumerate(rows, start=2):
        if row is None or all(c is None for c in row):
            continue

        def cell(name):
            idx = col.get(name)
            return row[idx] if idx is not None and idx < len(row) else None

        try:
            id_paciente = int(str(cell("idPaciente")).strip())
        except (TypeError, ValueError):
            erros.append(f"Linha {lin}: idPaciente vazio/invalido ('{cell('idPaciente')}')")
            continue
        data_exec = _parse_data(cell("DataExec"))
        if data_exec is None:
            erros.append(f"Linha {lin}: DataExec vazio/invalido ('{cell('DataExec')}')")
            continue
        guia = str(cell("Guia") or "").strip()
        if not guia:
            erros.append(f"Linha {lin}: Guia vazia")
            continue
        terapia = str(cell("Terapia") or "").strip()
        if not terapia:
            erros.append(f"Linha {lin}: Terapia vazia")
            continue
        try:
            id_prof = int(str(cell("ID_prof")).strip())
        except (TypeError, ValueError):
            erros.append(f"Linha {lin}: ID_prof vazio/invalido ('{cell('ID_prof')}')")
            continue
        hora = _parse_hora_hhmm(cell("HoraInicial"))
        if hora is None:
            erros.append(f"Linha {lin}: HoraInicial vazio/invalido ('{cell('HoraInicial')}')")
            continue

        nome = str(cell("nomePaciente") or "").strip()
        if id_paciente not in nome_por_paciente and nome:
            nome_por_paciente[id_paciente] = nome

        chave = (id_paciente, data_exec)
        grupo = grupos.setdefault(chave, {
            "idPaciente": id_paciente,
            "dataExec": data_exec.isoformat(),
            "itens": {},  # (guia, id_prof) -> {"guia","ID_prof","terapia","horas":[]}
        })
        item = grupo["itens"].setdefault((guia, id_prof), {
            "guia": guia, "ID_prof": id_prof, "terapia": terapia, "horas": []
        })
        if hora not in item["horas"]:
            item["horas"].append(hora)

    payloads = []
    for (id_paciente, data_exec), grupo in sorted(grupos.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        data_ini = (data_exec - timedelta(days=JANELA_DIAS)).isoformat()
        data_fim = (data_exec + timedelta(days=JANELA_DIAS)).isoformat()
        payloads.append({
            "idPaciente": id_paciente,
            "nomePaciente": nome_por_paciente.get(id_paciente, ""),
            "dataExec": data_exec.isoformat(),
            "dataInicial": data_ini,
            "dataFinal": data_fim,
            "itens": [it for it in grupo["itens"].values()],
        })
    return payloads, erros


# ─── Criacao dos jobs ───────────────────────────────────────────────────────

def _credenciais_params() -> dict:
    """login/senha entram em params (spec) quando configurados no env do backend;
    ausentes, o scraper usa o proprio env do worker (CLMF_LOGIN/CLMF_PASSWORD)."""
    login = os.getenv("CLMF_LOGIN", "").strip()
    senha = os.getenv("CLMF_PASSWORD", "").strip()
    out = {}
    if login:
        out["login"] = login
    if senha:
        out["senha"] = senha
    return out


def nome_lote(filename: str) -> str:
    return f"{filename}:{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def _pares_existentes(db: Session) -> set[tuple[str, str]]:
    """(idPaciente, dataExec) que JÁ possuem job da rotina (qualquer status).

    Torna o re-upload idempotente: pares já criados não duplicam (um lote
    interrompido no meio pode ser reenviado sem medo). Para reprocessar um par,
    exclua/retry o job existente antes.
    """
    rows = db.query(
        Job.params["idPaciente"].astext,
        Job.params["dataExec"].astext,
    ).filter(Job.rotina == ROTINA_EVOLUCAO).all()
    return {(r[0], r[1]) for r in rows if r[0] is not None}


def _commit_lote_jobs(db: Session, payloads: list[dict], lote: str, ancora_id: int) -> int:
    """Insere um lote de jobs + itens PENDENTE em UMA transação (flush unico
    popula os job.id). Retorna nº de itens criados."""
    job_objs = []
    for payload in payloads:
        job = Job(
            carteirinha_id=ancora_id,
            rotina=ROTINA_EVOLUCAO,
            params=payload,  # credenciais já embutidas
            status="pending",
        )
        db.add(job)
        job_objs.append(job)
    db.flush()  # popula os ids dos jobs

    itens = []
    for job_obj, payload in zip(job_objs, payloads):
        itens.extend(_itens_do_payload(job_obj.id, payload, lote))
    db.add_all(itens)
    db.commit()
    return len(itens)


def _itens_do_payload(job_id: int, payload: dict, lote: str) -> list[EvolucaoItem]:
    out = []
    for item in payload["itens"]:
        for hora in item["horas"]:
            out.append(EvolucaoItem(
                job_id=job_id,
                lote=lote,
                id_paciente=payload["idPaciente"],
                nome_paciente=payload["nomePaciente"],
                guia=item["guia"],
                data_exec=date.fromisoformat(payload["dataExec"]),
                profissional_id=item["ID_prof"],
                terapia=item["terapia"],
                hora_inicial=hora,
                status="PENDENTE",
            ))
    return out


def criar_jobs_evolucao(db: Session, payloads: list[dict], filename: str, lote: str | None = None) -> dict:
    """Cria 1 job por (idPaciente, DataExec) a partir de payloads JÁ parseados,
    com dedup de pares existentes e commits em lote (CRIACAO_BATCH_JOBS).
    Falha em um lote não derruba os demais (isolamento por lote)."""
    ancora = db.query(Carteirinha).filter(Carteirinha.carteirinha == CARTEIRINHA_ANCORA).first()
    if not ancora:
        raise RuntimeError(f"Carteirinha ancora '{CARTEIRINHA_ANCORA}' ausente — rode a migration 0032.")

    lote = lote or nome_lote(filename)
    credenciais = _credenciais_params()
    existentes = _pares_existentes(db)

    jobs_ok, itens_ok, pulados, falhas = 0, 0, 0, []
    pendentes: list[dict] = []

    for payload in payloads:
        chave = (str(payload["idPaciente"]), payload["dataExec"])
        if chave in existentes:
            pulados += 1
            continue
        existentes.add(chave)
        pendentes.append({**payload, **credenciais})

        if len(pendentes) >= CRIACAO_BATCH_JOBS:
            try:
                itens_ok += _commit_lote_jobs(db, pendentes, lote, ancora.id)
                jobs_ok += len(pendentes)
            except Exception as e:
                db.rollback()
                falhas.append(f"lote de {len(pendentes)} jobs a partir de "
                              f"{pendentes[0]['idPaciente']}/{pendentes[0]['dataExec']}: {e}")
            pendentes = []

    if pendentes:
        try:
            itens_ok += _commit_lote_jobs(db, pendentes, lote, ancora.id)
            jobs_ok += len(pendentes)
        except Exception as e:
            db.rollback()
            falhas.append(f"lote final de {len(pendentes)} jobs: {e}")

    return {
        "lote": lote,
        "jobs": jobs_ok,
        "itens": itens_ok,
        "pulados_duplicados": pulados,
        "falhas": falhas[:MAX_ERROS_RETORNO],
    }


def criar_jobs_background(payloads: list[dict], filename: str, lote: str):
    """Executa a criação de jobs em thread própria (session dedicada).

    Upload de planilhas grandes (20k+ linhas) não pode travar o event loop nem
    estourar timeout do proxy (Render): o endpoint responde imediatamente e a
    criação corre aqui; o progresso aparece no painel via /evolucoes/jobs.
    """
    try:
        from database import SessionLocal
        db = SessionLocal()
        try:
            resumo = criar_jobs_evolucao(db, payloads, filename, lote)
            print(f"[evolucoes] lote {lote}: {resumo['jobs']} jobs / {resumo['itens']} itens criados, "
                  f"{resumo['pulados_duplicados']} duplicados pulados, falhas={len(resumo['falhas'])}")
        finally:
            db.close()
    except Exception as e:
        print(f"[evolucoes] ERRO na criacao do lote {lote}: {e}")


# ─── Export de status (xlsx) ────────────────────────────────────────────────

EXPORT_HEADERS = ["idPaciente", "nomePaciente", "guia", "data", "profissional_id",
                  "terapia", "profissao_id", "horaInicial", "status", "motivo", "pdfPath", "lote"]


def gerar_xlsx_status(db: Session, lote: str | None = None, job_id: int | None = None,
                      status: str | None = None) -> io.BytesIO:
    """Gera o xlsx de status a partir de evolucao_itens (padrao routes/guias.py)."""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Evolucoes")
    ws.append(EXPORT_HEADERS)

    query = db.query(
        EvolucaoItem.id_paciente, EvolucaoItem.nome_paciente, EvolucaoItem.guia,
        EvolucaoItem.data_exec, EvolucaoItem.profissional_id, EvolucaoItem.terapia,
        EvolucaoItem.profissao_id, EvolucaoItem.hora_inicial, EvolucaoItem.status,
        EvolucaoItem.motivo, EvolucaoItem.pdf_path, EvolucaoItem.lote,
    )
    if lote:
        query = query.filter(EvolucaoItem.lote == lote)
    if job_id:
        query = query.filter(EvolucaoItem.job_id == job_id)
    if status:
        query = query.filter(EvolucaoItem.status == status)

    for row in query.yield_per(1000).order_by(EvolucaoItem.id_paciente, EvolucaoItem.data_exec,
                                              EvolucaoItem.guia, EvolucaoItem.hora_inicial):
        ws.append([
            row.id_paciente, row.nome_paciente or "", row.guia or "",
            row.data_exec.strftime("%d/%m/%Y") if row.data_exec else "",
            row.profissional_id, row.terapia or "", row.profissao_id or "",
            row.hora_inicial or "", row.status, row.motivo or "",
            row.pdf_path or "", row.lote or "",
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output
