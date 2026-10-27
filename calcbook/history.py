"""Revision bookkeeping shared by compile, diff, checks and the report."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Project
from .extract import DocAnalysis, analyze, page_hash
from .rules import RuleSet
from .store import DocRecord, Issue, Store

NEW, REVISED, UNCHANGED, REMOVED = "new", "revised", "unchanged", "removed"


def doc_statuses(old: dict[str, DocRecord] | None, new: dict[str, DocRecord]) -> dict[str, str]:
    """Status of every doc key between two issues (old=None means first issue)."""
    out: dict[str, str] = {}
    old = old or {}
    for k, d in new.items():
        if k not in old:
            out[k] = NEW
        elif old[k].sha256 != d.sha256:
            out[k] = REVISED
        else:
            out[k] = UNCHANGED
    for k in old:
        if k not in new:
            out[k] = REMOVED
    return out


def changed_pages(old_hashes: list[str] | None, new_hashes: list[str]) -> list[int]:
    """1-based pages of the new document whose text does not appear anywhere in
    the old one. Robust to inserted / deleted pages."""
    if old_hashes is None:
        return list(range(1, len(new_hashes) + 1))
    prev = set(old_hashes)
    return [i + 1 for i, h in enumerate(new_hashes) if h not in prev]


def compress_ranges(nums: list[int]) -> str:
    if not nums:
        return ""
    nums = sorted(set(nums))
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:  # type: ignore[list-item]
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(out)


def issues_upto(store: Store, issue: Issue) -> list[Issue]:
    return [i for i in store.issues() if i.id <= issue.id]


def package_revisions(store: Store, issue: Issue) -> dict[str, str]:
    """Current revision of each package at `issue`: the latest issue in which
    any of its documents was added, revised or removed."""
    revs: dict[str, str] = {}
    prev: dict[str, DocRecord] | None = None
    for iss in issues_upto(store, issue):
        docs = store.documents_by_key(iss)
        st = doc_statuses(prev, docs)
        for k, s in st.items():
            if s != UNCHANGED:
                pkg = (docs.get(k) or (prev or {}).get(k)).package  # type: ignore[union-attr]
                revs[pkg] = iss.label
        prev = docs
    return revs


@dataclass
class RegisterRow:
    doc_key: str
    package: str
    title: str
    cells: list[str]        # status per issue ("" = not present)
    current_rev: str        # last issue in which it changed


def register(store: Store, upto: Issue) -> tuple[list[Issue], list[RegisterRow]]:
    issues = issues_upto(store, upto)
    rows: dict[str, RegisterRow] = {}
    prev: dict[str, DocRecord] | None = None
    for col, iss in enumerate(issues):
        docs = store.documents_by_key(iss)
        for k, s in doc_statuses(prev, docs).items():
            d = docs.get(k) or (prev or {}).get(k)
            row = rows.setdefault(k, RegisterRow(k, d.package, d.title, [""] * len(issues), ""))
            row.cells[col] = s
            if k in docs:
                row.title = docs[k].title
            if s in (NEW, REVISED):
                row.current_rev = iss.label
        prev = docs
    return issues, sorted(rows.values(), key=lambda r: (r.package, r.doc_key))


class Workspace:
    """Caches page text and analyses for documents of one or more issues."""

    def __init__(self, project: Project, store: Store, rules: RuleSet):
        self.project, self.store, self.rules = project, store, rules
        self._analysis: dict[int, DocAnalysis] = {}
        self._pages: dict[int, list[str]] = {}

    def pages(self, doc: DocRecord) -> list[str]:
        if doc.id not in self._pages:
            self._pages[doc.id] = self.store.pages(doc)
        return self._pages[doc.id]

    def hashes(self, doc: DocRecord) -> list[str]:
        """Change-detection hashes, recomputed from stored text so improvements
        to normalisation apply to old issues too."""
        return [page_hash(t) for t in self.pages(doc)]

    def analysis(self, doc: DocRecord) -> DocAnalysis:
        if doc.id not in self._analysis:
            self._analysis[doc.id] = analyze(self.pages(doc), self.rules,
                                             self.project.package_ids, doc.package)
        return self._analysis[doc.id]

    def ordered_documents(self, issue: Issue) -> list[DocRecord]:
        """Documents in manifest package order, then file order."""
        order = {pid: i for i, pid in enumerate(self.project.package_ids)}
        return sorted(self.store.documents(issue),
                      key=lambda d: (order.get(d.package, 999), d.rel_path.lower()))
