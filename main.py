from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routes import auth, carteirinhas, jobs, guias, logs, dashboard

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Base Guias Unimed API", version="1.0.0")

# Configure CORS - Allow all origins for now
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite qualquer origem
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Base Guias Unimed API is running"}

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(carteirinhas.router)
app.include_router(jobs.router)
app.include_router(guias.router)
app.include_router(logs.router, prefix="/api/logs")
app.include_router(dashboard.router)
from routes import pei
app.include_router(pei.router)
