"""Fixed criteria for landscape pressure metrics (rule CURVE-11, methodology 0.12).

A pressure metric reads something whose reference condition is its absence:
impervious cover, agriculture, road density, flow regulation, dams. Fitting a
regional reference curve to one restates the reference screen, because the
least-disturbed stations are by definition the ones without the pressure. The
published curves showed the cost: 0.03 percent cropland scored zero in the
Northeastern Highlands, and 85 percent row crops scored Functioning in the
Eastern Corn Belt Plains, while EASI rated the same watersheds the other way.

So these metrics are scored on EASI's own cited criteria, identical in every
region, drawn as one continuous curve per metric (owner decision 2026-09-19).
The criteria are read from the vendored EASI scoring catalog by
``scripts/build_fixed_criteria.py`` into ``config/fixed_criteria.yaml``, and
:func:`criteria_drift` fails loudly when the two disagree.

HOW A THREE-BAND CRITERION BECOMES A CURVE
  DEEP classes an index with ``<=``: Non-Functioning at or below 0.39,
  Functioning-at-Risk at or below 0.69, Functioning above. The curve passes
  through index 0.69 at EASI's Good/Fair boundary and 0.39 at its Fair/Poor
  boundary, is 1.0 where the pressure is absent, and reaches zero where the
  line through the two boundaries does. Each anchor sits exactly on the
  boundary when the worse class owns the boundary value in EASI, and half a
  reporting step toward the worse side when the better class owns it, so a
  value on a boundary lands in the same class in both tiers.

  DEEP also classes a FUNCTION score (mean index times 15) at 5 and 10, which
  is 0.333 and 0.667 on the index scale, not 0.39 and 0.69. For a function
  served by one fixed metric, values just past a boundary can therefore read
  one class on the metric and the next on the function. That is DEEP's own
  pair of band systems, not a property of these curves.

Pure: file reads and arithmetic.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from . import methodology
from .config import read_yaml
from .paths import CONFIG_DIR

FIXED_CRITERIA_PATH = CONFIG_DIR / "fixed_criteria.yaml"
VENDORED_CATALOG_PATH = (Path(__file__).resolve().parent / "_vendor" / "easi" / "data"
                         / "screening-methods.json")

CRITERIA_BASIS = "fixed"
METRIC_ROLE = "pressure_fixed"
CONFIDENCE_LABEL = "Fixed criteria"

# Which EASI criterion each DEEP pressure metric inherits. ``input`` names the
# catalog input when the bands sit on one input of a multi-input method.
SPEC: dict[str, dict] = {
    "pctimp2019ws": {
        "easi_method": "catchment-land-cover-pressure", "input": "impervious",
        "display_name": "Impervious surface", "units": "%", "resolution": 0.01,
        "domain": [0.0, 100.0], "metric_family": "proportion",
        "streamcat": "pctimp2019",
        "functions": ["Catchment hydrology", "High flow dynamics"],
    },
    "pctag2019ws": {
        "easi_method": "catchment-land-cover-pressure", "input": "agriculture",
        "display_name": "Agriculture, crop and hay", "units": "%", "resolution": 0.01,
        "domain": [0.0, 100.0], "metric_family": "proportion",
        "streamcat": "pctag2019",
        "functions": ["Catchment hydrology"],
    },
    "rddensws": {
        "easi_method": "road-density-inflow-pressure", "input": None,
        "display_name": "Road density", "units": "km/sq km", "resolution": 0.0001,
        "domain": [0.0, None], "metric_family": "continuous",
        "streamcat": "rddens",
        "functions": ["Reach inflow"],
    },
    "dorws": {
        "easi_method": "degree-of-regulation", "input": None,
        "display_name": "Degree of regulation", "units": "%", "resolution": 0.01,
        "domain": [0.0, None], "metric_family": "continuous",
        "streamcat": "dor",
        "functions": ["Streamflow regime"],
    },
    "nid_dams_1mi": {
        "easi_method": "nearby-dam-proximity", "input": None, "count": True,
        "display_name": "Mapped dams within one mile", "units": "dams", "resolution": 1.0,
        "domain": [0.0, None], "metric_family": "count",
        "streamcat": None,
        "functions": ["Watershed connectivity"],
    },
}

_RANK = {"Good": 0, "Fair": 1, "Poor": 2}


# --------------------------------------------------------------------------- #
# generation from the vendored EASI catalog
# --------------------------------------------------------------------------- #
def _vendored_catalog() -> dict:
    return json.loads(VENDORED_CATALOG_PATH.read_text(encoding="utf-8"))


def catalog_sha256() -> str:
    return "sha256:" + hashlib.sha256(VENDORED_CATALOG_PATH.read_bytes()).hexdigest()


def _method(catalog: dict, key: str) -> dict:
    for m in catalog.get("methods") or []:
        if m.get("methodKey") == key:
            return m
    raise KeyError(f"the vendored EASI catalog has no method {key!r}")


def _bands_of(method: dict, input_key: Optional[str]) -> tuple[list[dict], dict]:
    """``(bands, source)``: the input's bands when ``input_key`` names one, else
    the method's own."""
    if input_key:
        for inp in method.get("inputs") or []:
            if inp.get("key") == input_key and inp.get("bands"):
                return list(inp["bands"]), inp
        raise KeyError(f"method {method.get('methodKey')!r} has no banded input {input_key!r}")
    if not method.get("bands"):
        raise KeyError(f"method {method.get('methodKey')!r} has no bands")
    return list(method["bands"]), method


