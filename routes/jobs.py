from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from database import get_db
from models import Job, Carteirinha
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime, timedelta

router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"]
)

class TemporaryPatientData(BaseModel):
    carteirinha: str
    paciente: str

class CreateJobRequest(BaseModel):
    type: str # 'single', 'multiple', 'all', 'temp'
    carteirinha_ids: Optional[List[int]] = None
    temp_patient: Optional[TemporaryPatientData] = None

@router.post("/")
def create_jobs(request: CreateJobRequest, db: Session = Depends(get_db)):
    created_count = 0
    from services import job_service
    
    if request.type == 'all':
        created_count = job_service.create_all_jobs(db)
            
    elif request.type in ['single', 'multiple']:
        if not request.carteirinha_ids:
             raise HTTPException(status_code=400, detail="carteirinha_ids required for single/multiple")
        
        created_count = job_service.create_jobs_bulk(db, request.carteirinha_ids)
    
    elif request.type == 'temp':
        if not request.temp_patient:
             raise HTTPException(status_code=400, detail="temp_patient data required for temp job")
             
        created_count = job_service.create_temp_job(db, request.temp_patient.carteirinha, request.temp_patient.paciente)
                
    else:
        raise HTTPException(status_code=400, detail="Invalid job type")

    db.commit()
    return {"message": f"Created/Queued jobs", "count": created_count}

@router.get("/")
def list_jobs(
    status: Optional[str] = None,
    created_at_start: Optional[date] = None,
    created_at_end: Optional[date] = None,
    limit: int = 25, 
    skip: int = 0,
    db: Session = Depends(get_db)
):
    query = db.query(Job)
    
    if status:
        query = query.filter(Job.status == status)
        
    if created_at_start:
        query = query.filter(Job.created_at >= created_at_start)
    if created_at_end:
        end_dt = datetime.combine(created_at_end, datetime.min.time()) + timedelta(days=1)
        query = query.filter(Job.created_at < end_dt)
    
    # Order by priority desc, created_at asc
    total = query.count()
    jobs = query.order_by(Job.priority.desc(), Job.created_at.desc()).limit(limit).offset(skip).all()
    # Note: Changed order to desc created_at to show newest first
    
    return {"data": jobs, "total": total, "skip": skip, "limit": limit}

@router.delete("/{id}")
def delete_job(id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == id).first()
    if not job:
        raise HTTPException(404, "Job not found")
        
    # Validation: Only delete if error and attempts > 3
    # User said: "probido exclusão de jobs em andamento ou com status sucess"
    # "um Job só poderá ser excluido se status seja error e tentativas maior que 3"
    
    allowed = (job.status == 'error' and job.attempts > 3)
    # Or maybe allow pending if it's stuck? User didn't specify. Sticking to strict rule.
    
    if not allowed:
         raise HTTPException(status_code=400, detail="Exclusão permitida apenas para Jobs com erro e mais de 3 tentativas.")
         
    db.delete(job)
    db.commit()
    return {"message": "Job deleted"}

@router.post("/{id}/retry")
def retry_job(id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    # Validation: Same as delete?
    # "ao clicar em reenviar exibir mensagem de confirmação, o status será alterado para pending"
    # User implied logic for buttons "Jobs error... e habilita botões de ação"
    # So implies retry is available for error jobs. 
    # And "reenviar(caso estatus seja error e tentativas maior que 3)"
    
    allowed = (job.status == 'error' and job.attempts > 3)
    
    if not allowed:
        raise HTTPException(status_code=400, detail="Reenvio permitido apenas para Jobs com erro e mais de 3 tentativas.")

    job.status = 'pending'
    job.attempts = 0
    job.locked_by = None
    job.updated_at = datetime.utcnow()
    
    db.commit()
    return {"message": "Job queued for retry", "status": job.status}
