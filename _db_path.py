from db.database import get_db, DB_PATH
print("DB_PATH:", DB_PATH)
with get_db() as conn:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print("Tables:", tables)
    print()
    print("core_* tables:", [t for t in tables if "core" in t.lower()])
