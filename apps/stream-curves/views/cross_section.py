"""Cross-Sections page — port of app/modules/mod_cross_section.R.

On-demand per-site geomorphic analysis: snap a site to its NHD reach (NLDI),
pull ~1000 ft of upstream 3DEP terrain, extract 3 cross-sections, let the user
set bankfull / low-bank stages on each, and commit the derived Rosgen metrics
(ER, BHR, bankfull/floodprone widths) back into the working dataset. Results
persist per site in ``state.cross_sections`` (serialized in JSON sessions).

The blocking REST calls run synchronously under ``ui.Progress`` (matching R's
``withProgress``). The 3 profile plots are interactive plotly figures rendered
htmlwidgets-style via ``views/plotly_html.py`` (figure JSON + Plotly.newPlot in
a plain ``@render.ui`` fragment) — R's ``renderPlotly`` mechanism, safe inside
this dynamically re-rendered editor. The modebar is disabled through the
fragment's plotly config, matching R's ``plotly::config(displayModeBar =
FALSE)``.

Workflow (diverges from R's dropdown-only UI by design): analyzed sites are a
chip row — click a chip to view it, ✕ to remove it (session-only; committed
dataset columns are untouched) — with a Site picker + Analyze button beneath
and the auto-detected ID column as a quiet caption (a "change" link reveals
the always-mounted select). Sites stored before the station-units fix
(ad3e8e1) are flagged stale via ``stale_stations`` and must be re-analyzed.
"""

from __future__ import annotations

import copy
import json
import logging
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from shiny import module, reactive, render, req, ui

from streamcurves.geo import bieger_division_at
from streamcurves.geomorph import (
    bieger_division_abbr,
    bieger_geometry,
    derive_from_stages,
    top_of_bank_elev,
)
from streamcurves.paths import DATA_DIR
from streamcurves.profiler import make_unique_name, sanitize_keys
from streamcurves.sites import M_TO_FT, SQKM_TO_SQMI
from streamcurves.terrain import nldi_basin_sqkm, nldi_reach, sample_transect_3dep
from streamcurves.workbook_tables import workbook_sheet_columns
from streamcurves.xs_geometry import (
    build_transects,
    reach_upstream_segment,
    transect_half_width,
)
from views.plotly_html import plotly_html_fragment
from views.rebuild import rebuild_app_from_tables
from views.state import AppState
from views.theme import fa
from views.uihelpers import no_data_alert

logger = logging.getLogger("streamcurves")

N_TRANSECTS = 3
_TRANSECT_NAMES = ["Upstream", "Middle", "Downstream"]
_PHYSIO_PATH = DATA_DIR / "physio_divisions.geojson"

# Column name -> committed-metric display label (R vals / labs, commit block).
_XS_METRICS = {
    "ER_xsec": "Entrenchment ratio (XS)",
    "BHR_xsec": "Bank-height ratio (XS)",
    "bankfull_ft_xsec": "Bankfull width ft (XS)",
    "floodprone_ft_xsec": "Floodprone width ft (XS)",
}


def detect_geo_cols(data) -> dict:
    """Port of R detect_geo_cols — first identifier + lat/lon columns by pattern."""
    nm = list(data.columns)

    def pick(pats):
        for p in pats:
            for c in nm:
                if re.search(p, c, re.IGNORECASE):
                    return c
        return None

    lat = pick([r"^lat$", r"^latitude$", r"^us_?lat", r"lat"])
    lon = pick([r"^lon$", r"^long$", r"^longitude$", r"^us_?lon", r"lon|long"])
    idc = pick([r"^site_?id$", r"^id$", r"^name$", r"^station"])
    if idc is None:
        idc = nm[0] if nm else None
    return {
        "id_col": idc,
        "lat_col": lat,
        "lon_col": lon,
        "ok": lat is not None and lon is not None,
    }


def _coalesce(value, default):
    """R ``%||%`` — replace only None (an empty-string override still wins)."""
    return value if value is not None else default


_STALE_SPAN_M = 2.0


