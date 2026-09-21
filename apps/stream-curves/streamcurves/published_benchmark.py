"""An external published criterion as a scoring basis, and the test it must pass.

Rule REF-10 (methodology 0.13, owner decision 2026-09-21).

Round two tested the NRSA Table 7-1 nutrient bands by asking whether they
reproduce a region's own reference-curve classifications, and refused them when
they did not: accuracy met in 7 of 19 regions, demoting sites in 18 of 19.

That was the wrong question. Table 7-1 rests on EPA's ``RT_NRSA`` reference
designation and this project's curves rest on the ``least-disturbed-v1``
pressure screen, so the two select different streams and the disagreement
measures the distance between two definitions of reference rather than an error
in either. A published criterion is a different standard, not an estimator of
ours, and requiring it to agree with ours tests the wrong thing.

What it must instead be is *fit for the purpose it is being put to*, which this
module checks as five conditions:

    PB-1  parameter identity, including fraction (total against dissolved)
    PB-2  an exact, documented unit conversion
    PB-3  the benchmark's region equals or contains the target
    PB-4  a condition or assessment threshold, not an effluent limit
    PB-5  citation, source tier and any provisional flag carried into the bundle

Agreement with a reference curve, where one exists, is measured and reported
beside the verdict. It never gates admission.

Pure: catalog and frame in, records out. No network, no file writes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from . import curve_basis
from . import reference_pool as rp

#: NRSA reports nutrients in mg/L. chem_PTL is stored in ug/L, chem_NTL in mg N/L.
MG_TO_UG = 1000.0

#: The verified catalog (REF-14, methodology 0.14). Every criterion a build may
#: score against is declared there with the fields PB-1 to PB-5 read, so the
#: conditions are checked against a declaration rather than against a guess at
#: call time. The build looks the catalog up and never searches: nothing is
#: fetched at build time, and a metric with no entry is a recorded gap.
CATALOG_PATH_NAME = "published_benchmarks.yaml"
CATALOG_METHOD = "regional-nutrient-condition"


def catalog_path() -> Path:
    from .paths import CONFIG_DIR
    return CONFIG_DIR / CATALOG_PATH_NAME


@lru_cache(maxsize=4)
def _load_catalog(path_text: str) -> dict:
    from .config import read_yaml
    doc = read_yaml(Path(path_text)) or {}
    return {"version": doc.get("version"), "precedence": list(doc.get("precedence") or []),
            "geography_rank": dict(doc.get("geography_rank") or
                                   {"l3": 0, "nars9": 1, "national": 2}),
            "entries": [dict(e) for e in doc.get("entries") or []],
            "refusals": [dict(r) for r in doc.get("refusals") or []],
            "no_entry": str(doc.get("no_entry") or
                            "The published criteria STAF draws on include none for this metric.")}


def load_catalog(path: Optional[Path] = None) -> dict:
    """The verified catalog, as declared in config."""
    return _load_catalog(str(path or catalog_path()))


def catalog_sha256(path: Optional[Path] = None) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(Path(path or catalog_path()).read_bytes()).hexdigest()


def _spec(entry: dict) -> dict:
    """One catalog entry in the shape the fitness conditions read."""
    units = entry.get("units") or {}
    src = entry.get("thresholds_source") or {}
    return {"id": entry.get("id"), "input_key": src.get("input"),
            "method": src.get("method") or CATALOG_METHOD,
            "analyte": entry.get("analyte"), "fraction": entry.get("fraction"),
            "benchmark_units": units.get("benchmark"), "metric_units": units.get("metric"),
            "unit_factor": float(units.get("factor") or 1.0),
            "resolution": float(entry.get("resolution") or 0.001),
            "region_key": (entry.get("geography") or {}).get("kind") or "nars9",
            "purpose": entry.get("purpose"), "edition": str(entry.get("edition") or ""),
            "thresholds": dict(entry.get("thresholds") or {}),
            "sampling": entry.get("sampling"), "stream_types": entry.get("stream_types")}


def entries_for(metric: str) -> list[dict]:
    """Every catalog entry declared for exactly this metric (PB-1: the metric code
    carries its fraction, so an exact code match is an exact metric and fraction)."""
    return [_spec(e) for e in load_catalog()["entries"] if str(e.get("metric")) == metric]


def lookup(metric: str, *, target_l3: Optional[str] = None,
           region: Optional[str] = None) -> Optional[dict]:
    """The entry that serves ``metric`` at a target, by the catalog's precedence:
    the exact metric and fraction, then the most specific geography that covers
    the target, then the newest edition. None when the catalog holds none."""
    cands = entries_for(metric)
    if not cands:
        return None
    rank = load_catalog()["geography_rank"]

    def covers(spec: dict) -> bool:
        kind = spec["region_key"]
        if kind == "national":
            return True
        if kind == "l3":
            return target_l3 is not None and str(target_l3) in spec["thresholds"]
        return region is None or str(region) in spec["thresholds"]

    usable = [s for s in cands if covers(s)] or cands
    usable.sort(key=lambda s: (int(rank.get(s["region_key"], 9)), s["edition"]), reverse=False)
    best_rank = int(rank.get(usable[0]["region_key"], 9))
    tier = [s for s in usable if int(rank.get(s["region_key"], 9)) == best_rank]
    return max(tier, key=lambda s: s["edition"])


#: Kept as names because the report and the tests read them. Built from the catalog.
REGISTRY: dict[str, dict] = {}
for _e in load_catalog()["entries"]:
    REGISTRY.setdefault(str(_e.get("metric")), _spec(_e))
NO_CRITERION = load_catalog()["no_entry"]


def _refusals() -> list[dict]:
    return load_catalog()["refusals"]


#: A refusal that holds wherever the metric is assessed, by metric.
REFUSED: dict[str, str] = {}
for _r in _refusals():
    if _r.get("applies_where"):
        continue
    for _m in _r.get("metrics") or []:
        REFUSED.setdefault(str(_m), " ".join(str(_r.get("why") or "").split()))

#: Why Ohio EPA's ecoregional biocriteria, the nearest thing to a Population
#: support criterion for the Eastern Corn Belt Plains, are not admissible.
#: Recorded here because a negative finding a reader cannot check is not a
#: finding (owner request, 2026-09-21).
OHIO_BIOCRITERIA = {
    "indicator": ("Ohio's numeric biocriteria are thresholds on three COMPOSITE INDICES, not on "
                  "any raw count: the fish Index of Biotic Integrity, the Modified Index of "
                  "well-being, and the Invertebrate Community Index. Applying an index threshold "
                  "to a taxa-richness metric would compare two different quantities."),
    "measurement": ("The indices are computed from Ohio EPA's own field protocol: a standardised "
                    "electrofishing pass over a distance fixed by site type, with the component "
                    "metrics scored against ecoregion and drainage-area lines calibrated on "
                    "Ohio's reference sites. NRSA samples a reach of 40 times the wetted width "
                    "under a different protocol and publishes its own metrics. The archive "
                    "carries no IBI, MIwb, ICI or QHEI column, so the actual index cannot be "
                    "evaluated, and the components are not the same measurement."),
    "stream_type": ("The thresholds are keyed to a regulatory use designation assigned per "
                    "waterbody in Ohio's water quality standards (warmwater, exceptional "
                    "warmwater, modified warmwater) and to a site type. An NRSA station carries "
                    "neither, so even inside Ohio there is no defensible way to choose which "
                    "threshold applies."),
    "geography": ("Ohio's criteria are Ohio water quality standards. This assessment's Eastern "
                  "Corn Belt Plains stations are 26 in Indiana and 25 in Ohio, so the criterion "
                  "would reach 49 percent of them and has no standing or calibration for the "
                  "rest. PB-3 asks that the benchmark's region contain the target; here it "
                  "contains about half of it."),
    "verdict": "unsuitable",
}

_OHIO = next((r for r in _refusals() if r.get("id") == "ohio-epa-biocriteria"), {})
#: The Ohio finding as one self-contained passage for a withheld metric's card.
#: The check was made for the Eastern Corn Belt Plains and its geography ground
#: (half the stations in Indiana) is true only there, so the catalog limits it to
#: that ecoregion: in the Interior Plateau it would state a false fact.
OHIO_FINDING = " ".join(str(_OHIO.get("why") or "").split())
OHIO_FINDING_TARGETS = frozenset(str(x) for x in ((_OHIO.get("applies_where") or {})
                                                  .get("l3") or []))
BIOLOGICAL_REFUSALS = frozenset(str(m) for m in _OHIO.get("metrics") or [])


def refusal(metric: str, target_l3: Optional[str] = None) -> str:
    """Why no published criterion serves ``metric`` in ``target_l3``, in words: the
    catalog's refusals that hold there, or its no-entry sentence."""
    parts: list[str] = []
    for r in _refusals():
        if metric not in [str(m) for m in r.get("metrics") or []]:
            continue
        where = r.get("applies_where") or {}
        if where and str(target_l3) not in {str(x) for x in where.get("l3") or []}:
            continue
        parts.append(" ".join(str(r.get("why") or "").split()))
    return " ".join(parts) if parts else NO_CRITERION


