"""
Protocolo Service — Async batch orchestration for PDF extraction.

Manages the lifecycle of lotes (batches):
  create → enqueue → process (background) → complete

Processing runs in a background thread so the HTTP request returns immediately.
"""

import os
import io
import zipfile
import logging
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Upload directory (relative to backend root)
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "protocolo")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "protocolo_output")

# Max files per batch
MAX_FILES_PER_LOTE = 100

# Max file size (10MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

# ZIP split size (10MB)
ZIP_SPLIT_SIZE_BYTES = 10 * 1024 * 1024


def _get_db_session():
    """Create a fresh DB session for background threads."""
    from database import SessionLocal
    return SessionLocal()


def _get_gemini_client():
    """Lazy-load the Gemini client singleton."""
    from services.gemini_client import GeminiClient
    return GeminiClient.from_env()


def recalculate_lote_totals(db: Session, lote_id: int):
    """Force recalculate all totals for a lote from its arquivos using optimized SQL queries."""
    from models import ProtocoloLote, ProtocoloArquivo
    from sqlalchemy import func
    
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        return

    # Aggregate counts in a single query if possible, or simple separate count queries
    counts = db.query(
        ProtocoloArquivo.status,
        func.count(ProtocoloArquivo.id)
    ).filter(ProtocoloArquivo.lote_id == lote_id).group_by(ProtocoloArquivo.status).all()
    
    status_map = dict(counts)
    
    lote.total_arquivos = sum(status_map.values())
    lote.total_sucesso = status_map.get("sucesso", 0)
    lote.total_erro = (
        status_map.get("erro", 0) + 
        status_map.get("falha", 0) + 
        status_map.get("revisao", 0)
    )
    lote.total_processado = sum(
        count for status, count in status_map.items() 
        if status not in ["pendente", "processando"]
    )
    
    db.commit()


# ---------------------------------------------------------------------------
# Lote Creation
# ---------------------------------------------------------------------------

