from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user
from models import PatientPei, PeiTemp, BaseGuia, Carteirinha
from services.pei_service import update_patient_pei
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, timedelta, datetime
from sqlalchemy import func, or_, text
import io
import openpyxl

router = APIRouter(
    prefix="/pei",
    tags=["PEI"]
)

class PeiOverrideRequest(BaseModel):
    guia_id: int
    pei_semanal: float

def apply_filters(query, search, status, validade_start, validade_end, vencimento_filter):
    # Text Search (Patient, Carteirinha, Therapy)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Carteirinha.paciente.ilike(search_term),
                Carteirinha.carteirinha.ilike(search_term),
                PatientPei.codigo_procedimento.ilike(search_term)
            )
        )
    
    # Status Enum
    if status:
        query = query.filter(PatientPei.status == status)

    # Date Range
    if validade_start:
        query = query.filter(PatientPei.validade >= validade_start)
    if validade_end:
        query = query.filter(PatientPei.validade <= validade_end)

    # Smart Vencimento Filters
    today = date.today()
    if vencimento_filter:
        if vencimento_filter == 'vencidos':
            query = query.filter(PatientPei.validade < today)
        elif vencimento_filter == 'vence_d7':
            target_date = today + timedelta(days=7)
            query = query.filter(PatientPei.validade >= today, PatientPei.validade <= target_date)
        elif vencimento_filter == 'vence_d30':
            target_date = today + timedelta(days=30)
            query = query.filter(PatientPei.validade >= today, PatientPei.validade <= target_date)
            
    return query

def latest_pei_ids_subquery(db):
    """IDs do registro patient_pei mais recente por (id_paciente, codigo_procedimento).

    Sem este filtro a listagem/export exibia guias duplicadas quando:
    - o mesmo id_paciente possui múltiplas carteirinhas (cada uma com seu
      patient_pei do mesmo procedimento), ou
    - existem registros concorrentes de patient_pei para o mesmo par
      (race histórico do trigger antes do unique index).
    Mantém apenas a linha mais recente (maior id)."""
    return db.query(func.max(PatientPei.id))\
        .join(Carteirinha, PatientPei.carteirinha_id == Carteirinha.id)\
        .group_by(Carteirinha.id_paciente, PatientPei.codigo_procedimento)\
        .subquery()


@router.get("/dashboard")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    today = date.today()
    
    # Base query for active patients? Or just all?
    # Stats: Vencidos, Vence D+7, Vence D+30
    
    # Vencidos
    vencidos = db.query(func.count(PatientPei.id)).filter(PatientPei.validade < today).scalar()
    
    # Vence D+7
    d7_end = today + timedelta(days=7)
    vence_d7 = db.query(func.count(PatientPei.id)).filter(
        PatientPei.validade >= today, 
        PatientPei.validade <= d7_end
    ).scalar()
    
    # Vence D+30
    d30_end = today + timedelta(days=30)
    vence_d30 = db.query(func.count(PatientPei.id)).filter(
        PatientPei.validade >= today, 
        PatientPei.validade <= d30_end
    ).scalar()
    
    total = db.query(func.count(PatientPei.id)).scalar()
    pendentes = db.query(func.count(PatientPei.id)).filter(PatientPei.status == 'Pendente').scalar()
    validados = db.query(func.count(PatientPei.id)).filter(PatientPei.status == 'Validado').scalar()

    return {
        "total": total,
        "vencidos": vencidos or 0,
        "vence_d7": vence_d7 or 0,
        "vence_d30": vence_d30 or 0,
        "pendentes": pendentes or 0,
        "validados": validados or 0
    }

