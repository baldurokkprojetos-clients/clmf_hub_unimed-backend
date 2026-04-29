"""
Routes for the Protocolo-Fichas module (PDF extraction via Gemini AI).

Endpoints:
    POST   /api/protocolo/lotes                    — Upload PDFs & create batch
    GET    /api/protocolo/lotes                    — List user's batches
    GET    /api/protocolo/lotes/{id}/status         — Detailed batch status + files
    POST   /api/protocolo/lotes/{id}/cancelar      — Cancel processing
    POST   /api/protocolo/lotes/{id}/reprocessar   — Reprocess failed files
    GET    /api/protocolo/arquivos/{id}/download    — Download individual file
    PATCH  /api/protocolo/arquivos/{id}             — Edit file's final name
    GET    /api/protocolo/lotes/{id}/download-zip   — Download ZIP (partitioned)
    GET    /api/protocolo/config                    — API keys/model status
"""

import os
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_protocolo_user

router = APIRouter(
    prefix="/protocolo",
    tags=["Protocolo Fichas"],
)


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class UpdateNomeRequest(BaseModel):
    nome_final: str

class AtendimentoItem(BaseModel):
    data: str
    assinatura: str

class UpdateAtendimentosRequest(BaseModel):
    atendimentos: List[AtendimentoItem]


# ---------------------------------------------------------------------------
# POST /lotes — Upload & Create Batch
# ---------------------------------------------------------------------------

