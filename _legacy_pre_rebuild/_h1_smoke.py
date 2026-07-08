from db.core_handling import list_unified_cores, core_processing_kpis, UNIFIED_PHASES
from collections import Counter

rows = list_unified_cores()
print("Total unified cores:", len(rows))
print("By phase:", dict(Counter(r["lifecycle_phase"] for r in rows)))
print("Overdue:", sum(1 for r in rows if r["is_overdue"]))
print()
print("KPIs:")
for k, v in core_processing_kpis().items():
    print(f'  {k}: count={v["count"]} dollars=${v["dollars"]:,.2f}')
print()
print("Phases:", [p[0] for p in UNIFIED_PHASES])
print()
print("Sample rows (first 3):")
for r in rows[:3]:
    print(f"  sku={r['sku']!r:20s} phase={r['lifecycle_phase']:18s} "
          f"vendor={r['vendor_name'] or '-':20s} cust={r['customer_name'] or '-':20s} "
          f"deposit=${r['deposit']:.2f} overdue={r['is_overdue']}")
