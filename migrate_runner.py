import os
import sys
from sqlalchemy import text
from database import engine

def run_migrations():
    print("Running migrations...")
    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    
    # Order matters: 0001 then 0002
    files = sorted([f for f in os.listdir(migrations_dir) if f.endswith(".sql")])
    
    with engine.connect() as connection:
        for file in files:
            print(f"Executing {file}...")
            with open(os.path.join(migrations_dir, file), "r", encoding="utf-8") as f:
                sql = f.read()
                # Split by statement if needed, or execute block. 
                # Simple SQL files usually can be executed as one block if supported, 
                # but SQLAlchemy text() might prefer single statements or BEGIN/COMMIT blocks.
                # Let's try executing the whole file content.
                try:
                    connection.execute(text(sql))
                    connection.commit()
                    print(f"Finished {file}")
                except Exception as e:
                    print(f"Error executing {file}: {e}")
                    connection.rollback()
                    
    print("Migrations completed.")

if __name__ == "__main__":
    run_migrations()
