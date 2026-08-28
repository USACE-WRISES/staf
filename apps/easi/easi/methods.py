"""Display projection of the canonical screening-method catalog.

``data/screening-methods.json`` is the single source of truth for every automated
formula and Good/Fair/Poor boundary; :mod:`easi.screening_methods` evaluates it. This
module declares **no** thresholds or math of its own — it only projects a catalog entry
into the :class:`ScoringMethod` shape the worksheet's reference-curve renderer
(``easi/method_plot.py``) and the "Scoring method" panel already consume, so the plot,
the criteria list, and the rating a site actually received all come from one place.

What-if exploration (the "Explore values" sliders) routes back through
``screening_methods.evaluate``, so a perturbed value is scored by the same evaluator as
the report and can never drift from it.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable, Optional

from . import config, scoring
from . import screening_methods as sm

HIGHER_BETTER = "higher_better"
HIGHER_WORSE = "higher_worse"
_RANK = {"Poor": 0, "Fair": 1, "Good": 2}

# catalog plot.direction -> the two directions this module has always exposed
_DIRECTION = {"higher_better": HIGHER_BETTER, "lower_better": HIGHER_WORSE,
              "mixed": HIGHER_WORSE}


# --------------------------------------------------------------------------- #
# Catalog data model (consumed by method_plot.py and app.py)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MethodInput:
    """One input to a metric's calculation (a slider under 'Explore values', unless
    ``context_only`` — a fixed site property shown but not perturbed, e.g. drainage area)."""
    key: str
    label: str
    unit: str = ""
    symbol: str = ""
    source_label: str = ""
    slider: Optional[tuple] = None          # (min, max, step); None => not a slider
    context_only: bool = False
    integer: bool = False


@dataclass(frozen=True)
class Band:
    """A colored Good/Fair/Poor region on the reference curve, in value units
    (None = open). Mirrors one catalog band; the rating itself comes from the evaluator."""
    rating: str
    lo: Optional[float] = None
    hi: Optional[float] = None


@dataclass(frozen=True)
class Decision:
    """One row of a categorical decision table (category -> rating)."""
    label: str
    rating: str


@dataclass
class ScoringMethod:
    metric_id: str
    mode: str                                # scalar | combined | worst | count | categorical
    inputs: tuple = ()
    equation: Optional[str] = None
    combine: Optional[Callable] = None       # (values) -> value (unrounded, for banding)
    rate: Optional[Callable] = None          # (value) -> "Good"|"Fair"|"Poor"
    value_rating: Optional[Callable] = None  # (values) -> (value, rating) when the two are coupled
    round_ndigits: Optional[int] = 2         # display rounding for the value
    bands: tuple = ()                        # Band[] for the plot regions (scalar/combined/count)
    per_input: tuple = ()                    # worst: ((key, rate_fn, (Band,...)), ...)
    decisions: tuple = ()                    # categorical: (Decision, ...)
    breakpoints: tuple = ()                  # annotation labels for interior boundaries (in order)
    domain: Optional[tuple] = None           # (lo, hi) x-domain for the plot
    direction: str = HIGHER_WORSE
    value_label: str = "Combined value"
    value_unit: str = ""
    method_key: str = ""                     # catalog methodKey this was projected from
    title: str = ""                          # revised method title, shown in the panel header


# --------------------------------------------------------------------------- #
# Projection: catalog dict -> ScoringMethod
# --------------------------------------------------------------------------- #
def _bands(raw: list[dict], domain: Optional[tuple] = None) -> tuple:
    """Projected plot regions. ``domain`` closes the outer open-ended edges so the first and
    last region have something to draw against; the evaluator always uses the raw catalog
    bands, so closing an edge here cannot change a rating."""
    lo_edge, hi_edge = domain if domain else (None, None)
    return tuple(
        Band(rating=b["rating"],
             lo=b.get("min") if b.get("min") is not None else lo_edge,
             hi=b.get("max") if b.get("max") is not None else hi_edge)
        for b in raw or ()
    )


def _input_domain(input_def: dict, raw_bands: list[dict]) -> Optional[tuple]:
    """Plot domain for one indicator of a worst-of method: its slider range when the catalog
    declares one, else the finite band edges with padding."""
    slider = input_def.get("slider")
    if slider:
        return (float(slider["min"]), float(slider["max"]))
    edges = [float(v) for b in raw_bands for v in (b.get("min"), b.get("max")) if v is not None]
    if not edges:
        return None
    lo, hi = min(edges), max(edges)
    pad = max((hi - lo) * 0.35, 1.0)
    return (max(0.0, lo - pad) if lo >= 0 else lo - pad, hi + pad)


def _rate_fn(raw_bands: list[dict]) -> Callable:
    """Rating closure for one input, delegating to the evaluator's own band logic so
    boundary inclusivity is applied identically here and in the report."""
    def f(value):
        if value is None:
            return None
        return sm.rating_for_value(float(value), raw_bands)
    return f


def _mode(method: dict) -> Optional[str]:
    """Render mode, derived from the operator that actually scores the metric.

    ``plot.mode`` is advisory only: the dam-proximity method is drawn as a two-category
    picture but is evaluated as a threshold on an integer count, so it projects as
    ``count`` (an integer step curve) rather than an empty decision table."""
    operator = method["operator"]
    if operator == "unscored":
        return None
    if operator == "categorical_lookup":
        return "categorical"
    if operator == "worst_index":
        return "worst"
    if operator == "best_index":
        return "best"
    if operator == "threshold":
        inp = next((i for i in method.get("inputs", []) if not i.get("contextOnly")), {})
        return "count" if inp.get("valueType") == "integer" else "scalar"
    return "combined"


def _derived_domain(bands: tuple) -> Optional[tuple]:
    """A plot x-domain from the band edges, for methods the catalog leaves unbounded."""
    edges = [float(v) for b in bands for v in (b.lo, b.hi) if v is not None]
    if not edges:
        return None
    lo, hi = min(edges), max(edges)
    pad = max((hi - lo) * 0.35, 1.0)
    return (max(0.0, lo - pad) if lo >= 0 else lo - pad, hi + pad)


def _value_label_unit(method: dict, mode: str) -> tuple[str, str]:
    """The name and unit of the plotted quantity, taken from the catalog."""
    formula = method.get("formula") or {}
    scored = [i for i in method.get("inputs", []) if not i.get("contextOnly")]
    if mode in {"scalar", "count"} and len(scored) == 1:
        return scored[0].get("label", ""), scored[0].get("units", "")
    symbol = formula.get("resultSymbol") or ""
    # "V" is the generic combined-value symbol; the method title reads better on a chip.
    label = method.get("title", "") if symbol in {"", "V"} else symbol
    return label, formula.get("units", "")


def _project(method: dict, context: Optional[dict] = None) -> Optional[ScoringMethod]:
    """Project one resolved catalog method into the renderer's shape.

    ``context`` supplies evaluation context the bands depend on — currently only the NARS
    region, which selects the nutrient method's regional TN/TP boundaries."""
    mode = _mode(method)
    if mode is None:
        return None
    inputs = tuple(
        MethodInput(
            key=i["key"],
            label=i.get("label", i["key"]),
            unit=i.get("units", ""),
            symbol=i.get("symbol", ""),
            source_label=i.get("sourceField", ""),
            slider=((i["slider"]["min"], i["slider"]["max"], i["slider"]["step"])
                    if i.get("slider") else None),
            context_only=bool(i.get("contextOnly")),
            integer=i.get("valueType") == "integer",
        )
        for i in method.get("inputs", [])
    )
    per_input: tuple = ()
    if mode in {"worst", "best"}:
        per_input = tuple(
            (i["key"], _rate_fn(bands), _bands(bands, _input_domain(i, bands)))
            for i, bands in ((i, sm.bands_for_input(method, i, context))
                             for i in method.get("inputs", []) if not i.get("contextOnly"))
            if bands
        )
    decisions: tuple = ()
    if mode == "categorical":
        decisions = tuple(
            Decision(label=item.get("label", key), rating=item["rating"])
            for key, item in ((method.get("formula") or {}).get("lookup") or {}).items()
            if item.get("rating")
        )
    plot = method.get("plot") or {}
    raw_bands = method.get("bands") or []
    domain = (tuple(plot["domain"]) if plot.get("domain")
              else _derived_domain(_bands(raw_bands)))
    bands = _bands(raw_bands, domain)
    value_label, value_unit = _value_label_unit(method, mode)
    return ScoringMethod(
        metric_id=method["metricId"],
        mode=mode,
        inputs=inputs,
        equation=sm.equation_for(method),
        bands=bands,
        per_input=per_input,
        decisions=decisions,
        breakpoints=tuple(b.get("label", "") for b in method.get("breakpoints", [])),
        domain=domain,
        direction=_DIRECTION.get(plot.get("direction", ""), HIGHER_WORSE),
        value_label=value_label,
        value_unit=value_unit,
        round_ndigits=0 if mode == "count" else 2,
        method_key=method["methodKey"],
        title=method.get("title", ""),
    )


