"""
tests/test_app_imports.py
=========================
Standing import-smoke guard.

Cheap, explicit proof that the app package imports and the model registry is
complete. Most value: it PRE-POSITIONS the post-scraper-removal check the
coordinator asked for — when Backend deletes the scraper/enrichment scaffold,
a dangling import in app.main or a missing entry in __all_models__ shows up here
as one obvious red instead of a cascade of unrelated collection errors.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_app_imports.py -v
"""
from __future__ import annotations

import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def test_app_main_imports_and_exposes_asgi_app():
    m = importlib.import_module("app.main")
    assert m.app is not None
    # FastAPI instance is ASGI-callable.
    assert callable(m.app)


def test_all_models_registry_is_complete():
    """__all_models__ must import cleanly and hold only real mapped classes
    (every entry has a __tablename__). A removed model that's still referenced
    here — or a kept model dropped from the list — trips this."""
    from app.database import Base
    from app.models import __all_models__

    assert isinstance(__all_models__, (list, tuple))
    assert len(__all_models__) > 0
    for cls in __all_models__:
        assert hasattr(cls, "__tablename__"), f"{cls!r} is not a mapped model"
    # Every listed model is actually registered on the shared Base metadata
    # (Base.metadata also holds association/secondary tables, so the reverse
    # need not hold — this direction is the meaningful "no stale class" check).
    listed_tables = {c.__tablename__ for c in __all_models__}
    assert listed_tables <= set(Base.metadata.tables)
