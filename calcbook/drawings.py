"""Visual change tracking for drawing sets.

Sheets are matched between issues by their sheet number (S-201), read from the
title block, so a set can be split or re-ordered across files between issues.
Each pair of sheets is rendered and compared pixel by pixel:

    red   = linework / text removed since the older issue
    blue  = linework / text added in the newer issue
    grey  = unchanged

Changed pixels are grouped into regions (the "clouds" shown in the viewer),
and the words that appeared or disappeared are listed so a reviewer can see
"W16X31 -> W18X35" without hunting for it.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .history import Workspace
from .store import DocRecord, Issue

DIFF_DPI = 150
THUMB_W = 520
INK_LEVEL = 200          # grey level below which a pixel counts as linework
CELL = 16                # px; change regions are built from cells this size
CELL_MIN_PX = 4          # changed pixels needed to mark a cell
SHIFT_TOLERANCE = 1      # px; ignore anti-aliasing / sub-pixel shifts


@dataclass
class SheetRef:
    sheet_id: str
    doc: DocRecord
    page: int            # 1-based
    title: str = ""

    @property
    def label(self) -> str:
        return f"{self.sheet_id} {self.title}".strip()


@dataclass
class SheetDiff:
    sheet_id: str
    status: str                          # new | removed | changed | unchanged
    old: SheetRef | None
    new: SheetRef | None
    regions: list[tuple[float, float, float, float]] = field(default_factory=list)  # x0,y0,x1,y1 in 0..1
    changed_ratio: float = 0.0
    words_added: list[str] = field(default_factory=list)
    words_removed: list[str] = field(default_factory=list)
    key: str = ""                        # cache key for images


# --------------------------------------------------------------------------- #
# Sheet identification
# --------------------------------------------------------------------------- #
def _cache_dir(ws: Workspace, name: str) -> Path:
    d = ws.store.dir / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _detect_sheets(ws: Workspace, doc: DocRecord) -> list[dict]:
    """[{page, sheet_id, title, words}] for every page of a drawing PDF (cached by
    sha). `words` are [text, x0, top, x1, bottom] with coordinates in 0..1."""
    cache = _cache_dir(ws, "sheets") / f"{doc.sha256}.json"
    pattern = ws.rules.sheet.pattern
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("pattern") == pattern and data.get("v") == 2:
            return data["pages"]

    import pdfplumber
    pages = []
    with pdfplumber.open(str(ws.store.archived_file(doc))) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pw, ph = float(page.width), float(page.height)
            words = page.extract_words(keep_blank_chars=False, extra_attrs=["size"])
            best, best_score = None, -1.0
            for w in words:
                t = w["text"].strip().strip(".,;:")
                if not ws.rules.sheet.match(t):
                    continue
                # Title-block sheet numbers are large and near the bottom-right.
                score = (float(w.get("size", 0)) * 3 + 20 * float(w["x0"]) / pw
                         + 20 * float(w["top"]) / ph)
                if score > best_score:
                    best, best_score = w, score
            title = ""
            if best:
                # Sheet title: the nearest line of text above the number, in the same column.
                above = [w for w in words if w is not best
                         and abs(float(w["x0"]) - float(best["x0"])) < 60
                         and 0 < float(best["top"]) - float(w["bottom"]) < 150]
                if above:
                    top = max(float(w["top"]) for w in above)
                    line = [w for w in words if w is not best and abs(float(w["top"]) - top) < 3
                            and float(w["x0"]) >= float(best["x0"]) - 60]
                    title = " ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"]))[:60]
            pages.append({
                "page": i,
                "sheet_id": best["text"].strip().strip(".,;:") if best else "",
                "title": title,
                "words": [[w["text"], round(float(w["x0"]) / pw, 5), round(float(w["top"]) / ph, 5),
                           round(float(w["x1"]) / pw, 5), round(float(w["bottom"]) / ph, 5)]
                          for w in words],
            })
    cache.write_text(json.dumps({"pattern": pattern, "v": 2, "pages": pages}))
    return pages


def _sheet_words(ws: Workspace, ref: SheetRef) -> list[list]:
    pages = _detect_sheets(ws, ref.doc)
    return pages[ref.page - 1]["words"] if ref.page - 1 < len(pages) else []


def sheet_index(ws: Workspace, issue: Issue) -> dict[str, SheetRef]:
    """Every drawing sheet in an issue, keyed by sheet number."""
    out: dict[str, SheetRef] = {}
    for d in ws.ordered_documents(issue):
        if not ws.project.is_drawing_package(d.package):
            continue
        for p in _detect_sheets(ws, d):
            sid = p["sheet_id"] or f"{d.name} p{p['page']}"
            key, n = sid, 2
            while key in out:                  # same number twice: keep both
                key, n = f"{sid} ({n})", n + 1
            out[key] = SheetRef(key, d, p["page"], p["title"])
    return out


def _sheet_sort(sid: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", sid)]


# --------------------------------------------------------------------------- #
# Rendering + pixel comparison
# --------------------------------------------------------------------------- #
def render(ws: Workspace, ref: SheetRef, dpi: int = DIFF_DPI) -> Path:
    path = _cache_dir(ws, "render") / f"{ref.doc.sha256}_p{ref.page}_{dpi}.png"
    if not path.exists():
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(ws.store.archived_file(ref.doc)))
        try:
            img = pdf[ref.page - 1].render(scale=dpi / 72).to_pil().convert("RGB")
        finally:
            pdf.close()
        img.save(path, optimize=True)
    return path


def _ink(img: Image.Image, size: tuple[int, int]) -> np.ndarray:
    a = np.asarray(img.convert("L"))
    out = np.zeros((size[1], size[0]), dtype=bool)
    out[:a.shape[0], :a.shape[1]] = a < INK_LEVEL
    return out


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Binary dilation by a (2r+1)^2 square, without wrapping at the edges."""
    if r <= 0:
        return mask
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if not (dx or dy):
                continue
            out[max(0, dy):h - max(0, -dy), max(0, dx):w - max(0, -dx)] |= \
                mask[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
    return out


def _regions(changed: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Group changed pixels into boxes (pixel coords) via a coarse cell grid."""
    h, w = changed.shape
    gh, gw = -(-h // CELL), -(-w // CELL)
    padded = np.zeros((gh * CELL, gw * CELL), dtype=bool)
    padded[:h, :w] = changed
    counts = padded.reshape(gh, CELL, gw, CELL).sum(axis=(1, 3))
    grid = counts >= CELL_MIN_PX
    # bridge small gaps so one edit gives one region
    grid = _dilate(grid, 1)
    seen = np.zeros_like(grid)
    boxes = []
    for y0, x0 in zip(*np.nonzero(grid)):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = True
        ys, xs = [], []
        while stack:
            y, x = stack.pop()
            ys.append(y)
            xs.append(x)
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < gh and 0 <= nx < gw and grid[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        boxes.append((min(xs) * CELL, min(ys) * CELL,
                      min(w, (max(xs) + 1) * CELL), min(h, (max(ys) + 1) * CELL)))
    return boxes


def _drop(words: list[list], counts: Counter) -> list[list]:
    """Remove up to counts[text] occurrences of each text from words."""
    left, out = Counter(counts), []
    for w in words:
        if left[w[0]] > 0:
            left[w[0]] -= 1
        else:
            out.append(w)
    return out


def _merge(boxes: list[list[float]]) -> list[list[float]]:
    """Merge overlapping / touching boxes until stable."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[float]] = []
        for b in boxes:
            for o in out:
                if b[0] <= o[2] and o[0] <= b[2] and b[1] <= o[3] and o[1] <= b[3]:
                    o[:] = [min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])]
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def compare_sheets(ws: Workspace, old: SheetRef, new: SheetRef, dpi: int = DIFF_DPI) -> SheetDiff:
    key = hashlib.sha1(f"{old.doc.sha256}:{old.page}:{new.doc.sha256}:{new.page}:{dpi}".encode()).hexdigest()[:16]
    meta_path = _cache_dir(ws, "diff") / f"{key}.json"
    sid = new.sheet_id

    if meta_path.exists():
        m = json.loads(meta_path.read_text())
        return SheetDiff(sid, m["status"], old, new, [tuple(r) for r in m["regions"]],
                         m["changed_ratio"], m["words_added"], m["words_removed"], key)

    a = Image.open(render(ws, old, dpi))
    b = Image.open(render(ws, new, dpi))
    size = (max(a.width, b.width), max(a.height, b.height))
    ia, ib = _ink(a, size), _ink(b, size)
    removed = ia & ~_dilate(ib, SHIFT_TOLERANCE)
    added = ib & ~_dilate(ia, SHIFT_TOLERANCE)
    changed = removed | added
    boxes = _regions(changed)

    # Overlay image
    rgb = np.full((size[1], size[0], 3), 255, dtype=np.uint8)
    rgb[ia | ib] = (176, 182, 191)
    rgb[_dilate(removed, 1)] = (220, 38, 38)
    rgb[_dilate(added, 1)] = (37, 99, 235)
    overlay = Image.fromarray(rgb)

    # Word-level changes, located on the sheet (catches W16X31 -> W18X35, which
    # barely moves any pixels).
    wa, wb = _sheet_words(ws, old), _sheet_words(ws, new)
    ta, tb = [w[0] for w in wa], [w[0] for w in wb]
    rem_w, add_w = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op in ("delete", "replace"):
            rem_w += wa[i1:i2]
        if op in ("insert", "replace"):
            add_w += wb[j1:j2]
    # A word that only moved in reading order is not a change.
    moved = Counter(w[0] for w in rem_w) & Counter(w[0] for w in add_w)
    rem_w, add_w = _drop(rem_w, moved), _drop(add_w, moved)

    W, H = size
    pad = 0.004
    boxes_n = [[x0 / W, y0 / H, x1 / W, y1 / H] for x0, y0, x1, y1 in boxes]
    boxes_n += [[w[1] - pad, w[2] - pad, w[3] + pad, w[4] + pad] for w in rem_w + add_w]
    regions = [tuple(round(v, 5) for v in r) for r in _merge(boxes_n)]
    regions.sort(key=lambda r: (round(r[1], 2), r[0]))

    # Inside changed words compare strictly (no shift tolerance), so an edit
    # like W16X31 -> W18X35 shows the old glyphs red and the new ones blue.
    if rem_w or add_w:
        box = np.zeros_like(ia)
        for w in rem_w + add_w:
            x0, y0 = max(0, int(w[1] * W) - 2), max(0, int(w[2] * H) - 2)
            x1, y1 = min(W, int(w[3] * W) + 3), min(H, int(w[4] * H) + 3)
            box[y0:y1, x0:x1] = True
        rgb[box & ia & ~ib] = (220, 38, 38)
        rgb[box & ib & ~ia] = (37, 99, 235)
        tint = Image.fromarray(rgb)
    else:
        tint = overlay
    tint.save(_cache_dir(ws, "diff") / f"{key}_overlay.png", optimize=True)

    thumb = tint.copy()
    dr = ImageDraw.Draw(thumb)
    for x0, y0, x1, y1 in regions:
        dr.rectangle((x0 * W - 6, y0 * H - 6, x1 * W + 6, y1 * H + 6), outline=(234, 88, 12), width=4)
    thumb.thumbnail((THUMB_W, THUMB_W * 2))
    thumb.save(_cache_dir(ws, "diff") / f"{key}_thumb.png", optimize=True)

    added_w = [w[0] for w in add_w]
    removed_w = [w[0] for w in rem_w]
    ratio = float(changed.sum()) / max(1, int((ia | ib).sum()))
    status = "changed" if regions else "unchanged"
    meta = {"status": status, "regions": regions, "changed_ratio": ratio,
            "words_added": added_w[:80], "words_removed": removed_w[:80]}
    meta_path.write_text(json.dumps(meta))
    return SheetDiff(sid, status, old, new, regions, ratio, meta["words_added"], meta["words_removed"], key)


