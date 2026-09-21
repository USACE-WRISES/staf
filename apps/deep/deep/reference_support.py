"""What stands behind a metric's criteria, read from the bundle (pure).

Bundles built under StreamCurves methodology 0.12 say, per metric, whether the
criteria are fixed or a regional reference curve (``criteriaBasis``), where the
reference stations came from and how far the pool was stretched
(``referenceSupport``), how the region's own better streams compare
(``localComparison``, a labeled comparison and never a baseline), which class
split the curve layers follow (``stratifier``), and whether the curve separates
pressured from reference stations (``discrimination``). The bundle also lists
the metrics withheld because no defensible reference pool exists
(``insufficientReferenceSupport``).

Every reader here returns an empty answer for a bundle that carries none of
this, so the 22 bundles published before 0.12 read exactly as they did.
"""
from __future__ import annotations

from typing import Any, Optional

FIXED = "fixed"
POOLED_LABEL = "All streams (pooled)"
_LEVEL_WORDS = {"l3": "Level III", "l2": "Level II", "l1": "Level I"}
_RISK_WORDS = {"low": "low", "moderate": "moderate", "high": "high",
               "unassessed": "not yet assessed"}


def is_fixed(metric_spec: Optional[dict]) -> bool:
    """The metric is scored on fixed criteria, the same in every region."""
    return str((metric_spec or {}).get("criteriaBasis") or "") == FIXED


def _raw(assessment) -> dict:
    raw = getattr(assessment, "raw", None)
    if raw is None:
        raw = assessment if isinstance(assessment, dict) else {}
    return raw or {}


# --------------------------------------------------------------------------- #
# the line on the metric card, the report and the CSV
# --------------------------------------------------------------------------- #
def support_line(metric_spec: Optional[dict]) -> str:
    """One plain line saying what the metric is scored against, or ``""`` for a
    bundle that does not say."""
    m = metric_spec or {}
    if is_fixed(m):
        return "Fixed criteria, the same in every region"
    sup = m.get("referenceSupport")
    if not isinstance(sup, dict) or not sup.get("status"):
        return ""
    n = sup.get("nUsable")
    status = str(sup.get("status"))
    if status == "local":
        return f"Reference curve from {n} least-disturbed stations of this ecoregion"
    level = _LEVEL_WORDS.get(str(sup.get("level") or ""), "a parent")
    where = f"{level} ecoregion {sup.get('regionCode')}"
    if sup.get("regionName"):
        where += f" ({sup.get('regionName')})"
    local = sup.get("nLocal")
    local_txt = "" if local in (None, "") else f", {local} of them in this ecoregion"
    risk = _RISK_WORDS.get(str(sup.get("transferRisk") or ""), "")
    risk_txt = f". Transfer risk {risk}" if risk else ""
    return (f"Reference curve from {n} least-disturbed stations borrowed from {where}"
            f"{local_txt}{risk_txt}")


def is_borrowed(metric_spec: Optional[dict]) -> bool:
    sup = (metric_spec or {}).get("referenceSupport") or {}
    return str(sup.get("status") or "").startswith("borrowed")


def comparison_line(metric_spec: Optional[dict]) -> str:
    """The local best-available comparison as text, or ``""``."""
    comp = (metric_spec or {}).get("localComparison")
    if not isinstance(comp, dict) or comp.get("q25") is None or comp.get("q75") is None:
        return ""
    label = comp.get("label") or "Local best-available comparison (not reference)"
    return (f"{label}: the middle half of {comp.get('n')} stations reads "
            f"{_num(comp.get('q25'))} to {_num(comp.get('q75'))}, median {_num(comp.get('q50'))}")


def discrimination_line(metric_spec: Optional[dict]) -> str:
    rec = (metric_spec or {}).get("discrimination")
    if not isinstance(rec, dict) or rec.get("aucRefVsPressure") is None:
        return ""
    words = {"discriminates": "separates", "weak": "weakly separates",
             "none": "does not separate", "inverted": "ranks backwards"}
    verb = words.get(str(rec.get("verdict")), "does not separate")
    return (f"In the builder's check this curve {verb} reference from pressured stations "
            f"(area under the curve {float(rec['aucRefVsPressure']):.2f})")


