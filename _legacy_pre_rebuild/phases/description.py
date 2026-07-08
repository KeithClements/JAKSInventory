from __future__ import annotations

import os
import re
from typing import List, TYPE_CHECKING, Union

from openai import OpenAI

if TYPE_CHECKING:
	from models import Product

# Allow local, untracked .env for keys (no hard-coded secrets)
try:
	from dotenv_simple import load_dotenv

	load_dotenv()
except Exception:
	pass

# Import templates for fast template-based generation
try:
	from phases.templates import TEMPLATE_MAP, GENERAL_TEMPLATE
except Exception:
	TEMPLATE_MAP = {}
	GENERAL_TEMPLATE = ""

# Import inference for product type detection
try:
	from engine.template_inference import infer_product_type
except Exception:

	def infer_product_type(title: str) -> str:
		return ""


def generate_engaging_description(
	brand_or_product: Union[str, "Product"] = "",
	engine: str = "",
	kit_contents: List[str] | None = None,
	dimensions: str = "",
	title: str = "",
	xrefs: List[str] | None = None,
	cpl_list: List[str] | None = None,
	*,
	brand: str = "",  # Support keyword-only 'brand' for backward compat
) -> str:
	"""Generate product descriptions.

	Old signature:
	  generate_engaging_description(brand, engine, kit_contents, dimensions, title, xrefs, cpl_list)

	New signature:
	  generate_engaging_description(product)  # Pass a Product object
	"""

	# Check if first arg is a Product object (duck typing)
	if hasattr(brand_or_product, "sku") and hasattr(brand_or_product, "title"):
		return _generate_from_product(brand_or_product)

	# Handle keyword 'brand' argument (test compatibility)
	actual_brand = brand if brand else str(brand_or_product)

	return _generate_legacy(
		brand=actual_brand,
		engine=engine,
		kit_contents=kit_contents or [],
		dimensions=dimensions,
		title=title,
		xrefs=xrefs,
		cpl_list=cpl_list,
	)


def _generate_from_product(p: "Product") -> str:
	"""Fast template-based description generator using Product object."""

	product_type = p.category if p.category else infer_product_type(p.title)
	template = TEMPLATE_MAP.get(product_type, GENERAL_TEMPLATE)
	if not template:
		return _generate_legacy(
			brand=p.brand,
			engine=p.engine,
			kit_contents=p.kit_contents or [],
			dimensions=p.dimensions,
			title=p.title,
			xrefs=p.xrefs,
			cpl_list=p.cpl_list,
		)

	overview = f"This {p.title} for {p.brand} {p.engine} engines delivers top performance and durability."
	features_list = ["OEM-spec quality", f"Optimized for {p.engine}", "Easy to install"]
	features = "".join(f"<li>{feat}</li>" for feat in features_list)

	kit_contents_html = "".join(f"<li>{item}</li>" for item in (p.kit_contents or []))

	compatibility = (
		f"Fits {p.brand} {p.engine}. "
		f"CPL: {', '.join(p.cpl_list) if p.cpl_list else 'N/A'}. "
		f"Tags: {', '.join(p.engine_model_tags) if p.engine_model_tags else 'N/A'}. "
		f"Xrefs: {', '.join(p.xrefs) if p.xrefs else 'N/A'}."
	)

	installation_notes = "Install by pros. Bundle for savings."

	part_number = p.primary_pn or p.sku or "N/A"
	brand = p.brand or "JAK's"
	condition = "New" if "new" in (p.title or "").lower() else "Remanufactured"
	weight = getattr(p, "weight", "") or "N/A"
	dimensions = p.dimensions or "N/A"
	warranty = "1 Year"

	flow_rate = getattr(p, "flow_rate", "") or "N/A"
	pressure_rating = getattr(p, "pressure_rating", "") or "N/A"
	material = getattr(p, "material", "") or "N/A"
	bore_size = getattr(p, "bore_size", "") or "N/A"
	turbo_type = getattr(p, "turbo_type", "") or "N/A"

	try:
		return template.format(
			overview=overview,
			features=features,
			kit_contents=kit_contents_html,
			part_number=part_number,
			brand=brand,
			condition=condition,
			weight=weight,
			dimensions=dimensions,
			warranty=warranty,
			compatibility=compatibility,
			installation_notes=installation_notes,
			flow_rate=flow_rate,
			pressure_rating=pressure_rating,
			material=material,
			bore_size=bore_size,
			turbo_type=turbo_type,
		).strip()
	except KeyError as e:
		print(f"Template format error: {e} - using legacy generator")
		return _generate_legacy(
			brand=p.brand,
			engine=p.engine,
			kit_contents=p.kit_contents or [],
			dimensions=p.dimensions,
			title=p.title,
			xrefs=p.xrefs,
			cpl_list=p.cpl_list,
		)


