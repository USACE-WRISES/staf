"""Validate stage - overlay field data on the published curves, record the
validation into the library, certify in place.

Post-publish by design: the target is the published version the session's
origin points at (a fresh publish sets it; opening a published assessment from
the library sets it), and nothing here gates publishing. The overlay is
evidence for the human's judgement, not a statistical acceptance test: scored
points on the curve tiles, one concordance line each, and the decision form.
A record can be written without an upload (checks made outside the app count).

Reuses the stage-5 tile pipeline (curve_gallery.gallery_rows ->
curve_svg.tile_svg with the overlay parameter) and the library's append-only
validation record (add_validation_record / set_version_validation /
set_version_status), so nothing here invents a second rendering or a second
audit trail.
"""
from __future__ import annotations

import os
import statistics

import pandas as pd
from shiny import module, reactive, render, req, ui

from streamcurves import curve_svg as cs
from streamcurves import curves as scurves
from streamcurves import library as lib
from views import curve_gallery as cg
from views.state import AppState
from views.theme import bi
from views.uihelpers import guard, lifecycle_badge, not_ready_panel

#: Case-insensitive header aliases for the minimal CSV (site, metric, value).
_COL_ALIASES = {
    "site": ("site", "site_id", "station", "station_id", "site_name"),
    "metric": ("metric", "metric_code", "code", "metric_key"),
    "value": ("value", "measured_value", "result", "measurement"),
}

_OUTCOMES = {"match": "Matches the curves",
             "minor": "Minor differences",
             "major": "Major differences"}


