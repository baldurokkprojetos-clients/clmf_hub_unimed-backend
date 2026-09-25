"""
Rotas da OP2 ImprimirEvolucao (rotina 'clmf_imprimir_evolucao').

  POST /evolucoes/upload  → parse da planilha modelo + criacao dos jobs (1 por idPaciente/DataExec)
  GET  /evolucoes/jobs    → painel agregado por paciente (contadores OK/PENDENTE/ERRO)
  GET  /evolucoes/export  → download xlsx de status (idPaciente, guia, data, profissional_id,
                            horaInicial, status, ... extras motivo/pdfPath)
"""
import threading

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models import EvolucaoItem, User
from services import evolucao_service

router = APIRouter(prefix="/evolucoes", tags=["Evolucoes"])


@router.post("/upload")
async def upload_evolucoes(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .xlsx (planilha modelo de evolucoes).")

    contents = await file.read()
    await file.close()

    # Parse fora do event loop (planilhas de 20k+ linhas levam segundos)
    try:
        payloads, erros = await run_in_threadpool(evolucao_service.parse_planilha, contents)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not payloads:
        raise HTTPException(status_code=422, detail=(
            "Nenhuma linha valida encontrada na planilha. "
            f"Primeiros erros: {'; '.join(erros[:5]) or 'planilha sem dados'}"))

    # Criacao dos jobs em background: resposta imediata (nao trava o backend nem
    # estoura timeout de proxy — Render). Progresso acompanhadivel no painel
    # (/evolucoes/jobs) e a criacao e idempotente (pares existentes sao pulados).
    lote = evolucao_service.nome_lote(file.filename)
    threading.Thread(
        target=evolucao_service.criar_jobs_background,
        args=(payloads, file.filename, lote),
        daemon=True,
        name=f"evolucoes-upload-{lote}",
    ).start()

    return {
        "lote": lote,
        "background": True,
        "mensagem": "Planilha validada — criação dos jobs em andamento em segundo plano. "
                    "Acompanhe o progresso no painel abaixo (atualiza a cada 5s).",
        "jobs": len(payloads),
        "itens": sum(len(i["horas"]) for p in payloads for i in p["itens"]),
        "pacientes": len({p["idPaciente"] for p in payloads}),
        "erros_planilha": erros[:50],
    }


@router.get("/jobs")
def list_evolucoes_jobs(
    lote: str | None = Query(None),
    paciente: str | None = Query(None, description="Busca por nome (contém, sem acento insensível) ou idPaciente exato"),
    status: str | None = Query(None, description="ok | pendente | erro — pacientes com ao menos 1 item no status"),
    limit: int = Query(25, ge=1, le=200),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Agregado por idPaciente para o painel do frontend (filtros + paginação + resumo)."""
    ok_n = case((EvolucaoItem.status == "OK", 1), else_=0)
    pend_n = case((EvolucaoItem.status.in_(("PENDENTE", "PROCESSANDO")), 1), else_=0)
    erro_n = case((EvolucaoItem.status == "ERRO", 1), else_=0)

    def filtros_escopo(query):
        """Filtros de escopo (lote + paciente) — reaproveitados no resumo do dashboard."""
        if lote:
            query = query.filter(EvolucaoItem.lote == lote)
        if paciente:
            termo = paciente.strip()
            if termo.isdigit():
                query = query.filter(or_(
                    EvolucaoItem.nome_paciente.ilike(f"%{termo}%"),
                    EvolucaoItem.id_paciente == int(termo),
                ))
            else:
                query = query.filter(EvolucaoItem.nome_paciente.ilike(f"%{termo}%"))
        return query

    query = filtros_escopo(db.query(
        EvolucaoItem.id_paciente,
        func.max(EvolucaoItem.nome_paciente).label("nome_paciente"),
        func.max(EvolucaoItem.lote).label("lote"),
        func.count(EvolucaoItem.id).label("total"),
        func.sum(ok_n).label("ok"),
        func.sum(pend_n).label("pendente"),
        func.sum(erro_n).label("erro"),
        func.max(EvolucaoItem.updated_at).label("updated_at"),
    ).group_by(EvolucaoItem.id_paciente))

    # Filtro de status atua no agregado (paciente precisa ter ao menos 1 item no status)
    if status:
        s = status.strip().lower()
        if s == "ok":
            query = query.having(func.sum(ok_n) > 0)
        elif s == "pendente":
            query = query.having(func.sum(pend_n) > 0)
        elif s == "erro":
            query = query.having(func.sum(erro_n) > 0)
        else:
            raise HTTPException(status_code=422, detail="Status inválido: use ok, pendente ou erro.")

    total = query.count()  # nº de pacientes (grupos) que casam com os filtros
    rows = query.order_by(func.max(EvolucaoItem.updated_at).desc()).offset(skip).limit(limit).all()

    # Resumo do dashboard: mesmo escopo de lote/paciente, mas ignora o filtro de
    # status — o quadro completo OK/PENDENTE/ERRO continua visível durante o filtro
    resumo = filtros_escopo(db.query(
        func.count(EvolucaoItem.id).label("itens"),
        func.count(func.distinct(EvolucaoItem.id_paciente)).label("pacientes"),
        func.sum(ok_n).label("ok"),
        func.sum(pend_n).label("pendente"),
        func.sum(erro_n).label("erro"),
    )).one()

    return {
        "data": [
            {
                "idPaciente": r.id_paciente,
                "nomePaciente": r.nome_paciente or "",
                "lote": r.lote or "",
                "total": r.total,
                "ok": int(r.ok or 0),
                "pendente": int(r.pendente or 0),
                "erro": int(r.erro or 0),
                "updated_at": r.updated_at,
            }
            for r in rows
        ],
        "total": total,
        "resumo": {
            "total": resumo.itens,
            "pacientes": resumo.pacientes,
            "ok": int(resumo.ok or 0),
            "pendente": int(resumo.pendente or 0),
            "erro": int(resumo.erro or 0),
        },
    }


@router.get("/export")
def export_evolucoes(
    lote: str | None = Query(None),
    job_id: int | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        output = evolucao_service.gerar_xlsx_status(db, lote=lote, job_id=job_id, status=status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar arquivo: {e}")

    return StreamingResponse(
        output,
        headers={"Content-Disposition": 'attachment; filename="evolucoes_status.xlsx"'},
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
