"""Grok-3 backed AI assistant for the Add/Edit Product dialog.

All functions return a dict of {field_key: value} pairs that the dialog
can apply directly to its form widgets, plus an "_ai_notes" key with
human-readable explanation for the activity log / toast.

The XAI_API_KEY is loaded from the .env file (already loaded by main.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Cached client — created lazily on first call
_client = None


# ─────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────
def is_available() -> bool:
    """True if a Grok / OpenAI key is configured."""
    return bool(os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY"))


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No XAI_API_KEY (or OPENAI_API_KEY) configured in .env")
    from openai import OpenAI
    if os.getenv("XAI_API_KEY"):
        _client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    else:
        _client = OpenAI(api_key=api_key)
    return _client


def _model() -> str:
    if os.getenv("XAI_API_KEY"):
        return os.getenv("GROK_MODEL", "grok-3")
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


_SYSTEM = (
    "You are a senior heavy-duty diesel parts specialist and inventory manager "
    "at a Class-8 truck / industrial diesel parts dealership. You know the "
    "Cummins, CAT, Detroit Diesel, Paccar, Volvo, Mack and Navistar product "
    "lines, common cross-references, freight classes for engine components, "
    "typical core deposits and warranty terms, and reasonable list / wholesale "
    "pricing for OEM and reman parts. "
    "Respond ONLY with strict JSON — no markdown fences, no commentary."
)


def _chat_json(user_prompt: str, *, max_tokens: int = 800,
               temperature: float = 0.3) -> Any:
    """Call Grok and parse the response as JSON. Strips markdown fences."""
    client = _get_client()
    resp = client.chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = (resp.choices[0].message.content or "").strip()
    # Strip fences ```json … ```
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
    # Some models prepend prose — find first '{' or '['
    m = re.search(r"[\{\[]", content)
    if m:
        content = content[m.start():]
        # Trim trailing prose after last brace
        last = max(content.rfind("}"), content.rfind("]"))
        if last >= 0:
            content = content[:last + 1]
    return json.loads(content)


def _ctx_block(ctx: dict[str, Any]) -> str:
    lines = ["Product context so far:"]
    for k, v in ctx.items():
        if v in (None, "", 0, "0"):
            continue
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines) if len(lines) > 1 else "Product context: (mostly empty)"


# ─────────────────────────────────────────────────────────
# Section ① Identity
# ─────────────────────────────────────────────────────────
def auto_fill_from_oem(ctx: dict[str, Any]) -> dict[str, Any]:
    """Look up an OEM part number and fill identity + category fields."""
    oem = (ctx.get("oem_part_number") or "").strip()
    brand = (ctx.get("manufacturer") or "").strip()
    if not oem:
        return {"_error": "Enter an OEM part number first."}
    prompt = f"""{_ctx_block(ctx)}

Given OEM part number "{oem}" (brand hint: "{brand or 'unknown'}"), identify the
part using your knowledge of HD diesel catalogs. Return JSON with these keys:

{{
  "manufacturer":       "<best brand: PAI Industries | Cummins | Holset | Fleetguard | Detroit Diesel | CAT | Paccar | Volvo | Mack | Navistar | Victor Reinz | Bosch | Garrett>",
  "title":              "<short 60-80 char marketable description>",
  "category":           "<best of: Inframe Kits, Injectors, Turbos, EGR Coolers, Gasket Sets, Cylinder Heads, Water Pumps, Filters, Air Compressors, Fuel Pumps, Oil Coolers, Pistons, Bearings>",
  "subcategory":        "<more specific subcategory>",
  "engine":             "<engine family if applicable, e.g. 'Cummins ISX15'>",
  "truck_model":        "<vehicle / equipment fitment, plain English>",
  "description_html":   "<2-4 sentence detailed product description>",
  "xref_part_number":   "<comma-separated cross-references / alt part numbers>",
  "country_of_origin":  "<USA | Mexico | India | China | Other>",
  "_notes":             "<one sentence explaining what this part is>"
}}

If you genuinely don't recognize the part, set "_notes" to "Unrecognized" and
leave the other string fields empty rather than guessing wildly."""
    return _chat_json(prompt, max_tokens=900)


def find_cross_refs(ctx: dict[str, Any]) -> dict[str, Any]:
    oem = (ctx.get("oem_part_number") or "").strip()
    brand = (ctx.get("manufacturer") or "").strip()
    if not oem:
        return {"_error": "Enter an OEM part number first."}
    prompt = f"""{_ctx_block(ctx)}

