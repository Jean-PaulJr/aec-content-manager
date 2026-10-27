"""Design-intent cross-checks across every document of an issue.

Each check returns Findings. The design basis used is the one captured when the
issue was ingested, so re-running a check on an old issue gives the same answer.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Basis, parse_basis
from .extract import ParamHit
from .history import UNCHANGED, Workspace, compress_ranges, doc_statuses, package_revisions
from .store import DocRecord, Issue
from .units import fmt

SEVERITIES = ("error", "warning", "info")
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}

CHECK_TITLES = {
    "basis-mismatch": "Value differs from design basis",
    "inconsistent-value": "Documents disagree",
    "code-edition": "Wrong code edition",
    "code-not-in-basis": "Code not in design basis",
    "dcr-exceeded": "Demand exceeds capacity",
    "dcr-near-limit": "Demand near capacity",
    "member-size-mismatch": "Member size differs between documents",
    "stale-reference": "References superseded revision",
    "unknown-reference": "References unknown revision",
    "basis-changed": "Basis changed, document not revised",
    "not-addressed": "Basis item not found in any document",
    "no-text": "No text layer",
    "basis-error": "Design basis problem",
}


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    package: str = ""
    doc_key: str = ""
    doc_title: str = ""
    pages: list[int] = field(default_factory=list)
    expected: str = ""
    found: str = ""
    evidence: str = ""
    locations: list[str] = field(default_factory=list)
    id: str = ""

    @property
    def title(self) -> str:
        return CHECK_TITLES.get(self.check, self.check)

    @property
    def where(self) -> str:
        if self.locations:
            return "; ".join(self.locations)
        if not self.doc_key:
            return "—"
        pg = compress_ranges(self.pages)
        return f"{self.doc_key}" + (f" p.{pg}" if pg else "")


def _loc(doc: DocRecord, pages: list[int]) -> str:
    return f"{doc.doc_key} p.{compress_ranges(pages)}"


def _pages(hits) -> list[int]:
    return sorted({h.page for h in hits})


# --------------------------------------------------------------------------- #
def run_checks(ws: Workspace, issue: Issue) -> tuple[list[Finding], Basis]:
    basis = parse_basis(issue.basis, ws.rules)
    docs = ws.ordered_documents(issue)
    f: list[Finding] = []

    for err in basis.errors:
        f.append(Finding("warning", "basis-error", err))
    for c in basis.unparsed_codes:
        f.append(Finding("info", "basis-error", f"Design basis code '{c}' is not recognised by any rule; "
                                                "it won't be checked"))

    for d in docs:
        if not d.has_text:
            f.append(Finding("warning", "no-text", "Scanned or image-only PDF — nothing in it can be "
                             "checked. Run OCR and re-ingest.", d.package, d.doc_key, d.title))

    f += check_basis_params(ws, docs, basis)
    f += check_consistency(ws, docs, basis)
    f += check_code_editions(ws, docs, basis)
    f += check_dcr(ws, docs, basis)
    f += check_member_sizes(ws, docs)
    f += check_references(ws, docs, issue)
    f += check_basis_changes(ws, docs, issue, basis)
    f += check_coverage(ws, docs, basis)

    f.sort(key=lambda x: (SEV_ORDER[x.severity], x.check, x.package, x.doc_key))
    for i, x in enumerate(f, 1):
        x.id = f"F-{i:03d}"
    return f, basis


def check_basis_params(ws: Workspace, docs: list[DocRecord], basis: Basis) -> list[Finding]:
    out = []
    for d in docs:
        groups: dict[tuple, list[ParamHit]] = defaultdict(list)
        for h in ws.analysis(d).params:
            bp = basis.params.get(h.key)
            if bp and bp.applies_to(d.package) and not bp.satisfied(h.value):
                groups[(h.key, h.value_key)].append(h)
        for (key, _), hits in groups.items():
            bp = basis.params[key]
            found = fmt(hits[0].value, hits[0].unit)
            rel = {"equals": "requires", "at_least": "requires at least",
                   "at_most": "allows at most"}[bp.check]
            out.append(Finding(
                "error", "basis-mismatch",
                f"{bp.label}: document uses {found}; design basis {rel} {fmt(bp.value, bp.unit)}."
                + (f" ({bp.note})" if bp.note else ""),
                d.package, d.doc_key, d.title, _pages(hits),
                expected=bp.display, found=found, evidence=hits[0].context))
    return out


def check_consistency(ws: Workspace, docs: list[DocRecord], basis: Basis) -> list[Finding]:
    """Parameters that should be identical everywhere but aren't (and aren't in
    the basis — those are already caught by check_basis_params)."""
    out = []
    for key, rule in ws.rules.params.items():
        if not rule.consistent or key in basis.params:
            continue
        by_value: dict = defaultdict(list)       # value_key -> [(doc, hits)]
        display: dict = {}
        for d in docs:
            hits = [h for h in ws.analysis(d).params if h.key == key]
            per_val = defaultdict(list)
            for h in hits:
                per_val[h.value_key].append(h)
                display[h.value_key] = fmt(h.value, h.unit)
            for v, hs in per_val.items():
                by_value[v].append((d, hs))
        if len(by_value) < 2:
            continue
        # The most common value is presumed intended; list the others.
        ranked = sorted(by_value.items(), key=lambda kv: -len(kv[1]))
        locs = [f"{display[v]}: " + ", ".join(_loc(d, _pages(hs)) for d, hs in entries)
                for v, entries in ranked]
        out.append(Finding(
            "warning", "inconsistent-value",
            f"{rule.label} has {len(by_value)} different values across the package. "
            "Add it to the design basis to make the intended value explicit.",
            found=" vs ".join(display[v] for v, _ in ranked), locations=locs,
            evidence=ranked[-1][1][0][1][0].context))
    return out


def check_code_editions(ws: Workspace, docs: list[DocRecord], basis: Basis) -> list[Finding]:
    out = []
    not_in_basis: dict[tuple, list[str]] = defaultdict(list)
    for d in docs:
        groups = defaultdict(list)
        for c in ws.analysis(d).codes:
            groups[(c.family, c.edition)].append(c)
        for (fam, ed), refs in groups.items():
            if fam in basis.codes:
                if basis.codes[fam] != ed:
                    out.append(Finding(
                        "error", "code-edition",
                        f"Cites {refs[0].raw.strip()}; design basis adopts {fam}-{basis.codes[fam]}"
                        if len(ed) == 2 else
                        f"Cites {refs[0].raw.strip()}; design basis adopts {basis.codes[fam]} {fam}",
                        d.package, d.doc_key, d.title, _pages(refs),
                        expected=f"{fam} ({basis.codes[fam]})", found=f"{fam} ({ed})",
                        evidence=refs[0].raw))
            elif basis.codes:
                not_in_basis[(fam, ed)].append(_loc(d, _pages(refs)))
    for (fam, ed), locs in not_in_basis.items():
        out.append(Finding("info", "code-not-in-basis",
                           f"{fam} ({ed}) is referenced but not listed in the design basis.",
                           found=f"{fam} ({ed})", locations=locs))
    return out


def check_dcr(ws: Workspace, docs: list[DocRecord], basis: Basis) -> list[Finding]:
    out = []
    for d in docs:
        for h in ws.analysis(d).dcrs:
            label = f"{h.mark}: " if h.mark else ""
            if h.value > basis.dcr_limit:
                out.append(Finding("error", "dcr-exceeded",
                                   f"{label}demand/capacity = {h.value:.2f} exceeds the limit of {basis.dcr_limit:.2f}.",
                                   d.package, d.doc_key, d.title, [h.page],
                                   expected=f"≤ {basis.dcr_limit:.2f}", found=f"{h.value:.2f}",
                                   evidence=h.context))
            elif basis.dcr_warning is not None and h.value >= basis.dcr_warning:
                out.append(Finding("info", "dcr-near-limit",
                                   f"{label}demand/capacity = {h.value:.2f} (≥ {basis.dcr_warning:.2f}); "
                                   "little reserve for later changes.",
                                   d.package, d.doc_key, d.title, [h.page],
                                   found=f"{h.value:.2f}", evidence=h.context))
    return out


def check_member_sizes(ws: Workspace, docs: list[DocRecord]) -> list[Finding]:
    marks: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for d in docs:
        for e in ws.analysis(d).elements:
            marks[e.mark][e.size].append((d, e))
    out = []
    for mark, sizes in sorted(marks.items()):
        if len(sizes) < 2:
            continue
        locs = []
        for size, entries in sizes.items():
            per_doc = defaultdict(list)
            for d, e in entries:
                per_doc[d.doc_key].append(e.page)
            locs.append(f"{size}: " + ", ".join(f"{k} p.{compress_ranges(p)}" for k, p in per_doc.items()))
        n_docs = len({d.doc_key for es in sizes.values() for d, _ in es})
        out.append(Finding(
            "error" if n_docs > 1 else "warning", "member-size-mismatch",
            f"Member {mark} is sized differently: {' vs '.join(sizes)}.",
            found=" vs ".join(sizes), locations=locs,
            evidence=next(iter(sizes.values()))[0][1].context))
    return out


def check_references(ws: Workspace, docs: list[DocRecord], issue: Issue) -> list[Finding]:
    pkg_rev = package_revisions(ws.store, issue)
    labels = [i.label.upper() for i in ws.store.issues()]
    out = []
    for d in docs:
        seen = set()
        for x in ws.analysis(d).xrefs:
            current = pkg_rev.get(x.package)
            if current is None or (x.package, x.rev) in seen:
                continue
            seen.add((x.package, x.rev))
            if x.rev == current.upper():
                continue
            pages = sorted({y.page for y in ws.analysis(d).xrefs
                            if y.package == x.package and y.rev == x.rev})
            if x.rev in labels:
                out.append(Finding(
                    "warning", "stale-reference",
                    f"Relies on {x.package} Rev {x.rev}, but {x.package} has since been revised "
                    f"(current Rev {current}). Confirm the inputs used are still valid.",
                    d.package, d.doc_key, d.title, pages,
                    expected=f"{x.package} Rev {current}", found=f"{x.package} Rev {x.rev}",
                    evidence=x.raw))
            else:
                out.append(Finding(
                    "info", "unknown-reference",
                    f"Cites {x.package} Rev {x.rev}, which is not an issue in the register "
                    f"(current Rev {current}).",
                    d.package, d.doc_key, d.title, pages,
                    expected=f"{x.package} Rev {current}", found=f"{x.package} Rev {x.rev}",
                    evidence=x.raw))
    return out


def check_basis_changes(ws: Workspace, docs: list[DocRecord], issue: Issue, basis: Basis) -> list[Finding]:
    prev = ws.store.previous(issue)
    if not prev:
        return []
    old = parse_basis(prev.basis, ws.rules)
    changed: dict[str, str] = {}
    for k in set(old.params) | set(basis.params):
        a, b = old.params.get(k), basis.params.get(k)
        if (a and a.display) != (b and b.display):
            changed[k] = f"{a.display if a else '—'} → {b.display if b else '—'}"
    changed_codes = {fam for fam in set(old.codes) | set(basis.codes)
                     if old.codes.get(fam) != basis.codes.get(fam)}
    if not changed and not changed_codes:
        return []

    status = doc_statuses(ws.store.documents_by_key(prev), ws.store.documents_by_key(issue))
    out = []
    for d in docs:
        if status.get(d.doc_key) != UNCHANGED:
            continue
        a = ws.analysis(d)
        hits = [h for h in a.params if h.key in changed]
        codes = [c for c in a.codes if c.family in changed_codes]
        if not hits and not codes:
            continue
        items = sorted({f"{ws.rules.label(h.key)} ({changed[h.key]})" for h in hits}
                       | {f"{c.family} edition" for c in codes})
        out.append(Finding(
            "warning", "basis-changed",
            f"Design basis changed since Issue {prev.label} for: {'; '.join(items)}. "
            "This document was not revised — re-check it.",
            d.package, d.doc_key, d.title, sorted({h.page for h in hits} | {c.page for c in codes})))
    return out


def check_coverage(ws: Workspace, docs: list[DocRecord], basis: Basis) -> list[Finding]:
    out = []
    for key, bp in basis.params.items():
        found = any(h.key == key for d in docs if bp.applies_to(d.package)
                    for h in ws.analysis(d).params)
        if not found:
            scope = f" in {', '.join(bp.packages)}" if bp.packages else ""
            out.append(Finding("info", "not-addressed",
                               f"{bp.label} ({bp.display}) was not found in any document{scope}. "
                               "Either it isn't stated or the wording isn't recognised.",
                               expected=bp.display))
    return out


# --------------------------------------------------------------------------- #
def counts(findings: list[Finding]) -> dict[str, int]:
    c = {s: 0 for s in SEVERITIES}
    for x in findings:
        c[x.severity] += 1
    return c


def write_findings(findings: list[Finding], json_path: Path | None, csv_path: Path | None) -> None:
    rows = [asdict(x) | {"title": x.title, "where": x.where} for x in findings]
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    if csv_path:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        cols = ["id", "severity", "check", "title", "package", "doc_key", "pages", "where",
                "message", "expected", "found", "evidence"]
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                r = dict(r)
                r["pages"] = compress_ranges(r["pages"])
                w.writerow(r)
