"""Compile every sub-package of an issue into one bookmarked calculation book.

Front matter: cover (with issue history and package register), contents, and a
revision summary. Every sheet gets a footer with project, document and
"Sheet n of N"; sheets whose text changed since the previous issue get a
revision delta tag in the top-right corner.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .history import (NEW, REMOVED, REVISED, UNCHANGED, Workspace, changed_pages, compress_ranges,
                      doc_statuses, issues_upto, package_revisions)
from .store import DocRecord, Issue

W, H = letter
M = 54
ROW = 17
ROWS_PER_PAGE = 34
INK = HexColor("#1d2430")
MUTED = HexColor("#6b7280")
RULE = HexColor("#d4d8de")
ACCENT = HexColor("#1f4e79")
DELTA = HexColor("#c0392b")
NEW_C = HexColor("#1e7a46")


@dataclass
class Entry:
    doc: DocRecord
    status: str
    changed: list[int]                  # 1-based pages within the document
    start: int = 0                      # first sheet number in the book
    sheets: list[int] = field(default_factory=list)


def _fit(text: str, font: str, size: float, width: float) -> str:
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def _page_header(c: canvas.Canvas, title: str, ws: Workspace, issue: Issue) -> float:
    c.setFillColor(ACCENT)
    c.rect(0, H - 8, W, 8, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(M, H - M - 4, title)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(MUTED)
    c.drawRightString(W - M, H - M - 2, f"{ws.project.number}  {ws.project.name}  ·  Issue {issue.label}")
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(M, H - M - 14, W - M, H - M - 14)
    return H - M - 36


def _status_label(status: str) -> tuple[str, object]:
    return {NEW: ("NEW", NEW_C), REVISED: ("REVISED", DELTA), REMOVED: ("REMOVED", MUTED),
            UNCHANGED: ("—", MUTED)}[status]


# --------------------------------------------------------------------------- #
def _draw_cover(c, ws: Workspace, issue: Issue, entries: list[Entry], total: int) -> None:
    p = ws.project
    c.setFillColor(ACCENT)
    c.rect(0, H - 8, W, 8, stroke=0, fill=1)
    y = H - 110
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M, y, "S T R U C T U R A L   C A L C U L A T I O N S")
    y -= 34
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(M, y, _fit(p.name, "Helvetica-Bold", 26, W - 2 * M))
    y -= 22
    c.setFont("Helvetica", 11)
    c.setFillColor(MUTED)
    bits = [b for b in (f"Project {p.number}" if p.number else "", p.client) if b]
    c.drawString(M, y, "   ·   ".join(bits))

    # Issue box
    y -= 48
    c.setStrokeColor(RULE)
    c.setFillColor(HexColor("#f4f6f9"))
    c.roundRect(M, y - 58, W - 2 * M, 70, 6, stroke=1, fill=1)
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 30)
    c.drawString(M + 18, y - 34, f"Issue {issue.label}")
    c.setFillColor(INK)
    c.setFont("Helvetica", 10)
    c.drawString(M + 200, y - 14, f"Date: {issue.issued_on}")
    c.drawString(M + 200, y - 30, f"Sheets: {total}    Documents: {len(entries)}")
    c.setFillColor(MUTED)
    c.drawString(M + 200, y - 46, _fit(issue.note or "", "Helvetica", 10, W - 2 * M - 220))

    # Package register
    y -= 96
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M, y, "Sub-packages")
    y -= 8
    cols = [M, M + 52, M + 260, M + 380, M + 430, W - M]
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(MUTED)
    y -= 14
    for x, t in zip(cols, ["ID", "Package", "Responsible", "Docs", "Sheets"]):
        c.drawString(x, y, t)
    c.drawRightString(W - M, y, "Pkg rev")
    c.setStrokeColor(RULE)
    c.line(M, y - 5, W - M, y - 5)
    pkg_rev = package_revisions(ws.store, issue)
    c.setFont("Helvetica", 9)
    for pkg in [q for q in p.packages if not q.is_drawings]:
        es = [e for e in entries if e.doc.package == pkg.id]
        y -= ROW
        c.setFillColor(INK)
        c.drawString(cols[0], y, pkg.id)
        c.drawString(cols[1], y, _fit(pkg.title, "Helvetica", 9, 200))
        c.drawString(cols[2], y, _fit(pkg.responsible or pkg.discipline, "Helvetica", 9, 115))
        c.drawString(cols[3], y, str(len(es)))
        c.drawString(cols[4], y, str(sum(e.doc.n_pages for e in es)))
        c.drawRightString(W - M, y, pkg_rev.get(pkg.id, "—"))

    # Issue history
    y -= 40
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M, y, "Issue history")
    y -= 22
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(MUTED)
    c.drawString(M, y, "Issue")
    c.drawString(M + 52, y, "Date")
    c.drawString(M + 140, y, "Description")
    c.line(M, y - 5, W - M, y - 5)
    c.setFont("Helvetica", 9)
    hist = issues_upto(ws.store, issue)[-8:]
    for iss in reversed(hist):
        y -= ROW
        c.setFillColor(ACCENT if iss.id == issue.id else INK)
        c.drawString(M, y, iss.label)
        c.drawString(M + 52, y, iss.issued_on or "")
        c.drawString(M + 140, y, _fit(iss.note or "", "Helvetica", 9, W - 2 * M - 140))

    # Engineer of record + stamp box
    c.setStrokeColor(RULE)
    c.rect(W - M - 150, M + 20, 150, 120, stroke=1, fill=0)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUTED)
    c.drawCentredString(W - M - 75, M + 26, "Engineer's stamp")
    if p.engineer_of_record:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(M, M + 52, "ENGINEER OF RECORD")
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(M, M + 36, p.engineer_of_record)
    c.showPage()


def _toc_rows(ws: Workspace, entries: list[Entry]) -> list[tuple]:
    rows = []
    for pkg in ws.project.packages:
        es = [e for e in entries if e.doc.package == pkg.id]
        if not es:
            continue
        rows.append(("pkg", pkg))
        rows.extend(("doc", e) for e in es)
    return rows


def _draw_toc(c, ws, issue, rows, n_pages: int) -> None:
    for pg in range(n_pages):
        y = _page_header(c, "Contents" + (" (cont.)" if pg else ""), ws, issue)
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(MUTED)
        c.drawString(M, y, "Document")
        c.drawString(M + 300, y, "Pages")
        c.drawString(M + 345, y, "Status")
        c.drawRightString(W - M, y, "Sheet")
        y -= 8
        for kind, obj in rows[pg * ROWS_PER_PAGE:(pg + 1) * ROWS_PER_PAGE]:
            y -= ROW
            if kind == "pkg":
                c.setFillColor(ACCENT)
                c.setFont("Helvetica-Bold", 10)
                c.drawString(M, y, _fit(f"{obj.id} — {obj.title}", "Helvetica-Bold", 10, W - 2 * M))
                c.setStrokeColor(RULE)
                c.line(M, y - 4, W - M, y - 4)
                continue
            e: Entry = obj
            c.setFillColor(INK)
            c.setFont("Helvetica", 9)
            c.drawString(M + 12, y, _fit(e.doc.title, "Helvetica", 9, 280))
            c.setFillColor(MUTED)
            c.drawString(M + 300, y, str(e.doc.n_pages))
            lab, col = _status_label(e.status)
            c.setFillColor(col)
            c.setFont("Helvetica-Bold" if e.status != UNCHANGED else "Helvetica", 8)
            c.drawString(M + 345, y, lab)
            c.setFillColor(INK)
            c.setFont("Helvetica", 9)
            c.drawRightString(W - M, y, str(e.start))
            # dotted leader
            c.setStrokeColor(RULE)
            c.setDash(1, 2)
            c.line(M + 410, y + 2, W - M - 24, y + 2)
            c.setDash()
        c.showPage()


def _rev_rows(entries: list[Entry], removed: list[DocRecord]) -> list[tuple]:
    rows: list[tuple] = [("doc", e) for e in entries if e.status in (NEW, REVISED)]
    rows += [("removed", d) for d in removed]
    return rows


def _draw_revsummary(c, ws, issue, prev: Issue | None, rows, n_pages: int) -> None:
    for pg in range(n_pages):
        y = _page_header(c, "Revision summary" + (" (cont.)" if pg else ""), ws, issue)
        c.setFont("Helvetica", 9.5)
        c.setFillColor(INK)
        if prev is None:
            c.drawString(M, y, f"Issue {issue.label} is the first issue — every sheet is new.")
            c.showPage()
            continue
        if pg == 0:
            c.drawString(M, y, f"Changes from Issue {prev.label} ({prev.issued_on}) to Issue {issue.label} "
                               f"({issue.issued_on}).")
            y -= 14
            c.setFillColor(MUTED)
            c.drawString(M, y, "Revised sheets carry a red revision tag (green for new) in the top-right corner. "
                               "Sheet numbers refer to this book.")
            y -= 10
        y -= 14
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(MUTED)
        c.drawString(M, y, "Document")
        c.drawString(M + 280, y, "Status")
        c.drawString(M + 350, y, "Revised sheets")
        c.setStrokeColor(RULE)
        c.line(M, y - 5, W - M, y - 5)
        chunk = rows[pg * ROWS_PER_PAGE:(pg + 1) * ROWS_PER_PAGE]
        if not chunk:
            y -= ROW
            c.setFont("Helvetica", 9)
            c.setFillColor(INK)
            c.drawString(M, y, "No documents changed.")
        for kind, obj in chunk:
            y -= ROW
            c.setFont("Helvetica", 9)
            c.setFillColor(INK)
            if kind == "removed":
                c.drawString(M, y, _fit(f"{obj.package} · {obj.title}", "Helvetica", 9, 270))
                c.setFillColor(MUTED)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(M + 280, y, "REMOVED")
                c.setFont("Helvetica", 9)
                c.drawString(M + 350, y, "withdrawn from this issue")
                continue
            e: Entry = obj
            c.drawString(M, y, _fit(f"{e.doc.package} · {e.doc.title}", "Helvetica", 9, 270))
            lab, col = _status_label(e.status)
            c.setFillColor(col)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(M + 280, y, lab)
            c.setFillColor(INK)
            c.setFont("Helvetica", 9)
            sheets = compress_ranges([e.start + p - 1 for p in e.changed]) or "text unchanged"
            c.drawString(M + 350, y, _fit(sheets, "Helvetica", 9, W - M - (M + 350)))
        c.showPage()


# --------------------------------------------------------------------------- #
def _overlay(w: float, h: float, left: str, center: str, right: str, delta: str | None,
             delta_color=DELTA):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setFillColorRGB(1, 1, 1)
    c.setFillAlpha(0.85)
    c.rect(0, 0, w, 18, stroke=0, fill=1)
    c.setFillAlpha(1)
    c.setFont("Helvetica", 6.5)
    c.setFillColor(MUTED)
    usable = (w - 2 * 24) / 3
    c.drawString(24, 7, _fit(left, "Helvetica", 6.5, usable))
    c.drawCentredString(w / 2, 7, _fit(center, "Helvetica", 6.5, usable))
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 6.5)
    c.drawRightString(w - 24, 7, right)
    if delta:
        tw = stringWidth(delta, "Helvetica-Bold", 7.5) + 22
        x, y = w - tw - 14, h - 24
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColor(delta_color)
        c.setLineWidth(1)
        c.roundRect(x, y, tw, 15, 3, stroke=1, fill=1)
        c.setFillColor(delta_color)
        p = c.beginPath()          # small triangle = revision delta
        p.moveTo(x + 5, y + 3.5)
        p.lineTo(x + 13, y + 3.5)
        p.lineTo(x + 9, y + 11.5)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 17, y + 4.5, delta)
    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def _stamp(page, left, center, right, delta=None, delta_color=DELTA) -> None:
    if page.get("/Rotate", 0):
        page.transfer_rotation_to_content()
    box = page.mediabox
    ov = _overlay(float(box.width), float(box.height), left, center, right, delta, delta_color)
    page.merge_transformed_page(ov, Transformation().translate(float(box.left), float(box.bottom)))


def compile_issue(ws: Workspace, issue: Issue, out_path: Path, mark_changes: bool = True) -> dict:
    prev = ws.store.previous(issue)
    calc = lambda d: not ws.project.is_drawing_package(d.package)  # noqa: E731
    prev_docs = {k: d for k, d in (ws.store.documents_by_key(prev) if prev else {}).items() if calc(d)}
    docs = [d for d in ws.ordered_documents(issue) if calc(d)]
    statuses = doc_statuses(prev_docs if prev else None, {d.doc_key: d for d in docs})
    removed = [d for k, d in prev_docs.items() if statuses.get(k) == REMOVED]

    entries = []
    for d in docs:
        st = statuses[d.doc_key]
        old_hashes = ws.hashes(prev_docs[d.doc_key]) if d.doc_key in prev_docs else None
        ch = changed_pages(old_hashes, ws.hashes(d)) if st in (NEW, REVISED) else []
        if st == NEW and prev is None:
            ch = []                         # first issue: no delta tags
        entries.append(Entry(d, st, ch))

    toc = _toc_rows(ws, entries)
    rev = _rev_rows(entries, removed) if prev else []
    n_toc = max(1, math.ceil(len(toc) / ROWS_PER_PAGE))
    n_rev = max(1, math.ceil(len(rev) / ROWS_PER_PAGE))
    front = 1 + n_toc + n_rev
    sheet = front + 1
    for e in entries:
        e.start = sheet
        sheet += e.doc.n_pages
    total = sheet - 1

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"{ws.project.name} — Structural Calculations, Issue {issue.label}")
    c.setAuthor(ws.project.engineer_of_record or "")
    _draw_cover(c, ws, issue, entries, total)
    _draw_toc(c, ws, issue, toc, n_toc)
    _draw_revsummary(c, ws, issue, prev, rev, n_rev)
    c.save()
    buf.seek(0)

    writer = PdfWriter()
    left = f"{ws.project.number}  {ws.project.name}".strip()
    for i, page in enumerate(PdfReader(buf).pages):
        wp = writer.add_page(page)
        _stamp(wp, left, "Front matter", f"Issue {issue.label}  ·  Sheet {i + 1} of {total}")
    writer.add_outline_item("Cover", 0)
    writer.add_outline_item("Contents", 1)
    writer.add_outline_item("Revision summary", 1 + n_toc)

    pkg_nodes = {}
    for e in entries:
        reader = PdfReader(str(ws.store.archived_file(e.doc)))
        changed = set(e.changed) if mark_changes else set()
        for pno, page in enumerate(reader.pages, start=1):
            wp = writer.add_page(page)
            n = e.start + pno - 1
            delta, col = None, DELTA
            if pno in changed:
                delta = f"{issue.label}  {'NEW' if e.status == NEW else 'REVISED'}"
                col = NEW_C if e.status == NEW else DELTA
            _stamp(wp, left, f"{e.doc.package} · {e.doc.title}",
                   f"Issue {issue.label}  ·  Sheet {n} of {total}", delta, col)
        if e.doc.package not in pkg_nodes:
            pkg = ws.project.package(e.doc.package)
            pkg_nodes[e.doc.package] = writer.add_outline_item(
                f"{e.doc.package} — {pkg.title if pkg else ''}", e.start - 1)
        writer.add_outline_item(e.doc.title, e.start - 1, parent=pkg_nodes[e.doc.package])

    writer.add_metadata({"/Title": f"{ws.project.name} — Structural Calculations, Issue {issue.label}",
                         "/Author": ws.project.engineer_of_record or "",
                         "/Subject": f"Project {ws.project.number}", "/Creator": "calcbook"})
    writer.page_mode = "/UseOutlines"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)

    return {
        "path": out_path, "sheets": total, "documents": len(entries), "front_matter": front,
        "revised_sheets": sum(len(e.changed) for e in entries if e.status in (NEW, REVISED)),
        "removed": len(removed),
    }
