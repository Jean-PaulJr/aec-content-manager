"""Multiple projects in one workspace folder.

    workspace/
        mission-bay-lab/      project.yaml, design_basis.yaml, packages/, drawings/, .calcbook/
        pier-70-garage/       ...

Each project folder is an ordinary calcbook project, so the CLI works on it too.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from .cli import BASIS_TEMPLATE
from .config import Project, load_project
from .extract import file_sha256, strip_rev_suffix
from .history import Workspace
from .ingest import doc_key_for, scan_package
from .rules import load_rules
from .store import Store

DEFAULT_PACKAGES = [
    {"id": "GRV", "title": "Gravity Framing", "path": "packages/gravity"},
    {"id": "LAT", "title": "Lateral System", "path": "packages/lateral"},
    {"id": "FDN", "title": "Foundations", "path": "packages/foundations"},
    {"id": "DWG", "title": "Structural Drawings", "path": "drawings/structural", "kind": "drawings"},
]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "project"


def open_workspace(project_yaml: Path) -> Workspace:
    project = load_project(project_yaml)
    return Workspace(project, Store(project.root), load_rules(project.rules_path))


@dataclass
class ProjectEntry:
    slug: str
    root: Path

    @property
    def yaml(self) -> Path:
        return self.root / "project.yaml"


class Home:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[ProjectEntry]:
        return sorted((ProjectEntry(p.name, p) for p in self.root.iterdir()
                       if p.is_dir() and (p / "project.yaml").exists()), key=lambda e: e.slug)

    def get(self, slug: str) -> ProjectEntry | None:
        if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
            return None
        p = self.root / slug
        return ProjectEntry(slug, p) if (p / "project.yaml").exists() else None

    def open(self, slug: str) -> Workspace:
        e = self.get(slug)
        if e is None:
            raise LookupError(f"project '{slug}' not found")
        return open_workspace(e.yaml)

    def create(self, name: str, number: str = "", client: str = "", eor: str = "",
               packages: list[dict] | None = None) -> str:
        slug = base = slugify(name or number)
        n = 2
        while (self.root / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        root = self.root / slug
        root.mkdir(parents=True)
        pkgs = packages or DEFAULT_PACKAGES
        data = {
            "project": {"name": name or "Untitled project", "number": number, "client": client,
                        "engineer_of_record": eor},
            "packages": pkgs, "basis": "design_basis.yaml", "output": "output",
        }
        (root / "project.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        (root / "design_basis.yaml").write_text(BASIS_TEMPLATE)
        for p in pkgs:
            (root / p["path"]).mkdir(parents=True, exist_ok=True)
        return slug

    def delete(self, slug: str) -> None:
        e = self.get(slug)
        if e:
            shutil.rmtree(e.root)


# --------------------------------------------------------------------------- #
# Working files (what will go into the next issue)
# --------------------------------------------------------------------------- #
@dataclass
class WorkingFile:
    package: str
    rel: str                 # relative to the package folder
    size: int
    doc_key: str
    status: str              # issued | modified | new
    issued_rev: str = ""


def safe_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ ()\-]+", "_", name).strip(" .")
    return name or "upload.pdf"


def working_files(ws: Workspace) -> tuple[dict[str, list[WorkingFile]], list]:
    """Current files per package compared with the latest issue, plus the
    issued documents that are no longer present (they'll be withdrawn)."""
    latest = ws.store.latest()
    issued = ws.store.documents_by_key(latest) if latest else {}
    seen = set()
    out: dict[str, list[WorkingFile]] = {}
    for pkg in ws.project.packages:
        rows = []
        for f in scan_package(pkg.path):
            rel = f.relative_to(pkg.path)
            key = doc_key_for(pkg.id, rel)
            seen.add(key)
            d = issued.get(key)
            if d is None:
                st = "new"
            elif d.sha256 == file_sha256(f):
                st = "issued"
            else:
                st = "modified"
            rows.append(WorkingFile(pkg.id, str(rel), f.stat().st_size, key, st,
                                    latest.label if d and latest else ""))
        out[pkg.id] = rows
    withdrawn = [d for k, d in issued.items() if k not in seen]
    return out, withdrawn


def save_upload(project: Project, package_id: str, filename: str, data: bytes) -> tuple[str, list[str]]:
    """Save an uploaded PDF into a package folder. A file that is the same
    calculation under another revision suffix (GRV-02_RevA.pdf vs
    GRV-02_RevB.pdf) is replaced, so the folder always holds the current set."""
    pkg = project.package(package_id)
    if pkg is None:
        raise ValueError(f"unknown package {package_id}")
    name = safe_name(filename)
    if not name.lower().endswith(".pdf"):
        raise ValueError(f"{name}: only PDF files can be uploaded")
    if not data.startswith(b"%PDF"):
        raise ValueError(f"{name}: not a valid PDF")
    pkg.path.mkdir(parents=True, exist_ok=True)
    stem_key = strip_rev_suffix(Path(name).stem).lower()
    replaced = []
    for f in pkg.path.glob("*.pdf"):
        if strip_rev_suffix(f.stem).lower() == stem_key:
            replaced.append(f.name)
            f.unlink()
    (pkg.path / name).write_bytes(data)
    return name, [r for r in replaced if r != name]


def delete_working_file(project: Project, package_id: str, rel: str) -> None:
    pkg = project.package(package_id)
    if pkg is None:
        raise ValueError("unknown package")
    target = (pkg.path / rel).resolve()
    if pkg.path.resolve() not in target.parents or not target.is_file():
        raise ValueError("file not found")
    target.unlink()
