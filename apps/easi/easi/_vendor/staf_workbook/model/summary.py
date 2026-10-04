"""The summary block every app shows, on the page and on the workbook's Summary tab.

Region names come from ``data/ecoregions.json``, generated from EASI's bundled Level III
crosswalk and NARS-9 regions by ``scripts/build_ecoregion_table.py`` (each Level III ecoregion
lies at least 97.7% inside one NARS-9 region; the table holds that majority region). EASI passes
its own polygon answers and only takes the names from here.
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DATA = Path(__file__).resolve().parent.parent / "data"
TIERS = {"EASI": "Screening", "SFARI": "Rapid", "DEEP": "Detailed"}
#: EPA / CEC Level I ecoregions of North America
L1_NAMES = {"1": "Arctic Cordillera", "2": "Tundra", "3": "Taiga", "4": "Hudson Plain", "5": "Northern Forests",
            "6": "Northwestern Forested Mountains", "7": "Marine West Coast Forest", "8": "Eastern Temperate Forests",
            "9": "Great Plains", "10": "North American Deserts", "11": "Mediterranean California",
            "12": "Southern Semiarid Highlands", "13": "Temperate Sierras", "14": "Tropical Dry Forests",
            "15": "Tropical Wet Forests"}
NARS9_NAMES = {"CPL": "Coastal Plains", "NAP": "Northern Appalachians", "NPL": "Northern Plains",
               "SAP": "Southern Appalachians", "SPL": "Southern Plains", "TPL": "Temperate Plains",
               "UMW": "Upper Midwest", "WMT": "Western Mountains", "XER": "Xeric"}
NOT_AVAILABLE = "Not available"


@functools.lru_cache(maxsize=1)
def _table() -> dict:
    path = DATA / "ecoregions.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("l3", {})


def clean_code(code) -> Optional[str]:
    if code is None:
        return None
    text = str(code).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text or None


def regions_for_l3(l3) -> dict:
    """``{l3, l3_name, l2, l2_name, l1, l1_name, nars9, nars9_name}`` for a Level III code."""
    code = clean_code(l3)
    entry = _table().get(code or "", {})
    l1 = entry.get("l1")
    nars = entry.get("nars9")
    return {"l3": code if entry else code, "l3_name": entry.get("name"), "l2": entry.get("l2"),
            "l2_name": entry.get("l2_name"), "l1": l1, "l1_name": L1_NAMES.get(l1 or ""),
            "nars9": nars, "nars9_name": NARS9_NAMES.get(nars or "")}


def _region(code, name) -> str:
    if not code and not name:
        return NOT_AVAILABLE
    if code and name:
        return f"{name} ({code})"
    return str(name or code)


@dataclass
class SummaryInfo:
    app: str                                  # "EASI", "SFARI", "DEEP"
    reach_name: Optional[str] = None
    reach_length_ft: Optional[float] = None
    drainage_area_sqkm: Optional[float] = None
    stream_order: Optional[int] = None
    regions: dict = field(default_factory=dict)  # regions_for_l3() shape (EASI may override codes)

    @property
    def tier(self) -> str:
        return TIERS.get(self.app.upper(), "")

    def rows(self) -> list:
        r = self.regions or {}
        length = f"{float(self.reach_length_ft):,.0f} ft" if _num(self.reach_length_ft) else NOT_AVAILABLE
        area = f"{float(self.drainage_area_sqkm):,.2f} km²" if _num(self.drainage_area_sqkm) else NOT_AVAILABLE
        order = str(int(self.stream_order)) if _num(self.stream_order) and float(self.stream_order) > 0 else NOT_AVAILABLE
        return [("Reach name", (self.reach_name or "").strip() or "Unnamed"),
                ("Assessment tier", self.tier),
                ("Reach length", length),
                ("Drainage area", area),
                ("Stream order (Strahler)", order),
                ("NARS-9 region", _region(r.get("nars9"), r.get("nars9_name"))),
                ("EPA Level I ecoregion", _region(r.get("l1"), r.get("l1_name"))),
                ("EPA Level II ecoregion", _region(r.get("l2"), r.get("l2_name"))),
                ("EPA Level III ecoregion", _region(r.get("l3"), r.get("l3_name")))]


def _num(v) -> bool:
    try:
        return v is not None and not isinstance(v, bool) and float(v) == float(v)
    except (TypeError, ValueError):
        return False
