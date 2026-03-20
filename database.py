import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Handle Supabase Pooler usually requiring transaction mode or specific port
# The .env has keys like SUPABASE_DB_HOST, etc. 
# Or we can construct from standard postgres connection string.
# Using the .env values if available, or falling back to a constructed string.

DB_USER = os.getenv("SUPABASE_DB_USER", "postgres")
DB_PASSWORD = os.getenv("SUPABASE_PASSWORD", "")
DB_HOST = os.getenv("SUPABASE_DB_HOST", "")
DB_PORT = os.getenv("SUPABASE_DB_PORT", "5432")
DB_NAME = os.getenv("SUPABASE_DB_NAME", "postgres")

# Construct URL
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# If variables are missing, fallback or error (but we'll assume they are there as per user context)
# Note: For Supabase transaction pooler (port 6543), we might need to disable statement cache working with sqlalchemy
# engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)

# Disable prepared statements for Supabase Transaction Pooler (port 6543) support
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,       # recycle connections every 5 min to avoid stale SSL
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    connect_args={
        "prepare_threshold": None,   # required for Supabase Transaction Pooler
        "connect_timeout": 10,       # fail fast on unreachable DB
        "keepalives": 1,             # enable TCP keepalives
        "keepalives_idle": 30,       # send keepalive after 30s idle
        "keepalives_interval": 10,   # retry every 10s
        "keepalives_count": 5,       # drop connection after 5 failed keepalives
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