def create_lote(db: Session, user_id: int, files: list) -> dict:
    """
    Create a new lote and save uploaded files to disk.

    Args:
        db: Active DB session
        user_id: ID of the authenticated user
        files: List of UploadFile objects from FastAPI

    Returns:
        dict with lote_id and file count
    """
    from models import ProtocoloLote, ProtocoloArquivo

    if len(files) > MAX_FILES_PER_LOTE:
        raise ValueError(f"Máximo de {MAX_FILES_PER_LOTE} arquivos por lote")

    # Create lote record
    lote = ProtocoloLote(
        user_id=user_id,
        status="pending",
        total_arquivos=len(files),
    )
    db.add(lote)
    db.flush()  # Get lote.id

    # Create upload directory for this lote
    lote_dir = os.path.join(UPLOAD_DIR, str(lote.id))
    os.makedirs(lote_dir, exist_ok=True)

    output_dir = os.path.join(OUTPUT_DIR, str(lote.id))
    os.makedirs(output_dir, exist_ok=True)

    # Save each file and create arquivo records
    for upload_file in files:
        content = upload_file.file.read()
        file_size = len(content)

        if file_size > MAX_FILE_SIZE_BYTES:
            # Create record with error status
            arquivo = ProtocoloArquivo(
                lote_id=lote.id,
                nome_original=upload_file.filename,
                status="erro",
                tamanho_bytes=file_size,
                erro_mensagem=f"Arquivo excede limite de {MAX_FILE_SIZE_BYTES // (1024*1024)}MB",
                caminho_original="",
            )
            db.add(arquivo)
            continue

        # Save to disk
        safe_name = f"{lote.id}_{upload_file.filename}"
        file_path = os.path.join(lote_dir, safe_name)
        with open(file_path, "wb") as f:
            f.write(content)

        arquivo = ProtocoloArquivo(
            lote_id=lote.id,
            nome_original=upload_file.filename,
            status="pendente",
            tamanho_bytes=file_size,
            caminho_original=file_path,
        )
        db.add(arquivo)

    db.commit()
    db.refresh(lote)

    # Start background processing
    thread = threading.Thread(
        target=_process_lote_background,
        args=(lote.id,),
        daemon=True,
    )
    thread.start()

    logger.info(f"Lote {lote.id} created with {len(files)} files. Background processing started.")

    return {
        "lote_id": lote.id,
        "total_arquivos": len(files),
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# Background Processing
# ---------------------------------------------------------------------------

def _process_lote_background(lote_id: int):
    """
    Process all pending files in a lote. Runs in a background thread.
    
    This function creates its own DB session and Gemini client,
    ensuring complete independence from the HTTP request lifecycle.
    """
    from models import ProtocoloLote, ProtocoloArquivo
    from services.extraction_pipeline import process_single_extraction, rename_file_safe

    db = _get_db_session()

    try:
        gemini = _get_gemini_client()
    except Exception as e:
        logger.error(f"Failed to initialize Gemini client for lote {lote_id}: {e}")
        lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
        if lote:
            lote.status = "error"
            lote.updated_at = datetime.utcnow()
            db.commit()
        db.close()
        return

    try:
        # Mark lote as processing
        lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
        if not lote:
            logger.error(f"Lote {lote_id} not found")
            return

        lote.status = "processing"
        lote.updated_at = datetime.utcnow()
        db.commit()

        # Get all pending files
        arquivos = db.query(ProtocoloArquivo).filter(
            ProtocoloArquivo.lote_id == lote_id,
            ProtocoloArquivo.status == "pendente",
        ).all()

        output_dir = os.path.join(OUTPUT_DIR, str(lote_id))
        os.makedirs(output_dir, exist_ok=True)

        total_sucesso = 0
        total_erro = 0

        for arquivo in arquivos:
            try:
                # Check for cancellation before processing each file
                db.refresh(lote)
                if lote.status == "cancelled":
                    logger.info(f"Lote {lote_id} foi cancelado pelo usuário.")
                    break

                # Mark file as processing
                arquivo.status = "processando"
                arquivo.updated_at = datetime.utcnow()
                db.commit()

                # Read PDF bytes
                if not arquivo.caminho_original or not os.path.exists(arquivo.caminho_original):
                    raise FileNotFoundError(f"Arquivo não encontrado: {arquivo.caminho_original}")

                with open(arquivo.caminho_original, "rb") as f:
                    pdf_bytes = f.read()

                # Call Gemini
                gemini_result = gemini.extract_from_pdf(pdf_bytes)

                # Store meta about which model/key was used
                meta = gemini_result.pop("_meta", {})
                arquivo.gemini_model_used = meta.get("model", "unknown")
                arquivo.gemini_api_key_index = meta.get("key_index", -1)

                # Run extraction pipeline
                pipeline_result = process_single_extraction(gemini_result)

                # Store extracted data
                arquivo.numero_guia_prestador = gemini_result.get("numeroGuiaPrestador", "")
                arquivo.nome_beneficiario = pipeline_result.get("nome_beneficiario", "")
                arquivo.numero_guia_principal = gemini_result.get("numeroGuiaPrincipal", "")
                arquivo.atendimentos = gemini_result.get("atendimentos", [])
                arquivo.guia_normalizada = pipeline_result.get("guia_normalizada", "")

                if pipeline_result["success"]:
                    # Rename the physical file
                    nome_final = pipeline_result["nome_final"]
                    arquivo.nome_final = nome_final

                    try:
                        final_path = rename_file_safe(
                            arquivo.caminho_original,
                            output_dir,
                            nome_final,
                        )
                        arquivo.caminho_final = final_path
                        arquivo.status = "sucesso"
                        total_sucesso += 1
                    except Exception as rename_err:
                        arquivo.status = "erro"
                        arquivo.erro_mensagem = f"Erro ao renomear: {str(rename_err)}"
                        total_erro += 1
                else:
                    # Validation/blacklist failed → mark for review
                    arquivo.status = "revisao"
                    arquivo.erro_mensagem = "; ".join(pipeline_result["errors"])
                    # Still build a proposed name for manual edit
                    arquivo.nome_final = pipeline_result.get("nome_final", "")
                    total_erro += 1

            except Exception as e:
                logger.error(f"Error processing file {arquivo.id} ({arquivo.nome_original}): {e}")
                arquivo.status = "erro"
                arquivo.erro_mensagem = str(e)[:500]
                total_erro += 1

            arquivo.updated_at = datetime.utcnow()
            db.commit()

        # Update lote totals only if not cancelled
        if lote_id:
            recalculate_lote_totals(db, lote_id)
            
            # Re-fetch lote to update status
            lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
            if lote and lote.status != "cancelled":
                lote.status = "completed"
                lote.updated_at = datetime.utcnow()
                db.commit()

            logger.info(f"Lote {lote_id} completed and totals recalculated.")

    except Exception as e:
        logger.error(f"Fatal error processing lote {lote_id}: {e}")
        try:
            lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
            if lote:
                lote.status = "error"
                lote.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Status Query
# ---------------------------------------------------------------------------

def get_lote_status(db: Session, lote_id: int) -> Optional[dict]:
    """
    Get full status of a lote including all arquivo details.
    """
    from models import ProtocoloLote, ProtocoloArquivo

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote:
        return None

    arquivos = db.query(ProtocoloArquivo).filter(
        ProtocoloArquivo.lote_id == lote_id
    ).order_by(ProtocoloArquivo.id.asc()).all()

    return {
        "lote_id": lote.id,
        "status": lote.status,
        "total_arquivos": lote.total_arquivos,
        "total_processado": lote.total_processado,
        "total_sucesso": lote.total_sucesso,
        "total_erro": lote.total_erro,
        "created_at": lote.created_at.isoformat() if lote.created_at else None,
        "updated_at": lote.updated_at.isoformat() if lote.updated_at else None,
        "arquivos": [
            {
                "id": a.id,
                "nome_original": a.nome_original,
                "nome_final": a.nome_final,
                "status": a.status,
                "tamanho_bytes": a.tamanho_bytes,
                "numero_guia_prestador": a.numero_guia_prestador,
                "nome_beneficiario": a.nome_beneficiario,
                "numero_guia_principal": a.numero_guia_principal,
                "atendimentos": a.atendimentos,
                "guia_normalizada": a.guia_normalizada,
                "erro_mensagem": a.erro_mensagem,
                "gemini_model_used": a.gemini_model_used,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
            for a in arquivos
        ],
    }


def list_lotes(db: Session, user_id: Optional[int] = None, limit: int = 25, skip: int = 0) -> dict:
    """List lotes with pagination, optionally filtered by user."""
    from models import ProtocoloLote

    query = db.query(ProtocoloLote)
    if user_id:
        query = query.filter(ProtocoloLote.user_id == user_id)

    total = query.count()
    lotes = query.order_by(ProtocoloLote.created_at.desc()).limit(limit).offset(skip).all()

    return {
        "data": [
            {
                "id": l.id,
                "status": l.status,
                "total_arquivos": l.total_arquivos,
                "total_processado": l.total_processado,
                "total_sucesso": l.total_sucesso,
                "total_erro": l.total_erro,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "updated_at": l.updated_at.isoformat() if l.updated_at else None,
            }
            for l in lotes
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# Reprocess Errors
# ---------------------------------------------------------------------------

def reprocess_errors(db: Session, lote_id: int) -> int:
    """
    Reset errored/review files back to 'pendente' and re-trigger processing.
    Returns the number of files queued for reprocessing.
    """
    from models import ProtocoloLote, ProtocoloArquivo

    arquivos = db.query(ProtocoloArquivo).filter(
        ProtocoloArquivo.lote_id == lote_id,
        ProtocoloArquivo.status.in_(["erro", "revisao"]),
    ).all()

    if not arquivos:
        return 0

    for a in arquivos:
        a.status = "pendente"
        a.erro_mensagem = None
        a.updated_at = datetime.utcnow()

    # Reset lote status
    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if lote:
        lote.status = "processing"
        lote.updated_at = datetime.utcnow()

    db.commit()

    # Restart background processing
    thread = threading.Thread(
        target=_process_lote_background,
        args=(lote_id,),
        daemon=True,
    )
    thread.start()

    return len(arquivos)


# ---------------------------------------------------------------------------
# Cancel Processing
# ---------------------------------------------------------------------------

def cancel_lote(db: Session, lote_id: int) -> bool:
    """
    Cancel an ongoing processing lote.
    """
    from models import ProtocoloLote

    lote = db.query(ProtocoloLote).filter(ProtocoloLote.id == lote_id).first()
    if not lote or lote.status not in ["pending", "processing"]:
        return False

    lote.status = "cancelled"
    lote.updated_at = datetime.utcnow()
    db.commit()

    return True


# ---------------------------------------------------------------------------
# Update Filename (Manual Edit)
# ---------------------------------------------------------------------------

def update_arquivo_nome(db: Session, arquivo_id: int, novo_nome: str) -> Optional[dict]:
    """Update the final filename of an arquivo (manual override) and validate it."""
    from models import ProtocoloArquivo, ProtocoloLote

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return None

    old_status = arquivo.status

    arquivo.nome_final = novo_nome
    # A manual edit implies manual validation
    arquivo.status = "sucesso"
    arquivo.erro_mensagem = None
    arquivo.updated_at = datetime.utcnow()

    arquivo.updated_at = datetime.utcnow()

    db.commit()
    
    # Recalculate lote totals
    recalculate_lote_totals(db, arquivo.lote_id)
    
    db.refresh(arquivo)

    return {
        "id": arquivo.id,
        "nome_final": arquivo.nome_final,
        "status": arquivo.status,
    }


def update_arquivo_atendimentos(db: Session, arquivo_id: int, atendimentos: list[dict]) -> Optional[dict]:
    """Update the atendimentos JSON of an arquivo."""
    from models import ProtocoloArquivo

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return None

    arquivo.atendimentos = atendimentos
    arquivo.status = "sucesso"  # Promoting to success after manual adjustment
    arquivo.erro_mensagem = None
    arquivo.updated_at = datetime.utcnow()
    
    db.commit()
    
    # Recalculate totals
    recalculate_lote_totals(db, arquivo.lote_id)
    
    db.refresh(arquivo)

    return {
        "id": arquivo.id,
        "atendimentos": arquivo.atendimentos,
        "status": arquivo.status
    }


# ---------------------------------------------------------------------------
# Delete Arquivo
# ---------------------------------------------------------------------------

def delete_arquivo(db: Session, arquivo_id: int) -> bool:
    """Delete a single file and update lote totals."""
    from models import ProtocoloArquivo, ProtocoloLote

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return False

    lote_id = arquivo.lote_id
    status = arquivo.status

    db.delete(arquivo)
    db.commit()
    
    # Update lote totals
    recalculate_lote_totals(db, lote_id)

    return True


# ---------------------------------------------------------------------------
# ZIP Download Generation
# ---------------------------------------------------------------------------

def generate_download_zip(db: Session, lote_id: int) -> list[io.BytesIO]:
    """
    Generate ZIP file(s) for all successfully processed files.
    Splits into chunks of ZIP_SPLIT_SIZE_BYTES.

    Returns a list of BytesIO objects (one per ZIP chunk).
    """
    from models import ProtocoloArquivo

    arquivos = db.query(ProtocoloArquivo).filter(
        ProtocoloArquivo.lote_id == lote_id,
        ProtocoloArquivo.status == "sucesso",
        ProtocoloArquivo.caminho_final != None,
    ).all()

    if not arquivos:
        return []

    # Collect valid files
    valid_files = []
    for a in arquivos:
        if a.caminho_final and os.path.exists(a.caminho_final):
            valid_files.append((a.nome_final or a.nome_original, a.caminho_final))

    if not valid_files:
        return []

    # Build ZIP(s) with size splitting
    zip_buffers = []
    current_buffer = io.BytesIO()
    current_zip = zipfile.ZipFile(current_buffer, "w", zipfile.ZIP_DEFLATED)
    current_size = 0

    for filename, filepath in valid_files:
        file_size = os.path.getsize(filepath)

        # Check if adding this file would exceed the split size
        if current_size + file_size > ZIP_SPLIT_SIZE_BYTES and current_size > 0:
            # Close current ZIP and start a new one
            current_zip.close()
            current_buffer.seek(0)
            zip_buffers.append(current_buffer)

            current_buffer = io.BytesIO()
            current_zip = zipfile.ZipFile(current_buffer, "w", zipfile.ZIP_DEFLATED)
            current_size = 0

        current_zip.write(filepath, arcname=filename)
        current_size += file_size

    # Close the last ZIP
    current_zip.close()
    current_buffer.seek(0)
    zip_buffers.append(current_buffer)

    return zip_buffers


def get_arquivo_file_path(db: Session, arquivo_id: int) -> Optional[tuple[str, str]]:
    """
    Get the file path for individual download.
    Returns (filepath, filename) or None.
    """
    from models import ProtocoloArquivo

    arquivo = db.query(ProtocoloArquivo).filter(ProtocoloArquivo.id == arquivo_id).first()
    if not arquivo:
        return None

    # Prefer final (renamed) file, fallback to original
    if arquivo.caminho_final and os.path.exists(arquivo.caminho_final):
        return arquivo.caminho_final, arquivo.nome_final or arquivo.nome_original
    elif arquivo.caminho_original and os.path.exists(arquivo.caminho_original):
        return arquivo.caminho_original, arquivo.nome_original

    return None