def _generate_legacy(
	*,
	brand: str,
	engine: str,
	kit_contents: List[str],
	dimensions: str = "",
	title: str = "",
	xrefs: List[str] | None = None,
	cpl_list: List[str] | None = None,
) -> str:
	"""Original implementation using Grok API for unique, SEO-optimized descriptions.

	Falls back to deterministic HTML when no API key.
	"""

	api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
	if not api_key:
		contents = [c for c in (kit_contents or []) if (c or "").strip()]
		contents_bullets = "".join(f"<li>{item}</li>" for item in contents)
		safe_title = (title or "Part").lower()
		contents_section = (
			f"<h2>Included Components</h2><ul>{contents_bullets}</ul>" if contents_bullets else ""
		)
		return (
			f"<h2>Premium {brand} {engine} Part from JAK's Diesel</h2>"
			f"<p>High-quality {safe_title} for heavy-duty applications.</p>"
			f"{contents_section}"
			f"<h2>Why JAK's Diesel</h2><ul><li>Brand-new parts</li><li>No core charge</li></ul>"
			f"<h2>Compatibility</h2><p>For {brand} {engine} engines.</p>"
			f"{dimensions if dimensions else ''}"
			"<p><strong>Order now!</strong></p>"
		)

	client = OpenAI(
		api_key=api_key,
		base_url="https://api.x.ai/v1",
	)

	xrefs = xrefs or []
	cpl_list = cpl_list or []

	contents_text = ", ".join([c for c in kit_contents if c])
	xrefs_text = ", ".join([x for x in xrefs if x])
	cpl_text = ", ".join([c for c in cpl_list if c])

	system = (
		"You write original, copyright-safe product descriptions for a diesel parts store. "
		"Return ONLY valid HTML (no markdown fences, no analysis, no word count, no extra commentary)."
	)

	prompt = f"""
Generate a unique, engaging, SEO-optimized product description for a diesel engine part.

Title: {title}
Brand/Make: {brand}
Engine: {engine}
Dimensions: {dimensions}
CPL/ESN Prefixes (if any): {cpl_text}
OEM Cross References (if any): {xrefs_text}
Contents/Included Components (verbatim list; DO NOT INVENT): {contents_text}

Rules:
- Do NOT fabricate kit contents. If no contents are provided, omit the Included Components section entirely.
- Do NOT list "inframe rebuild kit" items unless the provided contents explicitly indicate that.
- Do NOT copy or closely paraphrase any supplier text; keep it original and copyright-safe.

Output structure (HTML):
- <h2>Premium [Brand] [Engine] [Type] from JAK's Diesel</h2>
- 1–2 intro paragraphs focused on performance, durability, reduced downtime.
- If contents were provided: <h2>Included Components</h2><ul>...use only provided items...</ul>
- <h2>Why Choose JAK's Diesel</h2><ul> include: 100% new parts, easy install, zero core, fast shipping, expert support </ul>
- <h2>Compatibility</h2><p> mention verifying ESN/CPL prefix and part number before ordering </p>
- If OEM cross references provided: <h2>OEM Cross References</h2><p> list them as comma-separated text </p>
- Final <p> call to action.

Length: 250–450 words.
"""

	# Retry with exponential backoff for rate limits
	max_retries = 3
	base_delay = 5  # seconds
	last_error = None
	
	for attempt in range(max_retries):
		try:
			response = client.chat.completions.create(
				model="grok-3",
				messages=[
					{"role": "system", "content": system},
					{"role": "user", "content": prompt},
				],
				temperature=0.7,
				max_tokens=800,
			)
			desc = response.choices[0].message.content.strip()
			desc = re.sub(r"^```html\s*", "", desc)
			desc = re.sub(r"```\s*$", "", desc)
			desc = re.sub(r"^```\s*", "", desc)
			desc = re.sub(r"```\s*$", "", desc)
			return desc
		except Exception as e:
			last_error = e
			err_type = type(e).__name__
			msg = str(e)
			
			# Check for rate limit errors
			is_rate_limit = "RateLimitError" in err_type or "rate" in msg.lower() or "429" in msg
			
			if is_rate_limit and attempt < max_retries - 1:
				delay = base_delay * (2 ** attempt)  # 5s, 10s, 20s
				print(f"Rate limited, waiting {delay}s before retry {attempt + 2}/{max_retries}...")
				import time
				time.sleep(delay)
				continue
			
			# Non-retryable error or final retry
			break
	
	# All retries failed - log error and use fallback
	if last_error:
		err_type = type(last_error).__name__
		msg = str(last_error)
		is_rate_limit = "RateLimitError" in err_type or "rate" in msg.lower() or "429" in msg
		
		if "Incorrect API key" in msg or "Incorrect API key provided" in msg:
			print(
				"API error: XAI_API_KEY is set but invalid. "
				"Revoke/rotate it at https://console.x.ai, update .env, then restart. "
				"Using fallback."
			)
		elif is_rate_limit:
			print(f"API rate limit exceeded after {max_retries} retries—using fallback. Try again in a few minutes.")
		else:
			print(f"API error: {err_type}—using fallback.")

	contents = [c for c in (kit_contents or []) if (c or "").strip()]
	contents_bullets = "".join(f"<li>{item}</li>" for item in contents)
	contents_section = (
		f"<h2>Included Components</h2><ul>{contents_bullets}</ul>" if contents_bullets else ""
	)
	safe_title = (title or "Part").lower()
	return (
			f"<h2>Premium {brand} {engine} Part from JAK's Diesel</h2>"
			f"<p>High-quality {safe_title} for heavy-duty applications.</p>"
			f"{contents_section}"
			f"<h2>Why JAK's Diesel</h2><ul><li>Brand-new parts</li><li>No core charge</li></ul>"
			f"<h2>Compatibility</h2><p>For {brand} {engine} engines.</p>"
			f"{dimensions if dimensions else ''}"
			"<p><strong>Order now!</strong></p>"
		)