For OEM "{oem}" (brand: {brand or 'unknown'}), list ALL known cross-reference /
interchange part numbers across the aftermarket (PAI, Interstate-McBee, IPD,
Fleetguard, Donaldson, Mahle, Federal-Mogul, etc.) and OEM equivalents from
sister brands.

Return JSON:
{{
  "xref_part_number": "<comma-separated list, OEM equivalents first then aftermarket>",
  "substitutes":      "<comma-separated SKUs you'd substitute on a quote>",
  "_notes":           "<one sentence summary>"
}}"""
    return _chat_json(prompt, max_tokens=500)


def generate_long_description(ctx: dict[str, Any], mode: str = "regenerate") -> dict[str, Any]:
    """mode: regenerate | shorter | bullets | translate_es"""
    instructions = {
        "regenerate": "Write a polished 3-5 sentence marketing description.",
        "shorter":    "Rewrite the current description in 1-2 punchy sentences.",
        "bullets":    "Rewrite as 4-6 bullet points (use • prefix) highlighting features and fitment.",
        "translate_es": "Translate the current description to Spanish, professional tone.",
    }[mode]
    prompt = f"""{_ctx_block(ctx)}

{instructions}

Audience: HD diesel fleet maintenance buyers and independent shops. Mention
fitment / engine family where known. No marketing fluff like "high quality" or
"the best in the industry". Be specific and technical.

Return JSON: {{ "description_html": "<the rewritten description>", "_notes": "<one sentence what changed>" }}"""
    return _chat_json(prompt, max_tokens=600)


# ─────────────────────────────────────────────────────────
# Section ② Categorization & Fitment
# ─────────────────────────────────────────────────────────
def suggest_category(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest category & sub-category for this product. Allowed categories include:
Inframe Kits, Injectors, Turbos, EGR Coolers, Gasket Sets, Cylinder Heads,
Water Pumps, Filters, Air Compressors, Fuel Pumps, Oil Coolers, Pistons,
Bearings, Fuel Injection Pumps, Sensors, Belts & Hoses, Cooling, Exhaust,
Aftertreatment. You may invent a tighter subcategory.

Return JSON:
{{ "category": "<one>", "subcategory": "<one>", "_notes": "<why>" }}"""
    return _chat_json(prompt, max_tokens=300)


