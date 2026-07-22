"""Canonical EASI screening-method evaluator.

``data/screening-methods.json`` is the single source of truth for automated
screening formulas, exact Good/Fair/Poor boundaries, method provenance, and the
reference-method viewer.  This module deliberately supports only a small typed
operator set; it never evaluates arbitrary expressions.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Optional

from . import config

VALID_OPERATORS = {
    "threshold",
    "ratio",
    "minimum",
    "minimum_of_products",
    "worst_index",
    "weighted_capped_sum",
    "sum_capped",
    "categorical_lookup",
    "unscored",
}
VALID_STATUSES = {"computed", "conditional", "field_only"}
VALID_BASIS = {
    "published threshold",
    "published directional relationship",
    "dataset reference",
    "provisional STAF screening judgment",
    "field-only method",
}


@dataclass(frozen=True)
class Evaluation:
    """One typed-method evaluation and its serializable scoring trace."""

    rating: Optional[str]
    index: Optional[float]
    combined_value: Any
    trace: dict


def catalog() -> dict:
    return config.screening_methods()


def methods_by_metric() -> dict[str, dict]:
    return {m["metricId"]: m for m in catalog().get("methods", [])}


def method_for(metric_id: str) -> dict:
    try:
        return methods_by_metric()[metric_id]
    except KeyError as exc:
        raise KeyError(f"no screening method for metric {metric_id!r}") from exc


def _resolved_method(parent: dict, variant_key: str | None = None) -> dict:
    """Return a parent method or one of its canonical source-tier variants."""
    if not variant_key or variant_key == parent.get("methodKey"):
        return parent
    variant = next((item for item in parent.get("variants", [])
                    if item.get("methodKey") == variant_key), None)
    if variant is None:
        raise KeyError(
            f"method {parent.get('metricId')!r} has no variant {variant_key!r}")
    merged = {**parent, **variant}
    merged["metricId"] = parent["metricId"]
    merged["title"] = parent["title"]
    merged["catalogMethodKey"] = parent["methodKey"]
    merged["sourceHierarchy"] = list(parent.get("sourceHierarchy") or [])
    merged["variants"] = list(parent.get("variants") or [])
    return merged


def method_for_trace(metric_id: str, trace: dict | None = None) -> dict:
    """Resolve the exact method variant that produced a serialized trace."""
    parent = method_for(metric_id)
    return _resolved_method(parent, (trace or {}).get("methodKey"))


def _number(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _fmt(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def equation_for(method: dict) -> str:
    """Generate the human-readable equation from typed formula parameters."""
    operator = method["operator"]
    inputs = {i["key"]: i for i in method.get("inputs", [])}
    formula = method.get("formula") or {}

    if operator == "unscored":
        return "No automated rating"
    if operator == "categorical_lookup":
        key = formula.get("input")
        symbol = inputs.get(key, {}).get("symbol", key or "category")
        return f"Rating = lookup({symbol})"
    if operator == "threshold":
        inp = next((i for i in method.get("inputs", []) if not i.get("contextOnly")), None)
        symbol = (inp or {}).get("symbol", "value")
        return f"Rating = bands({symbol})"
    if operator == "ratio":
        numerator = inputs[formula["numerator"]].get("symbol", formula["numerator"])
        denominator = inputs[formula["denominator"]].get("symbol", formula["denominator"])
        n_mult = float(formula.get("numeratorMultiplier", 1))
        d_mult = float(formula.get("denominatorMultiplier", 1))
        n_text = numerator if n_mult == 1 else f"{_fmt(n_mult)} × {numerator}"
        d_text = denominator if d_mult == 1 else f"{_fmt(d_mult)} × {denominator}"
        result = formula.get("resultSymbol", "V")
        units = f" ({formula['units']})" if formula.get("units") else ""
        return f"{result}{units} = {n_text} / ({d_text})"
    if operator == "minimum":
        symbols = [i.get("symbol", i["key"]) for i in method.get("inputs", [])
                   if not i.get("contextOnly")]
        return f"{formula.get('resultSymbol', 'V')} = min({', '.join(symbols)})"
    if operator == "minimum_of_products":
        products = []
        names = []
        for name, keys in formula.get("products", {}).items():
            names.append(name)
            symbols = [inputs[key].get("symbol", key) for key in keys]
            products.append(f"{name} = {' × '.join(symbols)}")
        result = formula.get("resultSymbol", "V")
        products.append(f"{result} = min({', '.join(names)})")
        return "; ".join(products)
    if operator == "worst_index":
        symbols = [i.get("indexSymbol", f"I{i['key']}") for i in method.get("inputs", [])
                   if not i.get("contextOnly")]
        return f"Icombined = min({', '.join(symbols)})"
    if operator == "sum_capped":
        symbols = [i.get("symbol", i["key"]) for i in method.get("inputs", [])
                   if not i.get("contextOnly")]
        return (f"{formula.get('resultSymbol', 'V')} = "
                f"min({' + '.join(symbols)}, {_fmt(float(formula['cap']))})")
    if operator == "weighted_capped_sum":
        pieces = []
        for term in formula.get("terms", []):
            symbol = inputs[term["input"]].get("symbol", term["input"])
            weight = _fmt(float(term["weight"]))
            if term["transform"] == "cap":
                expr = f"min({symbol}/{_fmt(float(term['cap']))}, 1)"
            else:
                expr = (f"clamp(({symbol}−{_fmt(float(term.get('offset', 0)))})/"
                        f"{_fmt(float(term['cap']))}, 0, 1)")
            pieces.append(f"{weight} × {expr}")
        return f"{formula.get('resultSymbol', 'V')} = {' + '.join(pieces)}"
    raise ValueError(f"unsupported operator {operator!r}")


def regional_bands(input_def: dict, region: str | None) -> list[dict]:
    """Build exact NRSA Good/Fair/Poor bands for one analyte and region."""
    pair = (input_def.get("regionalBands") or {}).get(str(region or "").upper())
    if not pair:
        return []
    good_fair, fair_poor = (float(pair[0]), float(pair[1]))
    return [
        {"rating": "Good", "min": None, "max": good_fair,
         "minInclusive": False, "maxInclusive": True,
         "label": f"≤{_fmt(good_fair)}"},
        {"rating": "Fair", "min": good_fair, "max": fair_poor,
         "minInclusive": False, "maxInclusive": False,
         "label": f">{_fmt(good_fair)}–<{_fmt(fair_poor)}"},
        {"rating": "Poor", "min": fair_poor, "max": None,
         "minInclusive": True, "maxInclusive": False,
         "label": f"≥{_fmt(fair_poor)}"},
    ]


def bands_for_input(method: dict, input_def: dict, context: dict | None = None) -> list[dict]:
    if input_def.get("regionalBands"):
        key = (method.get("formula") or {}).get("regionalContextKey", "region")
        return regional_bands(input_def, (context or {}).get(key))
    return input_def.get("bands") or method.get("bands") or []


def rating_for_value(value: float, bands: list[dict]) -> Optional[str]:
    for band in bands:
        lo, hi = band.get("min"), band.get("max")
        lo_ok = (lo is None or value > float(lo)
                 or (band.get("minInclusive", False) and value == float(lo)))
        hi_ok = (hi is None or value < float(hi)
                 or (band.get("maxInclusive", False) and value == float(hi)))
        if lo_ok and hi_ok:
            return band["rating"]
    return None


def criteria_for(method: dict, context: dict | None = None) -> dict:
    """Serializable automated and field criteria for the tooltip/viewer."""
    auto: list[dict] = []
    if method["operator"] == "worst_index":
        for inp in method.get("inputs", []):
            bands = bands_for_input(method, inp, context)
            if bands:
                auto.append({
                    "input": inp["key"],
                    "label": inp["label"],
                    "units": inp.get("units", ""),
                    "bands": {b["rating"]: b["label"] for b in bands},
                })
    elif method.get("bands"):
        auto.append({
            "input": next((i["key"] for i in method.get("inputs", [])
                           if not i.get("contextOnly")), "value"),
            "label": method["title"],
            "units": next((i.get("units", "") for i in method.get("inputs", [])
                           if not i.get("contextOnly")), ""),
            "bands": {b["rating"]: b["label"] for b in method["bands"]},
        })
    elif method["operator"] == "categorical_lookup":
        grouped: dict[str, list[str]] = {}
        for item in (method.get("formula") or {}).get("lookup", {}).values():
            if item.get("rating"):
                grouped.setdefault(item["rating"], []).append(item.get("label", ""))
        if grouped:
            auto.append({
                "input": (method.get("formula") or {}).get("input", "category"),
                "label": method["title"],
                "units": "",
                "bands": {rating: "; ".join(labels) for rating, labels in grouped.items()},
            })
    return {"automated": auto, "fieldReference": method.get("fieldReference")}


def _normal_category(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _input_trace(method: dict, values: dict, input_meta: dict | None) -> list[dict]:
    meta = input_meta or {}
    out = []
    for inp in method.get("inputs", []):
        value = values.get(inp["key"])
        extra = meta.get(inp["key"]) or {}
        available = value is not None and (_number(value) is not None or isinstance(value, str))
        out.append({
            "key": inp["key"],
            "label": inp["label"],
            "value": value,
            "units": inp.get("units", ""),
            "source": extra.get("source") or inp.get("sourceField", ""),
            "available": bool(available),
            "required": bool(inp.get("required")),
            "contextOnly": bool(inp.get("contextOnly")),
            "rationale": inp.get("rationale", ""),
            **({"details": extra["details"]} if extra.get("details") is not None else {}),
        })
    return out


def _finish(method: dict, values: dict, input_meta: dict | None, confidence: str | None,
            *, rating: str | None, combined: Any = None, governing: str | None = None,
            completeness: str, warnings: list[str] | None = None,
            input_ratings: dict[str, str] | None = None, context: dict | None = None) -> Evaluation:
    index = (catalog().get("ratingIndex") or config.RATING_INDEX).get(rating) if rating else None
    traces = _input_trace(method, values, input_meta)
    for item in traces:
        if item["key"] in (input_ratings or {}):
            item["rating"] = input_ratings[item["key"]]
            item["index"] = (catalog().get("ratingIndex") or config.RATING_INDEX)[
                input_ratings[item["key"]]]
    source_tier = method.get("_sourceTierOverride") or method.get("sourceTier")
    if not source_tier:
        source_tier = "unavailable" if rating is None else "screening-proxy"
    trace = {
        "methodKey": method["methodKey"],
        "methodKind": method["operator"],
        "methodStatus": method["status"],
        "basisClass": method["basisClass"],
        "provisional": bool(method.get("provisional")),
        "equation": equation_for(method),
        "inputs": traces,
        "combinedValue": combined,
        "governingInput": governing,
        "generatedRating": rating,
        "generatedIndex": index,
        "completeness": completeness,
        "confidence": confidence or method.get("confidence", "L"),
        "sourceTier": source_tier,
        "evidenceFamily": (method.get("_evidenceFamilyOverride")
                           or method.get("evidenceFamily") or ""),
        "usedFallback": bool(
            method.get("_usedFallbackOverride", method.get("usedFallback", False))),
        "observedOverridesProxy": bool(
            method.get("_observedOverridesProxyOverride",
                       method.get("observedOverridesProxy", False))),
        "sourceHierarchy": list(method.get("sourceHierarchy") or []),
        "limitations": list(method.get("limitations") or []),
        "warnings": list(warnings or []),
        "context": dict(context or {}),
    }
    return Evaluation(rating=rating, index=index, combined_value=combined, trace=trace)


def evaluate(metric_id: str, values: dict[str, Any], *, context: dict | None = None,
             input_meta: dict | None = None, confidence: str | None = None,
             variant_key: str | None = None, source_tier: str | None = None,
             evidence_family: str | None = None, used_fallback: bool | None = None,
             observed_overrides_proxy: bool | None = None) -> Evaluation:
    """Evaluate one catalog method and return its rating plus full scoring trace."""
    method = dict(_resolved_method(method_for(metric_id), variant_key))
    if source_tier is not None:
        method["_sourceTierOverride"] = source_tier
    if evidence_family is not None:
        method["_evidenceFamilyOverride"] = evidence_family
    if used_fallback is not None:
        method["_usedFallbackOverride"] = used_fallback
    if observed_overrides_proxy is not None:
        method["_observedOverridesProxyOverride"] = observed_overrides_proxy
    operator = method["operator"]
    values = dict(values or {})
    context = dict(context or {})
    required = [i for i in method.get("inputs", [])
                if i.get("required") and not i.get("contextOnly")]
    available_required = [i for i in required if values.get(i["key"]) is not None]

    if operator == "unscored":
        any_context = any(values.get(i["key"]) is not None for i in method.get("inputs", []))
        return _finish(method, values, input_meta, confidence, rating=None,
                       completeness="context_only" if any_context else "not_assessed",
                       context=context)

    if operator == "categorical_lookup":
        key = (method.get("formula") or {}).get("input")
        raw = values.get(key)
        if raw is None:
            return _finish(method, values, input_meta, confidence, rating=None,
                           completeness="not_assessed", context=context)
        item = ((method.get("formula") or {}).get("lookup") or {}).get(
            _normal_category(raw), (method.get("formula") or {}).get("default") or {})
        rating = item.get("rating")
        return _finish(method, values, input_meta, confidence, rating=rating,
                       combined=item.get("label") or raw,
                       completeness="complete" if rating else "context_only",
                       context=context)

    if operator == "worst_index":
        allow_partial = bool((method.get("formula") or {}).get("allowPartial"))
        rated: list[tuple[float, str, str]] = []
        input_ratings: dict[str, str] = {}
        for inp in required:
            value = _number(values.get(inp["key"]))
            bands = bands_for_input(method, inp, context)
            if value is None or not bands:
                continue
            rating = rating_for_value(value, bands)
            if rating:
                idx = float((catalog().get("ratingIndex") or config.RATING_INDEX)[rating])
                rated.append((idx, inp["key"], rating))
                input_ratings[inp["key"]] = rating
        if not rated or (len(rated) < len(required) and not allow_partial):
            return _finish(method, values, input_meta, confidence, rating=None,
                           completeness="not_assessed", input_ratings=input_ratings,
                           context=context)
        idx, governing, rating = min(rated, key=lambda x: x[0])
        completeness = "complete" if len(rated) == len(required) else "partial"
        return _finish(method, values, input_meta, confidence, rating=rating,
                       combined=idx, governing=governing, completeness=completeness,
                       input_ratings=input_ratings, context=context)

    if len(available_required) < len(required):
        return _finish(method, values, input_meta, confidence, rating=None,
                       completeness="not_assessed", context=context)

    warnings: list[str] = []
    combined: Optional[float] = None
    if operator == "threshold":
        inp = next(i for i in method.get("inputs", []) if not i.get("contextOnly"))
        combined = _number(values.get(inp["key"]))
    elif operator == "ratio":
        formula = method["formula"]
        numerator = _number(values.get(formula["numerator"]))
        denominator = _number(values.get(formula["denominator"]))
        positive_key = next((i["key"] for i in method.get("inputs", [])
                             if i.get("positive")), None)
        if numerator is None or denominator is None or denominator == 0:
            return _finish(method, values, input_meta, confidence, rating=None,
                           completeness="not_assessed", context=context)
        if positive_key and (_number(values.get(positive_key)) or 0) <= 0:
            return _finish(method, values, input_meta, confidence, rating=None,
                           completeness="not_assessed", context=context)
        combined = (float(formula.get("numeratorMultiplier", 1)) * numerator
                    / (float(formula.get("denominatorMultiplier", 1)) * denominator))
    elif operator == "minimum":
        vals = [_number(values.get(i["key"])) for i in required]
        if any(value is None for value in vals):
            return _finish(method, values, input_meta, confidence, rating=None,
                           completeness="not_assessed", context=context)
        combined = min(float(value) for value in vals if value is not None)
    elif operator == "minimum_of_products":
        products: dict[str, float] = {}
        for name, keys in (method.get("formula") or {}).get("products", {}).items():
            vals = [_number(values.get(key)) for key in keys]
            if not vals or any(value is None for value in vals):
                return _finish(method, values, input_meta, confidence, rating=None,
                               completeness="not_assessed", context=context)
            products[name] = math.prod(float(value) for value in vals if value is not None)
        if not products:
            return _finish(method, values, input_meta, confidence, rating=None,
                           completeness="not_assessed", context=context)
        context["products"] = {key: round(value, 12) for key, value in products.items()}
        combined = min(products.values())
    elif operator == "sum_capped":
        vals = [_number(values.get(i["key"])) for i in required]
        if any(v is None for v in vals):
            return _finish(method, values, input_meta, confidence, rating=None,
                           completeness="not_assessed", context=context)
        combined = min(sum(float(v) for v in vals if v is not None),
                       float(method["formula"]["cap"]))
    elif operator == "weighted_capped_sum":
        total = 0.0
        for term in method["formula"].get("terms", []):
            value = _number(values.get(term["input"]))
            if value is None:
                return _finish(method, values, input_meta, confidence, rating=None,
                               completeness="not_assessed", context=context)
            if term["transform"] == "cap":
                transformed = min(value / float(term["cap"]), 1.0)
            elif term["transform"] == "offset_cap":
                transformed = max(0.0, min((value - float(term.get("offset", 0)))
                                           / float(term["cap"]), 1.0))
            else:  # guarded by catalog validation
                raise ValueError(f"unsupported transform {term['transform']!r}")
            total += float(term["weight"]) * transformed
        combined = total

    if combined is None:
        return _finish(method, values, input_meta, confidence, rating=None,
                       completeness="not_assessed", context=context)
    if operator in {"ratio", "minimum", "minimum_of_products",
                    "sum_capped", "weighted_capped_sum"}:
        # Derived values can otherwise fall a few binary floating-point ulps to
        # either side of an exact documented breakpoint (for example, 0.30).
        # Twelve decimal places is far finer than any source input or slider.
        combined = round(float(combined), 12)
    rating = rating_for_value(combined, method.get("bands") or [])
    if method.get("formula", {}).get("geometryWarningBelow") is not None:
        threshold = float(method["formula"]["geometryWarningBelow"])
        if combined < threshold:
            warnings.append(
                f"Value {combined:g} is below {threshold:g}; verify cross-section geometry.")
    return _finish(method, values, input_meta, confidence, rating=rating,
                   combined=combined, completeness="complete" if rating else "not_assessed",
                   warnings=warnings, context=context)


def validate_catalog() -> list[str]:
    """Return deterministic catalog/schema consistency problems."""
    data = catalog()
    problems: list[str] = []
    methods = data.get("methods") or []
    metric_ids = [m.get("metricId") for m in methods]
    method_keys = [definition.get("methodKey") for method in methods
                   for definition in [method, *(method.get("variants") or [])]]
    expected = set(config.metrics_by_id())
    if len(methods) != 20:
        problems.append(f"expected 20 methods, found {len(methods)}")
    if set(metric_ids) != expected:
        missing = sorted(expected - set(metric_ids))
        extra = sorted(set(metric_ids) - expected)
        if missing:
            problems.append(f"catalog missing metric IDs: {missing}")
        if extra:
            problems.append(f"catalog has unknown metric IDs: {extra}")
    if len(method_keys) != len(set(method_keys)):
        problems.append("methodKey values must be unique")

    citation_ids = set((data.get("citations") or {}).keys())

    def validate_bands(mid: str, label: str, bands: list[dict],
                       *, integer: bool = False) -> None:
        if not bands:
            problems.append(f"{mid}: {label} has no bands")
            return
        ordered = sorted(
            bands, key=lambda b: float("-inf") if b.get("min") is None
            else float(b["min"]))
        if integer:
            finite = [float(value) for band in ordered
                      for value in (band.get("min"), band.get("max"))
                      if value is not None]
            limit = int(max(finite or [0])) + 3
            for value in range(0, limit + 1):
                matches = [band for band in ordered
                           if rating_for_value(float(value), [band]) is not None]
                if len(matches) != 1:
                    problems.append(
                        f"{mid}: {label} integer value {value} matches {len(matches)} bands")
            return
        if ordered[0].get("min") is not None:
            problems.append(f"{mid}: {label} bands do not begin at -infinity")
        if ordered[-1].get("max") is not None:
            problems.append(f"{mid}: {label} bands do not end at +infinity")
        for left, right in zip(ordered, ordered[1:]):
            if left.get("max") != right.get("min"):
                problems.append(f"{mid}: {label} bands contain a gap or overlap")
                continue
            inclusive = int(bool(left.get("maxInclusive"))) + int(
                bool(right.get("minInclusive")))
            if inclusive != 1:
                problems.append(
                    f"{mid}: {label} shared boundary must belong to exactly one band")

    for method in methods:
        mid = method.get("metricId", "<missing>")
        if method.get("operator") not in VALID_OPERATORS:
            problems.append(f"{mid}: invalid operator {method.get('operator')!r}")
        if method.get("status") not in VALID_STATUSES:
            problems.append(f"{mid}: invalid status {method.get('status')!r}")
        if method.get("basisClass") not in VALID_BASIS:
            problems.append(f"{mid}: invalid basisClass {method.get('basisClass')!r}")
        unresolved = set(method.get("citations") or []) - citation_ids
        if unresolved:
            problems.append(f"{mid}: unresolved citations {sorted(unresolved)}")
        keys = [i.get("key") for i in method.get("inputs", [])]
        if len(keys) != len(set(keys)):
            problems.append(f"{mid}: input keys must be unique")
        if method.get("operator") == "weighted_capped_sum":
            terms = (method.get("formula") or {}).get("terms") or []
            if not math.isclose(sum(float(t.get("weight", 0)) for t in terms), 1.0,
                                abs_tol=1e-9):
                problems.append(f"{mid}: weighted terms must sum to 1")
            for term in terms:
                if term.get("input") not in keys:
                    problems.append(f"{mid}: unknown weighted input {term.get('input')!r}")
                if term.get("transform") not in {"cap", "offset_cap"}:
                    problems.append(f"{mid}: invalid transform {term.get('transform')!r}")
                if float(term.get("cap", 0)) <= 0:
                    problems.append(f"{mid}: caps must be positive")
        if method.get("bands"):
            input_def = next((i for i in method.get("inputs", [])
                              if not i.get("contextOnly")), {})
            validate_bands(mid, "method", method["bands"],
                           integer=input_def.get("valueType") == "integer")
        for inp in method.get("inputs", []):
            if inp.get("bands"):
                validate_bands(mid, inp["key"], inp["bands"],
                               integer=inp.get("valueType") == "integer")
            for region, pair in (inp.get("regionalBands") or {}).items():
                if len(pair) != 2 or float(pair[0]) >= float(pair[1]):
                    problems.append(f"{mid}: invalid regional bands for {inp['key']} {region}")
        if method.get("operator") == "ratio":
            formula = method.get("formula") or {}
            if formula.get("numerator") not in keys or formula.get("denominator") not in keys:
                problems.append(f"{mid}: ratio inputs are not defined")
        if method.get("operator") == "sum_capped":
            if float((method.get("formula") or {}).get("cap", 0)) <= 0:
                problems.append(f"{mid}: sum cap must be positive")
        try:
            equation_for(method)
        except Exception as exc:  # noqa: BLE001 - validator reports all issues
            problems.append(f"{mid}: equation generation failed: {exc}")

        available_keys = {method.get("methodKey"), *[
            variant.get("methodKey") for variant in method.get("variants", [])]}
        for tier in method.get("sourceHierarchy", []):
            if tier.get("methodKey") not in available_keys:
                problems.append(
                    f"{mid}: source hierarchy references unknown method "
                    f"{tier.get('methodKey')!r}")
        for variant in method.get("variants", []):
            resolved = _resolved_method(method, variant.get("methodKey"))
            label = f"{mid}/{variant.get('methodKey', '<missing>')}"
            if resolved.get("operator") not in VALID_OPERATORS:
                problems.append(f"{label}: invalid operator {resolved.get('operator')!r}")
            if resolved.get("status") not in VALID_STATUSES:
                problems.append(f"{label}: invalid status {resolved.get('status')!r}")
            if resolved.get("basisClass") not in VALID_BASIS:
                problems.append(f"{label}: invalid basisClass {resolved.get('basisClass')!r}")
            unresolved = set(resolved.get("citations") or []) - citation_ids
            if unresolved:
                problems.append(f"{label}: unresolved citations {sorted(unresolved)}")
            variant_inputs = [item.get("key") for item in resolved.get("inputs", [])]
            if len(variant_inputs) != len(set(variant_inputs)):
                problems.append(f"{label}: input keys must be unique")
            if resolved.get("bands"):
                input_def = next((item for item in resolved.get("inputs", [])
                                  if not item.get("contextOnly")), {})
                validate_bands(label, "method", resolved["bands"],
                               integer=input_def.get("valueType") == "integer")
            for item in resolved.get("inputs", []):
                if item.get("bands"):
                    validate_bands(label, item["key"], item["bands"],
                                   integer=item.get("valueType") == "integer")
            if resolved.get("operator") == "minimum_of_products":
                product_inputs = [key for keys in
                                  (resolved.get("formula") or {}).get("products", {}).values()
                                  for key in keys]
                missing = sorted(set(product_inputs) - set(variant_inputs))
                if missing:
                    problems.append(f"{label}: undefined product inputs {missing}")
            try:
                equation_for(resolved)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{label}: equation generation failed: {exc}")
    return problems
