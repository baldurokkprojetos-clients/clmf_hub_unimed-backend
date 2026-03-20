import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

DB_USER = os.getenv("SUPABASE_DB_USER", "postgres")
DB_PASSWORD = os.getenv("SUPABASE_PASSWORD", "")
DB_HOST = os.getenv("SUPABASE_DB_HOST", "")
DB_PORT = os.getenv("SUPABASE_DB_PORT", "5432")
DB_NAME = os.getenv("SUPABASE_DB_NAME", "postgres")

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Supabase Transaction Pooler (port 6543) + small persistent pool.
# NullPool was causing 400-600ms overhead per request (new TCP/SSL each time).
# Using a small pool (size=2 + overflow=3 = max 5 connections) for performance
# while staying well within Supabase's free tier connection limit.
# pool_recycle=120s prevents stale connections from Supabase idle timeouts.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=120,
    pool_size=2,
    max_overflow=3,
    pool_timeout=20,
    connect_args={
        "prepare_threshold": None,   # required for Supabase Transaction Pooler
        "connect_timeout": 10,       # fail fast if DB unreachable
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
