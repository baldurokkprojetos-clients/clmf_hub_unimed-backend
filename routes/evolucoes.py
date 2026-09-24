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
from sqlalchemy import case, func
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
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Agregado por idPaciente para o painel do frontend."""
    pendente = case((EvolucaoItem.status.in_(("PENDENTE", "PROCESSANDO")), 1), else_=0)
    query = db.query(
        EvolucaoItem.id_paciente,
        func.max(EvolucaoItem.nome_paciente).label("nome_paciente"),
        func.max(EvolucaoItem.lote).label("lote"),
        func.count(EvolucaoItem.id).label("total"),
        func.sum(case((EvolucaoItem.status == "OK", 1), else_=0)).label("ok"),
        func.sum(pendente).label("pendente"),
        func.sum(case((EvolucaoItem.status == "ERRO", 1), else_=0)).label("erro"),
        func.max(EvolucaoItem.updated_at).label("updated_at"),
    ).group_by(EvolucaoItem.id_paciente)

    if lote:
        query = query.filter(EvolucaoItem.lote == lote)

    rows = query.order_by(func.max(EvolucaoItem.updated_at).desc()).limit(limit).all()

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
        ]
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
