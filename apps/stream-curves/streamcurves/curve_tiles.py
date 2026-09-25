"""Gallery tiles without the app: the pure builders the Reference Curves page and the headless
register share.

``tile_row`` (moved here from ``views/curve_gallery.py`` on 2026-09-25; the page re-exports
it) turns a metric's curve rows into one tile; ``reference_tiles_for`` draws the curves a
session scores without having fitted them; ``mark_not_selected`` stamps SELECT-04's
placements on the fitted ones. :func:`tiles_for_fields` is the headless twin of
``views.curve_gallery.gallery_rows(state, include_reference=True)``: the same tiles from a
session's fields, or from a stage result through :func:`fields_from_result`, so the candidate
register and the rebuild ledger a batch writes are the ones the page shows.

No Shiny here. The drawing itself lives in ``curve_svg``.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from . import curve_sources as src
from . import curve_svg as cs
from . import curves


def tile_row(metric: str, curve_rows: Any, *, metric_entry: Mapping | None,
             review_entry: Mapping | None, function_label: str | None) -> dict:
    """One gallery tile from a metric's curve rows (a DataFrame with one row per
    stratum, a list of row dicts, or nothing)."""
    if isinstance(curve_rows, pd.DataFrame):
        rows = curve_rows.to_dict("records") if len(curve_rows) else []
    elif curve_rows is None:
        rows = []
    else:
        rows = [dict(r) for r in curve_rows]
    return cs.tile_from_curve_rows(metric, rows, metric_entry=metric_entry,
                                   review_entry=review_entry, function_label=function_label)


def assign_functions(rows: Iterable[Mapping], mapping: Any = None) -> list[dict]:
    """Discipline and function keys on every tile from the session's
    discipline-function mapping (every function a metric serves, primary
    first), falling back to the tile's own label."""
    return cs.assign_functions(rows, mapping)


def _reference_tile(mk: str, entry: Mapping) -> dict:
    """A read-only tile with the curve's own direction (its carried, ladder or criterion
    config, since a metric the session did not fit is not in metric_config: the register's
    basis digest reads it) and each stratum once. A carried curve's layers include the
    pooled curve again beside its class layers (``deep_export.pooled_layers``), and the tile
    builder draws the row and every layer, so without this the same curve digested
    differently when carried than when fitted."""
    tile = cs.reference_tile(mk, entry)
    tile["higher_is_better"] = (entry.get("config") or {}).get("higher_is_better")
    seen: set = set()
    strata = []
    for s in tile.get("strata") or []:
        key = (s.get("label"), tuple((float(x), float(y)) for x, y in s.get("points") or []))
        if key in seen:
            continue
        seen.add(key)
        strata.append(s)
    tile["strata"] = strata
    return tile


def reference_tiles_for(build, mapping, *, built=(), decisions=()) -> list[dict]:
    """Tiles for every curve a session scores without having fitted it
    (``pressure_evidence.reference_rows``): carried forward, from a rung above
    the hierarchy, or a fixed criterion, each placed in the functions the bundle
    places it in under the owner's decisions (REF-15). Each can be removed; a
    removed one stays on the page, dimmed, with the decision to undo."""
    from . import owner_curves as oc
    from . import pressure_evidence as pe
    decisions = list(decisions or [])
    effective = oc.effective_build(build, decisions, built=built)
    tiles, placement = [], []
    for mk, entry in pe.reference_rows(effective, mapping, built=built).items():
        tile = _reference_tile(mk, entry)
        tile["source_title"] = src.source_title(mk, entry, build=effective)
        tile["removable"] = True
        if entry.get("owner"):
            # a curve the owner chose: its undo gives the build's choice back
            tile["owner_decision"] = entry["owner"].get("id")
            tile["owner_note"] = (f"Chosen by {entry['owner'].get('recordedBy')}: "
                                  f"{entry['owner'].get('rationale')}")
        tiles.append(tile)
        placement.extend(entry["mapping"])
    removed = oc.removed(decisions)
    if removed:
        base = pe.reference_rows(build, mapping, built=built)
        for mk, d in removed.items():
            entry = base.get(mk)
            if entry is None:
                continue
            tile = _reference_tile(mk, entry)
            tile["source_title"] = src.source_title(mk, entry, build=build)
            tile["status_text"] = "Removed by the owner"
            tile["removed_decision"] = d.get("id")
            tile["in_scope"] = False
            tiles.append(tile)
            placement.extend(entry["mapping"])
    return cs.assign_functions(tiles, placement)