@functools.lru_cache(maxsize=None)
def _catalog_methods() -> dict[str, ScoringMethod]:
    """metricId -> the primary (top-level) projected method."""
    out = {}
    for method in sm.catalog().get("methods", []):
        projected = _project(method)
        if projected is not None:
            out[method["metricId"]] = projected
    return out


@functools.lru_cache(maxsize=None)
def _catalog_variants() -> dict[str, dict[str, ScoringMethod]]:
    """metricId -> {methodKey: projected variant}, including the parent's own key."""
    out: dict[str, dict[str, ScoringMethod]] = {}
    for method in sm.catalog().get("methods", []):
        mid = method["metricId"]
        by_key: dict[str, ScoringMethod] = {}
        for key in [method["methodKey"], *[v["methodKey"] for v in method.get("variants", [])]]:
            projected = _project(sm._resolved_method(method, key))
            if projected is not None:
                by_key[key] = projected
        out[mid] = by_key
    return out


def __getattr__(name: str):
    """``methods.METHODS`` stays a metricId -> ScoringMethod mapping for callers that
    enumerate the catalog, built on first access rather than at import time."""
    if name == "METHODS":
        return _catalog_methods()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --------------------------------------------------------------------------- #
# Public API (unchanged surface)
# --------------------------------------------------------------------------- #
def _needs_context(method: dict) -> bool:
    return any(i.get("regionalBands") for i in method.get("inputs", []))


