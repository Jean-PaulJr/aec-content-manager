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
class DocAnalysis:
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


def normalize_shape(size: str) -> str:
    return re.sub(r"\s+", "", size).upper().replace("×", "X")


def analyze(pages: list[str], rules: RuleSet, package_ids: list[str], own_package: str) -> DocAnalysis:
    a = DocAnalysis()
    pkg_set = set(package_ids)
    xref_re = None
    others = [p for p in package_ids if p != own_package]
    if others:
        alt = "|".join(re.escape(p) for p in sorted(others, key=len, reverse=True))
        xref_re = re.compile(
            rf"\b(?P<pkg>{alt})\b[^\n]{{0,40}}?\bRev(?:ision)?\.?\s*(?P<rev>[A-Z0-9]{{1,3}})\b"
        )

    for pno, text in enumerate(pages, start=1):
        if not text:
            continue

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
                a.xrefs.append(XRef(pno, m.group("pkg"), m.group("rev").upper(), m.group(0)))
    return a
