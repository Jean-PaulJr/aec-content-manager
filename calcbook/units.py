"""Number parsing and unit normalisation.

Every extracted quantity is converted to one canonical unit per dimension so
values written as "4 ksi" and "4,000 psi" compare as equal.
"""
from __future__ import annotations

import re

# Matches 4000, 4,000, 4000.5, .75, -1.2 (also the unicode minus sign).
NUM_RE = r"(?P<value>[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[-−]?\.\d+)"

# dimension -> (canonical unit, {unit: factor to canonical})
DIMENSIONS: dict[str, tuple[str, dict[str, float]]] = {
    "stress": ("psi", {"psi": 1.0, "ksi": 1000.0, "mpa": 145.037738, "kpa": 0.145037738}),
    "pressure": ("psf", {"psf": 1.0, "ksf": 1000.0, "kpa": 20.885434,
                         "kn/m2": 20.885434, "kn/m²": 20.885434, "pa": 0.020885434}),
    "speed": ("mph", {"mph": 1.0, "m/s": 2.236936, "km/h": 0.621371, "kph": 0.621371}),
    "force": ("kip", {"kip": 1.0, "kips": 1.0, "k": 1.0, "lb": 0.001, "lbs": 0.001, "kn": 0.224809}),
    "length": ("in", {"in": 1.0, "ft": 12.0, "mm": 0.0393701, "m": 39.3701}),
    "ratio": ("", {"": 1.0, "g": 1.0}),
    "number": ("", {"": 1.0}),
}


def to_float(text: str) -> float:
    return float(text.replace(",", "").replace("−", "-"))


def normalize(value: float, unit: str | None, dimension: str) -> tuple[float, str]:
    """Convert value in `unit` to the canonical unit of `dimension`."""
    canon, factors = DIMENSIONS.get(dimension, DIMENSIONS["number"])
    u = (unit or "").strip().lower().replace(" ", "")
    if not u:
        return value, canon
    if u not in factors:
        raise ValueError(f"unit '{unit}' is not valid for dimension '{dimension}'")
    return value * factors[u], canon


_QTY = re.compile(r"^\s*" + NUM_RE + r"\s*(?P<unit>[A-Za-z/²0-9]*)\s*$")


def parse_quantity(text: str | float | int, dimension: str, default_unit: str = "") -> tuple[float, str]:
    """Parse '4000 psi', '4 ksi', 1.2 ... into (canonical value, canonical unit)."""
    if isinstance(text, (int, float)):
        return normalize(float(text), default_unit, dimension)
    m = _QTY.match(str(text))
    if not m:
        raise ValueError(f"cannot parse quantity '{text}'")
    return normalize(to_float(m.group("value")), m.group("unit") or default_unit, dimension)


def fmt(value: float | str, unit: str = "") -> str:
    """Human formatting: 4500 -> '4,500 psf', 1.15 -> '1.15'."""
    if isinstance(value, str):
        return value
    if abs(value) >= 1000 and abs(value - round(value)) < 1e-6:
        s = f"{value:,.0f}"
    else:
        s = f"{value:.4g}"
    return f"{s} {unit}".strip()