@router.get("/")
def list_pei(
    page: int = 1,
    pageSize: int = 50,
    search: Optional[str] = None,
    status: Optional[str] = None, # Validado, Pendente
    validade_start: Optional[date] = None,
    validade_end: Optional[date] = None,
    vencimento_filter: Optional[str] = None, # vencidos, vence_d7, vence_d30
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # Optimized query selecting only necessary columns
    query = db.query(
        PatientPei.id,
        PatientPei.carteirinha_id,
        Carteirinha.carteirinha,
        Carteirinha.paciente,
        PatientPei.codigo_procedimento,
        PatientPei.pei_semanal,
        PatientPei.validade,
        PatientPei.status,
        PatientPei.base_guia_id,
        BaseGuia.guia.label("guia_vinculada"),
        BaseGuia.sessoes_autorizadas,
        PatientPei.updated_at,
        Carteirinha.id_paciente # For export matching if needed
    ).join(Carteirinha, PatientPei.carteirinha_id == Carteirinha.id)\
     .outerjoin(BaseGuia, PatientPei.base_guia_id == BaseGuia.id)
    
    query = apply_filters(query, search, status, validade_start, validade_end, vencimento_filter)

    # Dedup: apenas o PEI mais recente por (id_paciente, codigo_procedimento)
    query = query.filter(PatientPei.id.in_(latest_pei_ids_subquery(db)))

    total_items = query.count()
    
    # Pagination
    skip = (page - 1) * pageSize
    results = query.order_by(PatientPei.status.asc(), PatientPei.updated_at.desc()).offset(skip).limit(pageSize).all()
    
    data = []
    for row in results:
        data.append({
            "id": row.id,
            "carteirinha_id": row.carteirinha_id,
            "carteirinha": row.carteirinha or "",
            "paciente": row.paciente or "",
            "codigo_procedimento": row.codigo_procedimento,
            "pei_semanal": row.pei_semanal,
            "validade": row.validade,
            "status": row.status,
            "base_guia_id": row.base_guia_id,
            "guia_vinculada": row.guia_vinculada or "-",
            "sessoes_autorizadas": row.sessoes_autorizadas or 0,
            "updated_at": row.updated_at
        })

    return {
        "data": data,
        "total": total_items,
        "page": page,
        "pageSize": pageSize
    }

@router.get("/export")
def export_pei(
    search: Optional[str] = None,
    status: Optional[str] = None,
    validade_start: Optional[date] = None,
    validade_end: Optional[date] = None,
    vencimento_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    print("DEBUG: Starting PEI Export...")
    try:
        # Generate Excel (Write Only Mode for Performance)
        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet("PEI Export")
        
        # Header
        ws.append([
            "ID Paciente", "Paciente", "Carteirinha", "ID Pagamento", "Código Terapia", 
            "Guia Vinculada", "Data Autorização", "Senha", "Qtd Autorizada",
            "PEI Semanal", "Validade", "Status", "Atualizado Em"
        ])
        
        print("DEBUG: Executing Query (Raw Tuples)...")
        # Optimization: Select ONLY the columns we need as a tuple
        # This prevents SQLAlchemy from creating thousands of objects and doing N+1 loads
        query = db.query(
            Carteirinha.id_paciente,          # 0
            Carteirinha.paciente,             # 1
            Carteirinha.carteirinha,          # 2
            Carteirinha.id_pagamento,         # 3 - REQUESTED FIELD
            PatientPei.codigo_procedimento,        # 4
            BaseGuia.guia,                    # 5
            BaseGuia.data_autorizacao,        # 6
            BaseGuia.senha,                   # 7
            BaseGuia.sessoes_autorizadas,     # 8
            PatientPei.pei_semanal,           # 9
            PatientPei.validade,              # 10
            PatientPei.status,                # 11
            PatientPei.updated_at             # 12
        ).select_from(PatientPei)\
         .join(Carteirinha, PatientPei.carteirinha_id == Carteirinha.id)\
         .outerjoin(BaseGuia, PatientPei.base_guia_id == BaseGuia.id)
        
        query = apply_filters(query, search, status, validade_start, validade_end, vencimento_filter)
        
        # Exclude temporary patients from export
        query = query.filter(Carteirinha.is_temporary == False)

        # Dedup: apenas o PEI mais recente por (id_paciente, codigo_procedimento)
        # — elimina guias duplicadas no export (multi-carteirinha do mesmo
        # paciente e registros concorrentes de patient_pei)
        query = query.filter(PatientPei.id.in_(latest_pei_ids_subquery(db)))
        
        # Use yield_per to stream results from DB
        results = query.yield_per(1000)
        
        count = 0
        for row in results:
            count += 1
            # Row is now a tuple, access by index or name
            
            # Handle timezone naive
            updated_at_val = row.updated_at
            if updated_at_val and updated_at_val.tzinfo:
                updated_at_val = updated_at_val.replace(tzinfo=None)
            
            # Helper for dates
            def fmt(d): return d.strftime("%d/%m/%Y") if d else ""

            ws.append([
                row.id_paciente or "",                  # ID Paciente
                row.paciente or "",                     # Paciente
                row.carteirinha or "",                  # Carteirinha
                row.id_pagamento or "",                 # ID Pagamento (Direct from select)
                row.codigo_procedimento,                     # Codigo Procedimento
                row.guia or "-",                        # Guia
                fmt(row.data_autorizacao),              # Data Auth
                row.senha or "-",                       # Senha
                row.sessoes_autorizadas or 0,           # Qtd Aut
                row.pei_semanal,                        # PEI
                fmt(row.validade),                      # Validade
                row.status if row.status else "Pendente", # Status
                fmt(updated_at_val)                     # Atualizado Em
            ])
        
        print(f"DEBUG: Processed {count} rows. Saving...")
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"export_pei_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
        
        from fastapi.responses import StreamingResponse
        return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)

    except Exception as e:
        print(f"DEBUG: Export Error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erro ao exportar: {str(e)}")

@router.post("/override")
def override_pei(
    req: PeiOverrideRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # Upsert PeiTemp
    temp = db.query(PeiTemp).filter(PeiTemp.base_guia_id == req.guia_id).first()
    if not temp:
        temp = PeiTemp(base_guia_id=req.guia_id, pei_semanal=req.pei_semanal)
        db.add(temp)
    else:
        temp.pei_semanal = req.pei_semanal
    db.commit()
    

    # Recalculate
    # Actually, the trigger on PeiTemp (after_insert/update) should have already handled this 
    # because we committed above.
    # However, to be safe or if the commit happened before trigger fully propagated in some async scenarios (unlikely in sync sqlalchemy),
    # we can explicitly call it or just rely on the commit.
    # The event listener fires *after* flush/commit usually depending on config.
    # But since we just committed, the 'after_update' for PeiTemp should have fired.
    
    # Just in case we want to return the updated status immediately:
    # update_patient_pei(db, guia.carteirinha_id, guia.codigo_terapia)
    
    return {"status": "success"}

# Note: update_patient_pei_backend removed as it is now in services/pei_service.py

