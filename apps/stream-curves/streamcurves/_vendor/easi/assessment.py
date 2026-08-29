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

from . import basin, bieger, config, scoring, screening_methods
from .datasources import nlcd, nrsa, streamcat, threedep, wbd
from .metrics import registry
from .metrics.base import AnalysisContext, MetricResult, unavailable

VALID = {"Good", "Fair", "Poor"}


async def _to_thread(fn, *args):
    return await anyio.to_thread.run_sync(fn, *args)


async def assess(ctx: AnalysisContext, *,
                 metric_ids: Optional[list[str]] = None,
                 sources: Optional[dict[str, str]] = None,
                 overrides: Optional[dict[str, str]] = None,
                 prefetch: bool = True,
                 progress: Optional[dict] = None) -> dict:
    """Score the selected EASI metrics for ``ctx``.

    ``metric_ids`` limits which functions are computed (default = all registered);
    unselected ones appear as ``status="excluded"`` and drop out of the rollup.
    ``overrides`` force Good/Fair/Poor. ``progress`` is an optional shared dict updated
    as adapters finish (``{"done": int, "total": int}``) for live "X/N" feedback.

    ``sources``/``prefetch`` drive the source-variant plumbing consumed by
    ``apply_source_choices``. Every metric now resolves one fixed automatic hierarchy
    (connected observation -> published model -> screening proxy), so no adapter offers
    competing automatic formulas and nothing populates variants today; the parameters and
    the merge path are retained for manually supplied evidence.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v in VALID}
    selected = set(metric_ids) if metric_ids is not None else set(registry.REGISTRY)
    ctx.extras["source_choices"] = dict(sources or {})
    ctx.extras["prefetch_variants"] = bool(prefetch)
    if progress is not None:
        progress["done"] = 0
        progress["total"] = sum(1 for m in registry.REGISTRY if m in selected)
        progress["waiting"] = {}   # {service label: in-flight count} for the live "waiting on…" hint

    # Regional bankfull (Bieger 2015) for this location — the default geometry the
    # cross-section, ER/BHR, and floodplain hydraulics build on (overrideable in UI).
    bf = bieger.bankfull_geometry(ctx.drainage_area_sqkm, ctx.lat, ctx.lon)

    # --- prefetch shared data concurrently (off the event loop) ---
    sc, lc, huc12, geom, nrsa_record = await asyncio.gather(
        _to_thread(streamcat.metrics_by_comid, ctx.comid, registry.STREAMCAT_NAMES),
        _to_thread(nlcd.watershed_landcover, ctx.watershed_geojson),
        _to_thread(wbd.huc12_at_point, ctx.lat, ctx.lon),
        _to_thread(lambda: threedep.reach_geomorphology(
            ctx.reach_geojson, ctx.drainage_area_sqkm,
            bankfull=(bf["width_m"], bf["depth_m"]), bankfull_area_m2=bf["area_m2"],
            division=bf["division_name"])),
        _to_thread(nrsa.evidence_for_reach, ctx.comid, ctx.lat, ctx.lon),
    )
    ctx.extras["streamcat"] = sc
    ctx.extras["landcover"] = lc
    ctx.huc12 = huc12
    if isinstance(geom, dict):
        # Bieger fit-range flag rides the geometry block so the cross-section
        # adapters can downgrade confidence when bankfull is extrapolated.
        geom["bankfull_extrapolated"] = bool(bf.get("extrapolated"))
        geom["bankfull_fit_range_sqkm"] = bf.get("fit_range_sqkm")
    ctx.extras["reach_geomorph"] = geom
    ctx.extras["nrsa"] = nrsa_record

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
        catalog_method = screening_methods.method_for(mid)
        res = by_id.get(mid)
        if res is not None:
            generated, source, value_text = res.rating, res.source, res.value_text
            status, confidence, note = res.status, res.confidence, res.note
            scoring_trace = res.scoring
        elif mid in registry.REGISTRY and mid not in selected:
            generated, source, value_text = None, "", "not included in this analysis"
            status, confidence = "excluded", info.get("confidence", "L")
            note = "excluded from this analysis"
            scoring_trace = None
        else:
            generated, source, value_text = None, "", "not available"
            status, confidence = "pending", info.get("confidence", "L")
            note = "metric adapter not yet implemented"
            scoring_trace = None

        rating = generated
        generated_value_text = value_text
        effective_override = None
        if mid in overrides and status != "excluded":  # user override wins
            rating = overrides[mid]
            status, source = "override", "user override"
            value_text = f"user-provided: {rating}"
            note = "overrides generated value"
            effective_override = {
                "rating": rating,
                "generatedRating": generated,
                "generatedValueText": generated_value_text,
            }

        idx = fscore = None
        if rating in VALID:
            idx = scoring.rating_to_index(rating, meta.get("indexMidpoints"))
            fscore = scoring.function_score(idx)

        # ``detail`` remains presentation-only. The scoring contract lives in the
        # dedicated trace and is preserved even when a user overrides the result.
        detail = res.detail if res else None
        land_cover = detail if (detail and "governing" in detail) else None
        rip_veg = detail if (detail and detail.get("kind") == "riparian_veg") else None
        method = screening_methods.method_for_trace(mid, scoring_trace)
        method_criteria = screening_methods.criteria_for(
            method, (scoring_trace or {}).get("context") or {})
        automated = method_criteria.get("automated") or []
        bands = dict(automated[0].get("bands") or {}) if len(automated) == 1 else {}
        rows.append({
            # ``name`` stays the STAF metric name shared with SFARI/DEEP and the site's
            # metric library; ``methodTitle`` carries the revised automated-method identity
            # (e.g. "Thermal-regulation vulnerability") for the Scoring method panel.
            "metricId": mid, "name": meta["name"], "discipline": meta["discipline"],
            "methodTitle": catalog_method["title"],
            "functionId": meta["functionId"], "functionName": meta["functionName"],
            "scale": info.get("scale"), "confidence": confidence,
            "rating": rating, "generatedRating": generated,
            "generatedValueText": generated_value_text,
            "index": round(idx, 3) if idx is not None else None,
            "functionScore": fscore, "valueText": value_text,
            "criteria": bands.get(rating, "") if rating in VALID else "",
            "criteriaBands": bands,
            "methodCriteria": method_criteria,
            "methodKey": method["methodKey"],
            "methodKind": method["operator"],
            "basisClass": method["basisClass"],
            "sourceTier": (scoring_trace or {}).get("sourceTier"),
            "evidenceFamily": (scoring_trace or {}).get("evidenceFamily"),
            "usedFallback": bool((scoring_trace or {}).get("usedFallback")),
            "observedOverridesProxy": bool(
                (scoring_trace or {}).get("observedOverridesProxy")),
            "completeness": ((scoring_trace or {}).get("completeness")
                             or ("not_assessed" if rating is None else "complete")),
            "scoring": scoring_trace,
            "effectiveOverride": effective_override,
            "landCover": land_cover,
            "ripVeg": rip_veg,
            "source": source, "status": status, "note": note,
            "overrideable": bool(info.get("overrideable")),
        })

    _annotate_anchors(rows, ctx.extras.get("siteAnchor"))
    result = _finalize(rows, len(meta_by_id), overrides)
    if cross_section:
        result["crossSection"] = cross_section
    result["basin"] = basin.basin_characteristics(ctx)
    return result


# Labels for the per-metric anchoring, keyed (anchor kind, is routed site).
_ANCHOR_LABELS = {
    ("clickedReach", True): "clicked HR reach",
    ("clickedPoint", True): "clicked point",
    ("surrogateComid", True): "surrogate reach (COMID {comid})",
    ("surrogateWatershed", True): "surrogate watershed",
    ("clickedReach", False): "assessed reach",
    ("clickedPoint", False): "assessment point",
    ("surrogateComid", False): "assessed reach (COMID {comid})",
    ("surrogateWatershed", False): "assessed watershed",
}


def _annotate_anchors(rows: list[dict], site_anchor: Optional[dict]) -> None:
    """Stamp every row with its framework-fixed anchor + a human label.

    For covered (v2Direct or unanchored) runs the labels are neutral and
    nothing else changes, so historical results are label-only enriched. For a
    routed site the labels name the substitution, the StreamCat-fallback rule
    forces surrogateWatershed, and the per-metric table is stamped onto
    ``siteAnchor["metricAnchors"]`` for the report banner. If Phase 2
    re-anchoring did not actually apply (HR data unavailable), every clicked-*
    label says so rather than claiming a re-anchor that never happened.
    """
    anchor = site_anchor or {}
    routed = anchor.get("anchorKind") == "hrSurrogate"
    applied = bool((anchor.get("reanchored") or {}).get("applied"))
    comid = (anchor.get("scoredReach") or {}).get("comid")
    table: dict[str, dict] = {}
    for r in rows:
        a = registry.METRIC_ANCHOR.get(r["metricId"], "surrogateWatershed")
        if r.get("usedFallback"):
            a = "surrogateWatershed"     # every fallback source is StreamCat
        if routed and not applied and a in ("clickedReach", "clickedPoint"):
            label = "surrogate reach (HR data unavailable)"
        else:
            label = _ANCHOR_LABELS[(a, routed)].format(comid=comid)
        r["anchor"] = a
        r["anchorLabel"] = label
        table[r["metricId"]] = {"anchor": a, "label": label, "name": r["name"]}
    if routed:
        anchor["metricAnchors"] = table


def _xsection_caption(er=None, bhr=None, division=None, *, edited=False) -> str:
    # ER / BHR / widths now live in the summary table beside the plot, not here.
    if edited:
        return ("Edited cross-section. Floodprone width is measured at 2x max "
                "bankfull depth (Rosgen). The bank-height ratio uses your low-bank height.")
    reg = f" (Bieger bankfull, {division})" if division else ""
    return ("Representative 3DEP cross-section" + reg
            + ". DEM screening estimate (10 m). Edit the bankfull and low-bank "
            "heights in the table.")


def _xsection_geom_block(geom: dict, slope, fcode=None) -> Optional[dict]:
    """The minimal geometry the editable UI needs to recompute ER/BHR + redraw."""
    profile = (geom or {}).get("profile")
    d_bf, thalweg = geom.get("bankfull_depth_m"), geom.get("thalweg")
    if not profile or thalweg is None or not d_bf:
        return None
    fp_stage = geom.get("fp_stage_m") or (thalweg + 2.0 * d_bf)
    # Default low-bank stage: the slope-break bank exported by summarize_profile
    # (first definitive flattening onto a depositional surface — a low bench can
    # put it below bankfull), already capped at the floodprone stage. Legacy geom
    # dicts without the export fall back to the crest scan clamped to [bankfull,
    # floodprone]. The user can edit the height freely either way.
    lb_stage = geom.get("low_bank_stage_m")
    if lb_stage is None:
        lb_stage = min(max(geom.get("top_of_bank_m") or fp_stage, thalweg + d_bf), fp_stage)
    return {
        "stations": list(profile["stations"]), "elevs": list(profile["elevs"]),
        "thalweg": thalweg, "slope": slope,
        "bankfull_stage": thalweg + d_bf,
        "floodplain_stage": lb_stage,
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

    Returns ``{metricId: {"rating", "valueText"}}`` for the four metrics the editable
    cross-section drives: floodplain access (ER), floodplain engagement (BHR),
    bank-instability susceptibility (BHR), and channel-adjustment susceptibility
    (BHR + ER, except that a canal/ditch FCODE remains decisive). Reuses
    ``geomorph.derive_from_stages``
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
    er_ev = screening_methods.evaluate(
        hydraulics.ENTRENCHMENT_ID, {"er": er},
        input_meta={"er": {"source": "edited cross section"}}, confidence="M")
    if er_ev.rating:
        out[hydraulics.ENTRENCHMENT_ID] = {
            "rating": er_ev.rating,
            "valueText": f"entrenchment ratio {er} (flood-prone width / bankfull "
                         f"width, edited cross section)",
            "scoring": er_ev.trace}
    bhr = d.get("bank_height_ratio")
    bhr_ev = screening_methods.evaluate(
        hydraulics.FLOODPLAIN_ENGAGEMENT_ID, {"bhr": bhr},
        input_meta={"bhr": {"source": "edited cross section"}}, confidence="M")
    if bhr_ev.rating:
        out[hydraulics.FLOODPLAIN_ENGAGEMENT_ID] = {
            "rating": bhr_ev.rating,
            "valueText": f"bank-height ratio {bhr} (edited cross section)",
            "scoring": bhr_ev.trace}
    bank_ev = screening_methods.evaluate(
        geomorphology.BANK_EROSION_ID, {"bhr": bhr},
        input_meta={"bhr": {"source": "edited cross section"}},
        confidence="L", source_tier="screening-proxy",
        evidence_family="incision_geometry", used_fallback=True)
    if bank_ev.rating:
        out[geomorphology.BANK_EROSION_ID] = {
            "rating": bank_ev.rating,
            "valueText": (f"bank-instability susceptibility from BHR {bhr} "
                          "(edited cross section)"),
            "scoring": bank_ev.trace}
    fcode = block.get("fcode")
    channelized = fcode in geomorphology.CHANNELIZED_FCODES
    channel_ev = screening_methods.evaluate(
        geomorphology.CHANNEL_EVOL_ID,
        ({"fcode": fcode} if channelized
         else {"bhr": bhr, "er": er, "fcodeContext": fcode}),
        input_meta=(
            {"fcode": {"source": "NHDPlus FCODE"}} if channelized else {
                "bhr": {"source": "edited cross section"},
                "er": {"source": "edited cross section"},
                "fcodeContext": {"source": "NHDPlus FCODE"},
            }),
        confidence="M" if channelized else "L",
        variant_key="channelized-fcode" if channelized else None,
        source_tier="screening-proxy",
        evidence_family="channelization_class" if channelized else "incision_geometry",
        used_fallback=True)
    if channel_ev.rating:
        out[geomorphology.CHANNEL_EVOL_ID] = {
            "rating": channel_ev.rating,
            "valueText": (
                f"canal/ditch classification (NHD FCODE {fcode})"
                if channelized else
                f"channel-adjustment susceptibility (BHR {bhr}, ER {er}, "
                "edited cross section)"),
            "scoring": channel_ev.trace}
    return out


def apply_observed_evidence(report: dict, evidence: Optional[dict[str, dict]]) -> dict:
    """Replace bank/channel proxies with complete, stronger user observations.

    The automatic proxy remains in ``proxyResult`` and ``generatedRating`` so
    cross-section edits can continue to update it underneath the effective
    observed result.
    """
    supplied = dict(evidence or {})
    meta = config.metrics_by_id()
    from .metrics import geomorphology

    rows = [dict(row) for row in report.get("metricRows", [])]
    applied: list[str] = []
    for row in rows:
        mid = row.get("metricId")
        item = supplied.get(mid) or {}
        ev = None
        value_text = ""
        source = ""
        note = ""
        if mid == geomorphology.BANK_EROSION_ID:
            erosion = item.get("erodingBankPct")
            armoring = item.get("armoredBankPct")
            if erosion is None or armoring is None:
                continue
            values = {
                "erodingBankPct": erosion,
                "armoredBankPct": armoring,
                "annualRetreatContext": item.get("annualRetreatContext"),
            }
            ev = screening_methods.evaluate(
                mid, values,
                input_meta={key: {"source": "user field/verified-imagery observation"}
                            for key in values},
                confidence="H", variant_key="observed-bank-condition",
                source_tier="observed", evidence_family="bank_observation",
                used_fallback=False, observed_overrides_proxy=True)
            value_text = (f"observed bank condition ({float(erosion):.1f}% eroding, "
                          f"{float(armoring):.1f}% armored)")
            source = "user-observed bank erosion and armoring"
            note = "Both observed components supplied; the worse component governs."
            if item.get("annualRetreatContext") not in (None, ""):
                note += (f" Annual retreat {item['annualRetreatContext']} is supporting "
                         "evidence only and is not universally classified.")
        elif mid == geomorphology.CHANNEL_EVOL_ID:
            stage_class = item.get("stageClass")
            indicators = str(item.get("indicators") or "").strip()
            if stage_class not in VALID or not indicators:
                continue
            values = {"stageClass": stage_class, "indicators": indicators}
            ev = screening_methods.evaluate(
                mid, values,
                input_meta={
                    "stageClass": {"source": "user field assessment"},
                    "indicators": {"source": "user field notes"},
                },
                confidence="H", variant_key="observed-channel-adjustment",
                source_tier="observed", evidence_family="channel_observation",
                used_fallback=False, observed_overrides_proxy=True)
            labels = {"Good": "stable or recovered", "Fair": "moderately adjusting",
                      "Poor": "severely or destructively adjusting"}
            value_text = f"observed channel condition: {labels[stage_class]}"
            source = "user-documented channel-stage assessment"
            note = f"Observed indicators: {indicators}"
        if ev is None or ev.rating not in VALID:
            continue

        proxy = {
            "rating": row.get("generatedRating") or row.get("rating"),
            "valueText": row.get("generatedValueText") or row.get("valueText"),
            "source": row.get("source"),
            "scoring": row.get("scoring"),
        }
        method = screening_methods.method_for_trace(mid, ev.trace)
        criteria = screening_methods.criteria_for(method, ev.trace.get("context") or {})
        automated = criteria.get("automated") or []
        bands = dict(automated[0].get("bands") or {}) if len(automated) == 1 else {}
        idx = scoring.rating_to_index(ev.rating, meta[mid].get("indexMidpoints"))
        row.update(
            rating=ev.rating,
            index=round(idx, 3),
            functionScore=scoring.function_score(idx),
            valueText=value_text,
            source=source,
            status="observed",
            confidence="H",
            note=note,
            scoring=ev.trace,
            methodKey=method["methodKey"],
            methodKind=method["operator"],
            basisClass=method["basisClass"],
            sourceTier=ev.trace.get("sourceTier"),
            evidenceFamily=ev.trace.get("evidenceFamily"),
            usedFallback=False,
            observedOverridesProxy=True,
            completeness=ev.trace.get("completeness") or "complete",
            methodCriteria=criteria,
            criteriaBands=bands,
            criteria=bands.get(ev.rating, ""),
            proxyResult=proxy,
            effectiveOverride={
                "kind": "observed",
                "rating": ev.rating,
                "generatedRating": proxy["rating"],
                "generatedValueText": proxy["valueText"],
            },
        )
        applied.append(mid)

    result = _finalize(
        rows, report.get("totalCount", len(meta)),
        set(report.get("overridesApplied") or []) - set(applied))
    for key in ("crossSection", "basin"):
        if report.get(key):
            result[key] = report[key]
    result["observedEvidenceApplied"] = sorted(applied)
    return result


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
    selected_rows = [row for row in rows if row.get("status") != "excluded"]
    rated_rows = [row for row in selected_rows if row.get("rating") in VALID]
    overall_fraction = (len(rated_rows) / len(selected_rows) if selected_rows else None)
    mapping = config.cwa_mapping()
    outcome_coverage: dict[str, dict] = {}
    for outcome in config.OUTCOMES:
        selected_weight = 0.0
        available_weight = 0.0
        for row in selected_rows:
            code = (mapping.get(row.get("functionId")) or {}).get(outcome, "-")
            weight = config.WEIGHTS.get(code, 0.0)
            selected_weight += weight
            if row.get("rating") in VALID:
                available_weight += weight
        fraction = available_weight / selected_weight if selected_weight > 0 else None
        outcome_coverage[outcome] = {
            "availableWeight": round(available_weight, 3),
            "selectedWeight": round(selected_weight, 3),
            "fraction": None if fraction is None else round(fraction, 4),
        }
    limited = []
    if overall_fraction is not None and overall_fraction < 0.70:
        limited.append("overall")
    limited.extend(
        outcome for outcome, item in outcome_coverage.items()
        if item["fraction"] is not None and item["fraction"] < 0.70)
    coverage = {
        "overall": {
            "rated": len(rated_rows),
            "selected": len(selected_rows),
            "fraction": None if overall_fraction is None else round(overall_fraction, 4),
        },
        "outcomes": outcome_coverage,
        "threshold": 0.70,
        "provisional": bool(limited),
        "limited": limited,
    }
    evidence_profile = {
        "observed": 0,
        "connectedNearby": 0,
        "publishedModel": 0,
        "screeningProxy": 0,
        "manual": 0,
        "unavailable": 0,
    }
    family_rows: dict[str, list[dict]] = {}
    for row in selected_rows:
        if row.get("rating") not in VALID:
            evidence_profile["unavailable"] += 1
            continue
        if row.get("status") == "override":
            evidence_profile["manual"] += 1
            continue
        trace = row.get("scoring") or {}
        tier = trace.get("sourceTier") or row.get("sourceTier") or "screening-proxy"
        profile_key = {
            "observed": "observed",
            "connected-nearby": "connectedNearby",
            "published-model": "publishedModel",
            "screening-proxy": "screeningProxy",
            "manual": "manual",
        }.get(tier, "screeningProxy")
        evidence_profile[profile_key] += 1
        family = trace.get("evidenceFamily") or row.get("evidenceFamily")
        if family:
            family_rows.setdefault(family, []).append(row)
    family_labels = {
        "incision_geometry": "BHR/ER incision geometry",
        "iwi_landscape": "StreamCat integrity components",
        "nrsa_field": "shared NRSA reach evidence",
    }
    correlation_notes = []
    for family, related in sorted(family_rows.items()):
        if len(related) < 2 or family not in family_labels:
            continue
        names = [row.get("name") or row.get("metricId") for row in related]
        correlation_notes.append({
            "evidenceFamily": family,
            "label": family_labels[family],
            "metricCount": len(related),
            "metrics": names,
            "text": (f"{family_labels[family]} is reused by {len(related)} metrics "
                     "and should not be interpreted as independent evidence."),
        })
    proxy_derived = any(
        row.get("rating") in VALID
        and ((row.get("scoring") or {}).get("usedFallback")
             or (row.get("scoring") or {}).get("sourceTier") == "screening-proxy")
        for row in selected_rows)
    complete_with_proxies = bool(overall_fraction == 1.0 and proxy_derived)
    coverage.update({
        "evidenceProfile": evidence_profile,
        "completeWithProxies": complete_with_proxies,
        "statusMessage": (
            "Complete screening coverage (includes proxy-derived ratings)"
            if complete_with_proxies else
            ("Complete screening coverage" if overall_fraction == 1.0 else
             "Partial screening coverage")),
        "correlationNotes": correlation_notes,
    })
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
        "ecosystemConditionIndexRaw": roll.ecosystem_condition_index,
        "subIndicesRaw": roll.sub_indices,
        "computedCount": len(rated_rows),
        "selectedCount": len(selected_rows),
        "totalCount": total_count,
        "coverage": coverage,
        "evidenceProfile": evidence_profile,
        "completeScreeningCoverage": bool(overall_fraction == 1.0),
        "proxyDerivedRatings": proxy_derived,
        "correlationNotes": correlation_notes,
        "provisionalCoverage": bool(limited),
        "overridesApplied": sorted(overrides_applied),
    }


def _serialize_variant(mid: str, meta: dict, v) -> dict:
    """Serialize one prefetched source variant (a MetricResult) into the compact
    dict the worksheet card and read-only report read. ``available`` is False when
    that source produced no rating (its ``<option>`` is disabled in the UI)."""
    rating = v.rating
    idx = fscore = None
    if rating in VALID:
        idx = scoring.rating_to_index(rating, meta.get("indexMidpoints"))
        fscore = scoring.function_score(idx)
    detail = v.detail
    land_cover = detail if (detail and "governing" in detail) else None
    rip_veg = detail if (detail and detail.get("kind") == "riparian_veg") else None
    bands = config.criteria_bands(mid, (land_cover or {}).get("governing"))
    return {
        "rating": rating, "generatedRating": rating,
        "index": round(idx, 3) if idx is not None else None,
        "functionScore": fscore, "valueText": v.value_text,
        "source": v.source, "confidence": v.confidence, "note": v.note,
        "status": v.status,
        "criteria": bands.get(rating, "") if rating in VALID else "",
        "criteriaBands": bands, "landCover": land_cover, "ripVeg": rip_veg,
        "scoring": v.scoring,
        "available": rating in VALID,
    }


# fields a chosen source variant overwrites on its row (the generated view; a later
# rescore() lets an explicit override win)
_VARIANT_FIELDS = ("rating", "generatedRating", "index", "functionScore", "valueText",
                   "source", "confidence", "note", "status", "criteria",
                   "criteriaBands", "landCover", "ripVeg", "scoring")


def apply_source_choices(base_report: dict, choices: Optional[dict[str, str]]) -> dict:
    """Merge a chosen source variant into each matching row (pure / synchronous).

    Retained for manually supplied evidence. Automatic scoring no longer offers competing
    formulas — each metric resolves one fixed hierarchy — so no row carries
    ``sourceVariants`` today and this passes every row through unchanged.
    """
    choices = choices or {}
    if not choices:
        return base_report
    out = dict(base_report)
    new_rows = []
    for base in base_report.get("metricRows", []):
        row = dict(base)
        variants = row.get("sourceVariants") or {}
        key = choices.get(row["metricId"])
        chosen = variants.get(key) if key else None
        if chosen and chosen.get("available") and key != row.get("sourceChoice"):
            for f in _VARIANT_FIELDS:
                row[f] = chosen.get(f)
            row["sourceChoice"] = key
        new_rows.append(row)
    out["metricRows"] = new_rows
    return out


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
        if mid in overrides and base.get("status") != "excluded":
            rating = overrides[mid]
            idx = scoring.rating_to_index(rating, meta[mid].get("indexMidpoints"))
            # Keep criteria indicator-aware through overrides via the row's carried bands.
            row.update(rating=rating, status="override", source="user override",
                       valueText=f"user-provided: {rating}", note="overrides generated value",
                       index=round(idx, 3), functionScore=scoring.function_score(idx),
                       criteria=(base.get("criteriaBands") or {}).get(rating, ""),
                       effectiveOverride={
                           "rating": rating,
                           "generatedRating": base.get("generatedRating"),
                           "generatedValueText": base.get("generatedValueText"),
                       })
        else:
            row["rating"] = base.get("generatedRating")
            row["effectiveOverride"] = None
        rows.append(row)
    result = _finalize(rows, base_report.get("totalCount", len(meta)), overrides)
    # The cross-section + basin characteristics depend only on geometry — carry
    # them through unchanged so overrides never recompute them.
    if base_report.get("crossSection"):
        result["crossSection"] = base_report["crossSection"]
    if base_report.get("basin"):
        result["basin"] = base_report["basin"]
    return result
