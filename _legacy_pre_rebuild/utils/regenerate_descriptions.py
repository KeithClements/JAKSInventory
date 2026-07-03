import json
from pathlib import Path
from phases.description import generate_engaging_description  # Import your updated function

IN_PRODUCTS = Path("data") / "products.json"
OUT_PRODUCTS = Path("data") / "products_updated.json"

# Load JSON
with IN_PRODUCTS.open('r', encoding='utf-8') as f:
    data = json.load(f)

# Regenerate for each product
for sku, product in data.items():
    print(f"Regenerating for {sku}...")
    product['description_jaks_html'] = generate_engaging_description(
        brand=product['brand'],
        engine=product['engine'],
        kit_contents=product['kit_contents'],
        dimensions=product['dimensions'],
        title=product['title']
    )

# Save updated JSON
OUT_PRODUCTS.parent.mkdir(parents=True, exist_ok=True)
with OUT_PRODUCTS.open('w', encoding='utf-8') as f:
    json.dump(data, f, indent=4)

print(f"Done! Updated file: {OUT_PRODUCTS}")