def mark_not_selected(tiles: Iterable[dict], build) -> list[dict]:
    """Set ``not_selected_fids`` on every tile the session fitted: the functions
    SELECT-04 left it out of (``pressure_evidence.not_selected_pairs``). The
    gallery reads each placement against it, so a curve drawn under a function
    it does not score reads "Not selected here"."""
    from . import pressure_evidence as pe
    by_metric: dict[str, set] = {}
    for m, fid in pe.not_selected_pairs(build):
        by_metric.setdefault(str(m), set()).add(str(fid))
    included = owner_included(build)
    out = []
    for t in tiles:
        if not t.get("read_only"):
            mk = str(t.get("metric") or "")
            t["not_selected_fids"] = sorted(by_metric.get(mk, ()))
            t["owner_included"] = dict(included.get(mk) or {})
        out.append(t)
    return out


def owner_included(build) -> dict:
    """``{metric: {function id: decision id}}`` of the fitted curves the owner put
    back into a function the portfolio left them out of (REF-15)."""
    out: dict[str, dict] = {}
    for d in (build or {}).get("ownerDecisions") or []:
        if d.get("action") == "include":
            for fid in d.get("functions") or []:
                out.setdefault(str(d.get("metric")), {})[str(fid)] = d.get("id")
    return out


def not_selected_here(row: Mapping, here) -> bool:
    """The tile placed under function ``here`` (a function id) is a curve the
    portfolio left out of that function."""
    return here is not None and str(here) in {str(f) for f in row.get("not_selected_fids") or ()}


# --------------------------------------------------------------------------- #
# the headless path: tiles from session fields or a stage result
# --------------------------------------------------------------------------- #
def eligible_metrics(metric_config: Optional[Mapping]) -> list[str]:
    """The metrics the Reference Curves page lists, in the table's order: the same rule as
    ``views.summary_state.eligible_summary_metrics`` (no categorical metric, none excluded
    from the summary)."""
    out = []
    for mk, mc in (metric_config or {}).items():
        mc = mc or {}
        if mc.get("metric_family") == "categorical":
            continue
        if mc.get("include_in_summary") is False:
            continue
        out.append(mk)
    return out


def curve_rows_of(entry: Optional[Mapping]) -> pd.DataFrame:
    """A completed metric's curve rows (one per stratum), read the way the page reads them
    (``views.summary_state.extract_metric_phase4_curve_rows``): the stored ``curve_rows`` or
    ``phase4_curve_rows`` first, else the stratum results, else the reference curve's row.
    The page checks the entry's signature against the session before it reads it; a build's
    own entries and a published version's are current by construction, so no check here."""
    if entry is None or (entry or {}).get("type") == "regional":
        return pd.DataFrame()
    stored = entry.get("curve_rows")
    if stored is None:
        stored = entry.get("phase4_curve_rows")
    if stored is not None:
        return pd.DataFrame(stored)
    if entry.get("stratum_results") is not None:
        frames = []
        for lvl, x in (entry["stratum_results"] or {}).items():
            payload = (x or {}).get("reference_curve", x)
            result = curves.normalize_reference_curve_result(payload, stratum_label=lvl)
            if result is not None and result.get("curve_row") is not None:
                frames.append(result["curve_row"])
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    rc = entry.get("reference_curve")
    if rc is not None and (rc or {}).get("curve_row") is not None:
        normalized = curves.normalize_reference_curve_result(rc)
        row = (normalized or {}).get("curve_row")
        return row if row is not None else pd.DataFrame()
    return pd.DataFrame()


#: the session fields the headless tiles, the register and the ledger read
FIELD_KEYS = ("metric_config", "curve_review", "column_functions", "discipline_function_mapping",
              "completed_metrics", "reference_build", "owner_curve_decisions", "candidate_register",
              "function_coverage_exceptions", "region_of_applicability", "metric_phase_cache")


def is_session_shaped(doc: Mapping) -> bool:
    """True for a dict of session fields (``completed_metrics`` present), False for a stage
    result (``curve_rows`` present, no ``completed_metrics``)."""
    return "completed_metrics" in doc or "curve_rows" not in doc


