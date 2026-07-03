import sys
sys.path.insert(0, "C:/Users/keith/Inventory Program")
import os
os.environ["DATABASE_URL"] = "sqlite:///output/JAKs_Demo.db"
os.chdir("C:/Users/keith/Inventory Program")

from db import browse_products, add_po_line, get_all_purchase_orders

# Find an existing PO
try:
    pos = get_all_purchase_orders()
    print(f"POs found: {len(pos)}")
    if pos:
        po = pos[0]
        print(f"First PO: id={po.get('id')}, number={po.get('po_number')}")
        po_id = po['id']

        # Find a product
        products = browse_products(search="engine", limit=5)
        print(f"Products found: {len(products)}")
        if products:
            p = products[0]
            print(f"Product: id={p.get('id')}, sku={p.get('sku')}, title={p.get('title')}")
            
            # Try add_po_line
            result = add_po_line(po_id, p["id"], 1, float(p.get("cost") or 0))
            print(f"add_po_line result: {result}")
    else:
        print("No POs found")
except Exception as e:
    import traceback
    traceback.print_exc()
