from sqlalchemy.orm import Session
from models import Job, Carteirinha
from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import HTTPException
import random

def create_jobs_bulk(db: Session, carteirinha_ids: List[int]) -> int:
    """
    Creates multiple jobs for existing carteirinhas in a single bulk operation.
    """
    if not carteirinha_ids:
        return 0
        
    # Optimize: Fetch valid IDs in one query to avoid N+1
    valid_ids = db.query(Carteirinha.id).filter(Carteirinha.id.in_(carteirinha_ids)).all()
    # valid_ids is list of tuples [(1,), (2,)]
    valid_ids = [vid[0] for vid in valid_ids]
    
    if valid_ids:
        new_jobs = [Job(carteirinha_id=cid, status="pending") for cid in valid_ids]
        db.bulk_save_objects(new_jobs)
        return len(new_jobs)
    
    return 0

def create_all_jobs(db: Session) -> int:
    """
    Creates jobs for ALL non-temporary carteirinhas.
    """
    all_carteirinhas = db.query(Carteirinha).filter(Carteirinha.is_temporary == False).all()
    new_jobs = []
    for cart in all_carteirinhas:
         new_jobs.append(Job(carteirinha_id=cart.id, status="pending"))
    
    if new_jobs:
        db.bulk_save_objects(new_jobs)
        
    return len(new_jobs)

def create_temp_job(db: Session, carteirinha: str, paciente: str) -> int:
    """
    Creates a temporary patient and job.
    """
    # Check if carteirinha already exists (even temp)
    existing = db.query(Carteirinha).filter(Carteirinha.carteirinha == carteirinha).first()
    cart_id = None
    
    if existing:
        # Update expiry if temporary?
        if existing.is_temporary:
            existing.expires_at = datetime.utcnow() + timedelta(hours=1)
            existing.paciente = paciente # Update name just in case
        cart_id = existing.id
    else:
        # Create new temporary patient
        fake_id_paciente = random.randint(900000, 999999) 
        
        new_cart = Carteirinha(
            carteirinha=carteirinha,
            paciente=paciente,
            id_paciente=fake_id_paciente,
            is_temporary=True,
            expires_at=datetime.utcnow() + timedelta(hours=1)
        )
        db.add(new_cart)
        db.flush() # Get ID
        cart_id = new_cart.id
    
    # Create Job
    job = Job(carteirinha_id=cart_id, status="pending")
    db.add(job)
    
    return 1