def _band(bands: list[dict], rating: str) -> dict:
    for b in bands:
        if b.get("rating") == rating:
            return b
    raise KeyError(f"no {rating} band")


def _index_bands() -> tuple[float, float]:
    lo, hi = methodology.threshold("curve_rules.deep_index_bands", [0.39, 0.69])
    return float(lo), float(hi)


def _anchors(bands: list[dict], resolution: float) -> dict:
    """Direction and the two anchor positions for a three-band criterion."""
    good, fair, poor = _band(bands, "Good"), _band(bands, "Fair"), _band(bands, "Poor")
    half = float(resolution) / 2.0
    if good.get("min") is None:                       # lower is better
        b1, b2 = float(fair["min"]), float(poor["min"])
        # the better class owns a boundary when its own upper edge is inclusive
        x1 = b1 + half if good.get("maxInclusive") else b1
        x2 = b2 + half if fair.get("maxInclusive") and not poor.get("minInclusive") else b2
        return {"direction": "lower_better", "good_fair": b1, "fair_poor": b2,
                "anchor_good_fair": x1, "anchor_fair_poor": x2}
    b_low, b_high = float(fair["min"]), float(good["min"])    # higher is better
    x_low = b_low - half if fair.get("minInclusive") and not poor.get("maxInclusive") else b_low
    x_high = b_high - half if good.get("minInclusive") else b_high
    return {"direction": "higher_better", "good_fair": b_high, "fair_poor": b_low,
            "anchor_good_fair": x_high, "anchor_fair_poor": x_low}


def _points(entry: dict, rating_index: dict) -> list[list[float]]:
    lo, hi = _index_bands()
    if entry.get("count"):
        return [[0.0, 1.0], [1.0, float(rating_index["Fair"])],
                [2.0, float(rating_index["Poor"])], [3.0, 0.0]]
    x1, x2 = float(entry["anchor_good_fair"]), float(entry["anchor_fair_poor"])
    d_min, d_max = (entry.get("domain") or [None, None])
    slope = (lo - hi) / (x2 - x1)                     # index per unit along the Fair segment
    if entry["direction"] == "lower_better":
        x_zero = x2 - lo / slope
        pts = [[float(d_min if d_min is not None else 0.0), 1.0], [x1, hi], [x2, lo]]
        if d_max is not None and x_zero > float(d_max):
            pts.append([float(d_max), round(lo + slope * (float(d_max) - x2), 6)])
        else:
            pts.append([x_zero, 0.0])
        return [[round(x, 6), round(y, 6)] for x, y in pts]
    x_zero = x2 - lo / slope                          # slope is positive here
    x_one = x1 + (1.0 - hi) / slope
    floor = float(d_min if d_min is not None else 0.0)
    first = [floor, round(max(0.0, lo + slope * (floor - x2)), 6)] if x_zero < floor \
        else [x_zero, 0.0]
    pts = [first, [x2, lo], [x1, hi]]
    if d_max is not None and x_one > float(d_max):
        pts.append([float(d_max), round(hi + slope * (float(d_max) - x1), 6)])
    else:
        pts.append([x_one, 1.0])
    return [[round(x, 6), round(y, 6)] for x, y in pts]


