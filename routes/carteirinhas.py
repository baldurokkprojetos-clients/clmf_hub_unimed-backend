from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body
from sqlalchemy.orm import Session
from database import get_db
from models import Carteirinha, Job, BaseGuia
from typing import List, Optional
import io
from openpyxl import load_workbook
from sqlalchemy import or_, String, cast
import pandas as pd

router = APIRouter(
    prefix="/carteirinhas",
    tags=["Carteirinhas"]
)

def validate_carteirinha_format(code: str):
    # Format: 0064.8000.400948.00-5
    # Length: 21
    # Check simple length first
    if len(code) != 21:
        raise HTTPException(status_code=400, detail=f"Carteirinha inválida: {code}. Deve conter exatamente 21 caracteres.")
    
    # Check punctuation positions
    # Indices: 4, 9, 16 are '.', 19 is '-'
    if code[4] != '.' or code[9] != '.' or code[16] != '.' or code[19] != '-':
        raise HTTPException(status_code=400, detail=f"Carteirinha inválida: {code}. Formato incorreto de pontos e traços. Esperado: 0000.0000.000000.00-0")

@router.post("/upload")
async def upload_carteirinhas(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    db: Session = Depends(get_db)
):
    try:
        contents = await file.read()
        
        # Determine file type
        if file.filename.endswith('.csv'):
            # ... csv reading logic ...
            try:
                df = pd.read_csv(io.BytesIO(contents), encoding='utf-8', sep=';') 
                if len(df.columns) <= 1:
                     io.BytesIO(contents).seek(0)
                     df = pd.read_csv(io.BytesIO(contents), encoding='utf-8', sep=',')
            except UnicodeDecodeError:
                io.BytesIO(contents).seek(0)
                df = pd.read_csv(io.BytesIO(contents), encoding='latin1', sep=';')
        else:
             # Explicitly use openpyxl engine for .xlsx
             df = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
        
        # Normalize columns (uppercase/lowercase issues handling)
        # Create a dict of normalized -> original
        df.columns = df.columns.astype(str).str.strip()
        
        column_mapping = {
            'carteiras': 'Carteirinha',
            'Carteiras': 'Carteirinha',
            'carteirinha': 'Carteirinha',
            'Carteirinha': 'Carteirinha',
            'PACIENTE': 'Paciente',
            'paciente': 'Paciente',
            'Paciente': 'Paciente',
            'ID': 'IdPaciente',
            'id': 'IdPaciente',
            'IdPaciente': 'IdPaciente',
            'id_paciente': 'IdPaciente',
            'IdPagamento': 'IdPagamento',
            'id_pagamento': 'IdPagamento',
            'IDPAGAMENTO': 'IdPagamento'
        }
        
        df.rename(columns=column_mapping, inplace=True)
        
        if 'Carteirinha' not in df.columns:
             raise HTTPException(status_code=400, detail=f"Excel/CSV must contain 'Carteirinha' or 'carteiras' column. Found: {list(df.columns)}")

        carteirinhas_data = []
        errors = []
        
        for index, row in df.iterrows():
            cart = str(row['Carteirinha']).strip()
            paciente = str(row.get('Paciente', '')).strip()
            
            # Convert IDs to integers
            id_paciente = None
            id_pagamento = None
            
            if 'IdPaciente' in row:
                try:
                    val = str(row['IdPaciente']).strip()
                    if val and val.lower() != 'nan':
                        id_paciente = int(float(val))  # float first to handle "123.0"
                except (ValueError, TypeError):
                    pass
            
            if 'IdPagamento' in row:
                try:
                    val = str(row['IdPagamento']).strip()
                    if val and val.lower() != 'nan':
                        id_pagamento = int(float(val))
                except (ValueError, TypeError):
                    pass
            
            if cart and cart.lower() != 'nan':
                try:
                    validate_carteirinha_format(cart)
                    carteirinhas_data.append({
                        "carteirinha": cart,
                        "paciente": paciente,
                        "id_paciente": id_paciente,
                        "id_pagamento": id_pagamento
                    })
                except HTTPException as e:
                    errors.append(f"Linha {index+2}: {e.detail}")

        if errors:
            raise HTTPException(status_code=400, detail="Erros de validação encontrados:\n" + "\n".join(errors[:10]) + ("..." if len(errors) > 10 else ""))

        if overwrite:
            db.query(Carteirinha).delete()
            db.commit()


        count_added = 0
        count_skipped = 0
        
        for item in carteirinhas_data:
            existing = db.query(Carteirinha).filter(Carteirinha.carteirinha == item['carteirinha']).first()
            if existing:
                if overwrite:
                    # Should not match if we deleted, unless dups in file
                    count_skipped += 1
                else:
                    count_skipped += 1
            else:
                 new_cart = Carteirinha(
                     carteirinha=item['carteirinha'],
                     paciente=item['paciente'],
                     id_paciente=item.get('id_paciente'),
                     id_pagamento=item.get('id_pagamento'),
                     status='ativo'
                 )
                 db.add(new_cart)
                 count_added += 1
        
        db.commit()
        
        return {
            "message": "Upload proccessed",
            "added": count_added,
            "skipped": count_skipped,
            "overwrite": overwrite
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc() # Print stack trace to console for debugging
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@router.get("/")
def list_carteirinhas(
    skip: int = 0, 
    limit: int = 100, 
    search: Optional[str] = None, 
    status: Optional[str] = None,
    id_pagamento: Optional[str] = None,
    paciente: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Carteirinha)
    
    # Text Search (General)
    if search:
        search_filter = f"%{search}%"
        # Cast integer columns to string for LIKE search
        query = query.filter(
            or_(
                Carteirinha.paciente.ilike(search_filter), 
                Carteirinha.carteirinha.ilike(search_filter),
                Carteirinha.id_paciente.cast(String).ilike(search_filter),
                Carteirinha.id_pagamento.cast(String).ilike(search_filter)
            )
        )
        
    # Specific Filters
    if status:
        query = query.filter(Carteirinha.status == status)
        
    if id_pagamento:
        # Flexible handling: exact match if int, or partial if treating as string?
        # User asked for "Geral, ID Pagamento" filters not working.
        # Usually ID filter is exact, but let's allow partial for usability or exact if number.
        # Let's use cast for safety.
        query = query.filter(Carteirinha.id_pagamento.cast(String).ilike(f"%{id_pagamento}%"))
        
    if paciente:
        query = query.filter(Carteirinha.paciente.ilike(f"%{paciente}%"))
    
    # Sort alphabetically by patient name
    query = query.order_by(Carteirinha.paciente.asc())
    
    total = query.count()
    carteirinhas = query.offset(skip).limit(limit).all()
    
    return {
        "data": carteirinhas,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@router.post("/")
def create_carteirinha(item: dict = Body(...), db: Session = Depends(get_db)):
    """Create a new carteirinha"""
    if 'carteirinha' not in item:
        raise HTTPException(status_code=400, detail="Field 'carteirinha' is required")
    
    # Validate format
    validate_carteirinha_format(item['carteirinha'])
    
    # Check if already exists
    existing = db.query(Carteirinha).filter(Carteirinha.carteirinha == item['carteirinha']).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Carteirinha {item['carteirinha']} already exists")
    
    new_cart = Carteirinha(
        carteirinha=item['carteirinha'],
        paciente=item.get('paciente', ''),
        id_paciente=item.get('id_paciente'),
        id_pagamento=item.get('id_pagamento'),
        status=item.get('status', 'ativo')
    )
    
    db.add(new_cart)
    db.commit()
    db.refresh(new_cart)
    
    return new_cart

@router.put("/{carteirinha_id}")
def update_carteirinha(carteirinha_id: int, item: dict = Body(...), db: Session = Depends(get_db)):
    cart = db.query(Carteirinha).filter(Carteirinha.id == carteirinha_id).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Carteirinha not found")
    
    if 'carteirinha' in item:
        validate_carteirinha_format(item['carteirinha'])
        cart.carteirinha = item['carteirinha']
    if 'paciente' in item:
        cart.paciente = item['paciente']
    if 'id_paciente' in item:
        cart.id_paciente = item['id_paciente']
    if 'id_pagamento' in item:
        cart.id_pagamento = item['id_pagamento']
    if 'status' in item:
        cart.status = item['status']
        
    db.commit()
    db.refresh(cart)
    return cart

@router.delete("/{carteirinha_id}")
def delete_carteirinha(carteirinha_id: int, db: Session = Depends(get_db)):
    cart = db.query(Carteirinha).filter(Carteirinha.id == carteirinha_id).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Carteirinha not found")
    
    db.delete(cart)
    db.commit()
    return {"message": "Deleted successfully"}