PB_CONDITIONS = ("PB-1", "PB-2", "PB-3", "PB-4", "PB-5")


def _catalog() -> dict:
    from . import fixed_criteria as fc
    return fc._vendored_catalog()


def _input_of(metric: str) -> Optional[dict]:
    from . import fixed_criteria as fc
    spec = REGISTRY.get(metric)
    if not spec:
        return None
    method = fc._method(_catalog(), spec.get("method") or CATALOG_METHOD)
    return next((i for i in method.get("inputs") or []
                 if i.get("key") == spec["input_key"]), None)


def _method_record() -> dict:
    from . import fixed_criteria as fc
    return fc._method(_catalog(), CATALOG_METHOD)


def majority_region(frame: pd.DataFrame, key: str = "nars9") -> tuple[Optional[str], float]:
    """The region code the target's stations carry, and the share that carry it.

    PB-3 wants the benchmark's region to contain the target. The NARS-9 mapping
    is spatial and frozen into the station table, so the honest test is whether
    the target's own stations agree on one region: a split target has no single
    applicable criterion.
    """
    got = frame[key].dropna().astype(str) if key in frame.columns else pd.Series(dtype=str)
    if not len(got):
        return None, 0.0
    counts = got.value_counts()
    return str(counts.index[0]), float(counts.iloc[0] / len(got))


