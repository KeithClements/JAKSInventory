"""
scripts/fetch_fonts.py
======================
§21 — self-host the Axle brand fonts. Downloads the LATIN subset woff2 for every
weight the UI uses (Oswald / Barlow / IBM Plex Mono) into app/static/fonts/ and
regenerates app/static/fonts/fonts.css with @font-face rules pointing at the
local files. Run once (or to refresh): `python scripts/fetch_fonts.py`.

Rationale: the app is internet-exposed and the whole brand depended on the Google
Fonts CDN (breaks the UI on bad internet). Self-hosting also lets the CSP drop the
fonts.googleapis.com / fonts.gstatic.com origins.
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
CSS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Oswald:wght@400;500;600;700&"
    "family=Barlow:wght@400;500;600;700&"
    "family=IBM+Plex+Mono:wght@400;500;600&display=swap"
)
OUT = Path(__file__).resolve().parents[1] / "app" / "static" / "fonts"


def _fetch(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read() if binary else r.read().decode("utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    css = _fetch(CSS_URL)
    # Split into (subset-name, @font-face block) pairs; keep only 'latin'.
    parts = re.split(r"/\*\s*([\w-]+)\s*\*/", css)
    faces, n = [], 0
    for i in range(1, len(parts) - 1, 2):
        if parts[i].strip() != "latin":
            continue
        block = parts[i + 1]
        fam = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        weight = re.search(r"font-weight:\s*(\d+)", block).group(1)
        url = re.search(r"src:\s*url\(([^)]+)\)", block).group(1)
        fname = f"{fam.replace(' ', '')}-{weight}.woff2"
        (OUT / fname).write_bytes(_fetch(url, binary=True))
        n += 1
        faces.append(
            "@font-face{font-family:'%s';font-style:normal;font-weight:%s;"
            "font-display:swap;src:url('/static/fonts/%s') format('woff2');}"
            % (fam, weight, fname)
        )
    (OUT / "fonts.css").write_text("\n".join(faces) + "\n", encoding="utf-8")
    print(f"downloaded {n} woff2 files + fonts.css into {OUT}")


if __name__ == "__main__":
    main()