def _maintainer() -> str:
    """Same chain views/publish.py and the Region builder use."""
    return (os.environ.get("STAF_LIBRARY_MAINTAINER")
            or os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()


def parse_field_data(df: pd.DataFrame, metric_config: dict) -> dict:
    """The upload, matched to this assessment's metrics. Pure, so tests need no
    Shiny.

    Returns ``{values: {metric_key: [float, ...]}, sites: {metric_key: [...]},
    unmatched: [code, ...], n_rows, n_sites, n_dropped}``. Raises ValueError
    naming the missing column when the CSV has no usable header."""
    cols = {str(c).strip().lower(): c for c in df.columns}
    found = {}
    for role, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias in cols:
                found[role] = cols[alias]
                break
    missing = [r for r in ("metric", "value") if r not in found]
    if missing:
        raise ValueError(
            "The CSV needs columns for " + " and ".join(missing)
            + " (accepted headers: "
            + "; ".join(f"{r}: {', '.join(_COL_ALIASES[r])}" for r in missing) + ")")
    by_lower = {str(k).lower(): str(k) for k in (metric_config or {})}
    values: dict[str, list[float]] = {}
    sites: dict[str, list[str]] = {}
    unmatched: set[str] = set()
    n_dropped = 0
    site_col = found.get("site")
    all_sites: set[str] = set()
    for _, row in df.iterrows():
        code = str(row[found["metric"]]).strip()
        key = by_lower.get(code.lower())
        if key is None:
            if code and code.lower() != "nan":
                unmatched.add(code)
            continue
        value = pd.to_numeric(row[found["value"]], errors="coerce")
        if pd.isna(value):
            n_dropped += 1
            continue
        values.setdefault(key, []).append(float(value))
        site = str(row[site_col]).strip() if site_col is not None else ""
        sites.setdefault(key, []).append(site)
        if site:
            all_sites.add(site)
    return {"values": values, "sites": sites, "unmatched": sorted(unmatched),
            "n_rows": int(len(df)), "n_sites": len(all_sites),
            "n_dropped": n_dropped}


def _score_band(score: float, bands: tuple[float, float]) -> str:
    lo, hi = bands
    return "good" if score >= hi else ("fair" if score >= lo else "poor")


@module.ui
def validate_ui():
    return ui.output_ui("validate_page")


@module.server
def validate_server(input, output, session, state: AppState, active=None):
    ns = session.ns
    parsed = reactive.value(None)

    def _target() -> tuple[str, int] | None:
        """(assessment_id, version) of the loaded published version, or None."""
        origin = state.assessment_source() or {}
        if origin.get("kind") != "library" or not origin.get("library_id"):
            return None
        return str(origin["library_id"]), int(origin.get("version") or 0)

    @render.ui
    def validate_page():
        if active is not None and not active():
            return None
        state.validation_records()
        target = _target()
        if target is None:
            return not_ready_panel(
                "Nothing to validate yet",
                "Publish a version, or open a published assessment from the library.",
                action_label="Go to Publish",
                goto_nav="publish",
                icon="database")
        aid, ver = target
        name = (lib.read_manifest(aid) or {}).get("assessmentName") or aid
        status = lib.version_status(aid, ver)
        val_state = lib.version_validation_state(aid, ver)
        with reactive.isolate():
            n_records = len(state.validation_records() or [])
        writable = lib.writable()
        maintainer = _maintainer()
        blocked = (None if (writable and maintainer)
                   else ("The library is read-only here." if not writable
                         else "No maintainer name is available for the audit trail."))
        return ui.div(
            ui.div(
                ui.h4(f"{name} v{ver}", class_="mb-0"),
                ui.tags.span(lifecycle_badge(status), class_="ms-2"),
                ui.tags.span(lib.validation_label(val_state),
                             class_="badge ms-1 " + ("text-bg-success"
                                                     if val_state == "validated"
                                                     else "text-bg-warning")),
                ui.tags.span(f"{n_records} record(s)", class_="text-muted small ms-2"),
                class_="d-flex align-items-baseline flex-wrap mb-2"),
            ui.input_file(ns("field_csv"), "Field data (CSV: site, metric, value)",
                          accept=[".csv"], width="26rem"),
            ui.output_ui(ns("overlay_view")),
            ui.div(
                ui.row(
                    ui.column(3, ui.input_select(ns("val_outcome"), "Outcome",
                                                 _OUTCOMES)),
                    ui.column(4, ui.input_text(ns("val_method"), "Check method",
                                               value="field data overlay")),
                    ui.column(5, ui.input_text(ns("val_checker"), "Checker",
                                               placeholder=maintainer or "name")),
                ),
                ui.input_text_area(ns("val_note"), "Validation note", rows=2,
                                   width="100%",
                                   placeholder="Aggregate only, no site data"),
                ui.input_action_button(
                    ns("record_validation"),
                    ui.TagList(bi("ui-checks"), " Record validation"),
                    class_="btn btn-primary btn-sm",
                    disabled="disabled" if blocked else None),
                (ui.div(blocked, class_="text-muted small mt-1") if blocked else None),
                ui.output_ui(ns("approve_block")),
                ui.output_ui(ns("certify_block")),
                class_="card card-body mt-2",
            ),
            class_="validate-page",
        )

    @reactive.effect
    @reactive.event(input.field_csv)
    @guard("read the field data")
    def _read_csv():
        finfo = input.field_csv()
        req(finfo)
        with reactive.isolate():
            mc = state.metric_config() or {}
        try:
            df = pd.read_csv(finfo[0]["datapath"])
            parsed.set({**parse_field_data(df, mc),
                        "filename": finfo[0].get("name")})
        except ValueError as exc:
            parsed.set(None)
            ui.notification_show(str(exc), type="warning", duration=10)

    @render.ui
    def overlay_view():
        data = parsed()
        if not data:
            return None
        if not data["values"]:
            return ui.div(
                "No rows matched this assessment's metric codes."
                + (f" Unmatched: {', '.join(data['unmatched'][:8])}."
                   if data["unmatched"] else ""),
                class_="alert alert-warning py-2 small mt-2")
        rows = cg.gallery_rows(state, metrics=sorted(data["values"]))
        bands = cs.DEEP_INDEX_BANDS
        tiles = []
        for row in rows:
            metric = str(row.get("metric") or "")
            raw = data["values"].get(metric) or []
            strata = [s for s in (row.get("strata") or []) if s.get("points")]
            points = strata[0]["points"] if strata else []
            overlay, scores = [], []
            for v in raw:
                y = scurves.interp_curve(points, v) if points else None
                if y is not None:
                    overlay.append((float(v), float(y)))
                    scores.append(float(y))
            if scores:
                med = statistics.median(scores)
                caption = (f"{len(scores)} value(s), median score {med:.2f} "
                           f"({_score_band(med, bands)})")
                if len(strata) > 1:
                    caption += "; scored on the first stratum"
            else:
                caption = f"{len(raw)} value(s); no curve to score against"
            tiles.append(ui.div(
                ui.div(
                    ui.tags.span(row.get("display_name") or metric,
                                 class_="curve-tile-name"),
                    class_="curve-tile-head"),
                ui.HTML(cs.tile_svg(row, overlay=overlay)),
                ui.div(caption, class_="curve-tile-foot"),
                class_="curve-tile"))
        note = (f"{data['n_sites']} site(s), {len(data['values'])} metric(s) matched"
                + (f", {len(data['unmatched'])} code(s) unmatched"
                   if data["unmatched"] else "")
                + (f", {data['n_dropped']} non-numeric value(s) dropped"
                   if data["n_dropped"] else "") + ".")
        return ui.div(
            ui.div(note, class_="text-muted small mt-1 mb-2"),
            ui.div(*tiles, class_="curve-gallery"),
            class_="mt-2")

    @reactive.effect
    @reactive.event(input.record_validation)
    @guard("record the validation")
    def _record():
        target = _target()
        if target is None:
            return
        aid, ver = target
        maintainer = _maintainer()
        if not lib.writable() or not maintainer:
            ui.notification_show("The library is not writable here.",
                                 type="warning", duration=6)
            return
        with reactive.isolate():
            data = parsed()
        record = {
            "method": (input.val_method() or "").strip() or "field data overlay",
            "checker": (input.val_checker() or "").strip() or maintainer,
            "outcome": input.val_outcome() or "match",
        }
        if data:
            record["nSites"] = data.get("n_sites")
            record["nMetricsMatched"] = len(data.get("values") or {})
        lib.add_validation_record(aid, ver, record, actor=maintainer,
                                  note=(input.val_note() or "").strip() or None)
        n = len(lib._validation_records_for(aid, ver))
        lib.set_version_validation(aid, ver, "validated", {"n_records": n},
                                   maintainer)
        state.validation_records.set(lib._validation_records_for(aid, ver))
        with reactive.isolate():
            stamped = dict(state.run_stage_status() or {})
        stamped["validate"] = {"status": "done",
                               "label": f"{n} validation record(s)."}
        state.run_stage_status.set(stamped)
        ui.notification_show(f"Validation recorded for {aid} v{ver}.",
                             type="message", duration=6)

    def _rebake_and_toast(prefix: str):
        """Fold the change into DEEP's baked registry (status changes move bake
        eligibility) and tell the owner what to commit."""
        baked_ok, baked_msg = lib.rebake_deep()
        if baked_ok:
            ui.notification_show(
                f"{prefix} DEEP's registry is updated: commit apps/library and "
                "apps/deep/data, then redeploy DEEP.",
                type="message", duration=10)
        else:
            ui.notification_show(
                f"{prefix} DEEP registry not auto-updated ({baked_msg}). Run "
                "apps/deep/scripts/bake_library_into_deep.py, then commit "
                "apps/library and apps/deep/data.",
                type="warning", duration=12)

    @render.ui
    def approve_block():
        """Drafts are automation output; approving one IS the human review, so
        the button lives here where the owner is looking at the curves."""
        state.validation_records()
        target = _target()
        if target is None:
            return None
        aid, ver = target
        if lib.version_status(aid, ver) != "draft":
            return None
        if not (lib.writable() and _maintainer()):
            return ui.div("This is a Draft from an automated build. Approving it "
                          "needs a writable library and a maintainer name.",
                          class_="text-muted small mt-2")
        return ui.div(
            ui.div("This is a Draft from an automated build. Approving records "
                   "your review and makes it a Preliminary version DEEP can use.",
                   class_="text-muted small mt-2"),
            ui.input_action_button(
                ns("approve_prelim"), "Approve as Preliminary",
                class_="btn btn-success btn-sm mt-1"),
            class_="mt-1")

    @reactive.effect
    @reactive.event(input.approve_prelim)
    @guard("open the approve confirmation")
    def _approve_ask():
        target = _target()
        if target is None:
            return
        aid, ver = target
        ui.modal_show(ui.modal(
            f"Approve {aid} v{ver} as Preliminary? This records your review on "
            "the audited status history and makes the version eligible for DEEP.",
            title="Approve this draft",
            easy_close=True,
            footer=ui.TagList(
                ui.modal_button("Cancel"),
                ui.input_action_button(ns("approve_confirm"), "Approve",
                                       class_="btn btn-success"))))

    @reactive.effect
    @reactive.event(input.approve_confirm)
    @guard("approve the draft as preliminary")
    def _approve():
        ui.modal_remove()
        target = _target()
        if target is None:
            return
        aid, ver = target
        maintainer = _maintainer()
        lib.set_version_status(aid, ver, "preliminary", maintainer,
                               note="Reviewed in StreamCurves; approved as preliminary.")
        state.validation_records.set(lib._validation_records_for(aid, ver))
        _rebake_and_toast(f"{aid} v{ver} approved as Preliminary.")

    @render.ui
    def certify_block():
        state.validation_records()
        target = _target()
        if target is None:
            return None
        aid, ver = target
        if lib.version_validation_state(aid, ver) != "validated":
            return None
        if lib.version_status(aid, ver) == "certified":
            return ui.div("This version is certified (shown as Final).",
                          class_="text-success small mt-2")
        return ui.div(
            ui.input_action_button(
                ns("certify"), "Certify this version (Final)",
                class_="btn btn-outline-success btn-sm mt-2"),
            class_="mt-1")

    @reactive.effect
    @reactive.event(input.certify)
    @guard("open the certify confirmation")
    def _certify_ask():
        target = _target()
        if target is None:
            return
        aid, ver = target
        ui.modal_show(ui.modal(
            f"Certify {aid} v{ver} as Final? The status change is appended to "
            "the audited record.",
            title="Certify this version",
            easy_close=True,
            footer=ui.TagList(
                ui.modal_button("Cancel"),
                ui.input_action_button(ns("certify_confirm"), "Certify",
                                       class_="btn btn-success"))))

    @reactive.effect
    @reactive.event(input.certify_confirm)
    @guard("certify the version")
    def _certify():
        ui.modal_remove()
        target = _target()
        if target is None:
            return
        aid, ver = target
        maintainer = _maintainer()
        lib.set_version_status(aid, ver, "certified", maintainer,
                               note="Certified after field-data validation.")
        # nudge the page's disk-read renders
        state.validation_records.set(lib._validation_records_for(aid, ver))
        # Certification changes what DEEP prefers (certified beats preliminary
        # for defaultVersion), so the baked registry must follow. This was a
        # pre-existing gap: certify never rebaked.
        _rebake_and_toast(f"{aid} v{ver} certified (shown as Final).")
