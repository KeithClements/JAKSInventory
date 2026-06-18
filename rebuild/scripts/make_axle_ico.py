"""
scripts/make_axle_ico.py
========================
Render the Axle app-tile brand mark (app/static/brand/axle-app-tile.svg) to a
Windows multi-size .ico so the server launcher shortcut shows the Axle logo.

Mil-green rounded tile (#5a6630) + white concentric-ring "hub" mark with a lime
center dot — reproduced with Pillow (no SVG rasterizer needed). Supersampled then
downscaled (LANCZOS) for clean edges. Writes app/static/brand/axle.ico (+ a
256px PNG byproduct).

Run:  .venv\\Scripts\\python.exe scripts\\make_axle_ico.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_ICO = ROOT / "app" / "static" / "brand" / "axle.ico"
OUT_PNG = ROOT / "app" / "static" / "brand" / "axle-icon-256.png"

MIL   = (90, 102, 48, 255)    # #5a6630 tile background
WHITE = (255, 255, 255, 255)
LIME  = (182, 207, 69, 255)   # #b6cf45 center dot

S = 1024                       # supersample canvas
f = S / 64.0                   # svg viewBox is 0..64
# Mark group transform from the SVG: translate(6.4,6.4) scale(0.8)
def TX(v: float) -> float: return (v * 0.8 + 6.4) * f
def TR(r: float) -> float: return r * 0.8 * f


def _ring(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, width: float, color):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=max(1, round(width)))


def main() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Mil-green rounded tile
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=14 * f, fill=MIL)

    cx = cy = TX(32)
    _ring(d, cx, cy, TR(28), 4 * 0.8 * f, WHITE)   # outer ring (sw 4)
    _ring(d, cx, cy, TR(17), 3 * 0.8 * f, WHITE)   # inner ring (sw 3)
    # lime center dot
    rdot = TR(6.5)
    d.ellipse([cx - rdot, cy - rdot, cx + rdot, cy + rdot], fill=LIME)
    # white needle/tick at top
    d.rounded_rectangle([TX(30), TX(20), TX(34), TX(26)], radius=1 * 0.8 * f, fill=WHITE)

    base = img.resize((256, 256), Image.LANCZOS)
    base.save(OUT_PNG)
    base.save(OUT_ICO, format="ICO",
              sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"Wrote {OUT_ICO}")
    print(f"Wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