def criteria_lines(metric_spec: Optional[dict]) -> list[str]:
    """The fixed criteria as ``Good <10%`` style lines, with their sources."""
    src = (metric_spec or {}).get("criteriaSource")
    if not isinstance(src, dict):
        return []
    out = [f"{b.get('rating')} {b.get('label')}" for b in src.get("bands") or []
           if b.get("rating") and b.get("label")]
    cites = [str(c.get("text")) for c in src.get("citations") or [] if c.get("text")]
    if cites:
        out.append("Sources: " + "; ".join(cites))
    return out


def tip_lines(metric_spec: Optional[dict]) -> list[str]:
    """Everything the hover card says about the criteria basis, in order."""
    m = metric_spec or {}
    lines: list[str] = []
    line = support_line(m)
    if line:
        lines.append(line)
    if is_fixed(m):
        lines.extend(criteria_lines(m))
        return lines
    sup = m.get("referenceSupport") or {}
    if sup.get("screen"):
        lines.append(f"Reference screen: {sup.get('screen')}")
    if is_borrowed(m) and sup.get("transferNote"):
        lines.append(str(sup.get("transferNote")))
    for extra in (comparison_line(m), discrimination_line(m)):
        if extra:
            lines.append(extra)
    return lines


def _num(v: Any) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return ""


# --------------------------------------------------------------------------- #
# metrics withheld for insufficient reference support
# --------------------------------------------------------------------------- #
def withheld(assessment) -> list[dict]:
    items = _raw(assessment).get("insufficientReferenceSupport")
    return [w for w in items if isinstance(w, dict)] if isinstance(items, list) else []


def withheld_for_function(assessment, function_id: str) -> list[dict]:
    """The withheld metrics that would have scored ``function_id``."""
    out = []
    for w in withheld(assessment):
        fids = [f.get("functionId") for f in w.get("functions") or [] if isinstance(f, dict)]
        if not fids and w.get("functionId"):
            fids = [w.get("functionId")]
        if function_id in fids:
            out.append(w)
    return out


def withheld_only_functions(assessment) -> list[dict]:
    """Functions the bundle scores nothing for because every candidate metric
    was withheld: ``[{functionId, functionName, metrics: [...]}]``. They have no
    block in ``metricsByFunction``, so the worksheet needs them named."""
    scored = {fn.get("functionId") for fn in _raw(assessment).get("metricsByFunction") or []
              if fn.get("metrics")}
    by_fn: dict[str, dict] = {}
    for w in withheld(assessment):
        for f in w.get("functions") or []:
            fid = (f or {}).get("functionId")
            if not fid or fid in scored:
                continue
            rec = by_fn.setdefault(fid, {"functionId": fid,
                                         "functionName": f.get("functionName") or fid,
                                         "metrics": []})
            rec["metrics"].append(w)
    return list(by_fn.values())


def unassessed_functions(assessment) -> list[dict]:
    """Every STAF function this assessment has no basis to score.

    The framework's functions minus the ones the bundle carries a scoring block
    for. Wider than :func:`withheld_only_functions`, which names only the ones
    whose candidate metrics were all withheld for insufficient reference support:
    a function the bundle never mentions is just as unassessed, and just as
    capable of flattering the index by leaving the denominator.

    Each entry carries `metrics`, the withheld candidates where there were any, so
    a reader is told why and not merely that.
    """
    from . import config
    scored = {fn.get("functionId") for fn in _raw(assessment).get("metricsByFunction") or []
              if fn.get("metrics")}
    by_withheld = {f["functionId"]: f for f in withheld_only_functions(assessment)}
    out = []
    for f in config.functions():
        fid = f.get("id")
        if fid in scored:
            continue
        known = by_withheld.get(fid)
        # The framework's own name, not the exclusion record's: the bundle spells
        # one of them "Water and soil quality" where DEEP's walk says "Water & soil
        # quality", and the step has to match its nineteen neighbours.
        out.append({"functionId": fid,
                    "functionName": f.get("name") or (known or {}).get("functionName") or fid,
                    "discipline": f.get("discipline") or f.get("category") or "",
                    "metrics": (known or {}).get("metrics") or []})
    return out


def reference_method_statement(assessment) -> str:
    block = _raw(assessment).get("referenceMethod")
    return str(block.get("statement") or "") if isinstance(block, dict) else ""