def resolve(mid: str, model: Optional[str] = None,
            context: Optional[dict] = None) -> Optional[ScoringMethod]:
    """The ScoringMethod to render for a row: the exact catalog variant that produced the
    site's rating when the trace names one (``methodKey``), else the primary entry.

    ``context`` comes from the row's scoring trace. Methods whose bands depend on it (the
    nutrient method's NARS region) are projected per call rather than from the cache."""
    try:
        parent = sm.method_for(mid)
    except KeyError:
        return None
    # ``model`` is a catalog methodKey. Reports written before the catalog existed carry
    # legacy mode names ("combined", "wqp", ...); those resolve to the primary method
    # rather than failing, so an archived report still renders.
    variants = _catalog_variants().get(mid) or {}
    resolved = parent if not model or model not in variants else sm._resolved_method(parent, model)
    if context and _needs_context(resolved):
        return _project(resolved, context)
    if model and model in variants:
        return variants[model]
    return _catalog_methods().get(mid)


def sliderable(method: ScoringMethod) -> list:
    return [i for i in method.inputs if i.slider is not None and not i.context_only]


def _index_of(rating: Optional[str]) -> Optional[float]:
    if rating not in config.RATINGS:
        return None
    return scoring.rating_to_index(rating)


def evaluate_method(method: ScoringMethod, values: dict) -> dict:
    """Compute ``{value, rating, index, functionScore}`` for a method + input values.

    Routed through the canonical evaluator, so an explored value is scored exactly as the
    report would score it."""
    if method.mode == "categorical":
        rating = values.get("rating")     # site rating drives the highlighted decision row
        return {"value": values.get("value"), "rating": rating, "index": _index_of(rating),
                "functionScore": scoring.function_score(_index_of(rating))
                if rating in config.RATINGS else None}
    result = sm.evaluate(method.metric_id, values, variant_key=method.method_key or None)
    rating, value = result.rating, result.combined_value
    idx = _index_of(rating)
    if method.mode in {"worst", "best"}:
        # the combined value of a worst-of/best-of method is an index, not a plottable quantity
        value = None
    return {"value": _round(value, method), "rating": rating, "index": idx,
            "functionScore": scoring.function_score(idx) if idx is not None else None}


def _round(value, method: ScoringMethod):
    if value is None or method.round_ndigits is None:
        return value
    try:
        rounded = round(float(value), method.round_ndigits)
    except (TypeError, ValueError):
        return value
    return int(rounded) if method.round_ndigits == 0 else rounded


def evaluate(mid: str, values: dict, model: Optional[str] = None) -> dict:
    """Resolve the method for ``mid``/``model`` and evaluate it (see :func:`evaluate_method`)."""
    method = resolve(mid, model)
    if method is None:
        return {"value": None, "rating": None, "index": None, "functionScore": None}
    return evaluate_method(method, values)


def equation_for(method: ScoringMethod) -> Optional[str]:
    return method.equation


