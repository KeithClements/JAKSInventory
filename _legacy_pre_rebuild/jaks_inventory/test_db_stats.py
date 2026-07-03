"""Quick DB stats check."""
from db.database import get_db

with get_db() as db:
    db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in db.fetchall()]
    print(f"Tables: {len(tables)}")
    for t in tables:
        db.execute(f"SELECT COUNT(*) FROM [{t}]")
        c = db.fetchone()[0]
        if c > 0:
            print(f"  {t}: {c}")
