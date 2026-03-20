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

    log("🔌 Starting Senior-Level Performance Optimization...")
    
    # 1. Enable pg_trgm for fast text search
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.commit()
            log("✅ Extension 'pg_trgm' ensured.")
    except Exception as e:
        log(f"⚠️ Could not ensure 'pg_trgm': {e}")

    # 2. Advanced Index Definition
    # Format: (name, table, column, type) -- type can be 'gin' for trigrams
    indexes_to_check = [
        # Search Optimizations (Trigrams - GIN)
        ("trgm_idx_carteirinhas_paciente", "carteirinhas", "paciente", "USING gin (paciente gin_trgm_ops)"),
        ("trgm_idx_carteirinhas_numero", "carteirinhas", "carteirinha", "USING gin (carteirinha gin_trgm_ops)"),
        
        # Ordering & Filtering Optimizations (B-tree)
        ("idx_base_guias_created_at", "base_guias", "created_at", "(created_at DESC)"),
        ("idx_base_guias_updated_at", "base_guias", "updated_at", "(updated_at DESC)"),
        ("idx_patient_pei_updated_at_desc", "patient_pei", "updated_at", "(updated_at DESC)"),
        ("idx_patient_pei_status_order", "patient_pei", "status", "(status)"),
        
        # Foreign Keys (Join performance)
        ("idx_jobs_carteirinha_id", "jobs", "carteirinha_id", "(carteirinha_id)"),
        ("idx_base_guias_carteirinha_id", "base_guias", "carteirinha_id", "(carteirinha_id)"),
        ("idx_patient_pei_carteirinha_id", "patient_pei", "carteirinha_id", "(carteirinha_id)"),
        
        # Legacy/Essential checks
        ("ix_jobs_status", "jobs", "status", "(status)"),
        ("idx_carteirinhas_id_pagamento", "carteirinhas", "id_pagamento", "(id_pagamento)")
    ]

    try:
        with engine.connect() as conn:
            conn.commit()
            
            for idx_name, table, col, idx_def in indexes_to_check:
                check_sql = text(f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'")
                exists = conn.execute(check_sql).fetchone()
                
                if exists:
                    log(f"✅ Index '{idx_name}' already exists.")
                else:
                    log(f"⚠️ Index '{idx_name}' MISSING. Creating...")
                    try:
                        # Construct CREATE INDEX SQL
                        # If idx_def starts with 'USING', it's a specialized index type
                        if idx_def.startswith("USING"):
                            sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} {idx_def}"
                        else:
                            sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} {idx_def}"
                        
                        conn.execute(text(sql))
                        conn.commit()
                        log(f"   ✅ Created '{idx_name}'")
                    except Exception as e:
                        log(f"   ❌ Failed '{idx_name}': {str(e)}")
                        conn.rollback()

            # 3. Optimize DB Statistics
            log("📊 Running ANALYZE to optimize query planner...")
            conn.execute(text("ANALYZE"))
            conn.commit()
            log("✅ Database statistics updated.")

    except Exception as e:
        log(f"❌ Critical Error: {str(e)}")
        return {
            "status": "error",
            "log": web_log,
            "detail": str(e)
        }

    return {
        "status": "success",
        "message": "Advanced Optimization Complete",
        "log": web_log
    }