def stale_stations(cs: dict | None) -> bool:
    """True when a stored site's transects predate the lon/lat→metres station
    fix (ad3e8e1): every transect spans < ~2 m (degree-scale stations), so the
    derived bankfull/floodprone widths are bogus (~0). No lonlat is stored per
    transect, so the only remedy is re-running Analyze for the site."""
    transects = (cs or {}).get("transects") or []
    spans = []
    for tr in transects:
        st = np.asarray(tr.get("stations") or [], dtype=float)
        spans.append(float(np.max(st) - np.min(st)) if st.size else 0.0)
    return bool(spans) and max(spans) < _STALE_SPAN_M


def build_transect_plotly(tr: dict) -> go.Figure:
    """Port of R output$xs_<t> (mod_cross_section.R:167-193) — the interactive
    plotly profile: ground trace with station/height hover plus bankfull,
    floodprone (2×), and low-bank stage lines, stationed from the thalweg."""
    stations = np.asarray(tr["stations"], dtype=float)
    elevs = np.asarray(tr["elevs"], dtype=float)
    ti = int(np.argmin(elevs))
    thalweg = float(elevs[ti])
    st = stations - stations[ti]
    h = elevs - thalweg
    bf_h = float(tr["bankfull_h"])
    lb_h = float(tr["low_bank_h"])
    fp_h = 2 * bf_h
    rng = [float(np.min(st)), float(np.max(st))]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=st,
            y=h,
            mode="lines",
            name="Ground",
            line={"color": "#6b4f3a", "width": 2},
            hovertemplate="station %{x:.0f} m<br>%{y:.2f} m<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=rng, y=[bf_h, bf_h], mode="lines", name="Bankfull",
            line={"color": "#1f6fc0", "width": 1.5},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=rng, y=[fp_h, fp_h], mode="lines", name="Floodprone (2x)",
            line={"color": "#9a6b3f", "width": 1, "dash": "dot"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=rng, y=[lb_h, lb_h], mode="lines", name="Low bank",
            line={"color": "#3a8a5c", "width": 1.5, "dash": "dash"},
        )
    )
    # Layout set directly to mirror R's bare plot_ly; template "none" makes
    # plotly.js render on its client-side defaults — exactly what R sends.
    fig.update_layout(
        template="none",
        margin={"l": 45, "r": 10, "t": 10, "b": 35},
        xaxis={"title": {"text": "Station from thalweg (m)"}},
        yaxis={"title": {"text": "Height (m)"}},
        legend={"orientation": "h", "y": -0.2},
        showlegend=True,
    )
    return fig


def register_xs_metric(tables: dict, col: str, display: str) -> dict:
    """Port of R register_xs_metric — add a committed cross-section column to the
    metrics sheet (continuous, higher-is-better, linked to all predictors +
    stratifications). Idempotent on the column name."""
    metrics = tables["metrics"]
    if col in list(metrics["column_name"]):
        return tables

    existing = list(metrics["metric_key"]) + list(tables["predictors"]["predictor_key"])
    key = make_unique_name(sanitize_keys([col])[0], existing)
    metric_cols = list(workbook_sheet_columns()["metrics"])
    new = {c: "" for c in metric_cols}
    new.update(
        {
            "metric_key": key,
            "display_name": display,
            "column_name": col,
            "metric_family": "continuous",
            "higher_is_better": "TRUE",
            "monotonic_linear": "TRUE",
            "preferred_transform": "none",
            "min_sample_size": 8,
            "best_subsets_allowed": "TRUE",
            "count_model": "FALSE",
            "stratification_mode": "covariate",
            "include_in_summary": "TRUE",
            "notes": "cross-section derived",
        }
    )
    new_df = pd.DataFrame([new], columns=metric_cols)

    tables = dict(tables)
    tables["metrics"] = pd.concat([metrics, new_df], ignore_index=True)

    preds = list(tables["predictors"]["predictor_key"])
    if preds:
        mp = pd.DataFrame(
            {
                "metric_key": key,
                "predictor_key": preds,
                "sort_order": range(1, len(preds) + 1),
            }
        )
        tables["metric_predictors"] = pd.concat(
            [tables["metric_predictors"], mp], ignore_index=True
        )
    strats = list(tables["stratifications"]["strat_key"])
    if strats:
        ms = pd.DataFrame(
            {
                "metric_key": key,
                "strat_key": strats,
                "sort_order": range(1, len(strats) + 1),
            }
        )
        tables["metric_stratifications"] = pd.concat(
            [tables["metric_stratifications"], ms], ignore_index=True
        )
    return tables


