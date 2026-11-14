from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from dependencies import get_current_user
from models import InsightAcao
from pydantic import BaseModel
from datetime import datetime
import io
import openpyxl

router = APIRouter(
    prefix="/insights",
    tags=["Insights"]
)

# Janela UTC do lote noturno do cron Unimed (23:01 GMT+0 = 20:01 Brasília)
LOTE_INICIO, LOTE_FIM = "22:50:00", "23:10:00"
JANELA_PORTAL_DIAS = 7


def _dia_do_ultimo_lote(db: Session):
    """Data UTC do dia mais recente com lote do cron (22:50-23:10)."""
    row = db.execute(text(
        "SELECT max(created_at::date) FROM jobs "
        "WHERE created_at::time BETWEEN :i AND :f"
    ), {"i": LOTE_INICIO, "f": LOTE_FIM}).fetchone()
    return row[0]


def _resumo_ultimo_lote(db: Session, dia):
    """Distribuição tipo_json (+erros) do lote noturno mais recente."""
    rows = db.execute(text(
        "SELECT coalesce(valida_prestador->>'tipo_json','(null)') AS tipo, status, count(*) "
        "FROM jobs WHERE created_at::date = :d AND created_at::time BETWEEN :i AND :f "
        "GROUP BY 1, 2"
    ), {"d": dia, "i": LOTE_INICIO, "f": LOTE_FIM}).fetchall()
    por_tipo = {}
    for tipo, status, n in rows:
        chave = "erro" if tipo == "(null)" and status == "error" else tipo
        por_tipo[chave] = por_tipo.get(chave, 0) + int(n)
    return {"data": dia.isoformat(), "por_tipo": por_tipo}


def _renovar_guia(db: Session, dia):
    """Pacientes do último lote Antigas270/Vencidas — ação: solicitar nova guia.
    Exclui já notificados (insight_acoes tipo 'renovar_guia', ref=carteirinha)."""
    rows = db.execute(text(
        "SELECT j.id AS job_id, c.id AS carteirinha_id, c.paciente, c.carteirinha, "
        "       j.valida_prestador->>'tipo_json' AS tipo, "
        "       g.key AS guia, g.value->>'descricao_procedimento' AS descricao, "
        "       coalesce(g.value->>'validade', g.value->>'data_solicitacao', '') AS ref_data, "
        "       EXISTS (SELECT 1 FROM insight_acoes ia "
        "               WHERE ia.tipo = 'renovar_guia' AND ia.referencia_id = c.id "
        "                 AND ia.status = 'notificado') AS notificado "
        "FROM jobs j "
        "JOIN carteirinhas c ON c.id = j.carteirinha_id "
        "CROSS JOIN LATERAL jsonb_each(j.valida_prestador->'guias') g "
        "WHERE j.created_at::date = :d AND j.created_at::time BETWEEN :i AND :f "
        "  AND j.valida_prestador->>'tipo_json' IN ('Antigas270','Vencidas') "
        "ORDER BY c.paciente, g.key"
    ), {"d": dia, "i": LOTE_INICIO, "f": LOTE_FIM}).fetchall()

    pacientes = {}
    for r in rows:
        p = pacientes.setdefault(r.carteirinha_id, {
            "carteirinha_id": r.carteirinha_id,
            "paciente": r.paciente or "",
            "carteirinha": r.carteirinha or "",
            "tipo": r.tipo,
            "notificado": bool(r.notificado),
            "guias": [],
        })
        p["guias"].append({
            "guia": r.guia, "descricao": r.descricao or "",
            "ref_data": r.ref_data or "",
        })
    return list(pacientes.values())


def _portal(db: Session):
    """Erros recentes (7 dias) com mensagem — mapear mudanças no portal
    (ex.: 'Beneficiário ... intercâmbio' nos prefixos != 0064)."""
    jobs_err = db.execute(text(
        f"SELECT j.id, c.paciente, c.carteirinha, j.attempts, j.created_at "
        f"FROM jobs j JOIN carteirinhas c ON c.id = j.carteirinha_id "
        f"WHERE j.status = 'error' AND j.created_at >= NOW() - INTERVAL '{JANELA_PORTAL_DIAS} days' "
        f"ORDER BY j.created_at DESC LIMIT 40"
    )).fetchall()
    if not jobs_err:
        return []
    ids = [j.id for j in jobs_err]
    msgs = db.execute(text(
        "SELECT DISTINCT ON (job_id) job_id, message FROM logs "
        "WHERE job_id = ANY(:ids) AND level IN ('ERROR','WARN') "
        "  AND (message LIKE 'Timeout%' OR message LIKE 'Scraper Error%' "
        "       OR message LIKE 'Worker Error%' OR message LIKE 'Dispatcher Failed%') "
        "ORDER BY job_id, id DESC"
    ), {"ids": ids}).fetchall()
    msg_por_job = {m.job_id: m.message for m in msgs}
    portal = []
    for j in jobs_err:
        portal.append({
            "job_id": j.id, "paciente": j.paciente or "",
            "carteirinha": j.carteirinha or "", "attempts": j.attempts,
            "criado_em": j.created_at.isoformat() if j.created_at else "",
            "mensagem": (msg_por_job.get(j.id) or "sem mensagem de erro registrada")[:300],
        })
    return portal


