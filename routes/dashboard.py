from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import Job, Carteirinha, BaseGuia
from sqlalchemy import func, case

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)

@router.get("/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    # Simple counts
    total_carteirinhas = db.query(Carteirinha).count()
    total_guias = db.query(BaseGuia).count()
    
    # Aggregated Job stats
    # Fetch total and status counts in one query
    job_stats = db.query(
        func.count(Job.id).label("total"),
        func.sum(case((Job.status == 'success', 1), else_=0)).label("success"),
        func.sum(case((Job.status == 'error', 1), else_=0)).label("error"),
        func.sum(case((Job.status.in_(['pending', 'processing']), 1), else_=0)).label("pending")
    ).first()

    total_jobs = job_stats.total or 0
    jobs_success = job_stats.success or 0
    jobs_error = job_stats.error or 0
    jobs_pending = job_stats.pending or 0
    
    return {
        "overview": {
            "total_carteirinhas": total_carteirinhas,
            "total_guias": total_guias,
            "total_jobs": total_jobs
        },
        "jobs_status": {
            "success": jobs_success,
            "error": jobs_error,
            "pending": jobs_pending
        }
    }
