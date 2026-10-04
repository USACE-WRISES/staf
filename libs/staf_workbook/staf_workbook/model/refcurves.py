"""What the ReferenceCurves tab shows: curves (drawn as charts with their points beside them) and
plain tables (rating bands, scoring criteria)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CurveBlock:
    title: str                        # the metric
    subtitle: str = ""                # which curve (region, stratum) and what uses it
    x_label: str = ""
    y_label: str = "Index"
    series: list = field(default_factory=list)   # [(name, [(x, y), ...]), ...]
    note: str = ""


@dataclass
class TableBlock:
    title: str
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    note: str = ""
    widths: list = field(default_factory=list)


@dataclass
class RefCurves:
    intro: str = ""
    blocks: list = field(default_factory=list)