def suggest_fitment(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest fitment fields. Return JSON:
{{
  "engine":         "<engine family, e.g. 'Cummins ISX15' or 'Detroit DD15'>",
  "truck_model":    "<Class-8 OTR / marine / industrial / 'all Class-8 trucks 2007+'>",
  "year_range":     "<e.g. '2007 - 2016'>",
  "position":       "<- | Left | Right | Front | Rear | Inboard | Outboard>",
  "fitment_notes":  "<restrictions, e.g. 'pre-EGR only' or 'CGI engines only'>",
  "_notes":         "<one sentence>"
}}"""
    return _chat_json(prompt, max_tokens=400)


def suggest_tags(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest 4-8 short search tags / keywords this product should be findable by
on a parts website (lowercase, comma separated). Include common abbreviations,
slang names, and OEM cross-brands.

Return JSON: {{ "tags": "<comma list>", "_notes": "<short>" }}"""
    return _chat_json(prompt, max_tokens=200)


# ─────────────────────────────────────────────────────────
# Section ③ Costing & Pricing
# ─────────────────────────────────────────────────────────
def suggest_tier_pricing(ctx: dict[str, Any]) -> dict[str, Any]:
    cost = _to_float(ctx.get("cost")) or _to_float(ctx.get("pai_cost")) or 0.0
    list_price = _to_float(ctx.get("selling_price")) or 0.0
    if cost <= 0:
        return {"_error": "Enter a cost first so I can suggest pricing."}
    prompt = f"""{_ctx_block(ctx)}

Cost basis: ${cost:.2f}. Current list price: ${list_price:.2f}.

Suggest a complete pricing ladder for this HD diesel part. Tier 1 = fleet
account (best price), Tier 2 = independent dealer, Tier 3 = wholesale jobber,
Promo = limited-time sale. Use category-appropriate margins:
  - Kits / gaskets:  35-45% margin on List, fleet ~25%, wholesale ~15%
  - Injectors / turbos: 28-38% on List, fleet ~20%, wholesale ~12%
  - Filters / belts: 40-55% on List, fleet ~30%, wholesale ~20%
  - Reman cores: 30-40% on List

Return JSON with NUMBERS (no $ signs):
{{
  "selling_price":    <list price>,
  "compare_at_price": <MAP / minimum advertised>,
  "floor_price":      <never-sell-below>,
  "tier1_price":      <fleet>,
  "tier2_price":      <dealer>,
  "tier3_price":      <wholesale>,
  "promo_price":      <promo>,
  "target_margin":    "<percent string e.g. '32.0%'>",
  "_notes":           "<one sentence explaining the strategy>"
}}"""
    return _chat_json(prompt, max_tokens=400)


def competitor_scan(ctx: dict[str, Any]) -> dict[str, Any]:
    oem = (ctx.get("oem_part_number") or "").strip()
    if not oem:
        return {"_error": "Need an OEM # to scan competitors."}
    prompt = f"""{_ctx_block(ctx)}

Provide a competitive price snapshot for OEM "{oem}". Use your training-data
knowledge of typical street pricing at FinditParts, Diesel Pro Power, HHP,
Class-8 Truck Parts, Geno's Garage, etc. Be honest about uncertainty.

Return JSON:
{{
  "_notes": "<3-4 line summary of typical price range, e.g. '$185-$225 street; PAI ~$195; HHP $209; FleetPride $235. Recommend list $219 to win the search.'>"
}}"""
    return _chat_json(prompt, max_tokens=400)


def map_check(ctx: dict[str, Any]) -> dict[str, Any]:
    oem = (ctx.get("oem_part_number") or "").strip()
    brand = (ctx.get("manufacturer") or "").strip()
    prompt = f"""{_ctx_block(ctx)}

Does brand "{brand}" enforce MAP (minimum advertised price) on OEM "{oem}"?
Cummins, Cummins Filtration / Fleetguard, Donaldson, Holset, and Garrett often
do. PAI generally does not.

Return JSON:
{{
  "_notes": "<2-3 lines: is MAP enforced? typical MAP value? what happens if you advertise below?>"
}}"""
    return _chat_json(prompt, max_tokens=300)


# ─────────────────────────────────────────────────────────
# Section ⑤ Inventory & Reorder
# ─────────────────────────────────────────────────────────
def suggest_reorder_point(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest reasonable inventory thresholds for a busy independent diesel parts
dealer (avg 40-60 outbound orders/day). Use category-typical velocity:
  - Filters, belts, hoses: high velocity, keep 10-20 on hand
  - Gasket sets, water pumps: medium, 4-8 on hand
  - Injectors, turbos, reman cores: low+expensive, 1-3 on hand
  - Inframe kits: slow but high-value, 1-2 on hand

Return JSON with integers:
{{
  "min_qty":     <reorder point>,
  "reorder_qty": <typical PO qty>,
  "max_qty":     <max stock>,
  "_notes":      "<one sentence>"
}}"""
    return _chat_json(prompt, max_tokens=250)


def suggest_bin(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest a sensible warehouse bin code (A1-Z99 format) based on category:
  - Filters: F-zone
  - Gaskets/seals: G-zone
  - Injectors/fuel: I-zone
  - Turbos: T-zone
  - Heavy/oversize (heads, blocks): H-zone with low aisle number
  - Reman cores: R-zone

Return JSON: {{ "bin": "<code>", "_notes": "<short>" }}"""
    return _chat_json(prompt, max_tokens=200)


# ─────────────────────────────────────────────────────────
# Section ⑥ Core & Warranty
# ─────────────────────────────────────────────────────────
def suggest_core_deposit(ctx: dict[str, Any]) -> dict[str, Any]:
    list_price = _to_float(ctx.get("selling_price")) or 0.0
    prompt = f"""{_ctx_block(ctx)}

Current list price: ${list_price:.2f}.

If this part has a returnable core (turbos, injectors, fuel pumps, water pumps,
heads, starters, alternators, A/C compressors, EGR coolers), suggest a fair
core deposit. Typical industry guidance:
  - Turbos: 40-60% of list
  - Injectors: $50-$100 each
  - Heads: $500-$1,500
  - Water pumps / oil coolers: 15-25% of list
  - Filters / gaskets / belts: NO CORE

Return JSON:
{{
  "core_required":   "<'Yes — require core' | 'No core' | 'Optional (deposit waived if returned)'>",
  "core_charge":     <dollar amount as number, 0 if no core>,
  "core_window":     "<e.g. '60 days' or '90 days'>",
  "_notes":          "<one sentence>"
}}"""
    return _chat_json(prompt, max_tokens=300)


# ─────────────────────────────────────────────────────────
# Section ⑦ Shipping
# ─────────────────────────────────────────────────────────
def lookup_freight_class(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

What is the appropriate NMFC freight class for this part shipping via LTL?
Common HD-diesel classes:
  - Filters, small gaskets:        70-77.5
  - Injectors, water pumps:        85-92.5
  - Turbos, EGR coolers:           100-110
  - Gasket sets (boxed):           85
  - Cylinder heads (bare):         100-125
  - Inframe kits (palletized):     85-92.5

Return JSON: {{ "freight_class": "<class number string>", "_notes": "<density rationale>" }}"""
    return _chat_json(prompt, max_tokens=250)


def lookup_nmfc(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest the NMFC item code (sub-number) for this product. Common ranges:
  - Engine parts NOI: 121260
  - Diesel injectors: 120080
  - Turbochargers:    121280
  - Filters:          120880
  - Gaskets:          121200

Return JSON: {{ "nmfc": "<code>", "_notes": "<short>" }}"""
    return _chat_json(prompt, max_tokens=200)


def estimate_dimensions(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Estimate typical shipping dimensions and weight for this part in original
packaging. Use real-world averages for HD diesel parts.

Return JSON with numbers only (lbs / inches):
{{
  "weight":  <lbs>,
  "length":  <inches>,
  "width":   <inches>,
  "height":  <inches>,
  "_notes":  "<one sentence>"
}}"""
    return _chat_json(prompt, max_tokens=250)


# ─────────────────────────────────────────────────────────
# Section ⑧ Accounting & QuickBooks
# ─────────────────────────────────────────────────────────
def suggest_gl_accounts(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest QuickBooks GL accounts. Allowed values:
  Income: "4000 · Parts Sales" | "4010 · Reman Sales" | "4100 · Core Charges"
  COGS:   "5000 · Cost of Parts Sold" | "5010 · Reman COGS"
  Asset:  "1300 · Inventory Asset"
  Tax:    "Taxable (Auto)" | "Non-taxable" | "Resale Cert"

Return JSON:
{{
  "income_account": "<one>",
  "cogs_account":   "<one>",
  "asset_account":  "<one>",
  "tax_code":       "<one>",
  "_notes":         "<short>"
}}"""
    return _chat_json(prompt, max_tokens=250)


def hs_tax_lookup(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

Suggest the HS / HTSUS tariff code for cross-border shipments of this part.
Common HD diesel codes:
  - 8409.99.99 — Engine parts NOI
  - 8421.23.00 — Oil/fuel filters
  - 8413.30.90 — Fuel injection pumps / injectors
  - 8484.10.00 — Gaskets
  - 8414.59.65 — Turbochargers (centrifugal compressors)

Return JSON: {{ "hs_code": "<10-digit code>", "_notes": "<one sentence>" }}"""
    return _chat_json(prompt, max_tokens=250)


# ─────────────────────────────────────────────────────────
# Section ⑩ Notes
# ─────────────────────────────────────────────────────────
def find_substitutes(ctx: dict[str, Any]) -> dict[str, Any]:
    oem = (ctx.get("oem_part_number") or "").strip()
    if not oem:
        return {"_error": "Need an OEM # to find substitutes."}
    prompt = f"""{_ctx_block(ctx)}

List SKUs that can safely substitute for OEM "{oem}" in a typical repair.
Include direct supersessions, aftermarket equivalents (PAI, Interstate-McBee,
IPD, Mahle), and updated revisions. Note any caveats.

Return JSON:
{{
  "substitutes": "<comma-separated SKUs>",
  "_notes":      "<2-3 lines describing the substitution rules and caveats>"
}}"""
    return _chat_json(prompt, max_tokens=400)


def common_mistakes(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""{_ctx_block(ctx)}

What are the common mistakes / pitfalls when ordering or installing this part?
Things like: easily confused with the post-2010 revision; needs the 12.5mm
bolts not 11mm; wrong if engine is CGI; serial-break at ESN 79xxxxx; etc.

Return JSON:
{{
  "notes_internal": "<3-6 bullet lines describing the mistakes>",
  "_notes":         "<one sentence summary>"
}}"""
    return _chat_json(prompt, max_tokens=500)


# ─────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────
def _to_float(v: Any) -> float | None:
    if v is None: return None
    s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
    try: return float(s)
    except (ValueError, TypeError): return None
