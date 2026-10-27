from __future__ import annotations

import os
from dotenv import load_dotenv

# Load .env explicitly here, and force it to override any stuck shell variables
load_dotenv(override=True)

def _getenv_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


class Config:
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")

    POLL_SECONDS: int = int(os.getenv("POLL_SECONDS", "900"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    DRY_RUN: bool = _getenv_bool("DRY_RUN", False)

    # X/Twitter OAuth1 user-context (Updated to match your .env file)
    X_API_KEY: str | None = os.getenv("X_API_KEY")
    X_API_SECRET: str | None = os.getenv("X_API_SECRET")
    X_ACCESS_TOKEN: str | None = os.getenv("X_ACCESS_TOKEN")
    X_ACCESS_TOKEN_SECRET: str | None = os.getenv("X_ACCESS_TOKEN_SECRET")

    # OpenAI (optional; used for summary + engagement hook)
    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL: str | None = os.getenv("OPENAI_MODEL")


    @classmethod
    def validate(cls) -> None:
        if not cls.DATABASE_URL:
            raise RuntimeError("Missing required env var DATABASE_URL")
        if cls.DRY_RUN:
            return
            
        missing = [k for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET") if not getattr(cls, k)]
        if missing:
            raise RuntimeError(f"Missing required Twitter env vars: {', '.join(missing)}")