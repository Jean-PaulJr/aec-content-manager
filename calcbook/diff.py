"""Compare two issues: which documents changed, which pages, which design values."""
from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import dataclass, field

from .config import parse_basis
from .history import NEW, REMOVED, REVISED, UNCHANGED, Workspace, changed_pages, doc_statuses
from .store import DocRecord, Issue
from .units import fmt

MAX_DIFF_LINES = 400


@dataclass
class DocChange:
    status: str
    doc_key: str
    package: str
    title: str
    old: DocRecord | None
    new: DocRecord | None
    changed_pages: list[int] = field(default_factory=list)
    pages_dropped: int = 0
    param_changes: list[tuple[str, str, str]] = field(default_factory=list)   # label, old, new
    member_changes: list[tuple[str, str, str]] = field(default_factory=list)  # mark, old, new
    max_dcr: tuple[float | None, float | None] = (None, None)
    text_diff: str = ""


@dataclass
class IssueDiff:
    old: Issue
    new: Issue
    changes: list[DocChange]
    basis_changes: list[tuple[str, str, str]]

    def by_status(self, status: str) -> list[DocChange]:
        return [c for c in self.changes if c.status == status]

    @property
    def summary(self) -> dict[str, int]:
        return {s: len(self.by_status(s)) for s in (NEW, REVISED, REMOVED, UNCHANGED)}


def _value_sets(ws: Workspace, doc: DocRecord | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    if doc:
        for h in ws.analysis(doc).params:
            out[h.key].add(fmt(h.value, h.unit))
    return out


def _member_sets(ws: Workspace, doc: DocRecord | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    if doc:
        for e in ws.analysis(doc).elements:
            out[e.mark].add(e.size)
    return out


def _max_dcr(ws: Workspace, doc: DocRecord | None) -> float | None:
    if not doc:
        return None
    vals = [h.value for h in ws.analysis(doc).dcrs]
    return max(vals) if vals else None


def compare(ws: Workspace, old_issue: Issue, new_issue: Issue, with_text: bool = True) -> IssueDiff:
    old_docs = ws.store.documents_by_key(old_issue)
    new_docs = ws.store.documents_by_key(new_issue)
    statuses = doc_statuses(old_docs, new_docs)
    order = {pid: i for i, pid in enumerate(ws.project.package_ids)}

    changes = []
    for key, st in statuses.items():
        o, n = old_docs.get(key), new_docs.get(key)
        ref = n or o
        c = DocChange(st, key, ref.package, ref.title, o, n)  # type: ignore[union-attr]
        if st == REVISED:
            oh, nh = ws.hashes(o), ws.hashes(n)  # type: ignore[arg-type]
            c.changed_pages = changed_pages(oh, nh)
            c.pages_dropped = max(0, len(oh) - len(nh))
            if with_text:
                a = "\n".join(ws.pages(o)).splitlines()  # type: ignore[arg-type]
                b = "\n".join(ws.pages(n)).splitlines()  # type: ignore[arg-type]
                lines = list(difflib.unified_diff(a, b, f"{key} @ {old_issue.label}",
                                                  f"{key} @ {new_issue.label}", n=1, lineterm=""))
                if len(lines) > MAX_DIFF_LINES:
                    lines = lines[:MAX_DIFF_LINES] + [f"... ({len(lines) - MAX_DIFF_LINES} more lines)"]
                c.text_diff = "\n".join(lines)
        elif st == NEW:
            c.changed_pages = list(range(1, n.n_pages + 1))  # type: ignore[union-attr]

        if st != UNCHANGED:
            ov, nv = _value_sets(ws, o), _value_sets(ws, n)
            for k in sorted(set(ov) | set(nv), key=lambda k: ws.rules.label(k)):
                if ov.get(k, set()) != nv.get(k, set()):
                    c.param_changes.append((ws.rules.label(k),
                                            ", ".join(sorted(ov.get(k, set()))) or "—",
                                            ", ".join(sorted(nv.get(k, set()))) or "—"))
            om, nm = _member_sets(ws, o), _member_sets(ws, n)
            for m in sorted(set(om) | set(nm)):
                if om.get(m, set()) != nm.get(m, set()):
                    c.member_changes.append((m, ", ".join(sorted(om.get(m, set()))) or "—",
                                             ", ".join(sorted(nm.get(m, set()))) or "—"))
            c.max_dcr = (_max_dcr(ws, o), _max_dcr(ws, n))
        changes.append(c)

    rank = {REVISED: 0, NEW: 1, REMOVED: 2, UNCHANGED: 3}
    changes.sort(key=lambda c: (rank[c.status], order.get(c.package, 999), c.doc_key))

    ob, nb = parse_basis(old_issue.basis, ws.rules), parse_basis(new_issue.basis, ws.rules)
    basis_changes = []
    for k in sorted(set(ob.params) | set(nb.params), key=lambda k: ws.rules.label(k)):
        a, b = ob.params.get(k), nb.params.get(k)
        if (a and a.display) != (b and b.display):
            basis_changes.append((ws.rules.label(k), a.display if a else "—", b.display if b else "—"))
    for fam in sorted(set(ob.codes) | set(nb.codes)):
        if ob.codes.get(fam) != nb.codes.get(fam):
            basis_changes.append((f"{fam} edition", ob.codes.get(fam, "—"), nb.codes.get(fam, "—")))
    if ob.dcr_limit != nb.dcr_limit:
        basis_changes.append(("D/C limit", f"{ob.dcr_limit:g}", f"{nb.dcr_limit:g}"))
    return IssueDiff(old_issue, new_issue, changes, basis_changes)
