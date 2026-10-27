import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData, text

# Load environment variables
load_dotenv(override=True)

def nuke_database():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not found.")
        return

    engine = create_engine(db_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)

    # We use a transaction to ensure everything is wiped cleanly
    with engine.begin() as conn:
        print("☢️ Starting database nuke...")
        
        # Disable triggers temporarily to bypass foreign key constraints
        # This is the "brute force" way to empty linked tables in Postgres
        for table in reversed(metadata.sorted_tables):
            print(f"Emptying table: {table.name}")
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE;'))
            
        print("✅ Database is now empty and ID counters are reset.")

if __name__ == "__main__":
    confirm = input("This will DELETE ALL data in your database. Are you sure? (y/n): ")
    if confirm.lower() == 'y':
        nuke_database()
    else:
        print("Nuke aborted.")