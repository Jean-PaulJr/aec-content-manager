"""Extraction rules: what design parameters, code references, member sizes and
demand/capacity ratios look like in calculation text.

The defaults cover common US structural practice. A project can add or
override rules with a YAML file (see README, "Custom rules").
Patterns use `{num}` as a placeholder for a number and may define named groups
`value` (required for text parameters) and `unit`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .units import NUM_RE

# --------------------------------------------------------------------------- #
# Default design parameters
# --------------------------------------------------------------------------- #
DEFAULT_PARAMETERS: dict[str, dict] = {
    "fc": {
        "label": "Concrete strength f'c",
        "kind": "number", "dimension": "stress", "default_unit": "psi",
        "consistent": False,  # varies by element — checked against a minimum instead
        "patterns": [r"\bf\s*['’′`]?\s*c\s*['’′`]?\s*[=:]\s*{num}\s*(?P<unit>psi|ksi|mpa)\b"],
    },
    "rebar_fy": {
        "label": "Rebar yield strength fy",
        "kind": "number", "dimension": "stress", "default_unit": "ksi",
        "patterns": [
            r"(?-i:\bfy)\s*[=:]\s*{num}\s*(?P<unit>ksi|psi|mpa)\b",
            r"\bA\s?615\s*,?\s*Gr(?:ade|\.)?\s*(?P<value>\d{2,3})\b",
        ],
    },
    "steel_fy": {
        "label": "Structural steel Fy",
        "kind": "number", "dimension": "stress", "default_unit": "ksi",
        "patterns": [r"(?-i:\bFy)\s*[=:]\s*{num}\s*(?P<unit>ksi|psi|mpa)\b"],
    },
    "sds": {
        "label": "Seismic SDS",
        "kind": "number", "dimension": "ratio",
        "patterns": [r"\bS\s*_?\s*DS\s*[=:]\s*{num}(?:\s*(?P<unit>g)\b)?"],
    },
    "sd1": {
        "label": "Seismic SD1",
        "kind": "number", "dimension": "ratio",
        "patterns": [r"\bS\s*_?\s*D\s*1\s*[=:]\s*{num}(?:\s*(?P<unit>g)\b)?"],
    },
    "risk_category": {
        "label": "Risk Category",
        "kind": "text",
        "patterns": [r"\bRisk\s+Category\s*[=:]?\s*(?P<value>IV|III|II|I)\b"],
    },
    "site_class": {
        "label": "Site Class",
        "kind": "text",
        "patterns": [r"\bSite\s+Class\s*[=:]?\s*(?P<value>BC|CD|DE|[A-F])\b"],
    },
    "sdc": {
        "label": "Seismic Design Category",
        "kind": "text",
        "patterns": [r"\b(?:Seismic\s+Design\s+Category|SDC)\s*[=:]?\s*(?P<value>[A-F])\b"],
    },
    "r_factor": {
        "label": "Response modification R",
        "kind": "number", "dimension": "number",
        "patterns": [r"(?-i:\bR)\s*=\s*{num}(?![\d.,])(?!\s*(?:psi|ksi|psf|kips?|k\b|in|ft|lb))"],
    },
    "ie": {
        "label": "Seismic importance factor Ie",
        "kind": "number", "dimension": "number",
        "patterns": [r"(?-i:\bI\s*_?\s*e)\s*=\s*{num}(?![\d.,])"],
    },
    "wind_speed": {
        "label": "Basic wind speed V",
        "kind": "number", "dimension": "speed", "default_unit": "mph",
        "patterns": [
            r"\b(?:Basic\s+(?:Design\s+)?Wind\s+Speed|(?:Ultimate\s+)?Wind\s+Speed|(?-i:V)\s*_?\s*(?:ult)?)"
            r"\s*[=:,]?\s*(?:\([^)\n]{0,20}\)\s*[=:]?\s*)?{num}\s*(?P<unit>mph|m/s)\b",
        ],
    },
    "exposure": {
        "label": "Wind Exposure",
        "kind": "text",
        "patterns": [r"\bExposure(?:\s+Category)?\s*[=:]?\s*(?P<value>[BCD])\b"],
    },
    "floor_live_load": {
        "label": "Floor live load",
        "kind": "number", "dimension": "pressure", "default_unit": "psf",
        "patterns": [
            r"(?<!roof\s)\b(?:floor\s+)?(?:live\s+load|(?-i:LL))(?:\s*\([^)\n]{0,30}\))?\s*[=:]\s*{num}\s*(?P<unit>psf|kpa)\b",
        ],
    },
    "roof_live_load": {
        "label": "Roof live load",
        "kind": "number", "dimension": "pressure", "default_unit": "psf",
        "patterns": [r"\b(?:roof\s+live\s+load|(?-i:Lr))\s*[=:]\s*{num}\s*(?P<unit>psf|kpa)\b"],
    },
    "soil_bearing": {
        "label": "Allowable soil bearing",
        "kind": "number", "dimension": "pressure", "default_unit": "psf",
        "patterns": [
            r"\b(?:allowable\s+(?:soil\s+)?bearing(?:\s+(?:pressure|capacity))?|(?-i:q\s*_?\s*a(?:llow)?))"
            r"\s*[=:]\s*{num}\s*(?P<unit>psf|ksf|kpa)\b",
        ],
    },
}

# --------------------------------------------------------------------------- #
# Code / standard references: family -> patterns with an `ed` group
# --------------------------------------------------------------------------- #
DASH = r"\s*[-–—]\s*"
DEFAULT_CODES: dict[str, list[str]] = {
    "ASCE 7": [r"\bASCE\s*/?\s*(?:SEI\s*)?7" + DASH + r"(?P<ed>\d{2})\b"],
    "ACI 318": [r"\bACI\s*318" + DASH + r"(?P<ed>\d{2})\b"],
    "AISC 360": [r"\b(?:ANSI\s*/\s*)?AISC\s*360" + DASH + r"(?P<ed>\d{2})\b"],
    "AISC 341": [r"\b(?:ANSI\s*/\s*)?AISC\s*341" + DASH + r"(?P<ed>\d{2})\b"],
    "AISC 358": [r"\b(?:ANSI\s*/\s*)?AISC\s*358" + DASH + r"(?P<ed>\d{2})\b"],
    "TMS 402": [r"\bTMS\s*402" + DASH + r"(?P<ed>\d{2})\b"],
    "IBC": [r"\b(?P<ed>20\d{2})\s+IBC\b", r"\bIBC\s*[-–]?\s*(?P<ed>20\d{2})\b"],
    "CBC": [r"\b(?P<ed>20\d{2})\s+CBC\b", r"\bCBC\s*[-–]?\s*(?P<ed>20\d{2})\b"],
    "NDS": [r"\b(?P<ed>20\d{2})\s+NDS\b", r"\bNDS\s*[-–]?\s*(?P<ed>20\d{2})\b"],
    "SDPWS": [r"\b(?P<ed>20\d{2})\s+SDPWS\b", r"\bSDPWS\s*[-–]?\s*(?P<ed>20\d{2})\b"],
}

# Editions offered when switching the adopted code (2-digit = ASCE/ACI/AISC style suffix).
CODE_EDITIONS: dict[str, list[str]] = {
    "ASCE 7": ["10", "16", "22"],
    "ACI 318": ["14", "19", "25"],
    "AISC 360": ["10", "16", "22"],
    "AISC 341": ["10", "16", "22"],
    "AISC 358": ["16", "22"],
    "TMS 402": ["16", "22"],
    "IBC": ["2018", "2021", "2024"],
    "CBC": ["2019", "2022", "2025"],
    "NDS": ["2018", "2024"],
    "SDPWS": ["2021", "2024"],
}


def code_label(family: str, edition: str) -> str:
    """'ASCE 7', '22' -> 'ASCE 7-22';  'CBC', '2025' -> '2025 CBC'."""
    return f"{edition} {family}" if len(edition) == 4 else f"{family}-{edition}"


# Member marks like B-12, C-3, BR-1 (a hyphen is required so W18X35 / A992 don't match).
DEFAULT_MARK = r"\b[A-Z]{1,3}-\d{1,3}[A-Z]?\b"

# Steel shapes: W, HSS (rect/round), C/MC, L.
DEFAULT_SHAPE = (
    r"\b(?:W\d{1,2}\s*[xX×]\s*\d{1,3}(?:\.\d+)?"
    r"|HSS\s*\d{1,2}(?:\.\d+)?\s*[xX×]\s*\d{1,2}(?:\.\d+)?(?:\s*[xX×]\s*(?:\d+/\d+|\d*\.\d+))?"
    r"|(?:MC|C)\d{1,2}\s*[xX×]\s*\d{1,3}(?:\.\d+)?"
    r"|L\d{1,2}(?:\.\d+)?\s*[xX×]\s*\d{1,2}(?:\.\d+)?\s*[xX×]\s*(?:\d+/\d+|\d*\.\d+))"
)

# Drawing sheet numbers: S-201, S2.01, S201, S-001, SK-3, A-101 (not member marks like C-3).
DEFAULT_SHEET = r"^(?:(?:S|SD|A|C|M|E|P|G|L|FP)[-.]?\d\.?\d{2}|SK-?\d{1,3})[A-Z]?$"

DEFAULT_DCR = [
    r"\b(?:DCR|D\s*/\s*C(?:\s*ratio)?|Unity(?:\s+(?:check|ratio))?|Interaction(?:\s+(?:ratio|value))?"
    r"|Utili[sz]ation(?:\s+ratio)?)\s*[=:]\s*{num}(?![\d.,])",
]


# --------------------------------------------------------------------------- #
@dataclass
class ParamRule:
    key: str
    label: str
    kind: str = "number"          # "number" | "text"
    dimension: str = "number"
    default_unit: str = ""
    consistent: bool = True       # should every document use the same value?
    patterns: list[re.Pattern] = field(default_factory=list)


@dataclass
class RuleSet:
    params: dict[str, ParamRule]
    codes: dict[str, list[re.Pattern]]
    dcr: list[re.Pattern]
    mark: re.Pattern
    shape: re.Pattern
    sheet: re.Pattern = field(default_factory=lambda: re.compile(DEFAULT_SHEET))

    def label(self, key: str) -> str:
        r = self.params.get(key)
        return r.label if r else key


def _compile(pattern: str, flags: int = re.IGNORECASE | re.MULTILINE) -> re.Pattern:
    return re.compile(pattern.replace("{num}", NUM_RE), flags)


def _param_rule(key: str, spec: dict) -> ParamRule:
    return ParamRule(
        key=key,
        label=spec.get("label", key),
        kind=spec.get("kind", "number"),
        dimension=spec.get("dimension", "number"),
        default_unit=spec.get("default_unit", ""),
        consistent=bool(spec.get("consistent", True)),
        patterns=[_compile(p) for p in spec.get("patterns", [])],
    )


def load_rules(path: Path | None = None) -> RuleSet:
    params_spec = {k: dict(v) for k, v in DEFAULT_PARAMETERS.items()}
    codes_spec = {k: list(v) for k, v in DEFAULT_CODES.items()}
    dcr_spec = list(DEFAULT_DCR)
    mark_spec, shape_spec, sheet_spec = DEFAULT_MARK, DEFAULT_SHAPE, DEFAULT_SHEET

    if path:
        user = yaml.safe_load(Path(path).read_text()) or {}
        for key, spec in (user.get("parameters") or {}).items():
            if spec is None:                      # `key: null` disables a default rule
                params_spec.pop(key, None)
                continue
            merged = params_spec.get(key, {}) | spec
            params_spec[key] = merged
        for fam, pats in (user.get("codes") or {}).items():
            codes_spec[fam] = list(pats)
        if user.get("dcr_patterns"):
            dcr_spec = list(user["dcr_patterns"])
        mark_spec = user.get("mark_pattern", mark_spec)
        shape_spec = user.get("shape_pattern", shape_spec)
        sheet_spec = user.get("sheet_pattern", sheet_spec)

    return RuleSet(
        params={k: _param_rule(k, v) for k, v in params_spec.items()},
        codes={f: [_compile(p) for p in pats] for f, pats in codes_spec.items()},
        dcr=[_compile(p) for p in dcr_spec],
        mark=re.compile(mark_spec),
        shape=re.compile(shape_spec),
        sheet=re.compile(sheet_spec),
    )


def parse_code_name(text: str, rules: RuleSet) -> tuple[str, str] | None:
    """'ASCE 7-22' -> ('ASCE 7', '22'). Returns None if not recognised."""
    for fam, pats in rules.codes.items():
        for p in pats:
            m = p.search(text)
            if m:
                return fam, m.group("ed")
    return None
