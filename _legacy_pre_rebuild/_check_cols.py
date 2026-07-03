import sys, os
sys.path.insert(0, "C:/Users/keith/Inventory Program")
os.environ["DATABASE_URL"] = "sqlite:///output/JAKs_Demo.db"
os.chdir("C:/Users/keith/Inventory Program")

import sqlite3
conn = sqlite3.connect("output/JAKs_Demo.db")

# Check vendors columns
vendors_cols = [r[1] for r in conn.execute("PRAGMA table_info(vendors)").fetchall()]
print("vendors cols:", vendors_cols)

# Check what the SQLITE_SCHEMA defines for vendors
print()

# Check products table for any newer columns
products_cols = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
print("products cols:", products_cols)
conn.close()
