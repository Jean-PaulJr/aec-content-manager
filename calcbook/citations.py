"""Code citations and design assumptions across every document of an issue.

Brief items 8 and 9: cite all code use, and list assumptions so they are
visibly clear. Both feed the "Codes & assumptions" page and the matching
front-matter page of the calc book.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import parse_basis
from .history import Workspace
from .rules import CODE_EDITIONS, code_label
from .store import Issue


@dataclass
class CodeCitation:
    family: str
    edition: str
    adopted: str | None                  # edition in the design basis, if the family is adopted
    locations: list[tuple[str, str, list[int]]] = field(default_factory=list)  # (doc_key, title, pages)

    @property
    def label(self) -> str:
        return code_label(self.family, self.edition)

    @property
    def status(self) -> str:
        if self.adopted is None:
            return "not in basis"
        if self.adopted != self.edition:
            return "different edition"
        return "adopted" if self.locations else "adopted, not cited"


def collect_codes(entries, basis) -> list[CodeCitation]:
    """entries: iterable of (doc_key, title, DocAnalysis)."""
    found: dict[tuple[str, str], CodeCitation] = {}
    for key, title, a in entries:
        per: dict[tuple[str, str], list[int]] = {}
        for c in a.codes:
            per.setdefault((c.family, c.edition), []).append(c.page)
        for (fam, ed), pages in per.items():
            cit = found.setdefault((fam, ed), CodeCitation(fam, ed, basis.codes.get(fam)))
            cit.locations.append((key, title, sorted(set(pages))))
    for fam, ed in basis.codes.items():
        found.setdefault((fam, ed), CodeCitation(fam, ed, ed))
    order = list(CODE_EDITIONS)
    return sorted(found.values(), key=lambda c: (order.index(c.family) if c.family in order else 99,
                                                 c.family, c.edition))


def codes_cited(ws: Workspace, issue: Issue, basis_raw: dict | None = None) -> list[CodeCitation]:
    """Every (code, edition) cited in the issue, with where, plus adopted codes
    nobody cites. `basis_raw` evaluates a different basis (e.g. the working one
    after an edition was switched) against the same documents."""
    basis = parse_basis(basis_raw if basis_raw is not None else issue.basis, ws.rules)
    return collect_codes(((d.doc_key, d.title, ws.analysis(d)) for d in ws.ordered_documents(issue)), basis)


@dataclass
class AssumptionItem:
    text: str
    source: str            # "Design basis" or the document title
    doc_key: str = ""
    page: int = 0


def collect_assumptions(entries, basis_raw: dict) -> list[AssumptionItem]:
    out = [AssumptionItem(str(t), "Design basis") for t in (basis_raw.get("assumptions") or [])]
    for key, title, a in entries:
        for x in a.assumptions:
            out.append(AssumptionItem(x.text, title, key, x.page))
    return out


def assumptions(ws: Workspace, issue: Issue | None, basis_raw: dict | None = None) -> list[AssumptionItem]:
    raw = basis_raw if basis_raw is not None else (issue.basis if issue else {})
    entries = ((d.doc_key, d.title, ws.analysis(d)) for d in ws.ordered_documents(issue)) if issue else []
    return collect_assumptions(entries, raw)
