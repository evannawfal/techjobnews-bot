import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

def clear_queue():
    # 1. Fetch the environment variable
    db_url = os.getenv("DATABASE_URL")
    
    if not db_url:
        print("❌ Error: DATABASE_URL not found.")
        print("Tip: Run this using 'railway run python clear_queue.py' to inject variables.")
        sys.exit(1)

    # 2. Fix the driver prefix for SQLAlchemy 1.4+
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # 3. Create engine with a pre-ping to prevent connection timeouts
    engine = create_engine(db_url, pool_pre_ping=True)

    # 4. Execute the update within a transaction block
    try:
        with engine.begin() as conn:
            query = text("""
                UPDATE post_candidates 
                SET status = 'skipped' 
                WHERE status IN ('pending', 'error');
            """)
            result = conn.execute(query)
            
            print(f"✅ Success! {result.rowcount} rows marked as 'skipped'.")
            print("The bot will ignore these candidates in the next cycle.")
            
    except SQLAlchemyError as e:
        print(f"💥 Database Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"⚠️ An unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    clear_queue()