def generate(catalog: Optional[dict] = None) -> dict:
    """The full ``fixed_criteria.yaml`` document, from the vendored catalog."""
    catalog = catalog if catalog is not None else _vendored_catalog()
    citations = catalog.get("citations") or {}
    rating_index = catalog.get("ratingIndex") or {"Good": 0.85, "Fair": 0.545, "Poor": 0.195}
    lo, hi = _index_bands()
    metrics: dict[str, dict] = {}
    for key, spec in SPEC.items():
        method = _method(catalog, spec["easi_method"])
        bands, _src = _bands_of(method, spec.get("input"))
        entry: dict[str, Any] = {
            "display_name": spec["display_name"], "units": spec["units"],
            "metric_family": spec["metric_family"],
            "easi_method": spec["easi_method"], "easi_metric_id": method.get("metricId"),
            "easi_input": spec.get("input"), "easi_title": method.get("title"),
            "provisional": bool(method.get("provisional")),
            "resolution": spec["resolution"], "domain": list(spec["domain"]),
            "streamcat": spec.get("streamcat"),
            "functions": list(spec.get("functions") or []),
            "bands": [{"rating": b["rating"], "label": b.get("label"), "min": b.get("min"),
                       "max": b.get("max"), "minInclusive": bool(b.get("minInclusive")),
                       "maxInclusive": bool(b.get("maxInclusive"))}
                      for b in sorted(bands, key=lambda b: _RANK[b["rating"]])],
        }
        if spec.get("count"):
            entry["count"] = True
            entry["direction"] = "lower_better"
        else:
            entry.update(_anchors(bands, spec["resolution"]))
        entry["points"] = _points(entry, rating_index)
        entry["citations"] = [{"key": c, "text": (citations.get(c) or {}).get("title"),
                               "url": (citations.get(c) or {}).get("url")}
                              for c in (method.get("citations") or [])]
        entry["breakpoints"] = [
            {"label": b.get("label"), "description": b.get("description")}
            for b in (method.get("breakpoints") or [])
            if not spec.get("input") or b.get("input") in (None, spec.get("input"))]
        entry["limitations"] = list(method.get("limitations") or [])
        metrics[key] = entry
    return {
        "version": 1,
        "source": {"catalog": "streamcurves/_vendor/easi/data/screening-methods.json",
                   "catalog_sha256": catalog_sha256()},
        "construction": {"good_fair_index": hi, "fair_poor_index": lo, "best_index": 1.0,
                         "zero": "linear_extension_of_fair_segment",
                         "boundary_rule": "half_resolution_shift_when_better_class_owns"},
        "metrics": metrics,
    }


# --------------------------------------------------------------------------- #
# the committed file
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def load_fixed_criteria() -> dict:
    return read_yaml(FIXED_CRITERIA_PATH) or {}


def clear_cache() -> None:
    load_fixed_criteria.cache_clear()


def fixed_criteria_sha256() -> Optional[str]:
    p = FIXED_CRITERIA_PATH
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def metric_keys() -> list[str]:
    return list((load_fixed_criteria().get("metrics") or {}).keys())


def is_fixed(metric_key: str) -> bool:
    return str(metric_key) in (load_fixed_criteria().get("metrics") or {})


def entry_for(metric_key: str) -> dict:
    metrics = load_fixed_criteria().get("metrics") or {}
    if metric_key not in metrics:
        raise KeyError(f"{metric_key!r} is not a fixed-criteria metric")
    return metrics[metric_key]


def _walk(a: Any, b: Any, path: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k} is present on one side only")
            else:
                _walk(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path} has {len(a)} entries in the file and {len(b)} in the catalog")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _walk(x, y, f"{path}[{i}]", out)
    elif isinstance(a, float) or isinstance(b, float):
        try:
            if abs(float(a) - float(b)) > 1e-9:
                out.append(f"{path} is {a} in the file and {b} from the catalog")
        except (TypeError, ValueError):
            out.append(f"{path} is {a!r} in the file and {b!r} from the catalog")
    elif a != b:
        out.append(f"{path} is {a!r} in the file and {b!r} from the catalog")


def criteria_drift() -> list[str]:
    """Differences between the committed file and what the vendored EASI
    catalog generates today. Empty means none."""
    try:
        have, want = load_fixed_criteria(), generate()
    except Exception as exc:  # noqa: BLE001 - an unreadable side is itself the finding
        return [f"fixed_criteria: cannot compare ({exc})"]
    if not have:
        return ["fixed_criteria.yaml is missing; run scripts/build_fixed_criteria.py"]
    out: list[str] = []
    _walk(have, want, "fixed_criteria", out)
    return out


# --------------------------------------------------------------------------- #
# what the pipeline consumes
# --------------------------------------------------------------------------- #
def curve_points(entry: dict) -> pd.DataFrame:
    """The curve as the engine's point frame (``point_order``, ``metric_value``,
    ``index_score``)."""
    pts = entry.get("points") or []
    return pd.DataFrame({"point_order": list(range(1, len(pts) + 1)),
                         "metric_value": [float(p[0]) for p in pts],
                         "index_score": [float(p[1]) for p in pts]})


