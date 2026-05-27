"""Database migrations package."""

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent

__all__ = ["MIGRATIONS_DIR"]