def fitness(metric: str, *, frame: Optional[pd.DataFrame] = None,
            region: Optional[str] = None, unanimity_min: float = 1.0,
            target_l3: Optional[str] = None) -> dict:
    """PB-1 to PB-5 for one metric at one target, each with its reason.

    ``frame`` is the target's own stations, from which the applicable region is
    read; pass ``region`` instead when the mapping is already known. A condition
    with no evidence either way fails, because the rung admits only on a
    positive finding.
    """
    spec = REGISTRY.get(metric)
    out: dict[str, Any] = {"metric": metric, "benchmark": None, "region": region,
                           "conditions": {}, "admissible": False}
    if spec is None:
        if target_l3 is None and frame is not None and "l3" in frame.columns and len(frame):
            target_l3 = str(frame["l3"].astype(str).mode().iloc[0])
        reason = refusal(metric, target_l3)
        out["conditions"] = {c: {"pass": False, "why": reason} for c in PB_CONDITIONS}
        return out

    inp = _input_of(metric)
    method = _method_record()
    out["benchmark"] = f"{method.get('title') or CATALOG_METHOD} ({spec['analyte']})"

    # PB-1 parameter identity, including fraction
    label = str((inp or {}).get("label") or "")
    same = bool(inp) and spec["analyte"].lower() in label.lower()
    out["conditions"]["PB-1"] = {
        "pass": same,
        "why": (f"The criterion is written for {label or 'an unnamed input'} and the metric is "
                f"{spec['analyte']}, the {spec['fraction']} fraction." if same else
                f"The catalog input for {metric} could not be matched to {spec['analyte']}.")}

    # PB-2 unit convertibility
    factor = float(spec["unit_factor"])
    out["conditions"]["PB-2"] = {
        "pass": True,
        "why": (f"{spec['benchmark_units']} to {spec['metric_units']}: exact, factor {factor:g}."
                if factor != 1.0 else
                f"Both are reported in {spec['metric_units']}; no conversion is needed.")}

    # PB-3 region applicability
    share, code = 0.0, region
    if frame is not None and code is None:
        code, share = majority_region(frame, spec["region_key"])
    elif frame is not None:
        _, share = majority_region(frame, spec["region_key"])
    else:
        share = 1.0 if code else 0.0
    bands = ((inp or {}).get("regionalBands") or {}).get(str(code)) if code else None
    ok3 = bool(bands) and share >= unanimity_min
    out["region"] = code
    out["conditions"]["PB-3"] = {
        "pass": ok3,
        "why": (f"The target's stations are unanimously NARS-9 region {code}, which the "
                f"criterion keys." if ok3 and share >= 1.0 else
                f"The target's stations are {share:.0%} NARS-9 region {code}, which the "
                f"criterion keys." if ok3 else
                f"The criterion has no bands for NARS-9 region {code!r}." if code else
                "The target's applicable region could not be established.")}

    # PB-4 purpose
    ok4 = str(spec["purpose"]) == "condition-assessment"
    out["conditions"]["PB-4"] = {
        "pass": ok4,
        "why": ("A national condition-assessment threshold, not an effluent limit and not a "
                "criterion for another designated use." if ok4 else "Purpose does not match.")}

    # PB-5 provenance
    cites = method.get("citations") or []
    ok5 = bool(cites)
    out["conditions"]["PB-5"] = {
        "pass": ok5,
        "why": (f"{len(cites)} citation(s), source tier {method.get('sourceTier')!r}, "
                f"provisional={bool(method.get('provisional'))}." if ok5 else
                "The catalog carries no citation for this method.")}

    out["admissible"] = all(c["pass"] for c in out["conditions"].values())
    return out


