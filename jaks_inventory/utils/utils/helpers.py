# Moved from root helpers.py (kept as compatibility shim)

import os

# Canonical OEM family groupings (used for cross/reference logic).
OEM_GROUPS: dict[str, str] = {
    "Volvo": "Volvo Group",
    "Mack": "Volvo Group",
    "Renault": "Volvo Group",
}


def canonical_oem_group(manufacturer: str) -> str:
    m = str(manufacturer or "").strip()
    if not m:
        return ""
    return OEM_GROUPS.get(m, m)


# Allow local, untracked .env for keys (no hard-coded secrets)
try:
    from dotenv_simple import load_dotenv

    load_dotenv()
except Exception:
    pass


SHOPIFY_STORE = "jaks-diesel-3"
API_VERSION = "2026-01"
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")  # Use environment variable for security
ATTR_TABLE_ROW = ".woocommerce-product-attributes tr"
WM_SCALE = 0.50  # Increased from 0.35 → ~43% larger (relative to image width)
WM_OPACITY = 0.50  # Increased from 0.35 → darker/more visible (0.0 transparent, 1.0 fully opaque)

from datetime import datetime
from pathlib import Path
import re
import html
import unicodedata
from bs4 import BeautifulSoup
from PIL import Image, ImageEnhance
from typing import Optional, List, Dict


def now_stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    return unicodedata.normalize("NFKC", text)


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def infer_engine_from_title(title: str) -> str:
    t = title.upper()
    if "ISX15" in t:
        return "ISX15"
    if "ISX" in t:
        return "ISX"
    if "C15" in t:
        return "C15"
    if "C13" in t:
        return "C13"
    if "C7" in t:
        return "C7"
    if "3406" in t:
        return "3406"
    if "C18" in t:
        return "C18"
    if "SERIES 60" in t:
        return "Series 60"  # Added for Detroit
    if "SERIES 71" in t:
        return "Series 71"  # Example expansion; add more as needed
    if "DD15" in t:
        return "DD15"  # Detroit example
    if "MP8" in t:
        return "MP8"  # Mack example
    # Add more patterns for other manufacturers/engines based on common titles
    return ""