@module.ui
def cross_section_ui():
    return ui.div(ui.output_ui("panel"), class_="container-fluid py-2 cross-section-module")


@module.server
def cross_section_server(input, output, session, state: AppState):
    ns = session.ns
    selected_site = reactive.value(None)
    analyzed_nonce = reactive.value(0)
    pending_remove = reactive.value(None)  # site id awaiting removal confirm

    def _inp(name):
        try:
            return input[name]()
        except Exception:
            return None

    # ── Geo columns + sites (R geo / sites_df) ────────────────────────────────
    @reactive.calc
    def geo():
        data = state.data()
        req(data is not None)
        g = detect_geo_cols(data)
        g["id_col"] = _coalesce(_inp("id_col"), g["id_col"])
        g["lat_col"] = _coalesce(_inp("lat_col"), g["lat_col"])
        g["lon_col"] = _coalesce(_inp("lon_col"), g["lon_col"])
        cols = list(data.columns)
        g["ok"] = (
            g["lat_col"] is not None
            and g["lon_col"] is not None
            and g["lat_col"] in cols
            and g["lon_col"] in cols
        )
        return g

    @reactive.calc
    def sites_df():
        data = state.data()
        req(data is not None)
        g = geo()
        req(g["ok"])
        return pd.DataFrame(
            {
                "site_id": data[g["id_col"]].astype(str),
                "lat": pd.to_numeric(data[g["lat_col"]], errors="coerce"),
                "lon": pd.to_numeric(data[g["lon_col"]], errors="coerce"),
            }
        )

    def site_da_sqkm(site_id, comid):
        """Prefer an existing DA (sq mi) column for the site, else NLDI basin."""
        data = state.data()
        g = geo()
        da_cols = [c for c in data.columns if re.search(r"^da_?mi2$|drainage", c, re.I)]
        if da_cols:
            mask = data[g["id_col"]].astype(str) == site_id
            if mask.any():
                raw = data.loc[mask, da_cols[0]].iloc[0]
                v = pd.to_numeric(pd.Series([raw]), errors="coerce").iloc[0]
                if pd.notna(v) and v > 0:
                    return float(v) / SQKM_TO_SQMI
        return nldi_basin_sqkm(comid)

    # ── Analyze: snap reach + pull terrain + seed stages (R observeEvent) ──────
    @reactive.effect
    @reactive.event(input.analyze)
    def _analyze():
        site = _inp("site_pick")
        if not site:
            return
        sdf = sites_df()
        row = sdf[sdf["site_id"] == site]
        if row.empty or not np.isfinite(row["lat"].iloc[0]) or not np.isfinite(
            row["lon"].iloc[0]
        ):
            ui.notification_show("That site has no usable coordinates.", type="warning")
            return
        lat = float(row["lat"].iloc[0])
        lon = float(row["lon"].iloc[0])

        with ui.Progress(min=0, max=1) as prog:
            prog.set(0.15, message=f"Analyzing {site}", detail="Snapping to the NHD reach (NLDI)")
            reach = nldi_reach(lat, lon, 1000)
            if reach is None:
                ui.notification_show(
                    "Couldn't snap this site to a flowline (off-network).", type="error"
                )
                return
            da_km = site_da_sqkm(site, reach["comid"])
            try:
                division = bieger_division_at(
                    lon, lat, str(_PHYSIO_PATH), division_abbr=bieger_division_abbr
                )
            except Exception:  # noqa: BLE001
                division = "USA"
            bf = bieger_geometry(
                da_km if (da_km is not None and np.isfinite(da_km)) else 1, division
            )
            half = transect_half_width(bf["width_m"])
            prog.set(0.35, detail="Placing cross-sections")
            seg = reach_upstream_segment(
                reach["coords_lonlat"], reach["snap_lonlat"], length_ft=1000
            )
            n_samp = max(60, min(int(half), 300))
            trans = build_transects(
                seg, n_transects=N_TRANSECTS, half_m=half, n_samp=n_samp
            )

            out = []
            for k, tr in enumerate(trans):
                prog.set(
                    0.35 + 0.6 * (k + 1) / len(trans),
                    detail=f"Sampling 3DEP terrain ({k + 1}/{len(trans)})",
                )
                prof = sample_transect_3dep(tr["lonlat"], tr["stations"])
                if prof is None:
                    continue
                thalweg = float(np.min(prof["elevs"]))
                tob = top_of_bank_elev(prof["stations"], prof["elevs"])
                bf_h = max(bf["depth_m"], 0.05)
                lb_h = max(tob - thalweg, bf_h) if tob is not None else bf_h
                out.append(
                    {
                        "stations": list(np.asarray(prof["stations"], dtype=float)),
                        "elevs": list(np.asarray(prof["elevs"], dtype=float)),
                        "resolution_m": prof["resolution_m"],
                        "bankfull_h": round(bf_h, 3),
                        "low_bank_h": round(lb_h, 3),
                    }
                )

            if not out:
                ui.notification_show(
                    "3DEP returned no usable terrain for this reach.", type="error"
                )
                return

            cs = {
                "comid": reach["comid"],
                "da_sqkm": da_km,
                "division": division,
                "gnis_name": reach["gnis_name"],
                "curve": bf,
                "transects": out,
            }
            store = dict(state.cross_sections() or {})
            store[site] = cs
            state.cross_sections.set(store)
            selected_site.set(site)
            with reactive.isolate():
                analyzed_nonce.set(analyzed_nonce() + 1)

        ui.notification_show(
            f"Analyzed {site}: {len(out)} cross-section(s) from "
            f"{out[0]['resolution_m']} m terrain.",
            type="message",
            duration=5,
        )

    @reactive.effect
    @reactive.event(input.site_pick, ignore_init=True)
    def _sync_selected_site():
        s = _inp("site_pick")
        if s is not None and s in (state.cross_sections() or {}):
            # Equality guard: the picker echo from a chip-select must not
            # trigger a redundant editor re-render.
            if selected_site() != s:
                selected_site.set(s)

    # ── Per-transect metrics + stage editors ──────────────────────────────────
    def transect_metrics(t: int):
        site = selected_site()
        if site is None:
            return None
        cs = (state.cross_sections() or {}).get(site)
        if cs is None or len(cs["transects"]) < t:
            return None
        tr = cs["transects"][t - 1]
        thalweg = float(np.min(np.asarray(tr["elevs"], dtype=float)))
        return derive_from_stages(
            tr["stations"],
            tr["elevs"],
            bankfull_stage=thalweg + float(tr["bankfull_h"]),
            low_bank_stage=thalweg + float(tr["low_bank_h"]),
            thalweg=thalweg,
        )

    def _update_stage(site, idx, field, raw_value):
        try:
            val = float(raw_value)
        except (TypeError, ValueError):
            return
        store = dict(state.cross_sections() or {})
        cs = store.get(site)
        if cs is None or len(cs["transects"]) <= idx:
            return
        cs = copy.deepcopy(cs)
        cs["transects"][idx][field] = val
        store[site] = cs
        state.cross_sections.set(store)

    def _register_transect(t: int):
        idx = t - 1

        @reactive.effect
        @reactive.event(input[f"bf_{t}"], ignore_init=True)
        def _bf():
            site = selected_site()
            req(site)
            _update_stage(site, idx, "bankfull_h", _inp(f"bf_{t}"))

        @reactive.effect
        @reactive.event(input[f"lb_{t}"], ignore_init=True)
        def _lb():
            site = selected_site()
            req(site)
            _update_stage(site, idx, "low_bank_h", _inp(f"lb_{t}"))

        @output(id=f"xs_{t}", suspend_when_hidden=False)
        @render.ui
        def _xs():
            site = selected_site()
            req(site)
            cs = (state.cross_sections() or {}).get(site)
            req(cs is not None and len(cs["transects"]) >= t)
            return plotly_html_fragment(
                build_transect_plotly(cs["transects"][idx]),
                height_px=280,
                # R: plotly::config(displayModeBar = FALSE) (mod_cross_section.R)
                config={"displayModeBar": False},
            )

        @output(id=f"metrics_{t}", suspend_when_hidden=False)
        @render.ui
        def _metrics():
            site = selected_site()
            if site and stale_stations((state.cross_sections() or {}).get(site)):
                # Normally unmounted (the editor swaps to a warning card for
                # stale sites) — belt-and-braces for hand-edited sessions.
                return ui.tags.span(
                    "— re-run Analyze to refresh terrain", class_="text-muted small"
                )
            m = transect_metrics(t)
            if m is None:
                return None
            return _metric_chips(m)

    for _t in range(1, N_TRANSECTS + 1):
        _register_transect(_t)

    def _metric_chips(m: dict):
        def chip(lab, val):
            shown = "—" if val is None else val
            return ui.tags.span(
                f"{lab}: {shown}", class_="badge bg-light text-dark border me-1"
            )

        parts = [
            chip("ER", m["entrenchment_ratio"]),
            chip("BHR", m["bank_height_ratio"]),
            chip("Bankfull W (m)", m["bankfull_width_m"]),
            chip("Floodprone W (m)", m["flood_prone_width_m"]),
        ]
        if m.get("edge_limited"):
            parts.append(
                ui.tags.span(
                    fa("triangle-exclamation"),
                    " floodprone reached the transect edge",
                    class_="text-warning small ms-1",
                )
            )
        return ui.div(*parts, class_="small")

    # ── Reach summary (mean across transects) ─────────────────────────────────
    @reactive.calc
    def reach_metrics():
        site = selected_site()
        req(site)
        cs = (state.cross_sections() or {}).get(site)
        req(cs is not None)
        ms = [transect_metrics(i) for i in range(1, len(cs["transects"]) + 1)]
        ms = [m for m in ms if m is not None]
        if not ms:
            return None

        def avg(field):
            vals = [
                m.get(field)
                for m in ms
                if m.get(field) is not None and np.isfinite(m.get(field))
            ]
            return round(sum(vals) / len(vals), 2) if vals else None

        bw = avg("bankfull_width_m")
        fw = avg("flood_prone_width_m")
        return {
            "ER": avg("entrenchment_ratio"),
            "BHR": avg("bank_height_ratio"),
            "bankfull_width_ft": round(bw * M_TO_FT, 1) if bw is not None else None,
            "floodprone_width_ft": round(fw * M_TO_FT, 1) if fw is not None else None,
            "n": len(ms),
        }

    # suspend_when_hidden=False on the leaf outputs below: they bind while the
    # Cross-Sections tab is hidden (panel renders at data load) and can latch
    # suspended (the bb98c92 wedge) — all are cheap, guarded renders.
    @output(suspend_when_hidden=False)
    @render.ui
    def reach_summary():
        r = reach_metrics()
        site = selected_site()
        cs = (state.cross_sections() or {}).get(site) if site else None
        if r is None or cs is None:
            return None
        if stale_stations(cs):
            # Stale pre-fix stations → bogus ~0 widths; no stat boxes and no
            # Commit button (the editor shows the re-analyze warning).
            return None

        def box(lab, val):
            disp = "—" if val is None else val
            return ui.div(
                ui.div(disp, class_="xsec-stat-val"),
                ui.div(lab, class_="xsec-stat-lab"),
                class_="xsec-stat",
            )

        da = cs.get("da_sqkm")
        da_mi = (da * SQKM_TO_SQMI) if (da is not None and np.isfinite(da)) else float("nan")
        meta = (
            f"COMID {cs['comid']} · {cs['division']} · "
            f"DA {da_mi:.1f} sq mi · {r['n']} transect(s)"
        )
        return ui.div(
            ui.div(
                ui.div(
                    ui.tags.h6(f"Reach summary — {site}", class_="mb-2"),
                    ui.tags.span(meta, class_="text-muted small"),
                    class_="d-flex justify-content-between align-items-center flex-wrap",
                ),
                ui.div(
                    box("Entrenchment ratio", r["ER"]),
                    box("Bank-height ratio", r["BHR"]),
                    box("Bankfull width (ft)", r["bankfull_width_ft"]),
                    box("Floodprone width (ft)", r["floodprone_width_ft"]),
                    class_="xsec-stats d-flex gap-3 flex-wrap mt-1",
                ),
                ui.div(
                    ui.input_action_button(
                        ns("commit"),
                        "Commit these metrics to the dataset",
                        class_="btn-success btn-sm",
                        icon=fa("check"),
                    ),
                    class_="mt-3",
                ),
                class_="card-body",
            ),
            class_="card mb-3",
        )

    # ── Commit derived metrics into the working data sheet ────────────────────
    @reactive.effect
    @reactive.event(input.commit)
    def _commit():
        site = selected_site()
        req(site)
        r = reach_metrics()
        req(r)
        cs = (state.cross_sections() or {}).get(site)
        req(cs is not None and not stale_stations(cs))
        tables = state.input_metadata()
        if not tables or tables.get("data") is None:
            return
        g = geo()
        tables = copy.deepcopy(tables)
        data = tables["data"]
        mask = data[g["id_col"]].astype(str) == site
        if not mask.any():
            ui.notification_show("Site not found in the data sheet.", type="error")
            return
        vals = {
            "ER_xsec": r["ER"],
            "BHR_xsec": r["BHR"],
            "bankfull_ft_xsec": r["bankfull_width_ft"],
            "floodprone_ft_xsec": r["floodprone_width_ft"],
        }
        for nm, val in vals.items():
            if nm not in data.columns:
                data[nm] = np.nan
            data.loc[mask, nm] = val
            tables = register_xs_metric(tables, nm, _XS_METRICS[nm])
        tables["data"] = data

        ok = rebuild_app_from_tables(
            state,
            tables,
            success_text=f"Committed cross-section metrics for {site}.",
            error_prefix="Could not commit cross-section metrics",
        )
        if ok:
            ui.notification_show(
                f"Committed ER/BHR/widths for {site} into the dataset.",
                type="message",
                duration=6,
            )

    # ── Panel + editor UI ─────────────────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def panel():
        # data() is None, not app_data_loaded: matches Reference curves, Regional
        # Curves and phase1-4, which all gate on the frame itself.
        if state.data() is None:
            return no_data_alert()
        g = geo()
        data = state.data()
        cols = list(data.columns)
        if not g["ok"]:
            return ui.div(
                ui.div(
                    ui.tags.h5("No coordinates found"),
                    ui.tags.p(
                        "This dataset has no latitude/longitude columns to locate "
                        "sites. Pick the coordinate columns if they exist under "
                        "different names:",
                        class_="text-muted",
                    ),
                    ui.div(
                        ui.div(
                            ui.input_select(ns("id_col"), "Site ID", cols, selected=g["id_col"]),
                            class_="col-sm-4",
                        ),
                        ui.div(
                            ui.input_select(
                                ns("lat_col"), "Latitude", [""] + cols,
                                selected=g["lat_col"] or "",
                            ),
                            class_="col-sm-4",
                        ),
                        ui.div(
                            ui.input_select(
                                ns("lon_col"), "Longitude", [""] + cols,
                                selected=g["lon_col"] or "",
                            ),
                            class_="col-sm-4",
                        ),
                        class_="row g-2",
                    ),
                    class_="card-body",
                ),
                class_="card border-warning",
            )

        sdf = sites_df()
        # Read selected_site under isolate so the panel renders once (stable
        # structure) — a re-render on every analyze/select would remount the
        # site picker mid-interaction. The analyzed-site chips + count live in
        # separate reactive outputs.
        with reactive.isolate():
            sel = selected_site()
        choices = [str(sid) for sid in sdf["site_id"]]
        default_site = sel if sel in set(choices) else (choices[0] if choices else None)
        auto_id = detect_geo_cols(data)["id_col"]
        return ui.TagList(
            ui.div(
                ui.tags.h4("Geomorphic cross-sections", class_="mb-0"),
                ui.output_ui(ns("analyzed_count")),
                class_="d-flex justify-content-between align-items-center flex-wrap mb-2",
            ),
            ui.div(
                ui.div(
                    ui.output_ui(ns("site_chips")),
                    ui.div(
                        ui.div(
                            ui.input_select(
                                ns("site_pick"), "Site", choices, selected=default_site
                            ),
                            class_="col-sm-6 col-md-5",
                        ),
                        ui.div(
                            ui.input_action_button(
                                ns("analyze"),
                                "Analyze site (pull terrain)",
                                class_="btn-primary",
                                icon=fa("mountain"),
                            ),
                            class_="col-auto",
                        ),
                        class_="row g-2 align-items-end xsec-picker-row",
                    ),
                    ui.div(
                        "Snaps to the NHD reach, pulls ~1000 ft of upstream 3DEP "
                        "terrain, and extracts 3 cross-sections.",
                        class_="form-text",
                    ),
                    ui.div(
                        "ID column: ",
                        ui.tags.code(g["id_col"]),
                        " (auto-detected)" if g["id_col"] == auto_id else None,
                        ui.tags.button(
                            "change",
                            type="button",
                            class_="btn btn-link btn-sm p-0 ms-2 align-baseline",
                            onclick=(
                                f"document.getElementById('{ns('id_col_wrap')}')"
                                ".classList.toggle('d-none');return false;"
                            ),
                        ),
                        class_="form-text xsec-idcol-line",
                    ),
                    # Hidden-but-mounted so input.id_col stays registered (the
                    # inputs-must-be-structurally-static rule; geo() unchanged).
                    ui.div(
                        ui.input_select(
                            ns("id_col"), None, cols, selected=g["id_col"],
                            width="240px",
                        ),
                        id=ns("id_col_wrap"),
                        class_="d-none mt-1",
                    ),
                    class_="card-body",
                ),
                class_="card mb-3",
            ),
            ui.output_ui(ns("reach_summary")),
            ui.output_ui(ns("editor")),
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def analyzed_count():
        sdf = sites_df()
        done = set((state.cross_sections() or {}).keys())
        return ui.tags.span(
            f"{len(sdf)} sites · {len(done)} analyzed", class_="text-muted small"
        )

    # ── Analyzed-site chips: view / remove ────────────────────────────────────
    def _chip_js(site: str, action: str) -> str:
        # One delegated JSON channel (the summary_row_action pattern,
        # summary_page.py) — avoids per-chip dynamic action buttons.
        payload = json.dumps({"site": site, "action": action})
        return (
            f"Shiny.setInputValue('{ns('chip_action')}',{payload},"
            "{priority:'event'})"
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def site_chips():
        store = state.cross_sections() or {}
        sel = selected_site()
        if not store:
            return ui.div(
                "No sites analyzed yet — pick a site below and click Analyze.",
                class_="text-muted small mb-2",
            )
        chips = []
        for sid, cs in store.items():
            stale = stale_stations(cs)
            classes = "wb-chip xsec-chip"
            if sid == sel:
                classes += " xsec-chip-active"
            if stale:
                classes += " xsec-chip-stale"
                title = "Analyzed with an older version — select it and re-run Analyze"
            else:
                title = (
                    f"{len(cs.get('transects') or [])} cross-section(s) — "
                    "click to view"
                )
            chips.append(
                ui.tags.span(
                    ui.TagList(fa("triangle-exclamation"), " ") if stale else None,
                    ui.tags.span(sid, class_="wb-chip-label"),
                    ui.tags.button(
                        ui.HTML("&times;"),
                        type="button",
                        class_="wb-chip-x",
                        title="Remove this site's cross-sections",
                        onclick="event.stopPropagation();" + _chip_js(sid, "remove"),
                    ),
                    class_=classes,
                    title=title,
                    onclick=_chip_js(sid, "select"),
                )
            )
        return ui.div(*chips, class_="xsec-chips")

    @reactive.effect
    @reactive.event(input.chip_action)
    # No ignore_init: the first event on a never-set input IS the init run
    # in py-shiny (see summary_page.py's row-action channel).
    def _chip_action():
        payload = input.chip_action() or {}
        site = payload.get("site")
        action = payload.get("action")
        store = state.cross_sections() or {}
        if not site or site not in store:
            return
        if action == "select":
            if selected_site() != site:
                selected_site.set(site)
            # Sync the picker so "Analyze site (pull terrain)" re-pulls the
            # viewed site — the stale-chip recovery path.
            try:
                if site in set(sites_df()["site_id"].astype(str)) and _inp(
                    "site_pick"
                ) != site:
                    ui.update_select(ns("site_pick"), selected=site, session=session)
            except Exception:  # noqa: BLE001 — geo not ready yet
                pass
        elif action == "remove":
            pending_remove.set(site)
            ui.modal_show(
                ui.modal(
                    ui.tags.p(f"Remove the analyzed cross-sections for {site}?"),
                    ui.tags.p(
                        "This clears the pulled terrain and stage settings for "
                        "this site from the session. Metrics already committed "
                        "to the dataset are not changed. Re-run Analyze at any "
                        "time to rebuild.",
                        class_="text-muted small mb-0",
                    ),
                    title="Remove cross-sections",
                    footer=ui.TagList(
                        ui.modal_button("Cancel"),
                        ui.input_action_button(
                            ns("confirm_remove"), "Remove", class_="btn btn-danger"
                        ),
                    ),
                )
            )

    @reactive.effect
    @reactive.event(input.confirm_remove)
    def _remove_confirmed():
        ui.modal_remove()
        site = pending_remove()
        pending_remove.set(None)
        store = dict(state.cross_sections() or {})
        if site not in store:
            return
        order = list(store)
        idx = order.index(site)
        del store[site]
        state.cross_sections.set(store)
        if selected_site() == site:
            remaining = [s for s in order if s != site]
            selected_site.set(
                remaining[idx]
                if idx < len(remaining)
                else (remaining[-1] if remaining else None)
            )
        ui.notification_show(
            f"Removed cross-sections for {site}.", type="message", duration=4
        )

    @reactive.effect
    def _default_selection():
        # Session restore / project switch: pick the first stored site when the
        # current selection is empty or points at a site that no longer exists.
        # The store dependency is read BEFORE any guard — an effect whose first
        # run early-returns without reading a reactive source dies for good.
        store = state.cross_sections() or {}
        with reactive.isolate():
            current = selected_site()
        if store and (current is None or current not in store):
            selected_site.set(next(iter(store)))

    @output(suspend_when_hidden=False)
    @render.ui
    def editor():
        analyzed_nonce()
        site = selected_site()
        if site is None:
            return ui.div(
                "Pick a site and click Analyze to pull its cross-sections.",
                class_="text-muted",
            )
        with reactive.isolate():
            cs = (state.cross_sections() or {}).get(site)
        if cs is None or not cs.get("transects"):
            return ui.div(
                "No cross-sections yet for this site — click Analyze.",
                class_="text-muted",
            )
        if stale_stations(cs):
            return ui.div(
                ui.div(
                    ui.tags.h6(
                        fa("triangle-exclamation"),
                        f" {site} needs a fresh terrain pull",
                        class_="mb-2 text-warning-emphasis",
                    ),
                    ui.tags.p(
                        "This site was analyzed with an older version of the tool — "
                        "its stored stations are in the wrong units, so widths would "
                        "read as 0. Click “Analyze site (pull terrain)” to "
                        "refresh it.",
                        class_="text-muted mb-0",
                    ),
                    class_="card-body",
                ),
                class_="card border-warning mb-3",
            )

        cards = []
        for t in range(1, len(cs["transects"]) + 1):
            tr = cs["transects"][t - 1]
            elevs = np.asarray(tr["elevs"], dtype=float)
            relief = round(float(elevs.max() - elevs.min()), 1)
            tname = _TRANSECT_NAMES[t - 1] if t - 1 < len(_TRANSECT_NAMES) else str(t)
            cards.append(
                ui.div(
                    ui.div(
                        ui.tags.span(
                            f"Cross-section {t} — {tname}", class_="fw-semibold"
                        ),
                        ui.tags.span(
                            f"{tr['resolution_m']} m DEM · relief {relief} m",
                            class_="text-muted small",
                        ),
                        class_="card-header py-2 d-flex justify-content-between",
                    ),
                    ui.div(
                        ui.div(
                            ui.div(
                                ui.output_ui(ns(f"xs_{t}")),
                                class_="col-lg-8",
                            ),
                            ui.div(
                                ui.tags.label(
                                    "Bankfull height above thalweg (m)",
                                    class_="form-label small mb-1",
                                ),
                                ui.input_numeric(
                                    ns(f"bf_{t}"), None, value=tr["bankfull_h"],
                                    min=0, max=max(relief, 0.5), step=0.05,
                                ),
                                ui.tags.label(
                                    "Low-bank height above thalweg (m)",
                                    class_="form-label small mb-1",
                                ),
                                ui.input_numeric(
                                    ns(f"lb_{t}"), None, value=tr["low_bank_h"],
                                    min=0, max=max(relief, 0.5), step=0.05,
                                ),
                                ui.output_ui(ns(f"metrics_{t}")),
                                class_="col-lg-4",
                            ),
                            class_="row g-3",
                        ),
                        class_="card-body",
                    ),
                    class_="card mb-3",
                )
            )
        return ui.TagList(*cards)