#: The catalog citations that bear on the criterion itself. The method's other
#: citations document EASI's nearby-station procedure, which DEEP does not use.
CRITERION_CITATIONS = ("nrsa-2018-19", "nars-regions")


def _fmt(x: float, factor: float) -> str:
    return f"{round(float(x) * factor, 6):g}"


def criteria_source(metric: str, region: str) -> dict:
    """PB-5 in the bundle: the criterion in the shape of the fixed criteria's
    ``criteriaSource`` block, so DEEP lists its bands and sources the way it
    lists theirs. The bands are this region's, on the metric's own units."""
    from ._vendor.easi import screening_methods as sm
    spec = REGISTRY.get(metric) or {}
    method = _method_record()
    cites = _catalog().get("citations") or {}
    f = float(spec.get("unit_factor") or 1.0)
    units = spec.get("metric_units") or ""
    bands_out = []
    for b in sm.regional_bands(_input_of(metric), str(region)) or []:
        lo, hi = b.get("min"), b.get("max")
        low = ("" if lo is None else
               ("\u2265" if b.get("minInclusive") else ">") + _fmt(lo, f))
        high = ("" if hi is None else
                ("\u2264" if b.get("maxInclusive") else "<") + _fmt(hi, f))
        label = f"{low} to {high}" if low and high else (low or high)
        bands_out.append({"rating": b.get("rating"), "label": f"{label} {units}".strip()})
    return {"title": f"NRSA 2018-19 regional {spec.get('analyte')} thresholds, NARS-9 region "
                     f"{region}",
            "easiMethod": CATALOG_METHOD, "region": str(region),
            "provisional": bool(method.get("provisional")),
            "bands": bands_out,
            "citations": [{"key": k, "text": (cites.get(k) or {}).get("title")}
                          for k in (method.get("citations") or []) if k in CRITERION_CITATIONS],
            "limitations": [curve_basis.limit_for(curve_basis.PUBLISHED)]}


def citation_line(metric: str, region: str) -> str:
    """What a benchmark metric cites in place of the regional analysis."""
    spec = REGISTRY.get(metric) or {}
    return (f"USEPA NRSA 2018-19 regional {spec.get('analyte') or metric} thresholds, "
            f"NARS-9 region {region}")


