import sqlite3
conn = sqlite3.connect('output/JAKs_Demo.db')
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print('Tables:', tables)
if 'purchase_order_lines' in tables:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(purchase_order_lines)').fetchall()]
    print('POL cols:', cols)
else:
    print('NO purchase_order_lines table!')
if 'vendor_core_obligations' in tables:
    print('vendor_core_obligations: exists')
else:
    print('vendor_core_obligations: MISSING')
conn.close()
