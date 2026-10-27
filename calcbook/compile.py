"""Compile a report package (calculation book) from its sections.

The book follows the project's section template: sections in order, documents in
the order the user set, excluded sections left out. It contains

  * a cover with project info, section register, issue history, a signature
    block (prepared / checked / approved by) and an engineer's stamp box,
  * a table of contents with sheet numbers,
  * a revision summary listing the sheets that changed since the last issue,
  * a title page in front of each section,
  * every calculation sheet, stamped with "Sheet n of N", and tagged REVISED or
    NEW when its content changed since the previous issue,
  * bookmarks for sections and documents.

Because numbering, contents and the revision summary are generated, adding a
page anywhere renumbers everything. A book can be built from a recorded issue,
or as a draft from the files currently uploaded.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .citations import collect_assumptions, collect_codes
from .config import SIGN_ROLES, Package, parse_basis, read_basis_raw
from .extract import analyze, file_sha256, page_hash, read_pdf_pages
from .history import (NEW, REMOVED, REVISED, UNCHANGED, Workspace, changed_pages, compress_ranges,
                      doc_statuses, issues_upto, package_revisions)
from .ingest import doc_key_for, scan_package
from .store import Issue

W, H = letter
M = 54
ROW = 17
ROWS_PER_PAGE = 34
INK = HexColor("#1d2430")
MUTED = HexColor("#6b7280")
RULE = HexColor("#d4d8de")
ACCENT = HexColor("#1f4e79")
TINT = HexColor("#f4f6f9")
DELTA = HexColor("#c0392b")
NEW_C = HexColor("#1e7a46")
DRAFT_C = HexColor("#b45309")
LINK_C = HexColor("#1d4ed8")


# --------------------------------------------------------------------------- #
# Book model
# --------------------------------------------------------------------------- #
@dataclass
class BookDoc:
    key: str
    package: str
    title: str
    path: Path
    n_pages: int
    status: str = UNCHANGED
    changed: list[int] = field(default_factory=list)   # 1-based pages within the document
    number: str = ""                                    # "2.3"
    start: int = 0                                      # first sheet in the book


@dataclass
class BookSection:
    pkg: Package
    number: int
    docs: list[BookDoc]
    title_sheet: int = 0                                # 0 = no title page

    @property
    def first_sheet(self) -> int:
        return self.title_sheet or (self.docs[0].start if self.docs else 0)

    @property
    def last_sheet(self) -> int:
        return (self.docs[-1].start + self.docs[-1].n_pages - 1) if self.docs else self.title_sheet


@dataclass
class BookSpec:
    label: str                       # "B", or "Draft"
    issued_on: str
    note: str
    draft: bool
    prev_label: str | None
    prev_date: str | None
    sections: list[BookSection]
    removed: list[tuple[str, str]]   # (package, title)
    history: list[tuple[str, str, str]]
    pkg_revs: dict[str, str]
    codes: list = field(default_factory=list)          # citations.CodeCitation
    assumptions: list = field(default_factory=list)    # citations.AssumptionItem
    layout: dict = field(default_factory=dict)          # filled by number_spec

    @property
    def docs(self) -> list[BookDoc]:
        return [d for s in self.sections for d in s.docs]


def _sections_from(ws: Workspace, docs: list[BookDoc]) -> list[BookSection]:
    out, n = [], 0
    for pkg in ws.project.book_sections:
        ds = sorted((d for d in docs if d.package == pkg.id), key=lambda d: pkg.rank(d.key.split("/", 1)[-1]))
        if not ds:
            continue
        n += 1
        for i, d in enumerate(ds, 1):
            d.number = f"{n}.{i}"
        out.append(BookSection(pkg, n, ds))
    return out


def spec_from_issue(ws: Workspace, issue: Issue) -> BookSpec:
    prev = ws.store.previous(issue)
    in_book = {p.id for p in ws.project.book_sections}
    cur = {k: d for k, d in ws.store.documents_by_key(issue).items() if d.package in in_book}
    old = {k: d for k, d in (ws.store.documents_by_key(prev) if prev else {}).items() if d.package in in_book}
    st = doc_statuses(old if prev else None, cur)
    docs = []
    for k, d in cur.items():
        ch = []
        if prev and st[k] in (NEW, REVISED):
            ch = changed_pages(ws.hashes(old[k]) if k in old else None, ws.hashes(d))
        docs.append(BookDoc(k, d.package, d.title, ws.store.archived_file(d), d.n_pages, st[k], ch))
    removed = [(d.package, d.title) for k, d in old.items() if st.get(k) == REMOVED]
    spec = BookSpec(issue.label, issue.issued_on, issue.note, False,
                    prev.label if prev else None, prev.issued_on if prev else None,
                    _sections_from(ws, docs), removed,
                    [(i.label, i.issued_on, i.note) for i in issues_upto(ws.store, issue)],
                    package_revisions(ws.store, issue))
    entries = [(d.doc_key, d.title, ws.analysis(d)) for d in ws.ordered_documents(issue)]
    spec.codes = collect_codes(entries, parse_basis(issue.basis, ws.rules))
    spec.assumptions = collect_assumptions([e for e in entries if e[0] in cur], issue.basis)
    return spec


def spec_from_working(ws: Workspace) -> BookSpec:
    """A draft book from the files uploaded now, compared with the latest issue."""
    from .extract import humanize, pdf_title, strip_rev_suffix
    latest = ws.store.latest()
    old = ws.store.documents_by_key(latest) if latest else {}
    docs, seen, entries = [], set(), []
    for pkg in ws.project.book_sections:
        for f in scan_package(pkg.path):
            key = doc_key_for(pkg.id, f.relative_to(pkg.path))
            seen.add(key)
            pages = read_pdf_pages(f)
            o = old.get(key)
            if o is None:
                status, ch = NEW, (list(range(1, len(pages) + 1)) if latest else [])
            elif o.sha256 == file_sha256(f):
                status, ch = UNCHANGED, []
            else:
                status, ch = REVISED, changed_pages(ws.hashes(o), [page_hash(t) for t in pages])
            title = pdf_title(f) or humanize(strip_rev_suffix(f.stem))
            docs.append(BookDoc(key, pkg.id, title, f, len(pages), status, ch))
            entries.append((key, title, analyze(pages, ws.rules, ws.project.package_ids, pkg.id)))
    in_book = {p.id for p in ws.project.book_sections}
    removed = [(d.package, d.title) for k, d in old.items() if k not in seen and d.package in in_book]
    hist = [(i.label, i.issued_on, i.note) for i in ws.store.issues()]
    hist.append(("Draft", date.today().isoformat(), "Work in progress — not issued"))
    spec = BookSpec("Draft", date.today().isoformat(), "Work in progress — not issued", True,
                    latest.label if latest else None, latest.issued_on if latest else None,
                    _sections_from(ws, docs), removed, hist,
                    package_revisions(ws.store, latest) if latest else {})
    raw = read_basis_raw(ws.project)
    spec.codes = collect_codes(entries, parse_basis(raw, ws.rules))
    spec.assumptions = collect_assumptions(entries, raw)
    return spec


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #
LINKS: list[tuple[int, tuple[float, float, float, float], int]] = []   # (page index, rect, target sheet)


def _link(page_index: int, y: float, sheet: int, x0: float = M, x1: float = W - M) -> None:
    if sheet:
        LINKS.append((page_index, (x0, y - 4, x1, y + 11), sheet))


def _fit(text: str, font: str, size: float, width: float) -> str:
    text = text or ""
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def _issue_name(spec: BookSpec) -> str:
    return "Draft" if spec.draft else f"Issue {spec.label}"


def _band(c) -> None:
    c.setFillColor(ACCENT)
    c.rect(0, H - 8, W, 8, stroke=0, fill=1)


def _page_header(c, title: str, ws: Workspace, spec: BookSpec) -> float:
    _band(c)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(M, H - M - 4, title)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(MUTED)
    c.drawRightString(W - M, H - M - 2, f"{ws.project.number}  {ws.project.name}  ·  {_issue_name(spec)}")
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(M, H - M - 14, W - M, H - M - 14)
    return H - M - 36


def _status_label(status: str) -> tuple[str, object]:
    return {NEW: ("NEW", NEW_C), REVISED: ("REVISED", DELTA), REMOVED: ("REMOVED", MUTED),
            UNCHANGED: ("—", MUTED)}[status]


def _draw_cover(c, ws: Workspace, spec: BookSpec, total: int) -> None:
    from .brand import APP_NAME, draw_logo
    p = ws.project
    _band(c)
    draw_logo(c, W - M - 22, H - 66, 22)
    c.setFont("Helvetica", 7)
    c.setFillColor(MUTED)
    c.drawRightString(W - M - 28, H - 58, f"Compiled with {APP_NAME}")
    y = H - 104
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M, y, "S T R U C T U R A L   C A L C U L A T I O N S")
    y -= 32
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(M, y, _fit(p.name, "Helvetica-Bold", 26, W - 2 * M))
    y -= 20
    c.setFont("Helvetica", 10.5)
    c.setFillColor(MUTED)
    bits = [b for b in (f"Project {p.number}" if p.number else "", p.client, p.address) if b]
    c.drawString(M, y, _fit("   ·   ".join(bits), "Helvetica", 10.5, W - 2 * M))

    # Issue box
    y -= 40
    c.setStrokeColor(DRAFT_C if spec.draft else RULE)
    c.setFillColor(TINT)
    c.roundRect(M, y - 58, W - 2 * M, 70, 6, stroke=1, fill=1)
    c.setFillColor(DRAFT_C if spec.draft else ACCENT)
    c.setFont("Helvetica-Bold", 28)
    c.drawString(M + 18, y - 34, "DRAFT" if spec.draft else f"Issue {spec.label}")
    c.setFillColor(INK)
    c.setFont("Helvetica", 10)
    c.drawString(M + 200, y - 14, f"Date: {spec.issued_on}")
    c.drawString(M + 200, y - 30, f"Sheets: {total}    Sections: {len(spec.sections)}    "
                                  f"Calculations: {len(spec.docs)}")
    c.setFillColor(MUTED)
    c.drawString(M + 200, y - 46, _fit(spec.note or "", "Helvetica", 10, W - 2 * M - 220))

    # Section register
    y -= 90
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M, y, "Sections")
    y -= 22
    cols = [M, M + 26, M + 250, M + 380, M + 420]
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(MUTED)
    for x, t in zip(cols, ["No.", "Section", "Responsible", "Calcs", "Sheets"]):
        c.drawString(x, y, t)
    c.drawRightString(W - M, y, "Rev")
    c.setStrokeColor(RULE)
    c.line(M, y - 5, W - M, y - 5)
    c.setFont("Helvetica", 9)
    for s in spec.sections:
        y -= ROW - 2
        c.setFillColor(INK)
        c.drawString(cols[0], y, str(s.number))
        c.drawString(cols[1], y, _fit(s.pkg.title, "Helvetica", 9, 215))
        c.drawString(cols[2], y, _fit(s.pkg.responsible or s.pkg.discipline, "Helvetica", 9, 120))
        c.drawString(cols[3], y, str(len(s.docs)))
        c.drawString(cols[4], y, f"{s.first_sheet}–{s.last_sheet}")
        c.drawRightString(W - M, y, spec.pkg_revs.get(s.pkg.id, "—"))
        _link(0, y, s.first_sheet)

    # Issue history (as much as fits above the signature block)
    y -= 30
    sig_top = M + 150
    if y - 40 > sig_top:
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(M, y, "Issue history")
        y -= 20
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(MUTED)
        c.drawString(M, y, "Issue")
        c.drawString(M + 52, y, "Date")
        c.drawString(M + 130, y, "Description")
        c.line(M, y - 5, W - M, y - 5)
        c.setFont("Helvetica", 9)
        for lab, d, note in reversed(spec.history):
            if y - ROW < sig_top + 10:
                break
            y -= ROW - 2
            cur = lab == spec.label
            c.setFillColor(ACCENT if cur else INK)
            c.drawString(M, y, lab)
            c.drawString(M + 52, y, d or "")
            c.drawString(M + 130, y, _fit(note or "", "Helvetica", 9, W - 2 * M - 130))

    # Signature block + stamp
    bw = (W - 2 * M - 160) / 3
    top = M + 112
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 8)
    if p.engineer_of_record:
        c.drawString(M, top + 22, f"ENGINEER OF RECORD   {p.engineer_of_record}")
    for i, (role, label) in enumerate(SIGN_ROLES):
        x = M + i * bw
        info = p.signatures.get(role, {})
        c.setStrokeColor(RULE)
        c.rect(x, M + 20, bw - 8, top - M - 20, stroke=1, fill=0)
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 8, top - 14, label.upper())
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 8, top - 32, _fit(info.get("name", ""), "Helvetica-Bold", 10, bw - 24))
        c.setStrokeColor(MUTED)
        c.line(x + 8, M + 46, x + bw - 16, M + 46)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7)
        c.drawString(x + 8, M + 36, "Signature")
        c.drawRightString(x + bw - 16, M + 36, f"Date  {info.get('date', '') or '________'}")
    c.setStrokeColor(RULE)
    c.rect(W - M - 150, M + 20, 150, top - M + 22, stroke=1, fill=0)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUTED)
    c.drawCentredString(W - M - 75, M + 26, "Engineer's stamp")
    c.showPage()


def _toc_rows(spec: BookSpec) -> list[tuple]:
    rows: list[tuple] = []
    for s in spec.sections:
        rows.append(("sec", s))
        rows.extend(("doc", d) for d in s.docs)
    return rows


def _draw_toc(c, ws, spec, rows, n_pages: int, first: int = 1) -> None:
    for pg in range(n_pages):
        y = _page_header(c, "Contents" + (" (cont.)" if pg else ""), ws, spec)
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(MUTED)
        c.drawString(M, y, "Calculation")
        c.drawString(M + 300, y, "Pages")
        c.drawString(M + 345, y, "Status")
        c.drawRightString(W - M, y, "Sheet")
        y -= 8
        for kind, obj in rows[pg * ROWS_PER_PAGE:(pg + 1) * ROWS_PER_PAGE]:
            y -= ROW
            if kind == "sec":
                c.setFillColor(ACCENT)
                c.setFont("Helvetica-Bold", 10)
                c.drawString(M, y, _fit(f"{obj.number}   {obj.pkg.title}", "Helvetica-Bold", 10, W - 2 * M - 40))
                c.drawRightString(W - M, y, str(obj.first_sheet))
                c.setStrokeColor(RULE)
                c.line(M, y - 4, W - M, y - 4)
                _link(first + pg, y, obj.first_sheet)
                continue
            d: BookDoc = obj
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 8.5)
            c.drawString(M + 4, y, d.number)
            c.setFillColor(INK)
            c.setFont("Helvetica", 9)
            c.drawString(M + 30, y, _fit(d.title, "Helvetica", 9, 262))
            c.setFillColor(MUTED)
            c.drawString(M + 300, y, str(d.n_pages))
            lab, col = _status_label(d.status)
            if spec.prev_label is None:
                lab, col = "—", MUTED
            c.setFillColor(col)
            c.setFont("Helvetica-Bold" if lab != "—" else "Helvetica", 8)
            c.drawString(M + 345, y, lab)
            c.setFillColor(INK)
            c.setFont("Helvetica", 9)
            c.drawRightString(W - M, y, str(d.start))
            _link(first + pg, y, d.start)
            c.setStrokeColor(RULE)
            c.setDash(1, 2)
            c.line(M + 410, y + 2, W - M - 24, y + 2)
            c.setDash()
        c.showPage()


def _rev_rows(spec: BookSpec) -> list[tuple]:
    rows: list[tuple] = [("doc", d) for d in spec.docs if d.status in (NEW, REVISED)]
    rows += [("removed", r) for r in spec.removed]
    return rows


def _draw_revsummary(c, ws, spec, rows, n_pages: int, first: int = 2) -> None:
    for pg in range(n_pages):
        y = _page_header(c, "Revision summary" + (" (cont.)" if pg else ""), ws, spec)
        c.setFont("Helvetica", 9.5)
        c.setFillColor(INK)
        if pg == 0:
            target = "this draft" if spec.draft else f"Issue {spec.label} ({spec.issued_on})"
            c.drawString(M, y, f"Changes from Issue {spec.prev_label} ({spec.prev_date}) to {target}.")
            y -= 14
            c.setFillColor(MUTED)
            c.drawString(M, y, "Changed sheets carry a REVISED (red) or NEW (green) tag in the top-right corner. "
                               "Sheet numbers refer to this book.")
            y -= 10
        y -= 14
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(MUTED)
        c.drawString(M, y, "Calculation")
        c.drawString(M + 280, y, "Status")
        c.drawString(M + 350, y, "Changed sheets")
        c.setStrokeColor(RULE)
        c.line(M, y - 5, W - M, y - 5)
        chunk = rows[pg * ROWS_PER_PAGE:(pg + 1) * ROWS_PER_PAGE]
        if not chunk:
            y -= ROW
            c.setFont("Helvetica", 9)
            c.setFillColor(INK)
            c.drawString(M, y, "No calculations changed.")
        for kind, obj in chunk:
            y -= ROW
            c.setFont("Helvetica", 9)
            c.setFillColor(INK)
            if kind == "removed":
                c.drawString(M, y, _fit(f"{obj[0]} · {obj[1]}", "Helvetica", 9, 270))
                c.setFillColor(MUTED)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(M + 280, y, "REMOVED")
                c.setFont("Helvetica", 9)
                c.drawString(M + 350, y, "withdrawn")
                continue
            d: BookDoc = obj
            c.drawString(M, y, _fit(f"{d.number}  {d.title}", "Helvetica", 9, 270))
            lab, col = _status_label(d.status)
            c.setFillColor(col)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(M + 280, y, lab)
            c.setFillColor(INK)
            c.setFont("Helvetica", 9)
            sheets = compress_ranges([d.start + p - 1 for p in d.changed]) or "title block only"
            _link(first + pg, y, d.start + (d.changed[0] - 1 if d.changed else 0))
            c.drawString(M + 350, y, _fit(sheets, "Helvetica", 9, W - M - (M + 350)))
        c.showPage()


def _codes_rows(spec: BookSpec) -> list[tuple]:
    rows: list[tuple] = [("head", "Codes and standards")]
    rows += [("code", ct) for ct in spec.codes]
    rows.append(("gap", ""))
    rows.append(("head", "Assumptions"))
    rows += [("assume", a) for a in spec.assumptions] or [("note", "No assumptions stated.")]
    return rows


def _draw_codes(c, ws, spec: BookSpec, rows, n_pages: int, first: int) -> None:
    by_key = {d.key: d for d in spec.docs}
    status_col = {"adopted": NEW_C, "adopted, not cited": MUTED, "different edition": DELTA, "not in basis": DRAFT_C}
    for pg in range(n_pages):
        y = _page_header(c, "Codes & assumptions" + (" (cont.)" if pg else ""), ws, spec)
        if pg == 0:
            c.setFont("Helvetica", 9)
            c.setFillColor(MUTED)
            c.drawString(M, y, "Every code and standard cited in this package, and every assumption stated "
                               "in the design basis or in a calculation.")
            y -= 8
        for kind, obj in rows[pg * ROWS_PER_PAGE:(pg + 1) * ROWS_PER_PAGE]:
            y -= ROW
            if kind == "head":
                c.setFillColor(ACCENT)
                c.setFont("Helvetica-Bold", 10.5)
                c.drawString(M, y, obj)
                c.setStrokeColor(RULE)
                c.line(M, y - 4, W - M, y - 4)
            elif kind == "code":
                c.setFillColor(INK)
                c.setFont("Helvetica-Bold", 9)
                c.drawString(M + 4, y, obj.label)
                c.setFont("Helvetica-Bold", 7.5)
                c.setFillColor(status_col.get(obj.status, MUTED))
                c.drawString(M + 110, y, obj.status.upper())
                c.setFont("Helvetica", 8.5)
                c.setFillColor(MUTED)
                where = ", ".join(t for _, t, _ in obj.locations) or "—"
                c.drawString(M + 220, y, _fit(f"Cited in: {where}", "Helvetica", 8.5, W - 2 * M - 220))
                if obj.locations and obj.locations[0][0] in by_key:
                    d = by_key[obj.locations[0][0]]
                    _link(first + pg, y, d.start + obj.locations[0][2][0] - 1)
            elif kind == "assume":
                d = by_key.get(obj.doc_key)
                c.setFillColor(DRAFT_C)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(M + 4, y, "Assumption")
                c.setFillColor(INK)
                c.setFont("Helvetica", 9)
                c.drawString(M + 70, y, _fit(obj.text, "Helvetica", 9, W - 2 * M - 190))
                c.setFillColor(MUTED)
                c.setFont("Helvetica", 8)
                src = f"{d.number} · sh. {d.start + obj.page - 1}" if d else obj.source
                c.drawRightString(W - M, y, _fit(src, "Helvetica", 8, 110))
                if d:
                    _link(first + pg, y, d.start + obj.page - 1)
            elif kind == "note":
                c.setFillColor(MUTED)
                c.setFont("Helvetica", 9)
                c.drawString(M + 4, y, obj)
        c.showPage()


def _draw_section_title(c, ws, spec: BookSpec, s: BookSection) -> None:
    _band(c)
    c.setFillColor(TINT)
    c.rect(0, H * 0.42, W, H * 0.36, stroke=0, fill=1)
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 72)
    c.drawString(M, H * 0.42 + 150, f"{s.number}")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(M, H * 0.42 + 105, _fit(s.pkg.title, "Helvetica-Bold", 26, W - 2 * M))
    c.setFont("Helvetica", 10.5)
    c.setFillColor(MUTED)
    who = f"Responsible: {s.pkg.responsible}   ·   " if s.pkg.responsible else ""
    c.drawString(M, H * 0.42 + 80, f"{who}Sheets {s.first_sheet}–{s.last_sheet}   ·   "
                                  f"{len(s.docs)} calculation{'s' if len(s.docs) != 1 else ''}")
    y = H * 0.42 - 34
    c.setFont("Helvetica-Bold", 8)
    c.drawString(M, y, "Calculation")
    c.drawRightString(W - M, y, "Sheet")
    c.setStrokeColor(RULE)
    c.line(M, y - 5, W - M, y - 5)
    for d in s.docs[:22]:
        y -= ROW
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(M, y, d.number)
        c.setFillColor(INK)
        c.drawString(M + 30, y, _fit(d.title, "Helvetica", 9, W - 2 * M - 80))
        c.drawRightString(W - M, y, str(d.start))
        _link(s.title_sheet - 1, y, d.start)
    if len(s.docs) > 22:
        y -= ROW
        c.setFillColor(MUTED)
        c.drawString(M + 30, y, f"… and {len(s.docs) - 22} more (see Contents)")
    c.showPage()


# --------------------------------------------------------------------------- #
# Sheet stamping
# --------------------------------------------------------------------------- #
def _overlay(w: float, h: float, left: str, center: str, right: str, delta: str | None,
             delta_color=DELTA, marks=()):
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
        p = c.beginPath()
        p.moveTo(x + 5, y + 3.5)
        p.lineTo(x + 13, y + 3.5)
        p.lineTo(x + 9, y + 11.5)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 17, y + 4.5, delta)
    for mx, my, text in marks:            # current sheet numbers next to references
        c.setFont("Helvetica-Bold", 6)
        c.setFillColor(LINK_C)
        c.drawString(mx, my, text)
    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def _stamp(page, left, center, right, delta=None, delta_color=DELTA, marks=()) -> None:
    if page.get("/Rotate", 0):
        page.transfer_rotation_to_content()
    box = page.mediabox
    ov = _overlay(float(box.width), float(box.height), left, center, right, delta, delta_color, marks)
    page.merge_transformed_page(ov, Transformation().translate(float(box.left), float(box.bottom)))


# --------------------------------------------------------------------------- #
def number_spec(spec: BookSpec) -> dict:
    """Assign sheet numbers: cover, contents, codes & assumptions, revision
    summary, then each section's title page and documents."""
    toc, codes = _toc_rows(spec), _codes_rows(spec)
    rev = _rev_rows(spec) if spec.prev_label else []
    n_toc = max(1, math.ceil(len(toc) / ROWS_PER_PAGE))
    n_codes = max(1, math.ceil(len(codes) / ROWS_PER_PAGE))
    n_rev = math.ceil(max(1, len(rev)) / ROWS_PER_PAGE) if spec.prev_label else 0
    front = 1 + n_toc + n_codes + n_rev
    sheet = front + 1
    for s in spec.sections:
        s.title_sheet = 0
        if s.pkg.title_page:
            s.title_sheet = sheet
            sheet += 1
        for d in s.docs:
            d.start = sheet
            sheet += d.n_pages
    spec.layout = {"toc": toc, "codes": codes, "rev": rev, "n_toc": n_toc, "n_codes": n_codes,
                   "n_rev": n_rev, "front": front, "total": sheet - 1}
    return spec.layout


