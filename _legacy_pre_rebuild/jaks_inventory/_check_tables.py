import sqlite3
conn = sqlite3.connect('c:/Users/keith/Website/jaks_scraper/output/inventory.db')
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print(len(tables), 'tables:')
for t in tables:
    print(' ', t)
conn.close()
