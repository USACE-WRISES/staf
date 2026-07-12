"""Assessment orchestrator: context -> metric adapters -> scored report.

Prefetches shared data once (StreamCat row, NLCD landcover, HUC12), runs the
registered metric adapters concurrently (each sync adapter on a worker thread),
applies any user overrides, scores via the engine, and builds report rows for
ALL 20 EASI metrics (implemented ones rated; the rest 'pending').
"""
from __future__ import annotations

import asyncio
from typing import Optional

import anyio

from . import basin, bieger, config, scoring
from .datasources import nlcd, streamcat, threedep, wbd
from .metrics import registry
from .metrics.base import AnalysisContext, MetricResult, unavailable

VALID = {"Good", "Fair", "Poor"}


async def _to_thread(fn, *args):
    return await anyio.to_thread.run_sync(fn, *args)


async def assess(ctx: AnalysisContext, *,
                 metric_ids: Optional[list[str]] = None,
                 sources: Optional[dict[str, str]] = None,
                 overrides: Optional[dict[str, str]] = None,
                 progress: Optional[dict] = None) -> dict:
    """Score the selected EASI metrics for ``ctx``.

    ``metric_ids`` limits which functions are computed (default = all registered);
    unselected ones appear as ``status="excluded"`` and drop out of the rollup.
    ``sources`` maps metricId -> chosen data source for the few multi-source
    metrics (see ``config.SOURCE_OPTIONS``). ``overrides`` force Good/Fair/Poor.
    ``progress`` is an optional shared dict updated as adapters finish
    (``{"done": int, "total": int}``) so the UI can show live "X/N" feedback.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v in VALID}
    selected = set(metric_ids) if metric_ids is not None else set(registry.REGISTRY)
    ctx.extras["source_choices"] = dict(sources or {})
    if progress is not None:
        progress["done"] = 0
        progress["total"] = sum(1 for m in registry.REGISTRY if m in selected)
        progress["waiting"] = {}   # {service label: in-flight count} for the live "waiting on…" hint

    # Regional bankfull (Bieger 2015) for this location — the default geometry the
    # cross-section, ER/BHR, and floodplain hydraulics build on (overrideable in UI).
    bf = bieger.bankfull_geometry(ctx.drainage_area_sqkm, ctx.lat, ctx.lon)

    # --- prefetch shared data concurrently (off the event loop) ---
    sc, lc, huc12, geom = await asyncio.gather(
        _to_thread(streamcat.metrics_by_comid, ctx.comid, registry.STREAMCAT_NAMES),
        _to_thread(nlcd.watershed_landcover, ctx.watershed_geojson),
        _to_thread(wbd.huc12_at_point, ctx.lat, ctx.lon),
        _to_thread(lambda: threedep.reach_geomorphology(
            ctx.reach_geojson, ctx.drainage_area_sqkm,
            bankfull=(bf["width_m"], bf["depth_m"]), bankfull_area_m2=bf["area_m2"],
            division=bf["division_name"])),
    )
    ctx.extras["streamcat"] = sc
    ctx.extras["landcover"] = lc
    ctx.huc12 = huc12
    ctx.extras["reach_geomorph"] = geom

    # Render the representative cross-section once per analysis (off the loop) and
    # stash the geometry so the report can recompute ER/BHR from edited stages.
    cross_section = await _to_thread(_build_cross_section, geom, ctx.slope, ctx.fcode)

    # --- run only the SELECTED registered adapters, never failing the run ---
    async def _run(mid: str, fn) -> MetricResult:
        # These mutations run in the coroutine body on the event-loop thread (only
        # fn(ctx) runs on a worker), so the shared progress dict needs no lock.
        svc = registry.EXTERNAL_SERVICE.get(mid)
        if svc and progress is not None:      # mark this external service as in-flight
            w = progress.setdefault("waiting", {})
            w[svc] = w.get(svc, 0) + 1
        try:
            return await _to_thread(fn, ctx)
        except Exception as exc:  # noqa: BLE001
            conf = config.METRIC_REGISTRY.get(mid, {}).get("confidence", "L")
            return unavailable(mid, f"adapter error: {exc}", conf)
        finally:
            if progress is not None:  # advance the live "X/N" counter
                progress["done"] = progress.get("done", 0) + 1
                if svc:               # clear the service once done (count 0 drops the label)
                    w = progress.get("waiting", {})
                    if w.get(svc):
                        w[svc] -= 1
                        if w[svc] <= 0:
                            w.pop(svc, None)

    to_run = {m: f for m, f in registry.REGISTRY.items() if m in selected}
    results = await asyncio.gather(*[_run(m, f) for m, f in to_run.items()])
    by_id = {r.metric_id: r for r in results}

    # --- build rows for all 20 metrics + collect ratings ---
    meta_by_id = config.metrics_by_id()
    reg = config.METRIC_REGISTRY
    rows: list[dict] = []
    for mid, meta in meta_by_id.items():
        info = reg.get(mid, {})
        res = by_id.get(mid)
        if res is not None:
            generated, source, value_text = res.rating, res.source, res.value_text
            status, confidence, note = res.status, res.confidence, res.note
        elif mid in registry.REGISTRY and mid not in selected:
            generated, source, value_text = None, "", "not included in this analysis"
            status, confidence = "excluded", info.get("confidence", "L")
            note = "excluded from this analysis"
        else:
            generated, source, value_text = None, "", "not available"
            status, confidence = "pending", info.get("confidence", "L")
            note = "metric adapter not yet implemented"

        rating = generated
        if mid in overrides:  # user override wins
            rating = overrides[mid]
            status, source = "override", "user override"
            value_text = f"user-provided: {rating}"
            note = "overrides generated value"

        idx = fscore = None
        if rating in VALID:
            idx = scoring.rating_to_index(rating, meta.get("indexMidpoints"))
            fscore = scoring.function_score(idx)

        rows.append({
            "metricId": mid, "name": meta["name"], "discipline": meta["discipline"],
            "functionId": meta["functionId"], "functionName": meta["functionName"],
            "scale": info.get("scale"), "confidence": confidence,
            "rating": rating, "generatedRating": generated,
            "index": round(idx, 3) if idx is not None else None,
            "functionScore": fscore, "valueText": value_text,
            "criteria": meta.get("criteria", {}).get(rating, "") if rating in VALID else "",
            "source": source, "status": status, "note": note,
            "overrideable": bool(info.get("overrideable")),
        })

    result = _finalize(rows, len(meta_by_id), overrides)
    if cross_section:
        result["crossSection"] = cross_section
    result["basin"] = basin.basin_characteristics(ctx)
    return result


def _xsection_caption(er=None, bhr=None, division=None, *, edited=False) -> str:
    # ER / BHR / widths now live in the summary table beside the plot, not here.
    if edited:
        return ("Edited cross-section. Floodprone width is measured at 2x max "
                "bankfull depth (Rosgen); the bank-height ratio uses your low-bank height.")
    reg = f" — Bieger bankfull ({division})" if division else ""
    return ("Representative 3DEP cross-section" + reg
            + ". DEM screening estimate (10 m); edit the bankfull and low-bank "
            "heights in the table.")


def _xsection_geom_block(geom: dict, slope, fcode=None) -> Optional[dict]:
    """The minimal geometry the editable UI needs to recompute ER/BHR + redraw."""
    profile = (geom or {}).get("profile")
    d_bf, thalweg = geom.get("bankfull_depth_m"), geom.get("thalweg")
    if not profile or thalweg is None or not d_bf:
        return None
    return {
        "stations": list(profile["stations"]), "elevs": list(profile["elevs"]),
        "thalweg": thalweg, "slope": slope,
        "bankfull_stage": thalweg + d_bf,
        # floodplain engages at/above bankfull — never below it (low measured banks +
        # a regional bankfull estimate can otherwise put top-of-bank under bankfull),
        # so the default bank-height ratio (engagement frequency) stays >= 1.
        "floodplain_stage": max(geom.get("top_of_bank_m") or geom.get("fp_stage_m")
                                or (thalweg + d_bf), thalweg + d_bf),
        "bankfull_width_m": geom.get("bankfull_width_m"),
        "bankfull_depth_m": geom.get("bankfull_depth_m"),
        "flood_prone_width_m": geom.get("flood_prone_width_m"),
        "entrenchment_ratio": geom.get("entrenchment_ratio"),
        "bank_height_ratio": geom.get("bank_height_ratio"),
        "edge_limited": geom.get("edge_limited"),
        "bankfull_area_m2": geom.get("bankfull_area_m2"),
        "bankfull_area_edge_limited": geom.get("bankfull_area_edge_limited"),
        "division": geom.get("bankfull_division"),
        "fcode": fcode,
    }


def _build_cross_section(geom: dict, slope=None, fcode=None, unit: str = "ft") -> Optional[dict]:
    """Stash an editable geometry block for every candidate transect (upstream /
    middle / downstream) and render the selected one's PNG (others render on demand
    when switched in the report)."""
    cand_geoms = geom.get("candidates") or [geom]
    res = geom.get("dem_resolution_m")
    src = f"USGS 3DEP {res} m DEM" if res else "USGS 3DEP DEM"
    blocks = []
    for c in cand_geoms:
        b = _xsection_geom_block(c, slope, fcode)
        if b is not None:
            b["label"] = c.get("label")
            b["dem_resolution_m"] = res     # 1 or 10; drives the plot's source caption
            b["dem_source"] = src
            blocks.append(b)
    if not blocks:
        return None
    sel = min(max(int(geom.get("selected", len(blocks) // 2)), 0), len(blocks) - 1)
    try:
        from . import xsplot
        block = blocks[sel]
        er, bhr = block.get("entrenchment_ratio"), block.get("bank_height_ratio")
        png_b64 = xsplot.cross_section_png_b64(
            block["stations"], block["elevs"], bankfull_stage=block["bankfull_stage"],
            floodplain_stage=block["floodplain_stage"], thalweg=block["thalweg"],
            entrenchment_ratio=er, bank_height_ratio=bhr,
            bankfull_width_m=block["bankfull_width_m"],
            bankfull_depth_m=block["bankfull_depth_m"],
            division=block["division"], unit=unit, source=src)
        return {"png_b64": png_b64, "geom": block, "candidates": blocks, "selected": sel,
                "entrenchment_ratio": er, "bank_height_ratio": bhr,
                "caption": _xsection_caption(er, bhr, block["division"])}
    except Exception:  # noqa: BLE001 - resilience by design
        return None


def cross_section_from_stages(block: dict, bankfull_stage: float,
                              floodplain_stage: float, *, unit: str = "ft",
                              er=None, bhr=None, edited: bool = True) -> dict:
    """Redraw the cross-section from chosen stages (metres).

    With ``edited=True`` (default) ER/BHR are recomputed from the stages and the
    measured profile. Passing ``er``/``bhr`` (with ``edited=False``) redraws with
    the *original* ratios — used to switch units on the untouched default without
    diverging from the metric table.
    """
    from . import geomorph, xsplot
    st, el, thalweg = block["stations"], block["elevs"], block.get("thalweg")
    if er is None or bhr is None:
        d = geomorph.derive_from_stages(st, el, thalweg=thalweg,
                                        bankfull_stage=bankfull_stage,
                                        floodplain_stage=floodplain_stage)
        er = d["entrenchment_ratio"] if er is None else er
        bhr = d["bank_height_ratio"] if bhr is None else bhr
        bf_w = d.get("bankfull_width_m") or block.get("bankfull_width_m")
        bf_d = d.get("bankfull_depth_max_m")
    else:
        bf_w, bf_d = block.get("bankfull_width_m"), block.get("bankfull_depth_m")
    png_b64 = xsplot.cross_section_png_b64(
        st, el, bankfull_stage=bankfull_stage, floodplain_stage=floodplain_stage,
        thalweg=thalweg, entrenchment_ratio=er, bank_height_ratio=bhr,
        bankfull_width_m=bf_w, bankfull_depth_m=bf_d,
        division=block.get("division"), unit=unit, source=block.get("dem_source"))
    return {"png_b64": png_b64, "geom": block,
            "entrenchment_ratio": er, "bank_height_ratio": bhr,
            "caption": _xsection_caption(er, bhr, block.get("division"), edited=edited)}


def rate_metrics_from_stages(block: dict, bankfull_stage: float,
                             floodplain_stage: float) -> dict[str, dict]:
    """Recompute the cross-section-derived metric ratings from user-chosen stages.

    Returns ``{metricId: {"rating", "valueText"}}`` for the two metrics the editable
    cross-section drives, each on its own axis: floodplain **access / entrenchment** from
    the entrenchment ratio (lateral) and floodplain **engagement frequency** from the
    bank-height ratio (vertical). They can differ. Reuses ``geomorph.derive_from_stages``
    (the same ER/BHR shown in the cross-section caption); note ER depends on the bankfull
    stage while BHR depends on the floodplain stage. The per-metric ``valueText`` keeps
    the ER-vs-BHR distinction visible on edited rows.
    """
    from . import geomorph
    from .metrics import geomorphology, hydraulics
    out: dict[str, dict] = {}
    d = geomorph.derive_from_stages(
        block["stations"], block["elevs"], thalweg=block.get("thalweg"),
        bankfull_stage=bankfull_stage, floodplain_stage=floodplain_stage)
    er = d.get("entrenchment_ratio")
    er_rating = hydraulics.rate_entrenchment(er)
    if er_rating:
        out[hydraulics.ENTRENCHMENT_ID] = {
            "rating": er_rating,
            "valueText": f"entrenchment ratio {er} — floodprone width / bankfull "
                         f"width (edited cross-section)"}
    bhr = d.get("bank_height_ratio")
    eng_rating, t_years = hydraulics.rate_engagement(bhr)
    if eng_rating:
        out[hydraulics.FLOODPLAIN_ENGAGEMENT_ID] = {
            "rating": eng_rating,
            "valueText": f"floodplain engaged by ~{t_years:.0f}-yr flow — bank-height "
                         f"ratio {bhr} (edited cross-section)"}
    # channel-evolution stage shares this BHR; re-rate it too, but a channelized
    # reach (canal/ditch) stays Poor regardless of geometry edits.
    if block.get("fcode") not in geomorphology.CHANNELIZED_FCODES:
        ce_rating = geomorphology.rate_channel_evolution(bhr)
        if ce_rating:
            out[geomorphology.CHANNEL_EVOL_ID] = {
                "rating": ce_rating,
                "valueText": f"bank-height ratio {bhr} (edited cross-section)"}
    return out


def _finalize(rows: list[dict], total_count: int, overrides_applied) -> dict:
    """Build the scored report dict (rollup) from finished metric rows."""
    meta = config.metrics_by_id()
    ratings = {r["metricId"]: r["rating"] for r in rows if r["rating"] in VALID}
    function_scores = {
        meta[mid]["functionId"]: scoring.function_score(
            scoring.rating_to_index(rt, meta[mid].get("indexMidpoints")))
        for mid, rt in ratings.items()
    }
    roll = scoring.rollup(function_scores)
    return {
        "metricRows": rows,
        "functionScores": roll.function_scores,
        "subIndices": {k: scoring.round2(v) for k, v in roll.sub_indices.items()},
        "outcomes": {
            k: {"direct": o.direct, "indirect": o.indirect,
                "weighted": scoring.round2(o.weighted), "max": scoring.round2(o.max),
                "subIndex": scoring.round2(o.sub_index)}
            for k, o in roll.outcomes.items()
        },
        "ecosystemConditionIndex": scoring.round2(roll.ecosystem_condition_index),
        "computedCount": len(ratings),
        "totalCount": total_count,
        "overridesApplied": sorted(overrides_applied),
    }


def rescore(base_report: dict, overrides: Optional[dict[str, str]]) -> dict:
    """Re-apply user overrides to a base report and recompute the rollup.

    Pure / synchronous (no network) — drives instant override updates in the UI.
    ``base_report`` is the generated (overrides-free) report from ``assess``.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v in VALID}
    meta = config.metrics_by_id()
    rows: list[dict] = []
    for base in base_report.get("metricRows", []):
        mid = base["metricId"]
        row = dict(base)
        if mid in overrides:
            rating = overrides[mid]
            idx = scoring.rating_to_index(rating, meta[mid].get("indexMidpoints"))
            row.update(rating=rating, status="override", source="user override",
                       valueText=f"user-provided: {rating}", note="overrides generated value",
                       index=round(idx, 3), functionScore=scoring.function_score(idx),
                       criteria=meta[mid].get("criteria", {}).get(rating, ""))
        else:
            row["rating"] = base.get("generatedRating")
        rows.append(row)
    result = _finalize(rows, base_report.get("totalCount", len(meta)), overrides)
    # The cross-section + basin characteristics depend only on geometry — carry
    # them through unchanged so overrides never recompute them.
    if base_report.get("crossSection"):
        result["crossSection"] = base_report["crossSection"]
    if base_report.get("basin"):
        result["basin"] = base_report["basin"]
    return result