@router.post("/lotes")
async def create_lote(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """
    Upload multiple PDF files and create a processing batch.
    Returns the lote_id immediately; processing happens in background.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado")

    # Validate all files are PDFs
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Arquivo '{f.filename}' não é um PDF"
            )

    from services.protocolo_service import create_lote as svc_create

    try:
        result = svc_create(db, current_user.id, files)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar lote: {str(e)}")


# ---------------------------------------------------------------------------
# GET /lotes — List Batches
# ---------------------------------------------------------------------------

@router.get("/lotes")
def list_lotes(
    limit: int = Query(25, ge=1, le=100),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """List all batches for the current user."""
    from services.protocolo_service import list_lotes as svc_list

    return svc_list(db, user_id=current_user.id, limit=limit, skip=skip)


# ---------------------------------------------------------------------------
# GET /lotes/{id}/status — Detailed Status
# ---------------------------------------------------------------------------

@router.get("/lotes/{lote_id}/status")
def get_lote_status(
    lote_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Get detailed status of a lote and its files."""
    from services.protocolo_service import get_lote_status as svc_status
    from services.protocolo_service import recalculate_lote_totals
    from models import ProtocoloLote
    
    # Recalculate only if still active (processing or pending)
    lote_basic = db.query(ProtocoloLote.id, ProtocoloLote.status).filter(ProtocoloLote.id == lote_id).first()
    if lote_basic and lote_basic.status in ["pending", "processing"]:
        recalculate_lote_totals(db, lote_id)
    
    result = svc_status(db, lote_id)
    if not result:
        raise HTTPException(status_code=404, detail="Lote não encontrado")
    return result


# ---------------------------------------------------------------------------
# POST /lotes/{id}/cancelar — Cancel Processing
# ---------------------------------------------------------------------------

@router.post("/lotes/{lote_id}/cancelar")
def cancel_lote(
    lote_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Cancel an ongoing batch processing."""
    from services.protocolo_service import cancel_lote as svc_cancel

    success = svc_cancel(db, lote_id)
    if not success:
        raise HTTPException(status_code=400, detail="Não foi possível cancelar o lote (já finalizado ou inexistente)")

    return {"message": "Processamento cancelado com sucesso"}


# ---------------------------------------------------------------------------
# POST /lotes/{id}/reprocessar — Reprocess Errors
# ---------------------------------------------------------------------------

@router.post("/lotes/{lote_id}/reprocessar")
def reprocess_errors(
    lote_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Reprocess only the files that failed or need review."""
    from services.protocolo_service import reprocess_errors as svc_reprocess

    count = svc_reprocess(db, lote_id)
    if count == 0:
        raise HTTPException(status_code=400, detail="Nenhum arquivo com erro para reprocessar")

    return {"message": f"{count} arquivo(s) reenviado(s) para reprocessamento", "count": count}


# ---------------------------------------------------------------------------
# GET /arquivos/{id}/download — Individual Download
# ---------------------------------------------------------------------------

@router.get("/arquivos/{arquivo_id}/download")
def download_arquivo(
    arquivo_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Download a single processed file."""
    from services.protocolo_service import get_arquivo_file_path

    result = get_arquivo_file_path(db, arquivo_id)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    filepath, filename = result
    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/pdf",
    )


# ---------------------------------------------------------------------------
# PATCH /arquivos/{id} — Edit Final Filename
# ---------------------------------------------------------------------------

@router.patch("/arquivos/{arquivo_id}")
def update_arquivo(
    arquivo_id: int,
    body: UpdateNomeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Update the final filename of a file (manual override)."""
    from services.protocolo_service import update_arquivo_nome

    result = update_arquivo_nome(db, arquivo_id, body.nome_final)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return result


# ---------------------------------------------------------------------------
# PATCH /arquivos/{id}/atendimentos — Edit Datas/Assinaturas
# ---------------------------------------------------------------------------

@router.patch("/arquivos/{arquivo_id}/atendimentos")
def update_atendimentos(
    arquivo_id: int,
    body: UpdateAtendimentosRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Update the atendimentos (dates/signatures) of a file."""
    from services.protocolo_service import update_arquivo_atendimentos

    atend_dicts = [{"data": a.data, "assinatura": a.assinatura} for a in body.atendimentos]
    result = update_arquivo_atendimentos(db, arquivo_id, atend_dicts)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return result


# ---------------------------------------------------------------------------
# DELETE /arquivos/{id} — Delete Arquivo
# ---------------------------------------------------------------------------

@router.delete("/arquivos/{arquivo_id}")
def delete_arquivo(
    arquivo_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Delete a single file from the session."""
    from services.protocolo_service import delete_arquivo as svc_delete

    result = svc_delete(db, arquivo_id)
    if not result:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return {"message": "Arquivo excluído com sucesso"}


# ---------------------------------------------------------------------------
# GET /lotes/{id}/download-zip — ZIP Download (Partitioned)
# ---------------------------------------------------------------------------

@router.get("/lotes/{lote_id}/download-zip")
def download_zip(
    lote_id: int,
    part: int = Query(1, ge=1, description="Part number (1-indexed)"),
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """
    Download a ZIP file containing all successfully processed files.
    ZIPs are split into 10MB parts. Use ?part=N to download specific parts.
    """
    from services.protocolo_service import generate_download_zip

    zip_parts = generate_download_zip(db, lote_id)

    if not zip_parts:
        raise HTTPException(status_code=404, detail="Nenhum arquivo processado disponível para download")

    if part > len(zip_parts):
        raise HTTPException(
            status_code=400,
            detail=f"Part {part} não existe. Total de parts: {len(zip_parts)}"
        )

    zip_buffer = zip_parts[part - 1]
    part_suffix = f"_{part:02d}" if len(zip_parts) > 1 else ""
    filename = f"LOTE_{lote_id:03d}{part_suffix}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Parts": str(len(zip_parts)),
        },
    )


# ---------------------------------------------------------------------------
# GET /stats — Monthly Statistics
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    current_user=Depends(get_protocolo_user),
):
    """Get monthly statistics (total files processed and total cost)."""
    from models import ProtocoloLote
    from sqlalchemy import func
    from datetime import datetime

    # Get current month start
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    # Calculate total successful files this month for this user
    stats = db.query(
        func.sum(ProtocoloLote.total_sucesso).label("total_sucesso"),
        func.count(ProtocoloLote.id).label("total_lotes")
    ).filter(
        ProtocoloLote.user_id == current_user.id,
        ProtocoloLote.created_at >= month_start
    ).first()

    total_sucesso = stats.total_sucesso or 0
    
    # Pricing
    cost_per_file = 0.02
    monthly_cost = total_sucesso * cost_per_file

    return {
        "monthly_sucesso": total_sucesso,
        "monthly_cost": round(monthly_cost, 2),
        "total_lotes": stats.total_lotes or 0
    }



# ---------------------------------------------------------------------------
# GET /config — API Status
# ---------------------------------------------------------------------------

@router.get("/config")
def get_config(
    current_user=Depends(get_protocolo_user),
):
    """Get current Gemini API configuration status (no secrets exposed)."""
    try:
        from services.gemini_client import GeminiClient, MODELS_PRIORITY
        client = GeminiClient.from_env()
        return {
            "total_keys": client.total_keys,
            "models": MODELS_PRIORITY,
            "status": "ok",
        }
    except Exception as e:
        return {
            "total_keys": 0,
            "models": [],
            "status": f"error: {str(e)}",
        }
