import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routes import auth, carteirinhas, jobs, guias, logs, dashboard, debug_optimization, protocolo

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

async def run_unimed_cron_loop():
    global last_cron_date_clear, last_cron_date_jobs
    while True:
        try:
            now = datetime.now()
            
            # 20:00 - Limpar Guias e PEI
            if now.hour == 20 and now.minute == 0 and last_cron_date_clear != now.date():
                db = SessionLocal()
                from models import Convenio, Carteirinha, BaseGuia, PatientPei, PeiTemp
                unimed = db.query(Convenio).filter(Convenio.nome.ilike('%Unimed Goiania%')).first()
                if unimed:
                    cart_ids = [c.id for c in db.query(Carteirinha.id).filter(Carteirinha.id_convenio == unimed.id).all()]
                    if cart_ids:
                        guia_ids = [g.id for g in db.query(BaseGuia.id).filter(BaseGuia.carteirinha_id.in_(cart_ids)).all()]
                        if guia_ids:
                            db.query(PeiTemp).filter(PeiTemp.base_guia_id.in_(guia_ids)).delete(synchronize_session=False)
                        db.query(PatientPei).filter(PatientPei.carteirinha_id.in_(cart_ids)).delete(synchronize_session=False)
                        db.query(BaseGuia).filter(BaseGuia.carteirinha_id.in_(cart_ids)).delete(synchronize_session=False)
                        db.commit()
                        print(f"CRON (20:00): Guias e PEI limpos para Unimed Goiania.")
                db.close()
                last_cron_date_clear = now.date()
                
            # 20:01 - Criar Jobs
            if now.hour == 20 and now.minute == 1 and last_cron_date_jobs != now.date():
                db = SessionLocal()
                from models import Convenio
                from services import job_service
                unimed = db.query(Convenio).filter(Convenio.nome.ilike('%Unimed Goiania%')).first()
                if unimed:
                    created = job_service.create_all_jobs(db, id_convenio=unimed.id)
                    db.commit()
                    print(f"CRON (20:01): {created} jobs enfileirados para Unimed Goiania.")
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