def catalog_entry(mid: str, model: Optional[str] = None) -> Optional[dict]:
    """The raw catalog dict behind a rendered method — basis, limitations, citations.

    An unknown metric *or* an unknown/stale variant key yields None rather than raising, so a
    trace written against an older catalog degrades to a blank panel section instead of a
    render error. Mirrors the tolerance :func:`resolve` already has for the same situation.
    """
    try:
        return sm._resolved_method(sm.method_for(mid), model)
    except KeyError:
        return None


def citations_for(method_dict: dict) -> list[dict]:
    """Resolve a method's citation keys into ``{title, url}`` records."""
    registry = sm.catalog().get("citations") or {}
    return [dict(registry[key], key=key) for key in method_dict.get("citations") or []
            if key in registry]


# --------------------------------------------------------------------------- #
# Range text for the criteria list
# --------------------------------------------------------------------------- #
def _num(v) -> str:
    if v is None:
        return ""
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _join_unit(rng: str, unit: str) -> str:
    if not unit or not rng:
        return rng
    return f"{rng} {unit}"


def _range_from_bands(bands, lo_edge, hi_edge, integer=False) -> dict:
    """Human range text per rating, from the plot bands."""
    out: dict[str, str] = {}
    for b in bands:
        touch_lo = b.lo is None or (lo_edge is not None and float(b.lo) <= float(lo_edge))
        touch_hi = b.hi is None or (hi_edge is not None and float(b.hi) >= float(hi_edge))
        if integer:
            ilo = int(b.lo) if b.lo is not None else None
            ihi = int(b.hi) if b.hi is not None else None
            if touch_lo and not touch_hi:
                out[b.rating] = f"{ihi}" if ihi == (ilo or 0) else f"<= {ihi}"
            elif touch_hi and not touch_lo:
                out[b.rating] = f">= {ilo}"
            elif ilo is not None and ihi is not None:
                out[b.rating] = f"{ilo}" if ilo == ihi else f"{ilo}-{ihi}"
            continue
        if touch_lo and not touch_hi:
            out[b.rating] = f"< {_num(b.hi)}"
        elif touch_hi and not touch_lo:
            out[b.rating] = f"> {_num(b.lo)}"
        elif b.lo is not None and b.hi is not None:
            out[b.rating] = f"{_num(b.lo)}-{_num(b.hi)}"
    return out


def band_range_texts(method: ScoringMethod) -> dict:
    """The numeric value range for each Good/Fair/Poor rating, derived from the same catalog
    bands the evaluator used, so the criteria list and the reference curve cannot disagree.
    ``{}`` for categorical metrics (their criteria are a decision list, not a value range)."""
    if method.mode == "categorical" or not method.mode:
        return {}
    if method.mode in {"worst", "best"}:
        # one chip per rating joining each indicator's own range, tagged by its symbol
        per_rating: dict[str, list] = {"Good": [], "Fair": [], "Poor": []}
        for key, _rate_fn, bands in method.per_input:
            inp = next((mi for mi in method.inputs if mi.key == key), None)
            tag = (inp.symbol or inp.label) if inp else key
            unit = inp.unit if inp else ""
            los = [b.lo for b in bands if b.lo is not None]
            his = [b.hi for b in bands if b.hi is not None]
            lo_edge = min(los) if los else None
            hi_edge = max(his) if his else None
            for rating, rng in _range_from_bands(bands, lo_edge, hi_edge).items():
                per_rating[rating].append(f"{tag} {_join_unit(rng, unit)}")
        return {r: " · ".join(parts) for r, parts in per_rating.items() if parts}
    lo_edge, hi_edge = method.domain or (0.0, 1.0)
    integer = method.mode == "count"
    ranges = _range_from_bands(method.bands, lo_edge, hi_edge, integer=integer)
    prefix = "" if integer else (f"{method.value_label} " if method.value_label else "")
    return {r: f"{prefix}{_join_unit(rng, method.value_unit)}" for r, rng in ranges.items()}


def slider_specs(method: ScoringMethod, site_inputs: dict) -> list:
    """Per-slider ``(MethodInput, site_value, (min, max, step))`` for the active method, with the
    max auto-expanded when the site value exceeds the default domain."""
    out = []
    for inp in sliderable(method):
        lo, hi, step = inp.slider
        sv = (site_inputs or {}).get(inp.key)
        try:
            svf = float(sv)
            if svf > hi:
                hi = _nice_ceiling(svf)
            if svf < lo:
                lo = svf
        except (TypeError, ValueError):
            svf = None
        out.append((inp, sv, (lo, hi, step)))
    return out


def _nice_ceiling(v: float) -> float:
    """Round ``v`` up to a tidy slider maximum (1/2/5 x 10^n)."""
    if v <= 0:
        return 1.0
    import math
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag
