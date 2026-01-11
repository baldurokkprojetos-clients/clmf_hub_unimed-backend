from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Log, Carteirinha, Job
from typing import List, Optional

router = APIRouter(
    tags=["Logs"]
)

@router.get("/")
def list_logs(
    limit: int = 100, 
    level: Optional[str] = None, 
    job_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Log).join(Job, isouter=True).join(Carteirinha, isouter=True)
    
    if level:
        query = query.filter(Log.level == level)
    if job_id:
        query = query.filter(Log.job_id == job_id)
        
    logs = query.order_by(Log.created_at.desc()).limit(limit).all()
    
    # Return enriched data
    result = []
    for log in logs:
        result.append({
            "id": log.id,
            "level": log.level,
            "message": log.message,
            "created_at": log.created_at,
            "job_id": log.job_id,
            "carteirinha": log.carteirinha_rel.carteirinha if log.carteirinha_rel else None,
            "paciente": log.carteirinha_rel.paciente if log.carteirinha_rel else None
        })
        
    return result
