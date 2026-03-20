import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routes import auth, carteirinhas, jobs, guias, logs, dashboard, debug_optimization

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
    "https://clmf-hub-unimed-frontend.vercel.app"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_cleanup_loop())

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
