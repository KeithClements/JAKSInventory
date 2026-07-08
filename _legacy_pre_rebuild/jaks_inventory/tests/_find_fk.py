import sys
sys.path.insert(0, r"c:\Users\keith\Website\jaks_scraper")
import os
os.environ["DATABASE_URL"] = "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db"
from db.database import get_db

with get_db() as db:
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for t in tables:
        name = t[0]
        fks = db.execute(f"PRAGMA foreign_key_list({name})").fetchall()
        if fks:
            for fk in fks:
                print(f"  {name} -> {fk[2]} ({fk[3]} -> {fk[4]})")
    
    # Also check payments
    pay_tables = [t[0] for t in tables if "payment" in t[0].lower()]
    print(f"\nPayment tables: {pay_tables}")
    for pt in pay_tables:
        info = db.execute(f"PRAGMA table_info({pt})").fetchall()
        print(f"  {pt} cols: {[i[1] for i in info]}")
