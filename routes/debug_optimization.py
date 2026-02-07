from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from database import get_db, engine
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/debug",
    tags=["Debug"]
)

@router.get("/force-db-indexes")
def force_db_indexes(db: Session = Depends(get_db)):
    """
    Endpoint to force creation of performance indexes from within the Render environment.
    Useful when local connection is blocked (IPv6/Pooler).
    """
    web_log = []
    
    def log(msg):
        print(msg)
        web_log.append(msg)

    log("🔌 Starting Remote Index Verification...")
    
    indexes_to_check = [
        ("idx_patient_pei_status", "patient_pei", "status"),
        ("idx_patient_pei_validade", "patient_pei", "validade"),
        ("idx_patient_pei_base_guia", "patient_pei", "base_guia_id"),
        ("idx_patient_pei_updated_at", "patient_pei", "updated_at"),
        ("idx_base_guias_carteirinha", "base_guias", "carteirinha_id"),
        ("ix_jobs_status", "jobs", "status"),
        ("idx_carteirinhas_paciente", "carteirinhas", "paciente"),
        ("idx_carteirinhas_carteirinha", "carteirinhas", "carteirinha"),
        ("idx_users_api_key_manual", "users", "api_key")
    ]

    try:
        # We use the raw engine connection to avoid transaction nesting issues if any,
        # though inside a route we are usually in a transaction.
        # Ideally we want autocommit for CREATE INDEX concurrently if possible, 
        # but standard CREATE INDEX is fine.
        
        with engine.connect() as conn:
            # Commit any pending transaction
            conn.commit()
            
            for idx_name, table, col in indexes_to_check:
                check_sql = text(f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'")
                exists = conn.execute(check_sql).fetchone()
                
                if exists:
                    log(f"✅ Index '{idx_name}' exists.")
                else:
                    log(f"⚠️ Index '{idx_name}' MISSING. Creating...")
                    try:
                        create_sql = text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({col})")
                        conn.execute(create_sql)
                        # Commit immediately
                        conn.commit()
                        log(f"   ✅ Created '{idx_name}'")
                    except Exception as e:
                        log(f"   ❌ Failed to create '{idx_name}': {str(e)}")
                        # Rollback in case of error to proceed to next
                        conn.rollback()

    except Exception as e:
        log(f"❌ Critical Error: {str(e)}")
        return {
            "status": "error",
            "log": web_log,
            "detail": str(e)
        }

    return {
        "status": "success",
        "message": "Optimization complete",
        "log": web_log
    }
