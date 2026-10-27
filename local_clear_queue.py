import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.config import Config
from src.db.session import make_engine


def clear_queue_local() -> None:
    """
    Local helper to mark all pending/error candidates as skipped, using
    the same DATABASE_URL and normalization logic as the main app.

    Usage (from project root):
        python local_clear_queue.py
    """
    if not Config.DATABASE_URL:
        print("❌ Error: DATABASE_URL not set in your environment or .env file.")
        sys.exit(1)

    engine = make_engine(Config.DATABASE_URL)

    try:
        with engine.begin() as conn:
            query = text(
                """
                UPDATE post_candidates 
                SET status = 'skipped' 
                WHERE status IN ('pending', 'error');
                """
            )
            result = conn.execute(query)

            print(f"✅ Local Success! {result.rowcount} rows marked as 'skipped'.")

    except SQLAlchemyError as e:
        print(f"💥 Local Database Error: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    clear_queue_local()