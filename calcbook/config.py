"""Project manifest (project.yaml) and design basis (design_basis.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .rules import RuleSet, parse_code_name
from .units import fmt, parse_quantity


@dataclass
class Package:
    id: str
    title: str
    path: Path
    discipline: str = ""
    responsible: str = ""
    kind: str = "calcs"            # "calcs" | "drawings"

    @property
    def is_drawings(self) -> bool:
        return self.kind == "drawings"


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


def load_project(path: str | Path) -> Project:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run `calcbook init` first or pass -p")
    data = yaml.safe_load(path.read_text()) or {}
    root = path.parent
    proj = data.get("project", {})
    pkgs = []
    for p in data.get("packages", []):
        if "id" not in p or "path" not in p:
            raise ValueError("each package needs an `id` and a `path`")
        pkgs.append(Package(
            id=str(p["id"]).upper(),
            title=p.get("title", p["id"]),
            path=(root / p["path"]).resolve(),
            discipline=p.get("discipline", ""),
            responsible=p.get("responsible", ""),
            kind=str(p.get("kind", "calcs")).lower(),
        ))
    if len({p.id for p in pkgs}) != len(pkgs):
        raise ValueError("package ids must be unique")
    rules = data.get("rules")
    return Project(
        root=root,
        name=proj.get("name", "Untitled project"),
        number=str(proj.get("number", "")),
        engineer_of_record=proj.get("engineer_of_record", ""),
        client=proj.get("client", ""),
        packages=pkgs,
        basis_path=(root / data.get("basis", "design_basis.yaml")).resolve(),
        rules_path=(root / rules).resolve() if rules else None,
        output_dir=(root / data.get("output", "output")).resolve(),
    )


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