_DOC_CODE = re.compile(r"^[A-Z]{2,5}-\d{1,3}[A-Z]?")


def _reference_targets(spec: BookSpec) -> dict[str, BookDoc]:
    out = {}
    for d in spec.docs:
        m = _DOC_CODE.match(d.key.split("/", 1)[-1])
        if m:
            out[m.group(0)] = d
    return out


def _find_refs(d: BookDoc, targets: dict[str, BookDoc]) -> dict[int, list[tuple]]:
    """Words in a document that name another calc in the book, per page:
    (x0, top, x1, bottom, page height, target doc)."""
    import pdfplumber
    own = _DOC_CODE.match(d.key.split("/", 1)[-1])
    own = own.group(0) if own else None
    found: dict[int, list[tuple]] = {}
    try:
        with pdfplumber.open(str(d.path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                if page.rotation:
                    continue
                for w in page.extract_words():
                    t = w["text"].strip(".,;:()[]")
                    if t in targets and t != own:
                        found.setdefault(i, []).append((w["x0"], w["top"], w["x1"], w["bottom"],
                                                        float(page.height), targets[t]))
    except Exception:
        return {}
    return found


def build_book(ws: Workspace, spec: BookSpec, out_path: Path, mark_changes: bool = True,
               link_references: bool = True) -> dict:
    lay = number_spec(spec)
    front, total = lay["front"], lay["total"]
    LINKS.clear()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _draw_cover(c, ws, spec, total)
    _draw_toc(c, ws, spec, lay["toc"], lay["n_toc"], first=1)
    _draw_codes(c, ws, spec, lay["codes"], lay["n_codes"], first=1 + lay["n_toc"])
    if lay["n_rev"]:
        _draw_revsummary(c, ws, spec, lay["rev"], lay["n_rev"], first=1 + lay["n_toc"] + lay["n_codes"])
    for s in spec.sections:
        if s.title_sheet:
            _draw_section_title(c, ws, spec, s)
    c.save()
    buf.seek(0)
    generated = PdfReader(buf).pages
    title_pages = {s.pkg.id: generated[front + i]
                   for i, s in enumerate([s for s in spec.sections if s.title_sheet])}

    tag = "DRAFT" if spec.draft else f"Issue {spec.label}"
    left = f"{ws.project.number}  {ws.project.name}".strip()
    writer = PdfWriter()
    for i in range(front):
        wp = writer.add_page(generated[i])
        _stamp(wp, left, "Front matter", f"{tag}  ·  Sheet {i + 1} of {total}")
    writer.add_outline_item("Cover", 0)
    writer.add_outline_item("Contents", 1)
    writer.add_outline_item("Codes & assumptions", 1 + lay["n_toc"])
    if lay["n_rev"]:
        writer.add_outline_item("Revision summary", 1 + lay["n_toc"] + lay["n_codes"])

    targets = _reference_targets(spec) if link_references else {}
    ref_links: list[tuple[int, tuple, int]] = []
    for s in spec.sections:
        if s.title_sheet:
            wp = writer.add_page(title_pages[s.pkg.id])
            _stamp(wp, left, f"Section {s.number} · {s.pkg.title}", f"{tag}  ·  Sheet {s.title_sheet} of {total}")
        for d in s.docs:
            reader = PdfReader(str(d.path))
            changed = set(d.changed) if mark_changes else set()
            refs = _find_refs(d, targets) if targets else {}
            for pno, page in enumerate(reader.pages, start=1):
                wp = writer.add_page(page)
                delta, col = None, DELTA
                if pno in changed:
                    word = "NEW" if d.status == NEW else "REVISED"
                    delta = f"{'DRAFT' if spec.draft else spec.label}  {word}"
                    col = NEW_C if d.status == NEW else DELTA
                marks = []
                for x0, top, x1, bottom, ph, tgt in refs.get(pno, []):
                    y = ph - bottom
                    # superscript just above the end of the word, so it never covers the next word
                    marks.append((x1 - 2, ph - top + 1.5, f"sh. {tgt.start}"))
                    ref_links.append((d.start + pno - 2, (x0 - 1, y - 1, x1 + 24, ph - top + 8), tgt.start))
                _stamp(wp, left, f"{d.number}  {d.title}",
                       f"{tag}  ·  Sheet {d.start + pno - 1} of {total}", delta, col, marks)
        node = writer.add_outline_item(f"{s.number}  {s.pkg.title}", s.first_sheet - 1)
        for d in s.docs:
            writer.add_outline_item(f"{d.number}  {d.title}", d.start - 1, parent=node)

    from pypdf.annotations import Link
    for page_index, rect, sheet in LINKS + ref_links:
        if 0 <= page_index < len(writer.pages) and 1 <= sheet <= len(writer.pages):
            writer.add_annotation(page_index, Link(rect=rect, target_page_index=sheet - 1))

    title = f"{ws.project.name} — Structural Calculations, {tag}"
    writer.add_metadata({"/Title": title, "/Author": ws.project.engineer_of_record or "",
                         "/Subject": f"Project {ws.project.number}", "/Creator": "BigDelta"})
    writer.page_mode = "/UseOutlines"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return {
        "path": out_path, "sheets": total, "documents": len(spec.docs), "sections": len(spec.sections),
        "front_matter": front, "revised_sheets": sum(len(d.changed) for d in spec.docs),
        "removed": len(spec.removed), "links": len(LINKS) + len(ref_links),
        "references": len(ref_links),
    }


def compile_issue(ws: Workspace, issue: Issue, out_path: Path, mark_changes: bool = True) -> dict:
    return build_book(ws, spec_from_issue(ws, issue), out_path, mark_changes)


def compile_draft(ws: Workspace, out_path: Path) -> dict:
    return build_book(ws, spec_from_working(ws), out_path)
