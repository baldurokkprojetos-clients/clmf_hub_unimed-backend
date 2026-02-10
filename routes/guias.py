from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user
from models import BaseGuia, Carteirinha
from typing import Optional
from datetime import date, datetime, timedelta
from openpyxl import Workbook
import io

router = APIRouter(
    prefix="/guias",
    tags=["Guias"]
)

@router.get("/")
def list_guias(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    created_at_start: Optional[date] = None, 
    created_at_end: Optional[date] = None,
    carteirinha_id: Optional[int] = None,
    limit: int = 25,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(BaseGuia)
    
    if created_at_start:
        query = query.filter(BaseGuia.updated_at >= created_at_start)
    if created_at_end:
        # Inclusive end date (until end of day)
        end_dt = datetime.combine(created_at_end, datetime.min.time()) + timedelta(days=1)
        query = query.filter(BaseGuia.updated_at < end_dt)
    if carteirinha_id:
        query = query.filter(BaseGuia.carteirinha_id == carteirinha_id)

    total = query.count()
    guias = query.order_by(BaseGuia.created_at.desc()).limit(limit).offset(skip).all()
    
    return {"data": guias, "total": total, "skip": skip, "limit": limit}

@router.get("/export")
def export_guias(
    created_at_start: Optional[str] = Query(None, description="Start Date (YYYY-MM-DD)"),
    created_at_end: Optional[str] = Query(None, description="End Date (YYYY-MM-DD)"),
    carteirinha_id: Optional[int] = Query(None, description="Filter by Carteirinha ID"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # Optimized Excel Generation
    try:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Guias")
        
        headers = ["Carteirinha", "Paciente", "Guia", "Data_Autorização", "Senha", 
                   "Validade", "Código_Terapia", "Qtde_Solicitada", "Sessões Autorizadas", "Importado_Em"]
        ws.append(headers)
        
        print("DEBUG: Executing Query with raw tuples...")
        # Use yield_per to reduce memory overhead and tuple selection to avoid N+1 and lazy loading issues
        query = db.query(
            Carteirinha.carteirinha,         # 0
            Carteirinha.paciente,            # 1
            BaseGuia.guia,                   # 2
            BaseGuia.data_autorizacao,       # 3
            BaseGuia.senha,                  # 4
            BaseGuia.validade,               # 5
            BaseGuia.codigo_terapia,         # 6
            BaseGuia.qtde_solicitada,        # 7
            BaseGuia.sessoes_autorizadas,    # 8
            BaseGuia.created_at              # 9
        ).select_from(BaseGuia).join(Carteirinha, BaseGuia.carteirinha_id == Carteirinha.id)

        if created_at_start:
            query = query.filter(BaseGuia.updated_at >= created_at_start)
        if created_at_end:
            # Add one day to include full end date
            end_dt = datetime.strptime(created_at_end, '%Y-%m-%d').date() + timedelta(days=1)
            query = query.filter(BaseGuia.updated_at <= str(end_dt))
        if carteirinha_id:
            query = query.filter(BaseGuia.carteirinha_id == carteirinha_id)
        
        results = query.yield_per(1000)
        
        count = 0
        for row in results:
            count += 1
            ws.append([
                row.carteirinha or "",
                row.paciente or "",
                row.guia,
                fmt_date(row.data_autorizacao),
                row.senha,
                fmt_date(row.validade),
                row.codigo_terapia,
                row.qtde_solicitada,
                row.sessoes_autorizadas,
                row.created_at.strftime("%d/%m/%Y %H:%M:%S") if row.created_at else ""
            ])

        print(f"DEBUG: Processed {count} rows. Saving Workbook...")
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        headers = {
            'Content-Disposition': 'attachment; filename="guias_exportadas.xlsx"'
        }
        return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        print(f"Export Error: {e}")
        # Return the actual error details for debugging instead of generic 500
        # In production this might be bad, but for debugging now it's essential
        raise HTTPException(status_code=500, detail=f"Erro ao gerar arquivo: {str(e)}")
