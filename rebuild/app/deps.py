from __future__ import annotations

from typing import Generator
from sqlalchemy.orm import Session
from app.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user_id() -> int:
    """Single-user local app — always user #1. Replace with auth when needed."""
    return 1