def _bloqueios(db: Session):
    """Guias ativas por tipo de bloqueio (Vinculo_prestador != 'Guia Válida')."""
    rows = db.execute(text(
        "SELECT coalesce(valida_prestador->>'Vinculo_prestador','(sem vínculo)') AS vinculo, "
        "       count(*) AS total, count(DISTINCT c.id_paciente) AS pacientes "
        "FROM base_guias bg JOIN carteirinhas c ON c.id = bg.carteirinha_id "
        "WHERE bg.ativo = true "
        "  AND coalesce(bg.valida_prestador->>'Vinculo_prestador','') <> 'Guia Válida' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
    )).fetchall()
    return [{"vinculo": r.vinculo, "total": int(r.total), "pacientes": int(r.pacientes)} for r in rows]


def _pei_base(db: Session):
    """Inconsistências base/PEI: qtde=0 (resumo por vínculo + anomalias
    'Guia Válida' com 0 — conferir no portal), pacientes com 2+ carteirinhas
    (inativar a não usada) e PEIs Pendentes com qtde 0."""
    por_vinculo = db.execute(text(
        "SELECT coalesce(valida_prestador->>'Vinculo_prestador','(null)') AS vinculo, count(*) AS total "
        "FROM base_guias WHERE sessoes_autorizadas = 0 AND ativo = true "
        "GROUP BY 1 ORDER BY 2 DESC"
    )).fetchall()

    # Anomalia real: guia válida (ou sem vínculo) com qtde 0 — parsing/portal
    anomalias = db.execute(text(
        "SELECT bg.guia, c.paciente, c.carteirinha, "
        "       coalesce(bg.valida_prestador->>'Vinculo_prestador','(null)') AS vinculo, "
        "       to_char(bg.updated_at, 'DD/MM HH24:MI') AS atualizado "
        "FROM base_guias bg JOIN carteirinhas c ON c.id = bg.carteirinha_id "
        "WHERE bg.sessoes_autorizadas = 0 AND bg.ativo = true "
        "  AND (bg.valida_prestador->>'Vinculo_prestador' LIKE 'Guia V%lida' "
        "       OR bg.valida_prestador->>'Vinculo_prestador' IS NULL) "
        "ORDER BY bg.updated_at DESC LIMIT 50"
    )).fetchall()

    multi = db.execute(text(
        "SELECT c.id_paciente, max(c.paciente) AS paciente, count(DISTINCT c.id) AS n_cart, "
        "       string_agg(DISTINCT c.carteirinha, ' / ' ORDER BY c.carteirinha) AS carteirinhas, "
        "       EXISTS (SELECT 1 FROM insight_acoes ia "
        "               WHERE ia.tipo = 'duas_carteirinhas' AND ia.referencia_id = c.id_paciente "
        "                 AND ia.status = 'notificado') AS notificado "
        "FROM carteirinhas c "
        "WHERE c.id_paciente IS NOT NULL AND c.status = 'ativo' "
        "GROUP BY c.id_paciente HAVING count(DISTINCT c.id) > 1 "
        "ORDER BY n_cart DESC, c.id_paciente LIMIT 30"
    )).fetchall()

    pendentes_qtde_zero = db.execute(text(
        "SELECT c.paciente, c.carteirinha, pp.codigo_procedimento, bg.guia "
        "FROM patient_pei pp "
        "JOIN carteirinhas c ON c.id = pp.carteirinha_id "
        "JOIN base_guias bg ON bg.id = pp.base_guia_id "
        "WHERE pp.ativo = true AND pp.status = 'Pendente' "
        "  AND coalesce(bg.qtde_solicitada_pei, bg.qtde_solicitada, 0) = 0 "
        "ORDER BY pp.updated_at DESC LIMIT 20"
    )).fetchall()

    return {
        "qtde_zero_por_vinculo": [{"vinculo": r.vinculo, "total": int(r.total)} for r in por_vinculo],
        "qtde_zero_anomalias": [{
            "guia": r.guia, "paciente": r.paciente or "", "carteirinha": r.carteirinha or "",
            "vinculo": r.vinculo, "atualizado": r.atualizado,
        } for r in anomalias],
        "multi_carteirinhas": [{
            "id_paciente": r.id_paciente, "paciente": (r.paciente or ""),
            "n_carteirinhas": int(r.n_cart), "carteirinhas": (r.carteirinhas or ""),
            "notificado": bool(r.notificado),
        } for r in multi],
        "pei_pendentes_qtde_zero": [{
            "paciente": r.paciente or "", "carteirinha": r.carteirinha or "",
            "codigo": r.codigo_procedimento, "guia": r.guia,
        } for r in pendentes_qtde_zero],
    }


