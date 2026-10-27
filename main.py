import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routes import auth, carteirinhas, jobs, guias, logs, dashboard, debug_optimization, protocolo, evolucoes, cron

# Create tables — retry on temporary DB unavailability (e.g. Supabase instability)
for _attempt in range(3):
    try:
        Base.metadata.create_all(bind=engine)
        print("[startup] DB tables verified OK.")
        break
    except Exception as _e:
        print(f"[startup] DB not available (attempt {_attempt + 1}/3): {_e}")
        if _attempt < 2:
            time.sleep(5)
        else:
            print("[startup] Could not reach DB at startup — continuing anyway.")

app = FastAPI(title="Base Guias Unimed API", version="1.0.0", redirect_slashes=False)

# Configure CORS
origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://clmf-gestor.vercel.app",
    "https://clmf-hub-unimed-frontend.vercel.app",
    "https://base-guias-frontend.vercel.app",
    "https://base-guias-frontend-*.vercel.app",
]

import re

def is_allowed_origin(origin: str) -> bool:
    """Check if origin matches allowed patterns (including Vercel preview URLs)."""
    # Exact match
    if origin in origins:
        return True
    # Allow all *.vercel.app subdomains
    if re.match(r'https://[a-z0-9-]+-[a-z0-9-]+\.vercel\.app$', origin):
        return True
    return False

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r'https://.*\.vercel\.app',
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Base Guias Unimed API is running"}

import asyncio
from database import SessionLocal
from services.cleanup_service import delete_expired_patients

async def run_cleanup_loop():
    while True:
        try:
            db = SessionLocal()
            delete_expired_patients(db)
            db.close()
        except Exception as e:
            print(f"Cleanup Loop Error: {e}")
        
        await asyncio.sleep(600) # Run every 10 minutes

from datetime import datetime

last_cron_date_clear = None
last_cron_date_jobs = None

def _cron_unimed_pausado() -> bool:
    """Pausa do ciclo Unimed: env UNIMED_CRON_ENABLED=false ou toggle do
    frontend (convenios.status do convênio 3, id_pagamento=3). Pausado = não
    inativa guias (23:00) nem cria jobs (23:01) — congela o estado atual."""
    import os as _os
    from sqlalchemy import text as _text
    if _os.getenv("UNIMED_CRON_ENABLED", "true").lower() == "false":
        return True
    try:
        _db = SessionLocal()
        _row = _db.execute(_text("SELECT status FROM convenios WHERE id = 3")).fetchone()
        _db.close()
        return not (bool(_row) and _row[0] == "ativo")
    except Exception as _e:
        print(f"CRON: falha ao ler status do convênio 3 (assume ATIVO): {_e}")
        return False

async def run_unimed_cron_loop():
    global last_cron_date_clear, last_cron_date_jobs
    while True:
        try:
            now = datetime.now()
            
            # 23:00 GMT+00 (20:00 Brasília) - Inativar Guias e PEI (Unimed Goiania id_pagamento=3)
            # Guias/PEI não são mais apagados: guias sem atualização (updated_at) há mais
            # de 24h ficam Inativas; o trigger calculate_patient_pei inativa/recalcula o
            # patient_pei de cada par. Uma nova atualização da guia (worker) reativa o par.
            if now.hour == 23 and now.minute == 0 and last_cron_date_clear != now.date():
                if _cron_unimed_pausado():
                    print("CRON (23:00 GMT+00): inativação de guias PAUSADA (env/toggle do frontend).")
                else:
                    db = SessionLocal()
                    try:
                        from sqlalchemy import text
                        # Não altera updated_at — a idade da guia é o próprio critério
                        result = db.execute(text(
                            "UPDATE base_guias SET ativo = FALSE "
                            "WHERE ativo = TRUE AND updated_at < NOW() - INTERVAL '24 hours'"
                        ))
                        db.commit()
                        print(f"CRON (23:00 GMT+00): {result.rowcount} guias marcadas como inativas (sem atualização há mais de 24h).")
                    except Exception as e:
                        db.rollback()
                        print(f"CRON (23:00 GMT+00) ERRO DE BANCO: {e}")
                    finally:
                        db.close()
                last_cron_date_clear = now.date()
                
            # 23:01 GMT+00 (20:01 Brasília) - Criar Jobs (Unimed Goiania id_pagamento=3)
            # Pausável pelo frontend (convenio 3 status inativo → /cron/unimed)
            # ou via env UNIMED_CRON_ENABLED=false (corte geral). Padrão: ATIVO.
            if now.hour == 23 and now.minute == 1 and last_cron_date_jobs != now.date():
                if _cron_unimed_pausado():
                    print("CRON (23:01 GMT+00): criação de jobs Unimed PAUSADA (env/toggle do frontend).")
                    last_cron_date_jobs = now.date()
                else:
                    db = SessionLocal()
                    try:
                        from services import job_service
                        # id_pagamento=3 é Unimed Goiania (hardcoded conforme configuração do sistema)
                        total_created = job_service.create_all_jobs(db, id_convenio=3)
                        db.commit()
                        print(f"CRON (23:01 GMT+00): {total_created} jobs enfileirados para Unimed Goiania (id=3).")
                    except Exception as e:
                        db.rollback()
                        print(f"CRON (23:01 GMT+00) ERRO: {e}")
                    finally:
                        db.close()
                    last_cron_date_jobs = now.date()
                
        except Exception as e:
            print(f"Unimed Cron Loop Error: {e}")
            
        await asyncio.sleep(20) # Verifica a cada 20 segundos

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_cleanup_loop())
    asyncio.create_task(run_unimed_cron_loop())

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(carteirinhas.router)
app.include_router(jobs.router)
app.include_router(guias.router)
app.include_router(logs.router, prefix="/api/logs")
app.include_router(dashboard.router)
from routes import workers
app.include_router(workers.router)
from routes import pei
app.include_router(pei.router)
app.include_router(debug_optimization.router)
app.include_router(protocolo.router)
app.include_router(evolucoes.router)
app.include_router(cron.router)
