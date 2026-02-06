
from sqlalchemy.orm import Session
from models import Carteirinha
from datetime import datetime, timezone
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def delete_expired_patients(db: Session):
    """
    Deletes temporary patients whose expiration time has passed.
    Cascading deletes should handle related Jobs, Guias, and PEI records.
    """
    try:
        now = datetime.now(timezone.utc)
        
        # In SQLalchemy, DateTime with timezone=True usually works with timezone-aware datetimes.
        # Ensure DB is storing with TZ or consistently. Postgre constraints 'TIMESTAMP WITH TIME ZONE'
        
        expired_patients = db.query(Carteirinha).filter(
            Carteirinha.is_temporary == True,
            Carteirinha.expires_at <= now
        ).all()
        
        count = len(expired_patients)
        if count > 0:
            logger.info(f"Cleanup: Found {count} expired temporary patients. Deleting...")
            
            for patient in expired_patients:
                logger.info(f"Deleting expired patient: {patient.carteirinha} (ID: {patient.id})")
                db.delete(patient)
            
            db.commit()
            logger.info("Cleanup successfully completed.")
        else:
            logger.debug("Cleanup: No expired patients found.")
            
        return count
            
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        db.rollback()
        return 0
