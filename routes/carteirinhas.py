from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body
from sqlalchemy.orm import Session
from database import get_db
from models import Carteirinha, Job, BaseGuia, User
from typing import List, Optional
import io
import csv
from openpyxl import load_workbook
from sqlalchemy import or_, String, cast
from dependencies import get_current_user

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

def normalize_header(header):
    header = str(header).strip()
    mapping = {
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
        'IDPAGAMENTO': 'IdPagamento',
        'status': 'status',
        'Status': 'status',
        'STATUS': 'status'
    }
    return mapping.get(header, header)

@router.post("/upload")
async def upload_carteirinhas(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    try:
        contents = await file.read()
        rows = []
        
        # 1. Parse File into List of Dicts
        if file.filename.endswith('.csv'):
            # Try decoding with utf-8 then latin1
            text_content = ""
            try:
                text_content = contents.decode('utf-8')
            except UnicodeDecodeError:
                text_content = contents.decode('latin1')
            
            # Detect separator
            dialect = 'excel' # default comma
            if ';' in text_content and text_content.count(';') > text_content.count(','):
                class SemiColonDialect(csv.Dialect):
                    delimiter = ';'
                    quotechar = '"'
                    doublequote = True
                    skipinitialspace = False
                    lineterminator = '\r\n'
                    quoting = csv.QUOTE_MINIMAL
                dialect = SemiColonDialect
            
            f = io.StringIO(text_content)
            reader = csv.DictReader(f, dialect=dialect)
            
            # Normalize Headers
            # DictReader uses fieldnames from first row. We need to remap them.
            if reader.fieldnames:
                original_headers = reader.fieldnames
                normalized_fieldnames = [normalize_header(h) for h in original_headers]
                reader.fieldnames = normalized_fieldnames # Override fieldnames
            
            for row in reader:
                rows.append(row)
                
        else:
            # Excel (.xlsx)
            wb = load_workbook(filename=io.BytesIO(contents), read_only=True, data_only=True)
            ws = wb.active
            
            header_map = {} # col_index -> normalized_name
            is_header = True
            
            for row in ws.iter_rows(values_only=True):
                if not row: continue
                
                # Check for empty row
                if all(cell is None for cell in row): continue

                if is_header:
                    for i, cell_value in enumerate(row):
                        if cell_value:
                            header_map[i] = normalize_header(cell_value)
                    is_header = False
                    continue
                
                # Data Row
                row_data = {}
                for i, cell_value in enumerate(row):
                    if i in header_map:
                        row_data[header_map[i]] = cell_value
                
                rows.append(row_data)
            
            wb.close()
        
        # 2. Process Rows
        carteirinhas_data = []
        errors = []
        
        # Check required columns
        if rows:
             first_row_keys = rows[0].keys()
             if 'Carteirinha' not in first_row_keys:
                  raise HTTPException(status_code=400, detail=f"Arquivo inválido. Coluna 'Carteirinha' não encontrada. Colunas encontradas: {list(first_row_keys)}")
        else:
             # Empty file logic
             pass 

        for index, row in enumerate(rows):
            cart_raw = row.get('Carteirinha')
            cart = str(cart_raw).strip() if cart_raw is not None else ""
            
            paciente_raw = row.get('Paciente')
            paciente = str(paciente_raw).strip() if paciente_raw is not None else ""
            
            # Convert IDs to integers
            id_paciente = None
            id_pagamento = None
            
            if 'IdPaciente' in row and row['IdPaciente']:
                try:
                    val = str(row['IdPaciente']).strip()
                    if val and val.lower() != 'nan' and val.lower() != 'none':
                        id_paciente = int(float(val))
                except (ValueError, TypeError):
                    pass
            
            if 'IdPagamento' in row and row['IdPagamento']:
                try:
                    val = str(row['IdPagamento']).strip()
                    if val and val.lower() != 'nan' and val.lower() != 'none':
                        id_pagamento = int(float(val))
                except (ValueError, TypeError):
                    pass
            
            status_val = row.get('status', 'ativo')
            if not status_val or str(status_val).lower() == 'nan':
                status_val = 'ativo'

            if cart and cart.lower() != 'nan' and cart.lower() != 'none':
                try:
                    validate_carteirinha_format(cart)
                    carteirinhas_data.append({
                        "carteirinha": cart,
                        "paciente": paciente,
                        "id_paciente": id_paciente,
                        "id_pagamento": id_pagamento,
                        "status": status_val
                    })
                except HTTPException as e:
                    errors.append(f"Linha {index+2}: {e.detail}")

        if errors:
            raise HTTPException(status_code=400, detail="Erros de validação encontrados:\n" + "\n".join(errors[:10]) + ("..." if len(errors) > 10 else ""))

        count_added = 0
        count_updated = 0
        
        for item in carteirinhas_data:
            existing = db.query(Carteirinha).filter(Carteirinha.carteirinha == item['carteirinha']).first()
            if existing:
                # Update existing record
                changed = False
                if existing.paciente != item['paciente']:
                    existing.paciente = item['paciente']
                    changed = True
                if existing.id_paciente != item['id_paciente']:
                    existing.id_paciente = item['id_paciente']
                    changed = True
                if existing.id_pagamento != item['id_pagamento']:
                   existing.id_pagamento = item['id_pagamento']
                   changed = True
                
                # Update status if provided in CSV (it defaults to 'ativo' in parsing if missing, 
                # but if CSV has a column 'status', we want to use it).
                # The parsing logic above sets 'status' to 'ativo' if missing/nan.
                # So we effectively overwrite status with CSV or 'ativo'. 
                # If we want to PRESERVE existing status if CSV is empty, we need to change parsing logic.
                # User requirement: "ignore ou apenas atualize se tive algo diferente, staus, id,.."
                # Assuming CSV is the source of truth for status too.
                if existing.status != item['status']:
                    existing.status = item['status']
                    changed = True
                
                if changed:
                    count_updated += 1
            else:
                 new_cart = Carteirinha(
                     carteirinha=item['carteirinha'],
                     paciente=item['paciente'],
                     id_paciente=item.get('id_paciente'),
                     id_pagamento=item.get('id_pagamento'),
                     status=item.get('status', 'ativo')
                 )
                 db.add(new_cart)
                 count_added += 1
        
        db.commit()
        
        return {
            "message": "Upload processed successfully",
            "added": count_added,
            "updated": count_updated,
            "total_processed": len(carteirinhas_data)
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@router.get("/")
def list_carteirinhas(
    skip: int = 0, 
    limit: int = 100, 
    search: Optional[str] = None, 
    status: Optional[str] = None,
    id_pagamento: Optional[str] = None,
    paciente: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
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
def create_carteirinha(item: dict = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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
def update_carteirinha(carteirinha_id: int, item: dict = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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
def delete_carteirinha(carteirinha_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cart = db.query(Carteirinha).filter(Carteirinha.id == carteirinha_id).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Carteirinha not found")
    
    db.delete(cart)
    db.commit()
    return {"message": "Deleted successfully"}
