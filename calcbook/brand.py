"""BigDelta name and logo.

The mark is a delta (Δ) built like a truss: three steel-blue triangles around an
orange inverted triangle. The outer triangle is the most stable structural
form; the orange centre is the change, the "delta", that the app tracks. It
mirrors the revision delta engineers draw on revised sheets.

Every icon is generated from the same geometry by code (SVG, ICO, PNG) and
written into the web app's static folder at start-up, so no image files need
to be shipped or installed, and browsers on macOS, Windows, Linux, iOS and
Android each find the format they expect.
"""
from __future__ import annotations

import io
from pathlib import Path

APP_NAME = "BigDelta"
TAGLINE = "Calc packages, revisions and design intent for structural engineers"

NAVY = "#14202e"
STEEL = "#3b82c4"
ORANGE = "#f26a1b"

# Geometry on a 64 x 64 grid
OUTER = [(32, 7), (59, 55), (5, 55)]
INNER = [(18.5, 31), (45.5, 31), (32, 55)]


def _pts(points, s=1.0, dx=0.0, dy=0.0) -> str:
    return " ".join(f"{x * s + dx:.2f},{y * s + dy:.2f}" for x, y in points)


def logo_svg(size: int | None = None, background: bool = True) -> str:
    """The mark as SVG. With background, it sits on a navy rounded square
    (the app icon); without, it is the bare mark for use on light pages."""
    wh = f' width="{size}" height="{size}"' if size else ""
    bg = f'<rect width="64" height="64" rx="14" fill="{NAVY}"/>' if background else ""
    gap = NAVY if background else "#ffffff"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"{wh} role="img" aria-label="{APP_NAME}">'
            f"<title>{APP_NAME}</title>{bg}"
            f'<polygon points="{_pts(OUTER)}" fill="{STEEL}" stroke="{STEEL}" stroke-width="2" stroke-linejoin="round"/>'
            f'<polygon points="{_pts(INNER)}" fill="{ORANGE}" stroke="{gap}" stroke-width="3.2" stroke-linejoin="round"/>'
            "</svg>")


def logo_png(size: int = 512, background: bool = True) -> bytes:
    """Rasterise the mark with Pillow (4x supersampled for clean edges)."""
    from PIL import Image, ImageDraw
    k = 4
    big = size * k
    s = big / 64
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if background:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(14 * s), fill=NAVY)
    d.polygon([(x * s, y * s) for x, y in OUTER], fill=STEEL)
    gap = NAVY if background else (255, 255, 255, 255)
    # a slightly larger inverted triangle in the gap colour gives the white/navy web lines
    cx, cy = 32, (31 + 31 + 55) / 3
    grow = [(cx + (x - cx) * 1.16, cy + (y - cy) * 1.16) for x, y in INNER]
    d.polygon([(x * s, y * s) for x, y in grow], fill=gap)
    d.polygon([(x * s, y * s) for x, y in INNER], fill=ORANGE)
    img = img.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def write_icons(folder: Path) -> list[str]:
    """Write favicon.svg, favicon.ico (16-256 px), icon-192/512.png and
    apple-touch-icon.png. Returns the file names written."""
    from PIL import Image
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "favicon.svg").write_text(logo_svg())
    (folder / "logo-mark.svg").write_text(logo_svg(background=False))
    for n in (192, 512):
        (folder / f"icon-{n}.png").write_bytes(logo_png(n))
    (folder / "apple-touch-icon.png").write_bytes(logo_png(180))
    base = Image.open(io.BytesIO(logo_png(256)))
    base.save(folder / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    import json
    (folder / "site.webmanifest").write_text(json.dumps({
        "name": APP_NAME, "short_name": APP_NAME, "start_url": "/", "display": "standalone",
        "background_color": NAVY, "theme_color": NAVY,
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]}, indent=2))
    return ["favicon.svg", "logo-mark.svg", "icon-192.png", "icon-512.png", "apple-touch-icon.png", "favicon.ico",
            "site.webmanifest"]


ICON_FILES = ["favicon.svg", "logo-mark.svg", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
              "favicon.ico", "site.webmanifest"]


def draw_logo(c, x: float, y: float, size: float, background: bool = False) -> None:
    """Draw the mark on a ReportLab canvas with (x, y) the lower-left corner."""
    from reportlab.lib.colors import HexColor, white
    s = size / 64

    def pt(px, py):
        return x + px * s, y + (64 - py) * s

    if background:
        c.setFillColor(HexColor(NAVY))
        c.roundRect(x, y, size, size, 14 * s, stroke=0, fill=1)
    for pts, color in ((OUTER, HexColor(STEEL)),):
        p = c.beginPath()
        p.moveTo(*pt(*pts[0]))
        for q in pts[1:]:
            p.lineTo(*pt(*q))
        p.close()
        c.setFillColor(color)
        c.drawPath(p, stroke=0, fill=1)
    c.setStrokeColor(HexColor(NAVY) if background else white)
    c.setLineWidth(3.2 * s)
    c.setLineJoin(1)
    p = c.beginPath()
    p.moveTo(*pt(*INNER[0]))
    for q in INNER[1:]:
        p.lineTo(*pt(*q))
    p.close()
    c.setFillColor(HexColor(ORANGE))
    c.drawPath(p, stroke=1, fill=1)
