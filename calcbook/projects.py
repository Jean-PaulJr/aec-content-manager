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
from .config import Project, edit_project, load_project
from .extract import file_sha256, humanize, pdf_title, strip_rev_suffix
from .history import Workspace
from .ingest import doc_key_for, scan_package
from .rules import load_rules
from .store import Store

BUILTIN_TEMPLATES = {
    "commercial-steel-concrete": {
        "name": "Commercial building — steel & concrete",
        "sections": [
            {"id": "CRIT", "title": "Design Criteria"}, {"id": "GRV", "title": "Gravity Framing"},
            {"id": "LAT", "title": "Lateral System"}, {"id": "FDN", "title": "Foundations"},
            {"id": "CONN", "title": "Connections"}, {"id": "APX", "title": "Appendix"},
            {"id": "DWG", "title": "Structural Drawings", "kind": "drawings"},
            {"id": "SPEC", "title": "Specifications", "kind": "specs", "aliases": ["Specifications", "Specs"]},
            {"id": "PC", "title": "Plan Check Comments & Responses", "kind": "docs"},
        ]},
    "wood-multifamily": {
        "name": "Wood-framed multifamily",
        "sections": [
            {"id": "CRIT", "title": "Design Criteria"}, {"id": "FRM", "title": "Roof & Floor Framing"},
            {"id": "SW", "title": "Shear Walls"}, {"id": "HD", "title": "Holdowns & Anchorage"},
            {"id": "FDN", "title": "Foundations"}, {"id": "APX", "title": "Appendix"},
            {"id": "DWG", "title": "Structural Drawings", "kind": "drawings"},
            {"id": "SPEC", "title": "Specifications", "kind": "specs", "aliases": ["Specifications", "Specs"]},
            {"id": "PC", "title": "Plan Check Comments & Responses", "kind": "docs"},
        ]},
    "seismic-retrofit": {
        "name": "Seismic retrofit (ASCE 41)",
        "sections": [
            {"id": "EXC", "title": "Existing Conditions"}, {"id": "EVAL", "title": "Seismic Evaluation"},
            {"id": "RTF", "title": "Retrofit Design"}, {"id": "FDN", "title": "Foundations"},
            {"id": "APX", "title": "Appendix"},
            {"id": "DWG", "title": "Structural Drawings", "kind": "drawings"},
        ]},
    "blank": {
        "name": "Blank — one section",
        "sections": [{"id": "CALC", "title": "Calculations"},
                     {"id": "DWG", "title": "Structural Drawings", "kind": "drawings"}]},
}
DEFAULT_TEMPLATE = "commercial-steel-concrete"


def section_path(title: str, kind: str = "calcs") -> str:
    top = {"drawings": "drawings", "specs": "specifications", "docs": "documents"}.get(kind, "packages")
    return f"{top}/{slugify(title)}"


