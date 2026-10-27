"""Read PDF text and pull structured facts out of it."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .rules import RuleSet
from .units import normalize, to_float

# --------------------------------------------------------------------------- #
# PDF text
# --------------------------------------------------------------------------- #

def read_pdf_pages(path: Path) -> list[str]:
    """Return the text of every page (empty string for pages with no text layer)."""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return [(p.extract_text() or "") for p in pdf.pages]
    except Exception:
        from pypdf import PdfReader
        return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


def ocr_available() -> bool:
    import shutil
    return shutil.which("tesseract") is not None


def ocr_missing_text(path: Path, pages: list[str], dpi: int = 220) -> list[str]:
    """Fill in pages that have no text layer (scanned hand calcs) with OCR text,
    when Tesseract is installed. Pages that already have text are untouched."""
    missing = [i for i, t in enumerate(pages) if not t.strip()]
    if not missing or not ocr_available():
        return pages
    import subprocess
    import tempfile

    import pypdfium2 as pdfium
    out = list(pages)
    pdf = pdfium.PdfDocument(str(path))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for i in missing:
                img = Path(tmp) / f"p{i}.png"
                pdf[i].render(scale=dpi / 72).to_pil().convert("L").save(img)
                try:
                    r = subprocess.run(["tesseract", str(img), "stdout", "--psm", "6"],
                                       capture_output=True, text=True, timeout=120)
                    out[i] = r.stdout if r.returncode == 0 else ""
                except (subprocess.SubprocessError, OSError):
                    out[i] = ""
    finally:
        pdf.close()
    return out


def pdf_title(path: Path) -> str | None:
    try:
        from pypdf import PdfReader
        meta = PdfReader(str(path)).metadata
        title = (meta.title if meta else None) or ""
        title = title.strip()
        return title or None
    except Exception:
        return None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# Title-block noise that changes on every sheet of a re-issued calc without the
# calculation itself changing: "Sheet 2 of 5" and a bare "Rev B" at the end of a
# line. A "Rev" preceded by a package ID ("per GRV Rev A") is kept, because a
# changed cross-reference is a real change.
_TITLEBLOCK_NOISE = [
    re.compile(r"\bsheet\s+\d+\s+of\s+\d+\b", re.IGNORECASE),
    re.compile(r"(?<![A-Z]{3}\s)\b(?:REV|[Rr]ev)(?:ision)?\.?\s*[A-Z0-9]{1,3}[ \t]*$", re.MULTILINE),
]


def page_hash(text: str) -> str:
    for p in _TITLEBLOCK_NOISE:
        text = p.sub("", text)
    return hashlib.sha1(norm_text(text).encode()).hexdigest()


# File names often carry the revision ("..._RevB.pdf"). Strip it so the same
# calculation keeps one identity across issues.
_REV_SUFFIX = re.compile(
    r"(?:[\s_\-.]*\(?\s*rev(?:ision)?|[\s_\-.]+\(?r)[\s_\-.]*[A-Z0-9]{1,3}\)?$", re.IGNORECASE
)


def strip_rev_suffix(stem: str) -> str:
    out = _REV_SUFFIX.sub("", stem)
    return out or stem


def humanize(stem: str) -> str:
    return re.sub(r"[_]+", " ", stem).strip()


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #
@dataclass
class ParamHit:
    key: str
    page: int                  # 1-based
    value: float | str         # canonical units for numbers, upper-case for text
    unit: str
    raw: str
    context: str

    @property
    def value_key(self):
        return round(self.value, 6) if isinstance(self.value, float) else self.value


@dataclass
class DcrHit:
    page: int
    value: float
    mark: str
    context: str


@dataclass
class ElementHit:
    page: int
    mark: str
    size: str
    context: str


@dataclass
class CodeRef:
    page: int
    family: str
    edition: str
    raw: str


@dataclass
class XRef:
    page: int
    package: str
    rev: str
    raw: str


@dataclass
class Assumption:
    page: int
    text: str


@dataclass
class DocAnalysis:
    assumptions: list[Assumption] = field(default_factory=list)
    params: list[ParamHit] = field(default_factory=list)
    dcrs: list[DcrHit] = field(default_factory=list)
    elements: list[ElementHit] = field(default_factory=list)
    codes: list[CodeRef] = field(default_factory=list)
    xrefs: list[XRef] = field(default_factory=list)

    def params_by_key(self) -> dict[str, list[ParamHit]]:
        out: dict[str, list[ParamHit]] = {}
        for h in self.params:
            out.setdefault(h.key, []).append(h)
        return out


def _line_at(text: str, idx: int) -> tuple[str, int]:
    """Return (line containing idx, offset of line start)."""
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    end = len(text) if end == -1 else end
    return text[start:end].strip(), start


def _nearest_mark(line_before: str, rules: RuleSet, exclude: set[str], first: bool = False) -> str:
    marks = [m.group(0) for m in rules.mark.finditer(line_before)
             if m.group(0).split("-")[0] not in exclude]
    if not marks:
        return ""
    return marks[0] if first else marks[-1]


_ASSUME_HEAD = re.compile(r"^\s*(?:DESIGN\s+|GENERAL\s+)?ASSUMPTIONS?\s*:?\s*$", re.IGNORECASE)
_ASSUME_ITEM = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*]|[a-z][.)])\s+(?P<t>.+)$")
_ASSUME_LINE = re.compile(r"^\s*(?:\d{1,2}[.)]\s*)?(?:assume[sd]?|assumption)\b\s*:?\s*(?P<t>.+)$", re.IGNORECASE)


def find_assumptions(text: str) -> list[str]:
    """Assumptions written in a calc: items listed under an ASSUMPTIONS heading,
    and any line that starts with 'Assume' / 'Assumed' / 'Assumption'."""
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if _ASSUME_HEAD.match(ln):
            i += 1
            while i < len(lines) and len(out) < 200:
                m = _ASSUME_ITEM.match(lines[i])
                if not m:
                    break
                out.append(m.group("t").strip())
                i += 1
            continue
        m = _ASSUME_LINE.match(ln)
        if m:
            t = ln.strip()
            t = re.sub(r"^\d{1,2}[.)]\s*", "", t)
            out.append(t[0].upper() + t[1:] if t else t)
        i += 1
    seen, uniq = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq


def normalize_shape(size: str) -> str:
    return re.sub(r"\s+", "", size).upper().replace("×", "X")


def analyze(pages: list[str], rules: RuleSet, package_ids: list[str], own_package: str,
            aliases: dict[str, list[str]] | None = None) -> DocAnalysis:
    a = DocAnalysis()
    pkg_set = set(package_ids)
    xref_re, alias_of = None, {}
    others = [p for p in package_ids if p != own_package]
    for pid, names in (aliases or {}).items():
        if pid != own_package:
            for n in names:
                alias_of[n.lower()] = pid
    if others:
        ids = "|".join(re.escape(p) for p in sorted(others, key=len, reverse=True))
        al = "|".join(re.escape(n) for n in sorted(alias_of, key=len, reverse=True))
        name = ids + (f"|(?i:{al})" if al else "")
        xref_re = re.compile(
            rf"\b(?P<pkg>{name})\b[^\n]{{0,40}}?\b(?:(?:Rev(?:ision)?|REV|Version|VERSION|Ver)\.?\s*"
            rf"(?P<rev>[A-Z0-9]{{1,3}})|[VR](?P<rev2>\d{{1,3}}))\b"
        )

    for pno, text in enumerate(pages, start=1):
        if not text:
            continue

        for t in find_assumptions(text):
            a.assumptions.append(Assumption(pno, t))

        # Design parameters -------------------------------------------------
        for rule in rules.params.values():
            seen: set[int] = set()
            for pat in rule.patterns:
                for m in pat.finditer(text):
                    if m.start() in seen:
                        continue
                    seen.add(m.start())
                    raw_val = m.group("value")
                    unit = (m.groupdict().get("unit") or "")
                    context, _ = _line_at(text, m.start())
                    if rule.kind == "text":
                        a.params.append(ParamHit(rule.key, pno, raw_val.strip().upper(), "",
                                                 m.group(0), context))
                        continue
                    try:
                        v, canon = normalize(to_float(raw_val), unit or rule.default_unit, rule.dimension)
                    except ValueError:
                        continue
                    a.params.append(ParamHit(rule.key, pno, float(v), canon, m.group(0), context))

        # Demand / capacity ratios ------------------------------------------
        for pat in rules.dcr:
            for m in pat.finditer(text):
                line, lstart = _line_at(text, m.start())
                before = text[lstart:m.start()]
                mark = _nearest_mark(before, rules, pkg_set) or _nearest_mark(line, rules, pkg_set)
                if not mark:
                    # result often sits on the line below the member it belongs to
                    prev_lines = [ln for ln in text[:lstart].splitlines() if ln.strip()][-2:]
                    for ln in reversed(prev_lines):
                        mark = _nearest_mark(ln, rules, pkg_set, first=True)
                        if mark:
                            break
                a.dcrs.append(DcrHit(pno, to_float(m.group("value")), mark, line))

        # Member marks + sizes ----------------------------------------------
        for m in rules.shape.finditer(text):
            line, lstart = _line_at(text, m.start())
            before = text[lstart:m.start()][-60:]
            mark = _nearest_mark(before, rules, pkg_set)
            if mark:
                a.elements.append(ElementHit(pno, mark, normalize_shape(m.group(0)), line))

        # Code references ---------------------------------------------------
        for fam, pats in rules.codes.items():
            seen = set()
            for pat in pats:
                for m in pat.finditer(text):
                    if m.start() in seen:
                        continue
                    seen.add(m.start())
                    a.codes.append(CodeRef(pno, fam, m.group("ed"), m.group(0)))

        # References to other packages' revisions ---------------------------
        if xref_re:
            for m in xref_re.finditer(text):
                pkg = m.group("pkg")
                pkg = pkg if pkg in pkg_set else alias_of.get(pkg.lower(), pkg)
                rev = (m.group("rev") or m.group("rev2") or "").upper()
                a.xrefs.append(XRef(pno, pkg, rev, m.group(0)))
    return a
