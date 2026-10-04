"""Comparing scenarios: the ECI, the three sub-indices and every function, with the change from
Existing Conditions. A value may be an interval (DEEP's coverage-limited ECI), and a change
between intervals is the range of possible differences."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

SUB_INDICES = (("physical", "Physical"), ("chemical", "Chemical"), ("biological", "Biological"))


def excel_round(x: float, digits: int) -> float:
    """Excel's ROUND (half away from zero), so stored results match what Excel shows."""
    q = Decimal(1).scaleb(-digits)
    d = Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP)
    return float(d)


@dataclass(frozen=True)
class Measure:
    value: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None

    @property
    def is_interval(self) -> bool:
        return self.value is None and self.low is not None and self.high is not None

    @property
    def empty(self) -> bool:
        return self.value is None and not self.is_interval


@dataclass
class ScenarioScores:
    eci: Measure = Measure()
    sub: dict = field(default_factory=dict)           # physical/chemical/biological -> Measure
    functions: dict = field(default_factory=dict)     # function id -> Measure


@dataclass
class Row:
    key: str
    label: str
    group: str                      # "Index", or the function's category
    digits: int
    values: list                    # Measure per scenario
    deltas: list                    # per alternative: (low, high) or None; low == high for points


@dataclass
class Comparison:
    names: list
    rows: list


def _delta(a: Measure, b: Measure, digits: int):
    """Change from ``a`` (baseline) to ``b``, on the values as displayed."""
    if a.empty or b.empty:
        return None
    lo_a = excel_round(a.low if a.is_interval else a.value, digits)
    hi_a = excel_round(a.high if a.is_interval else a.value, digits)
    lo_b = excel_round(b.low if b.is_interval else b.value, digits)
    hi_b = excel_round(b.high if b.is_interval else b.value, digits)
    if a.is_interval or b.is_interval:
        return (round(lo_b - hi_a, digits), round(hi_b - lo_a, digits))
    d = round(lo_b - lo_a, digits)
    return (d, d)


def build(names: list, scores: list, functions: list, *, index_digits: int = 2, function_digits: int = 0) -> Comparison:
    """``functions`` = [(function id, label, category), ...] in display order."""
    rows = [Row("eci", "ECI", "Index", index_digits, [s.eci for s in scores], [])]
    for key, label in SUB_INDICES:
        rows.append(Row(key, label, "Index", index_digits, [s.sub.get(key, Measure()) for s in scores], []))
    for fid, label, category in functions:
        rows.append(Row(f"fn:{fid}", label, category or "", function_digits,
                        [s.functions.get(fid, Measure()) for s in scores], []))
    for row in rows:
        row.deltas = [_delta(row.values[0], v, row.digits) for v in row.values[1:]]
    return Comparison(list(names), rows)


def fmt_measure(m: Measure, digits: int) -> str:
    if m.empty:
        return ""
    if m.is_interval:
        return f"{excel_round(m.low, digits):.{digits}f} to {excel_round(m.high, digits):.{digits}f}"
    return f"{excel_round(m.value, digits):.{digits}f}"


def fmt_delta(d, digits: int) -> str:
    if d is None:
        return ""
    lo, hi = d

    def one(v):
        if abs(v) < 0.5 * 10 ** -digits:
            return f"{0:.{digits}f}"
        return f"{v:+.{digits}f}"
    return one(lo) if lo == hi else f"{one(lo)} to {one(hi)}"


def delta_sign(d) -> int:
    """+1 better, -1 worse, 0 unchanged or mixed (for colouring)."""
    if d is None:
        return 0
    lo, hi = d
    if lo > 0 and hi > 0:
        return 1
    if lo < 0 and hi < 0:
        return -1
    return 0