def template_packages(tpl: dict) -> list[dict]:
    out = []
    for s in tpl.get("sections", []):
        kind = s.get("kind", "calcs")
        d = {"id": str(s["id"]).upper(), "title": s["title"], "path": section_path(s["title"], kind)}
        if kind != "calcs":
            d["kind"] = kind
        if s.get("aliases"):
            d["aliases"] = list(s["aliases"])
        if s.get("title_page") is False:
            d["title_page"] = False
        out.append(d)
    return out


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
                       if p.is_dir() and not p.name.startswith("_") and (p / "project.yaml").exists()),
                      key=lambda e: e.slug)

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

    # ---------------- templates ----------------
    @property
    def templates_dir(self) -> Path:
        return self.root / "_templates"

    def templates(self) -> dict[str, dict]:
        out = {k: dict(v, builtin=True) for k, v in BUILTIN_TEMPLATES.items()}
        if self.templates_dir.exists():
            for f in sorted(self.templates_dir.glob("*.yaml")):
                try:
                    t = yaml.safe_load(f.read_text()) or {}
                    if t.get("sections"):
                        out[f.stem] = dict(t, builtin=False)
                except yaml.YAMLError:
                    continue
        return out

    def save_template(self, name: str, project: Project) -> str:
        key = slugify(name)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        sections = []
        for p in project.packages:
            s = {"id": p.id, "title": p.title}
            if not p.is_calcs:
                s["kind"] = p.kind
            if p.aliases:
                s["aliases"] = list(p.aliases)
            if not p.title_page:
                s["title_page"] = False
            if p.include:
                sections.append(s)
        (self.templates_dir / f"{key}.yaml").write_text(
            yaml.safe_dump({"name": name, "sections": sections}, sort_keys=False, allow_unicode=True))
        return key

    def create(self, name: str, number: str = "", client: str = "", eor: str = "",
               packages: list[dict] | None = None, template: str | None = None,
               address: str = "", phase: str = "Design Development") -> str:
        slug = base = slugify(name or number)
        n = 2
        while (self.root / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        root = self.root / slug
        root.mkdir(parents=True)
        tpl = self.templates().get(template or DEFAULT_TEMPLATE) or BUILTIN_TEMPLATES[DEFAULT_TEMPLATE]
        pkgs = packages or template_packages(tpl)
        data = {
            "project": {"name": name or "Untitled project", "number": number, "client": client,
                        "address": address, "engineer_of_record": eor, "phase": phase,
                        "milestones": [{"name": "Design Development", "due": "", "done": False},
                                       {"name": "Construction Documents", "due": "", "done": False},
                                       {"name": "Permit submittal", "due": "", "done": False},
                                       {"name": "Plan check responses", "due": "", "done": False},
                                       {"name": "Construction start", "due": "", "done": False}],
                        "signatures": {"prepared_by": {"name": "", "date": ""},
                                       "checked_by": {"name": "", "date": ""},
                                       "approved_by": {"name": eor, "date": ""}}},
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
    pages: int = 0
    title: str = ""
    start: int = 0           # sheet number in the next book (if the section is included)
    number: str = ""         # "2.3"

    @property
    def name(self) -> str:
        return self.doc_key.split("/", 1)[-1]


_PAGES_CACHE: dict[tuple, int] = {}


def _page_count(f: Path) -> int:
    st = f.stat()
    k = (str(f), st.st_size, st.st_mtime)
    if k not in _PAGES_CACHE:
        try:
            from pypdf import PdfReader
            _PAGES_CACHE[k] = len(PdfReader(str(f)).pages)
        except Exception:
            _PAGES_CACHE[k] = 0
    return _PAGES_CACHE[k]


def number_working(project: Project, files: dict[str, list[WorkingFile]], has_previous: bool) -> dict:
    """Assign the sheet numbers the next book will have, without building it.
    Mirrors compile.build_book's front matter and section title pages."""
    from .compile import ROWS_PER_PAGE
    secs = [(p, files.get(p.id, [])) for p in project.book_sections]
    secs = [(p, fs) for p, fs in secs if fs]
    toc_rows = sum(1 + len(fs) for _, fs in secs)
    changed = sum(1 for _, fs in secs for f in fs if f.status != "issued")
    n_toc = max(1, -(-toc_rows // ROWS_PER_PAGE))
    n_rev = -(-max(1, changed) // ROWS_PER_PAGE) if has_previous else 0
    n_codes = 1          # codes & assumptions page (one page unless the lists are very long)
    sheet = 1 + n_toc + n_codes + n_rev + 1
    sections = {}
    for i, (p, fs) in enumerate(secs, 1):
        first = sheet
        if p.title_page:
            sheet += 1
        for j, f in enumerate(fs, 1):
            f.number, f.start = f"{i}.{j}", sheet
            sheet += f.pages
        sections[p.id] = {"number": i, "first": first, "last": sheet - 1}
    return {"total": sheet - 1, "front": 1 + n_toc + n_codes + n_rev, "sections": sections}


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
                                    latest.label if d and latest else "", _page_count(f),
                                    pdf_title(f) or humanize(strip_rev_suffix(f.stem))))
        rows.sort(key=lambda r: pkg.rank(r.name))
        out[pkg.id] = rows
    withdrawn = [d for k, d in issued.items() if k not in seen]
    return out, withdrawn


IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
ACCEPTED = (".pdf",) + IMAGE_TYPES


def image_to_pdf(data: bytes) -> bytes:
    """Scanned hand calcs arrive as photos or scans; store them as PDF pages."""
    import io

    from PIL import Image, ImageSequence
    img = Image.open(io.BytesIO(data))
    frames = [f.convert("RGB") for f in ImageSequence.Iterator(img)]
    out = io.BytesIO()
    frames[0].save(out, "PDF", resolution=200.0, save_all=True, append_images=frames[1:])
    return out.getvalue()


def doc_name(filename: str) -> str:
    """Name used for ordering: file stem without its revision suffix."""
    return strip_rev_suffix(Path(filename).stem)


def save_upload(project: Project, package_id: str, filename: str, data: bytes,
                position: str = "end") -> tuple[str, list[str]]:
    """Save an uploaded file into a section folder and place it in the section's
    order. A file that is the same calculation under another revision suffix
    (GRV-02_RevA.pdf vs GRV-02_RevB.pdf) is replaced and keeps its place.

    position: "end", "start", or "after:<doc name>" (ignored when replacing).
    """
    pkg = project.package(package_id)
    if pkg is None:
        raise ValueError(f"unknown section {package_id}")
    name = safe_name(filename)
    ext = Path(name).suffix.lower()
    if ext not in ACCEPTED:
        raise ValueError(f"{name}: upload PDFs, or JPG / PNG / TIFF scans of hand calcs")
    if ext in IMAGE_TYPES:
        try:
            data = image_to_pdf(data)
        except Exception as e:
            raise ValueError(f"{name}: could not read the image ({e})")
        name = Path(name).stem + ".pdf"
    elif not data.startswith(b"%PDF"):
        raise ValueError(f"{name}: not a valid PDF")
    pkg.path.mkdir(parents=True, exist_ok=True)
    stem_key = doc_name(name).lower()
    replaced = []
    for f in pkg.path.glob("*.pdf"):
        if doc_name(f.name).lower() == stem_key:
            replaced.append(f.name)
            f.unlink()
    (pkg.path / name).write_bytes(data)
    if not replaced and pkg.is_calcs:
        place_document(project, package_id, doc_name(name), position)
    return name, [r for r in replaced if r != name]


def current_order(project: Project, package_id: str) -> list[str]:
    pkg = project.package(package_id)
    if pkg is None:
        return []
    names = [str(Path(f.relative_to(pkg.path)).with_name(doc_name(f.name)))
             for f in scan_package(pkg.path)]
    return sorted(names, key=pkg.rank)


def place_document(project: Project, package_id: str, name: str, position: str = "end") -> None:
    order = [n for n in current_order(project, package_id) if n.lower() != name.lower()]
    if position == "start":
        order.insert(0, name)
    elif position.startswith("after:"):
        target = position[6:].lower()
        idx = next((i for i, n in enumerate(order) if n.lower() == target), len(order) - 1)
        order.insert(idx + 1, name)
    else:
        order.append(name)
    set_order(project, package_id, order)


def set_order(project: Project, package_id: str, names: list[str]) -> None:
    def fn(data):
        for p in data["packages"]:
            if str(p["id"]).upper() == package_id:
                p["order"] = list(names)
    edit_project(project.path, fn)


def move_document(project: Project, name: str, from_id: str, to_id: str) -> None:
    """Move a calculation (all its files) to another section folder."""
    src, dst = project.package(from_id), project.package(to_id)
    if not src or not dst:
        raise ValueError("unknown section")
    dst.path.mkdir(parents=True, exist_ok=True)
    moved = False
    for f in scan_package(src.path):
        if doc_name(f.name).lower() == name.split("/")[-1].lower():
            shutil.move(str(f), str(dst.path / f.name))
            moved = True
    if not moved:
        raise ValueError(f"{name} not found in {from_id}")


# ---------------- sections ----------------
def add_section(project: Project, title: str, kind: str = "calcs", after: str | None = None) -> str:
    title = title.strip()
    if not title:
        raise ValueError("give the section a title")
    base = re.sub(r"[^A-Z]", "", title.upper())[:4] or "SEC"
    sid, n = base, 2
    while project.package(sid):
        sid, n = f"{base}{n}", n + 1
    path = section_path(title, kind)
    (project.root / path).mkdir(parents=True, exist_ok=True)

    def fn(data):
        entry = {"id": sid, "title": title, "path": path}
        if kind != "calcs":
            entry["kind"] = kind
        ids = [str(p["id"]).upper() for p in data["packages"]]
        idx = ids.index(after) + 1 if after in ids else len(ids)
        data["packages"].insert(idx, entry)
    edit_project(project.path, fn)
    return sid


def update_section(project: Project, sid: str, **changes) -> None:
    allowed = {"title", "responsible", "include", "title_page"}

    def fn(data):
        for p in data["packages"]:
            if str(p["id"]).upper() == sid:
                for k, v in changes.items():
                    if k in allowed:
                        p[k] = v
    edit_project(project.path, fn)


def remove_section(project: Project, sid: str) -> str:
    """Remove a section from the project. Its files are moved to
    _removed_sections/ in the project folder rather than deleted."""
    pkg = project.package(sid)
    if pkg is None:
        raise ValueError("unknown section")
    moved = ""
    if pkg.path.exists() and any(pkg.path.iterdir()):
        from datetime import datetime
        dest = project.root / "_removed_sections" / f"{sid}-{datetime.now():%Y%m%d-%H%M%S}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pkg.path), str(dest))
        moved = str(dest.relative_to(project.root))
    elif pkg.path.exists():
        pkg.path.rmdir()
    edit_project(project.path, lambda data: data.__setitem__(
        "packages", [p for p in data["packages"] if str(p["id"]).upper() != sid]))
    return moved


def reorder_sections(project: Project, ids: list[str]) -> None:
    def fn(data):
        by_id = {str(p["id"]).upper(): p for p in data["packages"]}
        new = [by_id.pop(i) for i in ids if i in by_id]
        data["packages"] = new + list(by_id.values())
    edit_project(project.path, fn)


def delete_working_file(project: Project, package_id: str, rel: str) -> None:
    pkg = project.package(package_id)
    if pkg is None:
        raise ValueError("unknown package")
    target = (pkg.path / rel).resolve()
    if pkg.path.resolve() not in target.parents or not target.is_file():
        raise ValueError("file not found")
    target.unlink()