def compare_sets(ws: Workspace, old_issue: Issue, new_issue: Issue) -> list[SheetDiff]:
    old, new = sheet_index(ws, old_issue), sheet_index(ws, new_issue)
    out = []
    for sid in sorted(set(old) | set(new), key=_sheet_sort):
        o, n = old.get(sid), new.get(sid)
        if o and n:
            if o.doc.sha256 == n.doc.sha256 and o.page == n.page:
                out.append(SheetDiff(sid, "unchanged", o, n))
            else:
                out.append(compare_sheets(ws, o, n))
        elif n:
            out.append(SheetDiff(sid, "new", None, n))
        else:
            out.append(SheetDiff(sid, "removed", o, None))
    return out


def diff_image(ws: Workspace, key: str, kind: str) -> Path | None:
    """kind: overlay | thumb"""
    if not re.fullmatch(r"[0-9a-f]{16}", key) or kind not in ("overlay", "thumb"):
        return None
    p = ws.store.dir / "diff" / f"{key}_{kind}.png"
    return p if p.exists() else None


def region_label(r) -> str:
    """Rough location of a region on the sheet, e.g. 'upper left'."""
    x, y = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    if r[0] > 0.78:
        return "Title block"
    v = "Upper" if y < 0.34 else ("Middle" if y < 0.67 else "Lower")
    h = "left" if x < 0.3 else ("centre" if x < 0.55 else "right")
    return f"{v} {h}"


def to_dict(d: SheetDiff) -> dict:
    out = asdict(d)
    out.pop("old"), out.pop("new")
    out["old_label"] = d.old.label if d.old else ""
    out["new_label"] = d.new.label if d.new else ""
    return out
