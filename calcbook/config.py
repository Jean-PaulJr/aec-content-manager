"""Project manifest (project.yaml) and design basis (design_basis.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .rules import RuleSet, parse_code_name
from .units import fmt, parse_quantity


KINDS = {"calcs": "Calculations", "drawings": "Drawing set", "specs": "Specifications",
         "docs": "Other documents"}
PHASES = ["Planning", "Schematic Design", "Design Development", "Construction Documents",
          "Permitting", "Bidding", "Construction", "Closeout"]
SIGN_ROLES = [("prepared_by", "Prepared by"), ("checked_by", "Checked by"), ("approved_by", "Approved by")]


@dataclass
class Package:
    """A section of the report package, backed by a folder of PDFs."""
    id: str
    title: str
    path: Path
    discipline: str = ""
    responsible: str = ""
    kind: str = "calcs"            # "calcs" | "drawings" | "specs" | "docs"
    aliases: list[str] = field(default_factory=list)  # other names documents use, e.g. "Specifications"
    include: bool = True           # include this section in the compiled book
    title_page: bool = True        # insert a section title page before it
    order: list[str] = field(default_factory=list)   # document order (doc names without rev suffix)

    @property
    def is_drawings(self) -> bool:
        return self.kind == "drawings"

    @property
    def is_calcs(self) -> bool:
        return self.kind == "calcs"

    def rank(self, name: str) -> tuple:
        """Sort key for a document name within this section: explicit order first,
        anything not yet placed after it, alphabetically."""
        low = name.lower()
        order = [o.lower() for o in self.order]
        return (order.index(low), "") if low in order else (len(order), low)


@dataclass
class Milestone:
    name: str
    due: str = ""
    done: bool = False


@dataclass
class Project:
    root: Path
    name: str
    number: str
    engineer_of_record: str
    client: str
    packages: list[Package]
    basis_path: Path
    rules_path: Path | None
    output_dir: Path
    address: str = ""
    phase: str = "Design Development"
    milestones: list[Milestone] = field(default_factory=list)
    signatures: dict[str, dict] = field(default_factory=dict)   # role -> {name, date}

    @property
    def package_ids(self) -> list[str]:
        return [p.id for p in self.packages]

    @property
    def path(self) -> Path:
        return self.root / "project.yaml"

    def is_drawing_package(self, pid: str) -> bool:
        p = self.package(pid)
        return bool(p and p.is_drawings)

    def package(self, pid: str) -> Package | None:
        return next((p for p in self.packages if p.id == pid), None)

    @property
    def book_sections(self) -> list[Package]:
        """Calculation sections that go into the compiled book, in order."""
        return [p for p in self.packages if p.is_calcs and p.include]

    def is_calc_package(self, pid: str) -> bool:
        p = self.package(pid)
        return bool(p and p.is_calcs)


def load_project(path: str | Path) -> Project:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run `calcbook init` first or pass -p")
    data = yaml.safe_load(path.read_text()) or {}
    root = path.parent
    proj = data.get("project", {}) or {}
    pkgs = []
    for p in data.get("packages", []) or []:
        if "id" not in p or "path" not in p:
            raise ValueError("each package needs an `id` and a `path`")
        pkgs.append(Package(
            id=str(p["id"]).upper(),
            title=p.get("title", p["id"]),
            path=(root / p["path"]).resolve(),
            discipline=p.get("discipline", ""),
            responsible=p.get("responsible", ""),
            kind=str(p.get("kind", "calcs")).lower(),
            aliases=[str(a) for a in (p.get("aliases") or [])],
            include=bool(p.get("include", True)),
            title_page=bool(p.get("title_page", True)),
            order=[str(o) for o in (p.get("order") or [])],
        ))
    if len({p.id for p in pkgs}) != len(pkgs):
        raise ValueError("package ids must be unique")
    rules = data.get("rules")
    ms = [Milestone(str(m.get("name", "")), str(m.get("due", "") or ""), bool(m.get("done", False)))
          for m in (proj.get("milestones") or []) if isinstance(m, dict)]
    return Project(
        root=root,
        name=proj.get("name", "Untitled project"),
        number=str(proj.get("number", "") or ""),
        engineer_of_record=proj.get("engineer_of_record", "") or "",
        client=proj.get("client", "") or "",
        packages=pkgs,
        basis_path=(root / data.get("basis", "design_basis.yaml")).resolve(),
        rules_path=(root / rules).resolve() if rules else None,
        output_dir=(root / data.get("output", "output")).resolve(),
        address=proj.get("address", "") or "",
        phase=proj.get("phase", "Design Development") or "Design Development",
        milestones=ms,
        signatures={k: dict(v) for k, v in (proj.get("signatures") or {}).items() if isinstance(v, dict)},
    )


def edit_project(path: Path, fn) -> None:
    """Load project.yaml as a dict, let fn modify it in place, write it back."""
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("project", {})
    data.setdefault("packages", [])
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def read_basis_raw(project: Project) -> dict:
    if not project.basis_path.exists():
        return {}
    return yaml.safe_load(project.basis_path.read_text()) or {}


# --------------------------------------------------------------------------- #
CHECKS = {"equals", "at_least", "at_most"}
REL_SYMBOL = {"equals": "=", "at_least": "≥", "at_most": "≤"}


@dataclass
class BasisParam:
    key: str
    label: str
    kind: str
    value: float | str
    unit: str
    check: str = "equals"
    tolerance: float = 0.0
    packages: list[str] = field(default_factory=list)
    note: str = ""

    def applies_to(self, package: str) -> bool:
        return not self.packages or package in self.packages

    def satisfied(self, found: float | str) -> bool:
        if self.kind == "text" or isinstance(self.value, str):
            return str(found).strip().upper() == str(self.value).strip().upper()
        tol = self.tolerance or 1e-6 * max(1.0, abs(self.value))
        if self.check == "at_least":
            return found >= self.value - tol
        if self.check == "at_most":
            return found <= self.value + tol
        return abs(found - self.value) <= tol

    @property
    def display(self) -> str:
        sym = REL_SYMBOL[self.check]
        return f"{'' if sym == '=' else sym + ' '}{fmt(self.value, self.unit)}"


@dataclass
class Basis:
    codes: dict[str, str]                 # family -> edition
    unparsed_codes: list[str]
    params: dict[str, BasisParam]
    dcr_limit: float = 1.0
    dcr_warning: float | None = None
    errors: list[str] = field(default_factory=list)


def parse_basis(raw: dict, rules: RuleSet) -> Basis:
    codes, unparsed, errors = {}, [], []
    for c in raw.get("codes", []) or []:
        parsed = parse_code_name(str(c), rules)
        if parsed:
            codes[parsed[0]] = parsed[1]
        else:
            unparsed.append(str(c))

    params: dict[str, BasisParam] = {}
    for key, spec in (raw.get("parameters") or {}).items():
        if not isinstance(spec, dict):
            spec = {"value": spec}
        rule = rules.params.get(key)
        if rule is None:
            errors.append(f"design basis parameter '{key}' has no extraction rule")
            continue
        check = spec.get("check", "equals")
        if check not in CHECKS:
            errors.append(f"'{key}': check must be one of {sorted(CHECKS)}")
            continue
        try:
            if rule.kind == "text":
                value, unit = str(spec["value"]).strip().upper(), ""
            else:
                value, unit = parse_quantity(spec["value"], rule.dimension, rule.default_unit)
            tol = 0.0
            if "tolerance" in spec:
                tol = parse_quantity(spec["tolerance"], rule.dimension, rule.default_unit)[0]
        except (KeyError, ValueError) as e:
            errors.append(f"'{key}': {e}")
            continue
        pk = spec.get("packages") or []
        params[key] = BasisParam(key, rule.label, rule.kind, value, unit, check, tol,
                                 [str(p).upper() for p in pk], spec.get("note", ""))

    return Basis(codes, unparsed, params,
                 float(raw.get("dcr_limit", 1.0)),
                 float(raw["dcr_warning"]) if raw.get("dcr_warning") is not None else None,
                 errors)