def fields_from_result(result: Mapping) -> dict:
    """The session fields a stage result implies, exactly as ``regional_agent.session_fields``
    writes them (the completed entries, the registry splits, the reference build), without
    the workbook tables. A dict that already holds session fields is returned as it is, so
    a session decoded from a version and a live result read the same way."""
    if is_session_shaped(result):
        out = {k: result.get(k) for k in FIELD_KEYS}
        if out.get("region_of_applicability") is None:
            out["region_of_applicability"] = result.get("region")
        if out.get("owner_curve_decisions") is None and result.get("curve_decisions") is not None:
            out["owner_curve_decisions"] = result.get("curve_decisions")
        if out.get("function_coverage_exceptions") is None:
            out["function_coverage_exceptions"] = result.get("coverage_exceptions")
        return out
    from . import regional_agent as ra
    from . import run_state
    curve_rows = result.get("curve_rows") or {}
    completed = {mk: ra._completed_metric_entry(mk, row) for mk, row in curve_rows.items()}
    applied = {mk: rec for mk, rec in (result.get("strata_applied") or {}).items()
               if rec.get("applied") and mk in completed
               and (result.get("stratum_rows") or {}).get(mk)}
    split_config = ra.registry_strat_config(result) if applied else {}
    applied = {mk: rec for mk, rec in applied.items() if rec.get("stratifier") in split_config}
    metric_config = result.get("metric_config") or {}
    if applied:
        metric_config = copy.deepcopy(metric_config)
        for mk, rec in applied.items():
            completed[mk] = ra._stratified_metric_entry(
                mk, curve_rows[mk], result["stratum_rows"][mk], rec["stratifier"])
            allowed = list(metric_config[mk].get("allowed_stratifications") or [])
            if rec["stratifier"] not in allowed:
                metric_config[mk]["allowed_stratifications"] = allowed + [rec["stratifier"]]
    reference_build = None
    if result.get("reference_method") == run_state.REFERENCE_METHOD_PRESSURE:
        from . import pressure_evidence as pe
        reference_build = pe.session_reference_build(result)
    return {
        "metric_config": metric_config,
        "curve_review": result.get("curve_review") or {},
        "column_functions": result.get("column_functions") or {},
        "discipline_function_mapping": result.get("discipline_function_mapping"),
        "completed_metrics": completed,
        "reference_build": reference_build,
        "owner_curve_decisions": [dict(d) for d in result.get("curve_decisions") or []],
        "candidate_register": result.get("candidate_register"),
        "function_coverage_exceptions": list(result.get("coverage_exceptions") or []),
        "region_of_applicability": result.get("region"),
        "metric_phase_cache": None,
    }


def tiles_for_fields(fields: Mapping) -> list[dict]:
    """Every tile the Reference Curves page would show for these session fields, the
    read-only reference tiles included: the headless ``gallery_rows(state,
    include_reference=True)``. The fitted tiles come in the table's order with their
    functions assigned and SELECT-04's placements marked; the curves the session did not fit
    follow, placed under the owner's decisions."""
    from . import owner_curves as oc
    from . import pressure_evidence as pe
    fields = fields_from_result(fields)
    mc = fields.get("metric_config") or {}
    review = fields.get("curve_review") or {}
    functions = fields.get("column_functions") or {}
    mapping = fields.get("discipline_function_mapping")
    completed = fields.get("completed_metrics") or {}
    decisions = [dict(d) for d in fields.get("owner_curve_decisions") or [] if isinstance(d, Mapping)]
    build = fields.get("reference_build")
    effective = oc.effective_build(build, decisions, built=completed)
    reference = set(pe.reference_keys(build)) - set(mc)
    out = []
    for m in eligible_metrics(mc):
        if m in reference:
            continue
        try:
            rows = curve_rows_of(completed.get(m))
        except (KeyError, TypeError, ValueError):
            rows = None
        tile = tile_row(m, rows, metric_entry=mc.get(m), review_entry=review.get(m),
                        function_label=functions.get(m))
        tile["higher_is_better"] = (mc.get(m) or {}).get("higher_is_better")
        out.append(tile)
    out = mark_not_selected(assign_functions(out, mapping), effective)
    out += reference_tiles_for(build, mapping, built=completed, decisions=decisions)
    return out


__all__ = ["tile_row", "assign_functions", "reference_tiles_for", "mark_not_selected",
           "owner_included", "not_selected_here", "eligible_metrics", "curve_rows_of",
           "FIELD_KEYS", "is_session_shaped", "fields_from_result", "tiles_for_fields"]
