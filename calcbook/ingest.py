"""Snapshot the current state of every sub-package as a named issue."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Project, read_basis_raw
from .extract import (file_sha256, humanize, ocr_available, ocr_missing_text, page_hash, pdf_title,
                      read_pdf_pages, strip_rev_suffix)
from .store import Issue, Store


@dataclass
class IngestResult:
    issue: Issue
    documents: int = 0
    pages: int = 0
    warnings: list[str] = field(default_factory=list)


def scan_package(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    files = [p for p in folder.rglob("*")
             if p.is_file() and p.suffix.lower() == ".pdf"
             and not any(part.startswith(".") for part in p.relative_to(folder).parts)]
    return sorted(files, key=lambda p: str(p.relative_to(folder)).lower())


def doc_key_for(package_id: str, rel: Path) -> str:
    parts = [*rel.parent.parts, strip_rev_suffix(rel.stem)]
    return f"{package_id}/" + "/".join(parts)


def ingest(project: Project, store: Store, label: str, note: str = "",
           issued_on: str | None = None, replace: bool = False) -> IngestResult:
    label = label.strip()
    if not label:
        raise ValueError("issue label is required")
    if store.issue(label):
        if not replace:
            raise ValueError(f"issue '{label}' already exists (use --replace to overwrite)")
        store.delete_issue(label)

    issue = store.create_issue(label, issued_on or date.today().isoformat(), note,
                               read_basis_raw(project))
    result = IngestResult(issue)
    if not project.basis_path.exists():
        result.warnings.append(f"design basis not found at {project.basis_path}")

    for pkg in project.packages:
        if not pkg.path.exists():
            result.warnings.append(f"{pkg.id}: folder {pkg.path} does not exist")
            continue
        chosen: dict[str, Path] = {}
        for f in scan_package(pkg.path):
            key = doc_key_for(pkg.id, f.relative_to(pkg.path))
            if key in chosen:
                result.warnings.append(
                    f"{pkg.id}: '{chosen[key].name}' and '{f.name}' look like the same calc "
                    f"({key}); keeping '{f.name}'")
            chosen[key] = f
        others = [p for p in pkg.path.rglob("*") if p.is_file() and p.suffix.lower() != ".pdf"
                  and not p.name.startswith(".")]
        if others:
            result.warnings.append(f"{pkg.id}: ignored {len(others)} non-PDF file(s)")

        for key, f in chosen.items():
            sha = file_sha256(f)
            try:
                pages = read_pdf_pages(f)
                if not all(t.strip() for t in pages):
                    pages = ocr_missing_text(f, pages)
            except Exception as e:  # corrupt / encrypted PDF
                result.warnings.append(f"{key}: could not read PDF ({e}); skipped")
                continue
            title = pdf_title(f) or humanize(strip_rev_suffix(f.stem))
            store.add_document(issue, pkg.id, key, str(f.relative_to(project.root)), title, sha,
                               store.archive(f, sha), pages, [page_hash(t) for t in pages])
            result.documents += 1
            result.pages += len(pages)
            if not any(t.strip() for t in pages):
                result.warnings.append(f"{key}: no readable text" + (
                    "" if ocr_available() else " — install Tesseract to read scanned hand calcs"))
    store.commit()
    return result