class NotificarRequest(BaseModel):
    tipo: str          # renovar_guia | duas_carteirinhas | guia_qtde_zero | ...
    referencia_id: int


@router.get("/resumo")
def get_resumo(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Agregado do painel de Inteligência (modal em Importações)."""
    dia = _dia_do_ultimo_lote(db)
    if dia is None:
        return {"gerado_em": datetime.utcnow().isoformat(), "ultimo_lote": None,
                "renovar_guia": [], "portal": [], "bloqueios": [], "pei_base": None}
    return {
        "gerado_em": datetime.utcnow().isoformat(),
        "ultimo_lote": _resumo_ultimo_lote(db, dia),
        "renovar_guia": _renovar_guia(db, dia),
        "portal": _portal(db),
        "bloqueios": _bloqueios(db),
        "pei_base": _pei_base(db),
    }


@router.post("/notificar")
def notificar(
    req: NotificarRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Marca um item do painel como notificado (idempotente: re-notificar
    apenas atualiza notified_at)."""
    row = db.query(InsightAcao).filter(
        InsightAcao.tipo == req.tipo,
        InsightAcao.referencia_id == req.referencia_id,
    ).first()
    now = datetime.utcnow()
    if row:
        row.status = "notificado"
        row.notified_at = now
        row.updated_at = now
    else:
        db.add(InsightAcao(tipo=req.tipo, referencia_id=req.referencia_id,
                           status="notificado", notified_at=now))
    db.commit()
    return {"status": "success", "tipo": req.tipo, "referencia_id": req.referencia_id}


@router.get("/export")
def export_insights(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Exporta o agregado atual em .xlsx (uma seção por tipo de insight)."""
    try:
        dia = _dia_do_ultimo_lote(db)
        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet("Insights")

        def fmt(d):
            return d.strftime("%d/%m/%Y") if d else ""

        ws.append(["PAINEL DE INSIGHTS", "", "", f"Gerado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M')}"])
        ws.append([])

        if dia is not None:
            ws.append(["ÚLTIMO LOTE", fmt(dia)])
            for tipo, n in _resumo_ultimo_lote(db, dia)["por_tipo"].items():
                ws.append(["", tipo, n])
            ws.append([])

            ws.append(["RENOVAR GUIA (solicitar nova guia à Unimed)"])
            ws.append(["Paciente", "Carteirinha", "Motivo", "Guias", "Notificado"])
            for p in _renovar_guia(db, dia):
                guias = "; ".join(g["guia"] for g in p["guias"][:10])
                ws.append([p["paciente"], p["carteirinha"], p["tipo"], guias,
                           "Sim" if p["notificado"] else "Não"])
            ws.append([])

        ws.append(["CARTEIRINHAS DUPLICADAS (inativar a não usada em Carteirinhas)"])
        ws.append(["ID Paciente", "Paciente", "Qtd", "Carteirinhas", "Notificado"])
        pei = _pei_base(db)
        for m in pei["multi_carteirinhas"]:
            ws.append([m["id_paciente"], m["paciente"], m["n_carteirinhas"],
                       m["carteirinhas"], "Sim" if m["notificado"] else "Não"])
        ws.append([])

        ws.append(["GUIAS COM QTDE=0 (por vínculo)"])
        ws.append(["Vínculo", "Total"])
        for v in pei["qtde_zero_por_vinculo"]:
            ws.append([v["vinculo"], v["total"]])
        ws.append([])
        ws.append(["ANOMALIAS QTDE=0 (guia válida/sem vínculo — conferir no portal)"])
        ws.append(["Guia", "Paciente", "Carteirinha", "Vínculo", "Atualizado"])
        for a in pei["qtde_zero_anomalias"]:
            ws.append([a["guia"], a["paciente"], a["carteirinha"], a["vinculo"], a["atualizado"]])
        ws.append([])

        ws.append(["PORTAL — ERROS RECENTES (7 dias)"])
        ws.append(["Job", "Paciente", "Carteirinha", "Tentativas", "Mensagem"])
        for e in _portal(db):
            ws.append([e["job_id"], e["paciente"], e["carteirinha"], e["attempts"], e["mensagem"]])
        ws.append([])

        ws.append(["BLOQUEIOS ATIVOS (por vínculo)"])
        ws.append(["Vínculo", "Guias", "Pacientes"])
        for b in _bloqueios(db):
            ws.append([b["vinculo"], b["total"], b["pacientes"]])

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        filename = f"insights_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
        from fastapi.responses import StreamingResponse
        return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao exportar: {str(e)}")
