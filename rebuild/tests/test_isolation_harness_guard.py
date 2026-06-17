"""Guard: no test module may call create_engine() directly.

All test-database setup must flow through activate(fresh_engine()) or
bare_engine() from tests.conftest so that:
  1. Every test gets its own StaticPool in-memory DB (no shared state).
  2. app.database globals (engine / SessionLocal / get_db override) are
     re-pointed at run time, making the suite immune to import order.

If this test fails, the named file still has a raw create_engine() call.
Fix: replace the module-level engine block with activate(fresh_engine())
inside the db() fixture (see tests/conftest.py for the canonical pattern).
"""
import pathlib

import pytest

_TESTS_DIR = pathlib.Path(__file__).parent
_EXEMPT = {
    "conftest.py",
    "test_isolation_harness_guard.py",
    # Backup-acceptance test creates a FILE-based SQLite DB via tmp_path — not
    # an in-memory harness anti-pattern; intentionally file-based for backup/restore.
    "test_backup_acceptance.py",
    # Alembic-adoption test deliberately builds isolated file + in-memory engines
    # to exercise app/db_migrate.adopt() (the file-DB path can't use the shared
    # in-memory harness); it saves/restores the app globals itself.
    "test_alembic_adoption.py",
}

_TARGETS = sorted(
    p for p in _TESTS_DIR.glob("test_*.py") if p.name not in _EXEMPT
)


@pytest.mark.parametrize("path", _TARGETS, ids=lambda p: p.name)
def test_no_raw_create_engine(path: pathlib.Path):
    """Fail if a test file calls create_engine() directly."""
    text = path.read_text(encoding="utf-8")
    assert "create_engine(" not in text, (
        f"\n\n{path.name} calls create_engine() directly.\n"
        "Use activate(fresh_engine()) in a db() fixture, or bare_engine() "
        "from tests.conftest for tests that need a schema-only engine.\n"
        "See the conftest docstring for the full rationale."
    )