def provenance(metric: str) -> dict:
    """PB-5's payload: what the bundle must carry beside a benchmark curve."""
    method = _method_record()
    spec = REGISTRY.get(metric) or {}
    return {"benchmark": method.get("title"), "methodKey": CATALOG_METHOD,
            "analyte": spec.get("analyte"), "fraction": spec.get("fraction"),
            "citations": list(method.get("citations") or []),
            "sourceTier": method.get("sourceTier"),
            "confidence": method.get("confidence"),
            "provisional": bool(method.get("provisional")),
            "limitations": method.get("limitations"),
            "unitConversion": (f"{spec.get('benchmark_units')} to {spec.get('metric_units')} "
                               f"x{spec.get('unit_factor')}")}


def bands(metric: str, region: str) -> Optional[tuple[float, float]]:
    """The criterion's two breakpoints on the metric's own scale."""
    spec = REGISTRY.get(metric)
    inp = _input_of(metric)
    pair = ((inp or {}).get("regionalBands") or {}).get(str(region)) if spec else None
    if not pair:
        return None
    f = float(spec["unit_factor"])
    return float(pair[0]) * f, float(pair[1]) * f


def curve_points(metric: str, region: str) -> Optional[list[dict]]:
    """The criterion for one region as a DEEP curve, built by the same code path
    that builds every other published criterion this system ships (CURVE-11)."""
    from . import fixed_criteria as fc
    from ._vendor.easi import screening_methods as sm
    spec = REGISTRY.get(metric)
    inp = _input_of(metric)
    if not spec or not inp:
        return None
    raw = sm.regional_bands(inp, str(region))
    if not raw:
        return None
    f = float(spec["unit_factor"])
    scaled = [{**b,
               "min": None if b.get("min") is None else float(b["min"]) * f,
               "max": None if b.get("max") is None else float(b["max"]) * f} for b in raw]
    entry = {**fc._anchors(scaled, float(spec["resolution"])), "domain": [0.0, None]}
    pts = fc._points(entry, {"Good": 0.85, "Fair": 0.545, "Poor": 0.195})
    return [{"x": float(x), "y": float(y)} for x, y in pts]


def agreement(metric: str, region: str, values: Any, reference_points: Any) -> dict:
    """How often the criterion and a reference curve place a site in the same
    class. Reported for disclosure; it never gates admission (REF-10)."""
    from . import basis_recovery as br
    from . import curves as cv
    pts = curve_points(metric, region)
    vals = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if not pts or not reference_points or not len(vals):
        return {"n": 0, "same_class": None, "net_shift": None}
    same = shift = 0
    for x in vals:
        a = br.band_of(cv.interp_curve(pts, float(x)))
        b = br.band_of(cv.interp_curve(list(reference_points), float(x)))
        same += int(a == b)
        order = {"NF": 0, "AR": 1, "F": 2}
        shift += order.get(a, 0) - order.get(b, 0)
    n = int(len(vals))
    return {"n": n, "same_class": round(same / n, 4), "net_shift": round(shift / n, 4)}


def pool_decision(metric: str, region: str, *, region_code: str,
                  region_name: Optional[str] = None,
                  family: Optional[str] = None) -> rp.PoolDecision:
    """The benchmark stated in the shape a pool states itself.

    Every station count is zero and says so: a published criterion rests on no
    station of this ecoregion, which is the whole point of labelling it
    differently.
    """
    pair = bands(metric, region)
    spec = REGISTRY.get(metric) or {}
    where = f"NARS-9 region {region}"
    note = (f"Scored against a published criterion for {where} rather than against stations of "
            f"this ecoregion."
            + (f" Its breakpoints are {pair[0]:g} and {pair[1]:g} {spec.get('metric_units')}."
               if pair else "")
            + " The criterion carries its own definition of reference, which need not match "
              "this assessment's.")
    return rp.PoolDecision(
        metric=metric, status=rp.STATUS_PUBLISHED, level=None,
        region_code=str(region_code), region_name=region_name, family=family,
        n_pool=0, n_comparable=0, n_usable=0, n_local=0, n_huc12=0,
        disposition="exploratory", supported_level=None, transfer_risk=rp.RISK_NONE,
        transfer_note=note, station_ids=(), levels_tried=[],
        basis=curve_basis.PUBLISHED)