# --------------------------------------------------------------------------- #
# the curve set a reach belongs to
# --------------------------------------------------------------------------- #
def stratum_label(label: Optional[str], metric_spec: Optional[dict] = None) -> str:
    """What the curve-set chooser shows for a layer. The pooled layer is unnamed
    in the bundle, and a class layer is named by its key (``ge_2``), which the
    metric's ``stratifier`` block turns into words."""
    if label in (None, ""):
        return POOLED_LABEL
    block = (metric_spec or {}).get("stratifier")
    if isinstance(block, dict):
        for cls in block.get("classes") or []:
            if str((cls or {}).get("key")) == str(label) and cls.get("label"):
                return str(cls["label"])
    return str(label)


def _class_of(value: float, breaks: list, right: bool) -> int:
    """Index of the class ``value`` falls in. ``right`` means a break belongs to
    the class below it (drainage area 10 km2 is in the first class), otherwise
    to the class above (a slope of exactly 0.005 is in the second)."""
    idx = 0
    for b in breaks:
        if (value > b) if right else (value >= b):
            idx += 1
    return idx


def reach_class(metric_spec: Optional[dict], delineation: Optional[dict]) -> Optional[dict]:
    """The stratifier class the delineated reach falls in: ``{label, value,
    variable, hasCurve}``, or ``None`` when the metric has no stratifier or the
    reach has no usable value. ``hasCurve`` says whether the bundle carries a
    curve layer for that class."""
    m = metric_spec or {}
    block = m.get("stratifier")
    layers = m.get("curveLayers") or []
    if not isinstance(block, dict) or len(layers) < 2:
        return None
    d = (delineation or {}).get("delineation") or delineation or {}
    variable = str(block.get("variable"))
    raw = {"nhd_slope": d.get("slope"),
           "drainage_area_sqkm": d.get("drainage_area_sqkm")}.get(variable)
    try:
        value = float(raw)
        breaks = [float(b) for b in block.get("breaks") or []]
    except (TypeError, ValueError):
        return None
    # the builder classes a drainage area only above zero and a slope from zero up
    if value != value or value < 0 or (bool(block.get("right")) and value <= 0):
        return None
    classes = block.get("classes") or []
    idx = _class_of(value, breaks, bool(block.get("right")))
    if idx >= len(classes):
        return None
    cls = classes[idx] or {}
    key, label = str(cls.get("key") or ""), str(cls.get("label") or "")
    # a layer is named by the class key; a label is accepted for robustness
    have = {str(layer.get("stratum", "")) for layer in layers}
    layer = key if key and key in have else label if label and label in have else None
    return {"key": key, "label": label or key, "value": value, "variable": variable,
            "layer": layer, "hasCurve": layer is not None}


def auto_stratum(metric_spec: Optional[dict], delineation: Optional[dict]) -> Optional[str]:
    """The curve layer the delineated reach belongs to, from the bundle's
    ``stratifier`` block and the reach's own NHDPlus slope or drainage area.

    Returns the layer's stratum label, ``""`` (the pooled layer) when the
    reach's class has no curve of its own or the reach has no value, or
    ``None`` when the metric has no stratifier (nothing to choose).
    """
    m = metric_spec or {}
    if not isinstance(m.get("stratifier"), dict) or len(m.get("curveLayers") or []) < 2:
        return None
    cls = reach_class(m, delineation)
    return cls["layer"] if cls and cls["hasCurve"] else ""


def auto_strata(assessment, delineation: Optional[dict]) -> dict[str, str]:
    """``{metricId: stratum label}`` for every metric with a stratifier."""
    out: dict[str, str] = {}
    for fn in _raw(assessment).get("metricsByFunction") or []:
        for m in fn.get("metrics") or []:
            got = auto_stratum(m, delineation)
            if got is not None and m.get("metricId"):
                out[str(m["metricId"])] = got
    return out


def stratifier_note(metric_spec: Optional[dict], delineation: Optional[dict]) -> str:
    """Why the chooser preselected what it did, for the card."""
    if not isinstance((metric_spec or {}).get("stratifier"), dict):
        return ""
    cls = reach_class(metric_spec, delineation)
    if cls is None:
        return "The reach has no NHDPlus value to choose a curve set from, so the pooled curve applies"
    what = (f"NHDPlus slope ({100 * cls['value']:.2f} percent)" if cls["variable"] == "nhd_slope"
            else f"drainage area ({cls['value']:,.1f} km2)")
    if cls["hasCurve"]:
        return f"Chosen from the reach's {what}"
    return (f"The reach's {what} puts it in {cls['label']}, a class with too few reference "
            "stations for a curve of its own, so the pooled curve applies")
