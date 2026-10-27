"""BigDelta name and logo.

The mark is the team's BigDelta logo: a teal delta (Δ) outline, the revision
triangle engineers draw on revised sheets and the most stable structural
form, with a bronze node at its apex and a bronze bar inside it for the change
being tracked. Colours are taken from the supplied logo.

Every icon is generated from the same geometry by code (SVG, ICO, PNG) and
written into the web app's static folder at start-up, so no image files need
to be shipped or installed, and browsers on macOS, Windows, Linux, iOS and
Android each find the format they expect.
"""
from __future__ import annotations

import io
from pathlib import Path

APP_NAME = "BigDelta"
TAGLINE = "Structural Workspace"

NAVY = "#132b40"
TEAL = "#3e8585"
BRONZE = "#887355"
SLATE = "#909ca6"
STEEL = TEAL          # kept for older callers
ORANGE = BRONZE

# Geometry on a 64 x 64 grid: a delta drawn as an outline (the structure), a
# bronze node at its apex and a bronze bar inside (the change it tracks).
APEX, LEFT, RIGHT = (32.0, 11.0), (9.0, 53.0), (55.0, 53.0)
STROKE = 5.2
DOT_R = 4.4
BAR = (28.0, 39.0, 43.0, 44.5)    # x0, y0, x1, y1


def logo_svg(size: int | None = None, background: bool = True) -> str:
    """The mark as SVG; with background it is the navy app icon."""
    wh = f' width="{size}" height="{size}"' if size else ""
    bg = f'<rect width="64" height="64" rx="14" fill="{NAVY}"/>' if background else ""
    pts = " ".join(f"{x},{y}" for x, y in (APEX, LEFT, RIGHT))
    x0, y0, x1, y1 = BAR
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"{wh} role="img" aria-label="{APP_NAME}">'
            f"<title>{APP_NAME}</title>{bg}"
            f'<polygon points="{pts}" fill="none" stroke="{TEAL}" stroke-width="{STROKE}" stroke-linejoin="round"/>'
            f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="1" fill="{BRONZE}"/>'
            f'<circle cx="{APEX[0]}" cy="{APEX[1]}" r="{DOT_R}" fill="{BRONZE}"/>'
            "</svg>")


def logo_png(size: int = 512, background: bool = True) -> bytes:
    """Rasterise the mark with Pillow (4x supersampled)."""
    from PIL import Image, ImageDraw
    k = 4
    big = size * k
    s = big / 64
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if background:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(14 * s), fill=NAVY)
    pts = [(x * s, y * s) for x, y in (APEX, LEFT, RIGHT)]
    w = int(STROKE * s)
    d.line(pts + [pts[0]], fill=TEAL, width=w, joint="curve")
    for x, y in pts:                                   # round the corners
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=TEAL)
    x0, y0, x1, y1 = BAR
    d.rounded_rectangle((x0 * s, y0 * s, x1 * s, y1 * s), radius=int(1 * s), fill=BRONZE)
    r = DOT_R * s
    d.ellipse((APEX[0] * s - r, APEX[1] * s - r, APEX[0] * s + r, APEX[1] * s + r), fill=BRONZE)
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
    from reportlab.lib.colors import HexColor
    s = size / 64

    def pt(px, py):
        return x + px * s, y + (64 - py) * s

    if background:
        c.setFillColor(HexColor(NAVY))
        c.roundRect(x, y, size, size, 14 * s, stroke=0, fill=1)
    c.setStrokeColor(HexColor(TEAL))
    c.setLineWidth(STROKE * s)
    c.setLineJoin(1)
    p = c.beginPath()
    p.moveTo(*pt(*APEX))
    p.lineTo(*pt(*LEFT))
    p.lineTo(*pt(*RIGHT))
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.setFillColor(HexColor(BRONZE))
    x0, y0, x1, y1 = BAR
    bx, by = pt(x0, y1)
    c.roundRect(bx, by, (x1 - x0) * s, (y1 - y0) * s, 1 * s, stroke=0, fill=1)
    cx, cy = pt(*APEX)
    c.circle(cx, cy, DOT_R * s, stroke=0, fill=1)
