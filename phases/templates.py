"""HTML templates for product descriptions.

Moved from root `templates.py`; root remains as a compatibility shim.
"""

# General fallback (simple, no extras)
GENERAL_TEMPLATE = """
<h2>Product Overview</h2>
<p>{overview}</p>

<h2>Key Features</h2>
<ul>{features}</ul>

<h2>Specifications</h2>
<table style="width:100%; border:1px solid #ddd;">
  <tr><th>Part Number</th><td>{part_number}</td></tr>
  <tr><th>Brand</th><td>{brand}</td></tr>
  <tr><th>Condition</th><td>{condition}</td></tr>
  <tr><th>Weight</th><td>{weight}</td></tr>
  <tr><th>Dimensions</th><td>{dimensions}</td></tr>
  <tr><th>Warranty</th><td>{warranty}</td></tr>
</table>

<h2>Compatibility</h2>
<p>{compatibility}</p>

<h2>Installation Notes</h2>
<p>{installation_notes}</p>
"""

# Rebuild Kit (adds kit_contents section)
REBUILD_KIT_TEMPLATE = GENERAL_TEMPLATE.replace(
	"<h2>Key Features</h2>\n<ul>{features}</ul>",
	"<h2>Key Features</h2>\n<ul>{features}</ul>\n\n<h2>Kit Contents</h2>\n<ul>{kit_contents}</ul>",
)

# Fuel Injectors (adds flow_rate to specs)
FUEL_INJECTOR_TEMPLATE = GENERAL_TEMPLATE.replace(
	"<tr><th>Weight</th><td>{weight}</td></tr>",
	"<tr><th>Flow Rate</th><td>{flow_rate}</td></tr>\n  <tr><th>Weight</th><td>{weight}</td></tr>",
)

# Oil Pumps (adds pressure_rating)
OIL_PUMP_TEMPLATE = GENERAL_TEMPLATE.replace(
	"<tr><th>Condition</th><td>{condition}</td></tr>",
	"<tr><th>Condition</th><td>{condition}</td></tr>\n  <tr><th>Pressure Rating</th><td>{pressure_rating}</td></tr>",
)

# Gaskets (adds material + kit_contents)
GASKETS_TEMPLATE = REBUILD_KIT_TEMPLATE.replace(
	"<tr><th>Brand</th><td>{brand}</td></tr>",
	"<tr><th>Brand</th><td>{brand}</td></tr>\n  <tr><th>Material</th><td>{material}</td></tr>",
)

# Piston/Liners (adds bore_size)
PISTON_LINER_TEMPLATE = GENERAL_TEMPLATE.replace(
	"<tr><th>Condition</th><td>{condition}</td></tr>",
	"<tr><th>Condition</th><td>{condition}</td></tr>\n  <tr><th>Bore Size</th><td>{bore_size}</td></tr>",
)

# Turbocharger template (specialized for turbos)
TURBOCHARGER_TEMPLATE = GENERAL_TEMPLATE.replace(
	"<tr><th>Condition</th><td>{condition}</td></tr>",
	"<tr><th>Condition</th><td>{condition}</td></tr>\n  <tr><th>Turbo Type</th><td>{turbo_type}</td></tr>",
)

# Map (dict lookup is O(1) fast)
TEMPLATE_MAP = {
	"Engine Rebuild Kits": REBUILD_KIT_TEMPLATE,
	"Fuel Injectors": FUEL_INJECTOR_TEMPLATE,
	"Lubrication System": OIL_PUMP_TEMPLATE,
	"Oil Pumps & Coolers": OIL_PUMP_TEMPLATE,
	"Gaskets": GASKETS_TEMPLATE,
	"Piston Cylinder Kit & Components": PISTON_LINER_TEMPLATE,
	"Pistons & Liners": PISTON_LINER_TEMPLATE,
	"Fuel System": GENERAL_TEMPLATE,
	"Cooling System": GENERAL_TEMPLATE,
	"Exhaust System": GENERAL_TEMPLATE,
	"Crankshaft & Damper": GENERAL_TEMPLATE,
	"Air Intake System": GENERAL_TEMPLATE,
	"Miscellaneous": GENERAL_TEMPLATE,
	"Turbochargers": TURBOCHARGER_TEMPLATE,
	"Cylinder Heads": GENERAL_TEMPLATE,
	"Cylinder Head & Components": GENERAL_TEMPLATE,
}
