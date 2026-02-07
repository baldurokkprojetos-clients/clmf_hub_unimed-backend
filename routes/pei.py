from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db
from models import PatientPei, PeiTemp, BaseGuia, Carteirinha
from services.pei_service import update_patient_pei
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, timedelta, datetime
from sqlalchemy import func, or_, text, case, and_
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
                PatientPei.codigo_terapia.ilike(search_term)
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

@router.get("/dashboard")
def get_dashboard_stats(db: Session = Depends(get_db)):
    today = date.today()
    d7_end = today + timedelta(days=7)
    d30_end = today + timedelta(days=30)
    
    # Aggregated query to reduce round-trips (1 query instead of 6)
    stats = db.query(
        func.count(PatientPei.id).label("total"),
        func.sum(case((PatientPei.validade < today, 1), else_=0)).label("vencidos"),
        func.sum(case((and_(PatientPei.validade >= today, PatientPei.validade <= d7_end), 1), else_=0)).label("vence_d7"),
        func.sum(case((and_(PatientPei.validade >= today, PatientPei.validade <= d30_end), 1), else_=0)).label("vence_d30"),
        func.sum(case((PatientPei.status == 'Pendente', 1), else_=0)).label("pendentes"),
        func.sum(case((PatientPei.status == 'Validado', 1), else_=0)).label("validados")
    ).first()

    return {
        "total": stats.total or 0,
        "vencidos": stats.vencidos or 0,
        "vence_d7": stats.vence_d7 or 0,
        "vence_d30": stats.vence_d30 or 0,
        "pendentes": stats.pendentes or 0,
        "validados": stats.validados or 0
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
    db: Session = Depends(get_db)
):
    query = db.query(PatientPei).join(Carteirinha).outerjoin(BaseGuia, PatientPei.base_guia_id == BaseGuia.id)
    query = apply_filters(query, search, status, validade_start, validade_end, vencimento_filter)
    
    total_items = query.count()
    
    # Pagination
    skip = (page - 1) * pageSize
    results = query.order_by(PatientPei.status.asc(), PatientPei.updated_at.desc()).offset(skip).limit(pageSize).all()
    
    data = []
    for row in results:
        data.append({
            "id": row.id,
            "carteirinha_id": row.carteirinha_id,
            "carteirinha": row.carteirinha_rel.carteirinha if row.carteirinha_rel else "",
            "paciente": row.carteirinha_rel.paciente if row.carteirinha_rel else "",
            "codigo_terapia": row.codigo_terapia,
            "pei_semanal": row.pei_semanal,
            "validade": row.validade,
            "status": row.status,
            "base_guia_id": row.base_guia_id,
            "guia_vinculada": row.base_guia_rel.guia if row.base_guia_rel else "-",
            "sessoes_autorizadas": row.base_guia_rel.sessoes_autorizadas if row.base_guia_rel else 0,
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
    db: Session = Depends(get_db)
):
    query = db.query(PatientPei).join(Carteirinha).outerjoin(BaseGuia, PatientPei.base_guia_id == BaseGuia.id)
    query = apply_filters(query, search, status, validade_start, validade_end, vencimento_filter)
    
    try:
        results = query.all()
        
        # Generate Excel
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PEI Export"
        
        # Header
        ws.append([
            "ID Paciente", "Paciente", "Carteirinha", "Código Terapia", 
            "Guia Vinculada", "Data Autorização", "Senha", "Qtd Autorizada",
            "PEI Semanal", "Validade", "Status", "Atualizado Em"
        ])
        
        for row in results:
            # Handle timezone naive for Excel
            updated_at_val = row.updated_at
            if updated_at_val and updated_at_val.tzinfo:
                updated_at_val = updated_at_val.replace(tzinfo=None)
            
            # Base Guia Helpers
            guia_num = row.base_guia_rel.guia if row.base_guia_rel else "-"
            data_auth = row.base_guia_rel.data_autorizacao if row.base_guia_rel else None
            senha = row.base_guia_rel.senha if row.base_guia_rel else "-"
            qtd_aut = row.base_guia_rel.sessoes_autorizadas if row.base_guia_rel else 0
            
            # ID Paciente (from Carteirinha model field id_paciente, not database PK)
            id_paciente_real = row.carteirinha_rel.id_paciente if row.carteirinha_rel else ""

            ws.append([
                id_paciente_real,
                row.carteirinha_rel.paciente if row.carteirinha_rel else "",
                row.carteirinha_rel.carteirinha if row.carteirinha_rel else "",
                row.codigo_terapia,
                guia_num,
                data_auth,
                senha,
                qtd_aut,
                row.pei_semanal,
                row.validade,
                row.status,
                updated_at_val
            ])
            
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"export_pei_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
        
        return Response(
            content=output.getvalue(), 
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            headers=headers
        )
    except Exception as e:
        print(f"Export Error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erro ao exportar: {str(e)}")

@router.post("/override")
def override_pei(
    req: PeiOverrideRequest,
    db: Session = Depends(get_db)
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

