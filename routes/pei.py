from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db
from models import PatientPei, PeiTemp, BaseGuia, Carteirinha
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
    db: Session = Depends(get_db)
):
    query = db.query(PatientPei).join(Carteirinha)
    query = apply_filters(query, search, status, validade_start, validade_end, vencimento_filter)
    
    total_items = query.count()
    
    # Pagination
    skip = (page - 1) * pageSize
    # Sorting: Pendente first, then Updated At desc
    # status is text: 'Pendente', 'Validado'
    # We can use order_by with a case statement or just simple text sort if alphabetical works (P < V).
    # 'Pendente' comes before 'Validado' alphabetically, so ASC status puts Pendente first.
    # Then updated_at DESC.
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
    query = db.query(PatientPei).join(Carteirinha)
    query = apply_filters(query, search, status, validade_start, validade_end, vencimento_filter)
    
    try:
        results = query.all()
        
        # Generate Excel
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PEI Export"
        
        # Header
        ws.append([
            "ID", "Paciente", "Carteirinha", "Código Terapia", 
            "PEI Semanal", "Validade", "Status", "Atualizado Em"
        ])
        
        for row in results:
            # Handle timezone naive for Excel
            updated_at_val = row.updated_at
            if updated_at_val and updated_at_val.tzinfo:
                updated_at_val = updated_at_val.replace(tzinfo=None)

            ws.append([
                row.id,
                row.carteirinha_rel.paciente if row.carteirinha_rel else "",
                row.carteirinha_rel.carteirinha if row.carteirinha_rel else "",
                row.codigo_terapia,
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
    guia = db.query(BaseGuia).filter(BaseGuia.id == req.guia_id).first()
    if not guia:
        raise HTTPException(404, "Guia not found")
        
    update_patient_pei_backend(db, guia.carteirinha_id, guia.codigo_terapia)
    return {"status": "success"}

def update_patient_pei_backend(db: Session, carteirinha_id: int, codigo_terapia: str):
    # Same logic as Worker
    latest_guia = db.query(BaseGuia).filter(
        BaseGuia.carteirinha_id == carteirinha_id,
        BaseGuia.codigo_terapia == codigo_terapia
    ).order_by(BaseGuia.data_autorizacao.desc(), BaseGuia.id.desc()).first()

    if not latest_guia:
        return

    override = db.query(PeiTemp).filter(PeiTemp.base_guia_id == latest_guia.id).first()
    
    status = "Pendente"
    pei_semanal = 0.0
    validade = None
    
    if latest_guia.data_autorizacao:
        validade = latest_guia.data_autorizacao + timedelta(days=180)
    
    if override:
        pei_semanal = float(override.pei_semanal)
        status = "Validado" 
    else:
        if latest_guia.qtde_solicitada:
             val = float(latest_guia.qtde_solicitada) / 16.0
             pei_semanal = val
             if val.is_integer():
                 status = "Validado"
             else:
                 status = "Pendente"
        else:
            pei_semanal = 0.0
            status = "Pendente"

    patient_pei = db.query(PatientPei).filter(
        PatientPei.carteirinha_id == carteirinha_id,
        PatientPei.codigo_terapia == codigo_terapia
    ).first()

    if not patient_pei:
        patient_pei = PatientPei(
            carteirinha_id=carteirinha_id,
            codigo_terapia=codigo_terapia
        )
        db.add(patient_pei)
    
    patient_pei.base_guia_id = latest_guia.id
    patient_pei.pei_semanal = pei_semanal
    patient_pei.validade = validade
    patient_pei.status = status
    
    db.commit()