def interpolate(entry: dict, x: Any) -> Optional[float]:
    """DEEP's interpolation over the fixed curve (clamped, first match wins)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    pts = sorted(((float(p[0]), float(p[1])) for p in entry.get("points") or []),
                 key=lambda p: p[0])
    if not pts:
        return None
    if v <= pts[0][0]:
        return pts[0][1]
    if v >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if v <= x1:
            return y0 if x1 == x0 else y0 + (v - x0) / (x1 - x0) * (y1 - y0)
    return pts[-1][1]


def deep_class(index: Optional[float]) -> Optional[str]:
    """DEEP's class of an index (``<=`` at both cuts)."""
    if index is None:
        return None
    lo, hi = _index_bands()
    return "Non-Functioning" if index <= lo else "Functioning-at-Risk" if index <= hi \
        else "Functioning"


def class_of(entry: dict, x: Any) -> Optional[str]:
    """EASI's rating of a value, by the entry's own bands."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    for b in entry.get("bands") or []:
        lo, hi = b.get("min"), b.get("max")
        above = True if lo is None else (v >= lo if b.get("minInclusive") else v > lo)
        below = True if hi is None else (v <= hi if b.get("maxInclusive") else v < hi)
        if above and below:
            return b["rating"]
    return None


def metric_config_entries() -> dict[str, dict]:
    """``metric_config`` entries for the fixed metrics (never fitted)."""
    out = {}
    for key, e in (load_fixed_criteria().get("metrics") or {}).items():
        out[key] = {
            "column_name": key, "display_name": e["display_name"], "units": e["units"],
            "metric_family": e["metric_family"],
            "higher_is_better": e.get("direction") == "higher_better",
            "metric_role": METRIC_ROLE, "criteria_basis": CRITERIA_BASIS,
            "domain_min": (e.get("domain") or [None, None])[0],
            "domain_max": (e.get("domain") or [None, None])[1],
            "notes": criteria_sentence(e),
        }
    return out


def criteria_sentence(entry: dict) -> str:
    """One plain sentence stating the criterion, for the metric's tooltip."""
    by = {b["rating"]: b.get("label") for b in entry.get("bands") or []}
    unit = "" if entry.get("count") else f" ({entry.get('units')})"
    return (f"{entry['display_name']}{unit} is scored on the same fixed criteria EASI uses in "
            f"every region. Good {by.get('Good')}, Fair {by.get('Fair')}, Poor {by.get('Poor')}.")


def citation_line(entry: dict) -> str:
    """What a fixed metric cites in place of the regional analysis."""
    return f"EASI fixed criteria, {entry.get('easi_title') or entry.get('display_name')}"


def criteria_source(entry: dict) -> dict:
    """The ``criteriaSource`` block of a published bundle entry."""
    return {"title": entry.get("easi_title"), "easiMethod": entry.get("easi_method"),
            "provisional": bool(entry.get("provisional")),
            "bands": [{"rating": b["rating"], "label": b.get("label")}
                      for b in entry.get("bands") or []],
            "citations": [{"key": c.get("key"), "text": c.get("text")}
                          for c in entry.get("citations") or []],
            "limitations": list(entry.get("limitations") or [])}


def curve_row(metric_key: str) -> dict:
    """A curve row shaped like the engine's, so the exporter treats a fixed
    metric like any other curve. ``n_reference`` is None by design."""
    e = entry_for(metric_key)
    return {"metric": metric_key, "display_name": e["display_name"], "stratum": "",
            "curve_status": "complete", "curve_source": "fixed_criteria",
            "n_reference": None, "curve_points": curve_points(e)}


def mapping_rows(metric_keys=None) -> pd.DataFrame:
    """Discipline / function assignments of the fixed metrics, in the shape of the
    workbench mapping frame (``metric_key``, ``discipline``, ``function_label``,
    ``sort_order``). The assignments are part of the criterion, not an editable
    choice: a fixed metric scores the function EASI scores with it."""
    from .staf_library import staf_canonical_function
    rows = []
    metrics = load_fixed_criteria().get("metrics") or {}
    for key in (list(metric_keys) if metric_keys is not None else list(metrics)):
        for label in (metrics.get(key) or {}).get("functions") or []:
            canon = staf_canonical_function(label)
            if canon is None:
                raise KeyError(f"fixed metric {key!r} names an unknown function {label!r}")
            rows.append({"metric_key": key, "discipline": canon["discipline"],
                         "function_label": canon["name"]})
    out = pd.DataFrame(rows, columns=["metric_key", "discipline", "function_label"])
    out["sort_order"] = range(1, len(out) + 1)
    return out