def parse_price(text: str) -> Optional[float]:
    m = re.search(r"([\d,.]+)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def extract_description(soup: BeautifulSoup) -> str:
    parts = []
    short_desc = soup.select_one(".woocommerce-product-details__short-description")
    if short_desc:
        parts.append(str(short_desc))
    full_desc = soup.select_one("#tab-description")
    if full_desc:
        parts.append(str(full_desc))
    html_out = "\n<hr/>\n".join(parts)
    html_out = normalize_text(html_out)
    html_out = re.sub(r'\s*(class|id|style|aria-[a-z\-]+)="[^"]+"', "", html_out, flags=re.I)
    return html_out.strip()


def extract_price_and_core(soup: BeautifulSoup):
    price = None
    core = None
    currency = "USD"
    price_el = soup.select_one(".summary .price") or soup.select_one(".woocommerce-Price-amount")
    if price_el:
        price = parse_price(price_el.get_text())
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(core charge|core)\s*[:\-]?\s*\$\s*([\d,.]+)", text, re.I)
    if m:
        core = float(m.group(2).replace(",", ""))
    return price, core, currency


def parse_attrs(soup: BeautifulSoup) -> Dict[str, str]:
    data = {}
    for row in soup.select(ATTR_TABLE_ROW):
        th = row.select_one("th")
        td = row.select_one("td")
        if th and td:
            key = th.text.strip().lower()
            value = normalize(td.text)
            data[key] = value
    # Explicitly extract weight if present
    data["weight"] = data.get("shipping weight", data.get("weight", ""))
    return data


def extract_xrefs(title: str, soup: BeautifulSoup) -> List[str]:
    """Extract OEM Cross Reference numbers ONLY.
    
    IMPORTANT: This should ONLY extract from the explicit "OEM Cross References" 
    section on the page, plus the SKU from the title. It should NOT include:
    - Kit contents (component part numbers like gaskets, bearings, etc.)
    - Random part numbers found elsewhere on the page
    
    Family grouping depends on accurate xrefs - including kit contents would
    incorrectly group unrelated products together.
    """
    found: set[str] = set()

    # Extract part number from title (e.g., "ISC103001" from title)
    # This is the primary SKU and should always be included
    title_match = re.search(r'\(([A-Z0-9\-]+)\)\s*$', title.upper())
    if title_match:
        found.add(title_match.group(1))
    
    # Also extract any part number pattern at the start of title before the description
    # e.g., "ISC103001" from "ISC103001 - Cummins ISC Inframe..."
    title_start_match = re.match(r'^([A-Z0-9\-]{5,20})\b', title.upper())
    if title_start_match:
        found.add(title_start_match.group(1))

    # Look ONLY for explicit "OEM Cross References" section
    roots = []
    sd = soup.select_one(".woocommerce-product-details__short-description")
    if sd:
        roots.append(sd)
    desc = soup.select_one("#tab-description")
    if desc:
        roots.append(desc)
    for panel in soup.select(".woocommerce-Tabs-panel"):
        roots.append(panel)
    text = "\n".join(r.get_text("\n", strip=True) for r in roots if r) or soup.get_text("\n", strip=True)
    
    # STRICT: Only extract from "OEM Cross References" section
    m = re.search(
        r"OEM\s+Cross\s+References\s*[:\-]?\s*(.+?)(?:\n\s*\n|\n(?:Contents|Compatibility|Guaranteed|Why\s+Choose|Included)\b|$)",
        text,
        flags=re.I | re.S,
    )
    if m:
        raw = m.group(1)
        for token in re.split(r"[,\n]", raw):
            t = normalize(token).upper()
            t = t.replace("\r", "").replace("\n", "").strip()
            if not t:
                continue
            # Keep only tokens that look like part numbers (contain a digit)
            if any(ch.isdigit() for ch in t) and 4 <= len(t) <= 32:
                found.add(t)

    # DO NOT do a general page scan - that picks up kit contents!
    # The old code below was causing incorrect family groupings:
    #
    # pattern = re.compile(r"\b[0-9A-Z]{2,}(?:-[0-9A-Z]{2,})+\b|\b[0-9A-Z]{5,}\b")
    # candidates = set(pattern.findall(title.upper()))
    # for panel in soup.select(".woocommerce-Tabs-panel"):
    #     candidates.update(pattern.findall(panel.get_text(" ", strip=True).upper()))

    # Blocklist: ECM codes and engine models that aren't part numbers
    blocklist = {
        # ECM codes
        "CM2250", "CM871", "CM870", "CM2350",
        # International/Navistar engine models
        "DT466", "DT466E", "DT530", "DT530E", "DT570", "HT570",
        "DT408", "DT414", "I530E", "T444E", "VT365", "VT275",
        "MAXXFORCE", "MAXXFORCE7", "MAXXFORCE9", "MAXXFORCE10", "MAXXFORCE11", "MAXXFORCE13", "MAXXFORCE15",
        # Cummins engine models  
        "ISX15", "ISX12", "ISB67", "ISL9", "ISC", "ISM", "ISX", "ISB", "ISL",
        "N14", "M11", "L10", "B59", "C87", "QSB67", "QSX15", "QSL9",
        # Caterpillar engine models
        "C15", "C13", "C12", "C11", "C10", "C9", "C7", "3406", "3406E", "3406B", "3406C",
        "3176", "3196", "3126", "3126B", "3126E",
        # Detroit Diesel engine models
        "DD15", "DD13", "DD16", "S60", "S50", "SERIES60", "60SERIES",
        # Volvo/Mack engine models
        "D11", "D12", "D13", "D16", "MP7", "MP8", "MP10",
        # PACCAR engine models
        "MX13", "MX11", "PX7", "PX9",
    }
    filtered = [x for x in found if x not in blocklist]
    return sorted(filtered)


def extract_cpl_list(soup: BeautifulSoup) -> List[str]:
    """Extract CPL/ESN Prefix values from the specs section."""

    cpl_values: list[str] = []

    # 1) Prefer the attributes/specs table
    for row in soup.select(".woocommerce-product-attributes tr"):
        th = row.select_one("th")
        td = row.select_one("td")
        label = th.get_text(strip=True).lower() if th else ""
        if not td:
            continue

        # Be flexible: some pages use "CPL/ESN Prefix", "ESN Prefix", etc.
        if "cpl" in label and "prefix" in label:
            value_text = td.get_text(strip=True)
            parts = [part.strip() for part in value_text.split(",")]
            cpl_values = [p for p in parts if p]
            break
        if "esn" in label and "prefix" in label:
            value_text = td.get_text(strip=True)
            parts = [part.strip() for part in value_text.split(",")]
            cpl_values = [p for p in parts if p]
            break

    if cpl_values:
        return cpl_values

    # 2) Fallback: search within product description areas
    roots = []
    sd = soup.select_one(".woocommerce-product-details__short-description")
    if sd:
        roots.append(sd)
    desc = soup.select_one("#tab-description")
    if desc:
        roots.append(desc)
    for panel in soup.select(".woocommerce-Tabs-panel"):
        roots.append(panel)

    text = "\n".join(r.get_text("\n", strip=True) for r in roots if r)
    m = re.search(r"\b(?:CPL\s*/?\s*ESN|ESN)\s+Prefix\s*[:\-]?\s*([A-Z0-9,\s]+)", text, re.I)
    if m:
        raw = m.group(1)
        parts = [p.strip() for p in re.split(r"[,\s]+", raw) if p.strip()]
        parts = [p for p in parts if 2 <= len(p) <= 8 and p.isalnum()]
        return list(dict.fromkeys(parts))

    return []


def assign_cpl_prefix(brand, engine, cpl_list):
    brand = (brand or "").lower()
    engine = (engine or "").upper()
    numeric_cpls = sorted([c for c in cpl_list if c.isdigit() and int(c) >= 100], key=int)
    if brand == "caterpillar" and engine.startswith("C15"):
        # For CAT, HHP often provides ESN prefixes like "2WS, 5EK, 6NZ".
        return cpl_list[0] if cpl_list else ""
    if brand == "cummins" and numeric_cpls:
        return numeric_cpls[0]
    return ""


def extract_kit_contents(soup: BeautifulSoup, title: str = "") -> List[str]:
    """Extract contents list from HHP page.

    Important: do NOT invent kit contents. If none are found, return an empty list.
    """

    t = (title or "").lower()
    is_kit_like = any(k in t for k in ["kit", "inframe", "rebuild", "overhaul"])

    roots = []
    sd = soup.select_one(".woocommerce-product-details__short-description")
    if sd:
        roots.append(sd)
    desc = soup.select_one("#tab-description")
    if desc:
        roots.append(desc)
    for panel in soup.select(".woocommerce-Tabs-panel"):
        roots.append(panel)

    if roots:
        full = "\n".join(r.get_text("\n", strip=True) for r in roots if r)
    else:
        full = soup.get_text("\n", strip=True)

    found: list[str] = []

    lines = [normalize(x) for x in full.splitlines()]
    start_idx = -1
    first_inline = ""
    for i, line in enumerate(lines):
        low = line.lower()
        if low.startswith("contents"):
            start_idx = i
            if ":" in line:
                after = normalize(line.split(":", 1)[1])
                if after:
                    first_inline = after
            break

    def _is_stop_line(s: str) -> bool:
        low = (s or "").lower().strip()
        if not low:
            return True
        stop_prefixes = (
            "oem cross references",
            "cross references",
            "compatibility",
            "why choose",
            "description",
            "details for",
            "weight",
            "dimensions",
            "additional shipping",
            "vendor warranty",
            "condition",
            "warranty",
        )
        return low.startswith(stop_prefixes)

    def _is_noise(s: str) -> bool:
        low = (s or "").lower().strip()
        if not low:
            return True
        noise_starts = (
            "share",
            "close",
            "loading",
            "phone:",
            "text:",
            "email:",
            "copyright",
            "privacy policy",
            "terms & conditions",
            "looking for something else",
            "suggest a product",
        )
        if ">" in low and low.split(">", 1)[0].isalpha():
            return True
        return low.startswith(noise_starts)

    if start_idx >= 0:
        if first_inline:
            found.append(first_inline)
        for j in range(start_idx + 1, min(start_idx + 40, len(lines))):
            line = lines[j]
            if _is_stop_line(line):
                break
            if _is_noise(line):
                continue
            if len(line) < 3:
                continue
            found.append(line)

    # Heuristic extraction for kit-like products
    if not found and is_kit_like:
        patterns = [
            r"(?:kit\s+contents|included\s+components|kit\s+includes?|includes?|contains?|complete\s+with)\s*(?::|\-|–)?\s*(.+?)(?:\n\s*\n|\.|$)",
        ]
        tmp: set[str] = set()
        for pattern in patterns:
            for match in re.findall(pattern, full, re.I | re.S):
                items = re.split(r",|\band\b|•|\*|\n", match)
                for item in items:
                    item = normalize(item).strip(" .;:")
                    if len(item) < 4:
                        continue
                    tmp.add(item)
        found = sorted(tmp)

    deduped = list(dict.fromkeys([f for f in found if f]))
    return deduped


def watermark_folder(input_dir: Path, output_dir: Path, logo_path: str) -> int:
    """Watermark all JPEGs in input_dir and save to output_dir.
    
    Returns count of successfully watermarked images.
    """
    ensure_dir(output_dir)
    
    # Check if input directory has any JPEGs
    jpg_files = list(input_dir.glob("*.jpg"))
    if not jpg_files:
        print(f"[WATERMARK] No JPEGs found in {input_dir}")
        return 0
    
    print(f"[WATERMARK] Found {len(jpg_files)} JPEGs in {input_dir}")
    print(f"[WATERMARK] Output dir: {output_dir}")
    print(f"[WATERMARK] Logo path: {logo_path}")
    
    # Check if logo exists
    if not Path(logo_path).exists():
        print(f"[WATERMARK ERROR] Logo file not found: {logo_path}")
        return 0
    
    try:
        logo = Image.open(logo_path).convert("RGBA")
        print(f"[WATERMARK] Logo loaded: {logo.width}x{logo.height}")
    except Exception as e:
        print(f"[WATERMARK ERROR] Failed to load logo: {e}")
        return 0
    
    count = 0
    for img_path in jpg_files:
        try:
            with Image.open(img_path) as base_img:
                base = base_img.convert("RGBA")
                w, h = base.size
                wm_w = int(w * WM_SCALE)
                ratio = wm_w / logo.width
                wm = logo.resize((wm_w, int(logo.height * ratio)))
                alpha = ImageEnhance.Brightness(wm.split()[-1]).enhance(WM_OPACITY)
                wm.putalpha(alpha)
                overlay = Image.new("RGBA", base.size)
                overlay.paste(wm, ((w - wm.width) // 2, (h - wm.height) // 2), wm)
                out = Image.alpha_composite(base, overlay).convert("RGB")
                out_path = output_dir / img_path.name
                out.save(out_path, quality=92)
                count += 1
                print(f"[WATERMARK] Saved: {out_path.name}")
        except (IOError, OSError) as e:
            print(f"[WATERMARK ERROR] Failed to watermark {img_path.name}: {e}")
        except Exception as e:
            print(f"[WATERMARK ERROR] Unexpected error for {img_path.name}: {e}")
    
    print(f"[WATERMARK] Complete: {count}/{len(jpg_files)} images watermarked")
    return count


def extract_engine_model_tags(soup: BeautifulSoup, manufacturer: str) -> List[str]:
    """Extract engine model tags like 'Caterpillar>C15' from the product page."""

    make = normalize(manufacturer)
    if not make or make.lower() in {"all", "unknown"}:
        return []

    roots = []
    sd = soup.select_one(".woocommerce-product-details__short-description")
    if sd:
        roots.append(sd)
    desc = soup.select_one("#tab-description")
    if desc:
        roots.append(desc)
    for panel in soup.select(".woocommerce-Tabs-panel"):
        roots.append(panel)

    text = "\n".join(r.get_text("\n", strip=True) for r in roots if r) or soup.get_text("\n", strip=True)

    make_re = re.escape(make)
    matches = re.findall(rf"\b{make_re}\s*>\s*([A-Za-z0-9][A-Za-z0-9 \-]{{0,30}})\b", text, flags=re.I)
    out: list[str] = []
    for m in matches:
        model = normalize(m)
        if not model:
            continue
        out.append(f"{make}>{model}")

    return list(dict.fromkeys(out))
