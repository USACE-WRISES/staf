"""Map-first data-import wizard — port of app/modules/mod_import_map.R.

A single self-contained flow for building a new dataset: 1 Region → 2 Add data
→ 3 Screen sites → 4 Choose metrics → 5 Compile → 6 Classify → 7 Review & build.
The steps have no stepper of their own: the workflow strip (views/stagebar.py)
groups them under its stages and renders the sub-step chips; internal step
numbers stay 1-7 here.
Step 1 defines the region of applicability (EPA Level III ecoregion, state, a
drawn area, or none); all data entry, metric selection, classification and the
build happen inside the wizard. Classify/Build reuse the shared classify UI and
the same build path as everything else (build_config_tables_from_roles →
rebuild_app_from_tables). Compile runs as a detached async task that paints a
bottom-right progress toast between steps (st.task_flush); sources fail to NA.

Maps use ipyleaflet (the DEEP persistent-map pattern): built once, mutated in
place via an ``_layers`` dict, and rendered through shinywidgets. Region
selection is per-feature ``GeoJSON.on_click`` (replacing R's layerId-prefix
routing); polygon draw uses ipyleaflet's native ``DrawControl`` (no
leaflet.extras gap). The one visible behaviour change from R: hover shows a
``WidgetControl`` name readout rather than a sticky leaflet tooltip.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from functools import lru_cache

import numpy as np
import pandas as pd
from shiny import module, reactive, render, req, ui

from pathlib import Path  # noqa: E402

from streamcurves import easi_screening  # noqa: E402
from streamcurves import run_state as rs  # noqa: E402

try:
    from ipyleaflet import (
        DrawControl,
        GeoJSON,
        LayersControl,
        Map,
        ScaleControl,
        TileLayer,
        WidgetControl,
    )
    from ipywidgets import HTML as IPHTML
    from ipywidgets import Layout
    from shinywidgets import output_widget, render_widget

    _HAS_MAP = True
except Exception:  # noqa: BLE001
    _HAS_MAP = False

from streamcurves.datasources.dep3 import epqs_elev
from streamcurves.datasources.mmw import mmw_available, mmw_core_metrics, mmw_site_metrics
from streamcurves.datasources.nldi import nldi_comids
from streamcurves.datasources.streamcat import streamcat_metrics
from streamcurves.datasources.streamstats import ss_basin_characteristics, ss_core_bcs
from streamcurves.geo import point_in_polygon_rings, state_abbr_from_name, state_at
from streamcurves.geomorph import bieger_division_abbr, bieger_geometry
from streamcurves import metric_picker as mpick
from streamcurves import workbook as wb
from streamcurves.metric_map import (
    metric_map_function_label,
    metric_map_functions_for,
)
from streamcurves import nrsa_dataset as nds
from streamcurves.nrsa import (
    attach_nrsa_metrics,
    load_nrsa_catalog,
    load_nrsa_values,
    nrsa_source_for,
)
from streamcurves.paths import DATA_DIR
from streamcurves.profiler import (
    build_config_tables_from_roles,
    current_role_membership,
    profile_and_suggest,
    profile_columns,
    reconcile_tables_with_new_data,
)
from streamcurves.sites import (
    assemble_sites,
    compile_site_table,
)
from streamcurves.sites import coverage_table as compute_coverage_table
from streamcurves.staf_library import staf_canonical_function, staf_functions_by_discipline
from streamcurves.terrain import nldi_basin_sqkm_many
from views.classify_ui import (
    classify_assignments_from_input,
    classify_role_summary_html,
    classify_table_html,
)
from views.rebuild import rebuild_app_from_tables
from views import state as st
from views.state import AppState
from views.theme import bi, fa
from views.uihelpers import (
    CompileProgress,
    not_ready_panel,
    remove_final_loading_notification,
    show_final_loading_notification,
)
from views.wb_table import render_wb_table

logger = logging.getLogger("streamcurves")

_ECOREGIONS = DATA_DIR / "ecoregions_l3.geojson"
_CROSSWALK = DATA_DIR / "ecoregion_code_crosswalk.csv"
_NRSA_SITES = DATA_DIR / "nrsa_sites.csv"
_PHYSIO = DATA_DIR / "physio_divisions.geojson"
_STATES = DATA_DIR / "us_states.geojson"

N_STEPS = 7
_STEP_LABELS = [
    "Region", "Add data", "Screen sites", "Choose metrics",
    "Compile", "Classify", "Review & build",
]

# region_of_applicability "kind" -> the step-1 region_approach radio value.
_KIND_TO_APPROACH = {
    "ecoregion": "ecoregion", "state": "state", "polygon": "draw", "none": "none",
}


def wizard_seed_from_state(region: dict | None) -> dict:
    """Map a saved region_of_applicability onto the wizard's region widgets.

    Pure so tests can pin the mapping; the hydration effect applies it."""
    region = region or {}
    kind = region.get("kind") or "none"
    if kind not in _KIND_TO_APPROACH:
        kind = "none"
    return {
        "region_kind": kind,
        "region_code": region.get("code"),
        "region_name": region.get("name"),
        "user_polygon": region.get("polygon") if kind == "polygon" else None,
        "region_approach": _KIND_TO_APPROACH[kind],
    }
_NRSA_SECTIONS = [
    ("chem", "Water chemistry"),
    ("phab", "Physical habitat"),
    ("bent", "Benthic macroinvertebrates"),
    ("fish", "Fish"),
    ("land", "Landscape"),
]

# Fixed STAF discipline order (matches streamcurves.mapping.fixed_discipline_order).
_DISCIPLINE_ORDER = ["Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"]


def _js1(s) -> str:
    """Escape a string for embedding in a single-quoted JS literal."""
    return str(s).replace("\\", "\\\\").replace("'", "\\'")

_USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
_USGS_IMAGERY = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
_CARTO_LIGHT = "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
_USGS_ATTR = "Basemaps © USGS National Map"

_ECO_STYLE = {"weight": 1, "color": "#2f4b7c", "opacity": 0.7, "fillColor": "#4a7fb5", "fillOpacity": 0.15}
_STATE_STYLE = {"weight": 1, "color": "#2f4b7c", "opacity": 0.7, "fillColor": "#4a7fb5", "fillOpacity": 0.12}
_HOVER_STYLE = {"weight": 2, "color": "#243a61", "fillColor": "#8fb4dd", "fillOpacity": 0.50}
_SELECTED_STYLE = {"weight": 3, "color": "#243a61", "opacity": 1, "fillColor": "#2f4b7c", "fillOpacity": 0.55}
_SITE_POINT_STYLE = {"radius": 6, "weight": 1.5, "color": "#7a2a12", "fillColor": "#e2603a", "fillOpacity": 0.9}

# Nominal pixel size of the sites map (it renders full-width in the wizard card).
# Only affects how tight the fitted zoom is, never correctness.
_MAP_VIEWPORT_PX = (1200, 440)
_MAP_ZOOM_RANGE = (3, 12)


def _mercator_y(lat: float) -> float:
    """Web Mercator northing, normalized to 0..1 over the projected world."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def _view_for_extent(lat_min: float, lat_max: float, lon_min: float,
                     lon_max: float, *, pad: float = 1.25):
    """Center and zoom that frame a lat/lon extent -> ``(center, zoom)``.

    Deliberately synchronous. ipyleaflet's ``fit_bounds`` schedules a coroutine
    that sets a center and then decrements zoom in a loop, awaiting a client
    ``bounds`` event each pass. The wizard hides inactive steps with
    ``display:none`` rather than unmounting them, so the map can be unsized when
    the call is made, the awaited event never arrives, and the parked loop
    resumes later (when a panel re-render reflows the map) and runs its
    zoom-out to completion. Assigning center/zoom pushes state one way with
    nothing to await, so nothing can fire late.
    """
    center = ((lat_min + lat_max) / 2.0, (lon_min + lon_max) / 2.0)
    vw, vh = _MAP_VIEWPORT_PX
    lo, hi = _MAP_ZOOM_RANGE
    lon_span = max(abs(lon_max - lon_min), 1e-6) * pad
    lat_span = max(abs(_mercator_y(lat_max) - _mercator_y(lat_min)), 1e-9) * pad
    zoom = min(math.log2(vw * 360.0 / (lon_span * 256.0)),
               math.log2(vh / (lat_span * 256.0)))
    return center, int(max(lo, min(hi, math.floor(zoom))))


# --------------------------------------------------------------------------- #
# Reference data (loaded once).
# --------------------------------------------------------------------------- #


# maxsize covers every bundled file at once: callers alternate between the
# ecoregion and state collections, and a single slot evicted on every switch,
# re-parsing multiple MB of json each time a highlight was rebuilt.
@lru_cache(maxsize=4)
def _load_geojson(path_str: str) -> dict:
    with open(path_str, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _region_choices() -> dict[str, str]:
    """Ecoregion L3 code -> "Name (code)" label, from the crosswalk csv."""
    try:
        xw = pd.read_csv(_CROSSWALK)
    except Exception:  # noqa: BLE001
        return {}
    return {
        str(r["us_l3code"]): f"{r['us_l3name']} ({r['us_l3code']})"
        for _, r in xw.iterrows()
    }


@lru_cache(maxsize=1)
def _state_choices() -> dict[str, str]:
    """State full name -> 2-letter abbr (sorted by name)."""
    feats = _load_geojson(str(_STATES)).get("features", [])
    pairs = []
    for f in feats:
        props = f.get("properties") or {}
        nm, ab = props.get("name"), props.get("state")
        if nm and ab:
            pairs.append((str(nm), str(ab)))
    pairs.sort()
    return {ab: nm for nm, ab in pairs}  # value->label handled at UI build


def _nrsa_values_for(sites: pd.DataFrame) -> pd.DataFrame:
    """Metric values keyed by the site ids in ``sites``.

    The site source and the value source have to move together: the archive
    keys a station by its oldest cycle's id, so pulling archive sites and then
    joining them against the single-cycle ``nrsa_metrics.parquet`` would leave
    roughly half the columns empty. A site id saved before the archive landed is
    translated through the alias table first, so a reopened project still joins.
    """
    everything = _nrsa_all()
    if (not nds.multi_cycle_available() or everything is None
            or "station_key" not in everything.columns):
        return load_nrsa_values()
    try:
        aliases = _nrsa_site_id_aliases()
        asked = [str(s) for s in sites.get("site_id", pd.Series(dtype=object))]
        # caller id -> station key, then back again, so the returned frame is
        # keyed by whatever the caller already has in its site_id column
        wanted_key = {sid: aliases.get(sid, sid) for sid in asked}
        panel = everything[everything["station_key"].astype(str)
                           .isin(set(wanted_key.values()))]
        if panel.empty:
            return load_nrsa_values()
        values = nds.panel_values(panel, dataset=nds.MULTI_CYCLE_DATASET_ID)
        by_key = values.set_index(values["site_id"].astype(str))
        rows = [sid for sid in asked if wanted_key[sid] in by_key.index]
        out = by_key.loc[[wanted_key[sid] for sid in rows]].reset_index(drop=True)
        out["site_id"] = rows
        return out
    except Exception:  # noqa: BLE001  (fall back to the bundled values)
        logger.exception("multi-cycle NRSA values unavailable, using nrsa_metrics.parquet")
        return load_nrsa_values()


def _state_matches(column: pd.Series, wanted_name: str) -> pd.Series:
    """Rows whose state is ``wanted_name``, however the source spells it.

    ``nrsa_sites.csv`` was all full names, but the archive mixes three forms:
    2,311 full names, 1,972 two-letter abbreviations and 95 border sites listed
    as ``AL:GA``. Comparing the raw strings against a full name silently drops
    47 percent of stations, so resolve every form to the full name first and
    match a border site under either of its states.
    """
    to_name = _state_choices()          # {"IN": "Indiana", ...}
    target = str(wanted_name).strip().casefold()

    def hit(raw) -> bool:
        for part in str(raw).split(":"):
            part = part.strip()
            if not part:
                continue
            full = to_name.get(part.upper(), part)
            if str(full).strip().casefold() == target:
                return True
        return False

    return column.astype(str).map(hit)


@lru_cache(maxsize=1)
def _nrsa_all() -> pd.DataFrame | None:
    """Every NRSA station the wizard can offer, one row each.

    The three-cycle archive when it is built, which is what the NRSA explorer
    shows: roughly 4,378 stations against the 1,908 in the single-cycle
    ``nrsa_sites.csv``, so Eastern Corn Belt Plains offers 51 rather than 18.
    Each station contributes the visit from the most recent cycle that sampled
    it, the same pooling policy the headless agent uses.

    Falls back to the bundled CSV when ``data/nrsa/`` is absent, since a
    checkout without the archive still has to run.
    """
    if nds.multi_cycle_available():
        try:
            panel, _ = nds.resolve_site_panel(
                None, dataset=nds.MULTI_CYCLE_DATASET_ID)
            if len(panel):
                return panel
        except Exception:  # noqa: BLE001  (fall back to the bundled catalog)
            logger.exception("multi-cycle NRSA panel unavailable, using nrsa_sites.csv")
    try:
        return pd.read_csv(_NRSA_SITES)
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=1)
def _nrsa_site_id_aliases() -> dict[str, str]:
    """Per-cycle EPA site id -> the station key the archive files it under.

    A station sampled in more than one cycle is keyed by its oldest id, so the
    2018-19 id ``NRS18_IN_10013`` lives under ``INLS-1045``. 930 of the 1,906
    ids in the old ``nrsa_sites.csv`` are in that position, so without this a
    project saved before the archive landed would have half its sites read as
    uploads and join to no metrics. All 1,906 resolve through the visit table.
    """
    if not nds.multi_cycle_available():
        return {}
    try:
        visits = nds.load_dataset(nds.MULTI_CYCLE_DATASET_ID).visits
    except Exception:  # noqa: BLE001
        return {}
    if visits is None or "site_id" not in visits.columns:
        return {}
    return {str(sid): str(key)
            for sid, key in zip(visits["site_id"], visits["station_key"])}


def _known_nrsa_site_ids() -> set[str]:
    """Every NRSA site id the app can recognize offline: the bundled candidate
    catalog plus the vendored screening-evidence file, and every per-cycle site
    id in the archive so a project saved against an older id still reads as NRSA."""
    ids: set[str] = set(_nrsa_comids().keys())
    d = _nrsa_all()
    if d is not None and "site_id" in d.columns:
        ids.update(str(v) for v in d["site_id"].tolist())
    ids.update(_nrsa_site_id_aliases())
    return ids


def _restore_candidate_sites(candidates, screening, nrsa_ids=None):
    """Rebuild the wizard's candidate frame on project re-entry.

    ``candidates`` is the verbatim saved frame and is always preferred. Sessions
    saved without it (older schema, or runs where the wizard never assembled
    sites) only have the screening table, which is a lossy stand-in: its
    ``state`` column is the EASI run state (``succeeded``/``failed``), not the
    US state the candidate frame means by that name. Copying ``state`` across
    therefore silently mislabels every row, so the fallback drops it and takes
    only columns that mean the same thing in both tables. The screening table
    also carries no ``.source``; the fallback re-derives it from the site id
    (known NRSA ids -> "nrsa", everything else -> "upload") so the sites
    summary and the NRSA metric pool survive re-entry. ``nrsa_ids`` overrides
    the known-id lookup for tests. Returns ``None`` when nothing can be
    restored.
    """
    if candidates is not None:
        df = (candidates if isinstance(candidates, pd.DataFrame)
              else pd.DataFrame(candidates))
        if len(df):
            return df.copy()
    if screening is None:
        return None
    df = (screening if isinstance(screening, pd.DataFrame)
          else pd.DataFrame(screening))
    if not len(df) or "site_id" not in df.columns:
        return None
    keep = [c for c in ("site_id", "lat", "lon", ".source", "ag_eco9", "huc8")
            if c in df.columns]
    out = df[keep].copy()
    if ".source" not in out.columns:
        known = _known_nrsa_site_ids() if nrsa_ids is None else set(nrsa_ids)
        out[".source"] = [
            "nrsa" if str(sid) in known else "upload"
            for sid in out["site_id"].tolist()
        ]
    return out


@lru_cache(maxsize=1)
def _nrsa_comids() -> dict[str, int]:
    """Published EPA reach per NRSA site id, from the bundled evidence file.

    Offline and stdlib-only, so it stays importable without the geo stack.
    Empty dict when unavailable, which just means every site snaps live.
    """
    try:
        from streamcurves._vendor.easi.datasources.nrsa import comid_by_site_id
        return comid_by_site_id()
    except Exception:  # noqa: BLE001
        return {}


def _js_geojson(path) -> dict:
    return _load_geojson(str(path))


def _polygon_rings(polygon) -> list[list[list[float]]]:
    """Coordinate rings from either shape a drawn region is held in.

    The draw control stores a bare list of rings as numpy arrays; a region
    restored from a saved session carries whatever was written to
    region_of_applicability["polygon"], which may be a full
    {"type": "Polygon", "coordinates": [...]} geometry. Accept both, and drop
    rings too short to enclose anything.
    """
    if polygon is None:
        return []
    if isinstance(polygon, dict):
        if str(polygon.get("type") or "").lower() != "polygon":
            return []  # MultiPolygon etc.: the draw control never makes one
        polygon = polygon.get("coordinates") or []
    rings = []
    for ring in polygon:
        pts = [[float(p[0]), float(p[1])] for p in ring if len(p) >= 2]
        if len(pts) >= 3:
            rings.append(pts)
    return rings


def _region_features(kind, code, polygon=None) -> list[dict]:
    """GeoJSON features for a region selection; empty when there is nothing to draw.

    Shared by the region map's selected-region highlight and the sites map's
    region-context outline, which each resolved this lookup independently.
    """
    if kind == "polygon":
        rings = _polygon_rings(polygon)
        if not rings:
            return []
        return [{"type": "Feature", "properties": {},
                 "geometry": {"type": "Polygon", "coordinates": rings}}]
    if not code:
        return []
    if kind == "ecoregion":
        path, prop = _ECOREGIONS, "US_L3CODE"
    elif kind == "state":
        path, prop = _STATES, "state"
    else:
        return []
    feats = _js_geojson(path)["features"]
    return [f for f in feats
            if str((f.get("properties") or {}).get(prop)) == str(code)]


# --------------------------------------------------------------------------- #
# Provenance (R build_col_provenance).
# --------------------------------------------------------------------------- #


def build_col_provenance(compiled, streamcat_wide, nrsa_cols, upload_cols) -> dict[str, str]:
    nm = list(compiled.columns)
    src = {c: "" for c in nm}

    def tag(cols, label):
        for c in cols:
            if c in src:
                src[c] = label

    tag(["site_id", "lat", "lon"], "Site")
    tag(["state", "ag_eco9", "huc8"], "NRSA (site info)")
    tag(["comid"], "USGS NLDI")
    if streamcat_wide is not None and streamcat_wide.shape[1] > 1:
        tag([c for c in streamcat_wide.columns if c != "COMID"], "EPA StreamCAT")
    for m in [c for c in (nrsa_cols or []) if c in nm]:
        src[m] = nrsa_source_for(m)
    tag(["DA_mi2"], "USGS NLDI basin")
    tag(["bieger_division", "pred_BW_ft", "pred_BD_ft", "pred_BA_ft2"], "Regional curve (Bieger)")
    tag(["elev_3dep_m"], "USGS 3DEP")
    tag([c for c in nm if c.startswith("ss_")], "USGS StreamStats")
    tag([c for c in nm if c.startswith("mmw_")], "Model My Watershed")
    for c in [c for c in (upload_cols or []) if c in nm]:
        if not src.get(c):
            src[c] = "Uploaded (user)"
    for c in nm:
        if not src[c]:
            src[c] = "Other"
    return src


# --------------------------------------------------------------------------- #
# Module UI.
# --------------------------------------------------------------------------- #


@module.ui
def import_map_ui():
    if not _HAS_MAP:
        return ui.div(
            ui.div(
                ui.tags.strong("Map import unavailable."),
                " Install ", ui.tags.code("ipyleaflet"), " to enable the map-first "
                "import wizard. You can still open an existing workbook or session.",
                class_="card-body",
            ),
            class_="card border-warning mb-3",
        )
    return ui.div(
        ui.div(
            # Reopening a project mounts this same wizard, so a fixed "Start a
            # new project" claimed the user was doing something they weren't.
            ui.output_ui("wizard_header_title"),
            ui.tags.span(
                "Define the region, gather sites, pull metrics, build",
                class_="text-muted small",
            ),
            class_="card-header data-setup-card-header d-flex justify-content-between align-items-center",
        ),
        ui.div(
            ui.output_ui("step_style"),
            # Region block (step 1) — persistent so the map survives step nav.
            ui.div(
                ui.tags.h5("Region of applicability"),
                ui.tags.p(
                    "Define where these reference curves apply. Choose how to set the "
                    "region, then pick it from the list or draw it on the map. You can "
                    "also proceed without tying the analysis to a region.",
                    class_="text-muted",
                ),
                ui.div(
                    ui.div(output_widget("region_map", height="440px"), class_="col-12 col-lg-8"),
                    ui.div(
                        ui.div(
                            ui.input_radio_buttons(
                                "region_approach", "Region type",
                                choices={"ecoregion": "Ecoregion", "state": "State",
                                         "draw": "Draw", "none": "None"},
                                selected="ecoregion", inline=True,
                            ),
                            class_="region-approach-toggle mb-2",
                        ),
                        ui.output_ui("region_control"),
                        ui.output_ui("region_readout"),
                        class_="col-12 col-lg-4",
                    ),
                    class_="row g-3",
                ),
                class_="wiz-step-block wiz-step-block-1",
            ),
            # Confirm-sites block (step 3) — persistent for the same reason.
            #
            # The sites map lives here permanently and is shown/hidden with a CSS
            # class, never swapped in and out of an output. shinywidgets' output.js
            # calls create_view on every re-bind and prunes only surplus DOM, so the
            # previous view is never removed from model.views and its L.Map keeps
            # running at 0x0, still writing center/zoom into the shared model. Every
            # destroy/recreate of this div therefore leaks a map that fights the real
            # one for control of the view.
            ui.div(
                ui.tags.h5("Screen and confirm sites"),
                ui.output_ui("step3_blocker"),
                ui.div(
                    ui.tags.p("These are the sites we'll pull data for.",
                              class_="text-muted"),
                    ui.output_ui("sites_summary"),
                    ui.div(
                        ui.input_radio_buttons(
                            "sites_view", None, choices={"map": "Map", "table": "Table"},
                            selected="map", inline=True,
                        ),
                        class_="sites-view-toggle d-flex justify-content-end mb-2",
                    ),
                    # Both views are always mounted; sites_view_style toggles them.
                    ui.div(output_widget("sites_map", height="460px")
                           if _HAS_MAP else
                           ui.div("Map requires ipyleaflet.", class_="text-muted"),
                           class_="sites-view sites-view-map"),
                    ui.div(ui.output_data_frame("sites_table"),
                           class_="sites-view sites-view-table"),
                    ui.output_ui("easi_screening_panel"),
                    class_="step3-live",
                ),
                ui.output_ui("step3_style"),
                class_="wiz-step-block wiz-step-block-3",
            ),
            ui.output_ui("body"),
            ui.tags.hr(class_="mt-3 mb-2"),
            ui.output_ui("nav"),
            class_="card-body",
        ),
        class_="card border-primary mb-3 import-map-card",
    )


# --------------------------------------------------------------------------- #
# Module server.
# --------------------------------------------------------------------------- #


@module.server
def import_map_server(
    input, output, session, state: AppState, mount_nonce=None, active=None
):
    """``active``: optional reactive callable — True while the wizard UI is
    mounted. The map render_widgets gate on it so their ipywidget comms open
    when the wizard shows (the leaflet bundle is long loaded by then), not at
    session init where they race the bundle fetch and log retry errors."""
    ns = session.ns

    step = reactive.value(1)
    region_kind = reactive.value("ecoregion")
    region_code = reactive.value(None)
    region_name = reactive.value(None)
    user_polygon = reactive.value(None)
    upload_df = reactive.value(None)
    upload_source = reactive.value(None)
    sites = reactive.value(None)
    metric_sel = reactive.value(None)
    nrsa_sel = reactive.value(None)
    ss_sel = reactive.value(None)
    mmw_sel = reactive.value(None)
    picker_sel = reactive.value(None)  # step-4 unified selection: set[code]
    compiled = reactive.value(None)
    # Whether compiled() came from a compile run THIS session (vs hydrated from
    # state.data). Decides if the screening-derived site_mask_config is valid
    # against the frame -- mask ids are row positions, so a hydrated frame
    # (masks already applied) must not have them re-applied.
    compiled_is_fresh = reactive.value(False)
    coverage = reactive.value(None)
    unmatched = reactive.value(0)
    col_source = reactive.value(None)
    col_function = reactive.value(None)
    saved_assignments = reactive.value(None)
    id_col = reactive.value(None)
    lat_col = reactive.value(None)
    lon_col = reactive.value(None)

    _map_click = reactive.value(None)  # ("ecoregion"|"state", code, name)
    _map_draw = reactive.value(None)   # rings

    def _inp(name):
        try:
            return input[name]()
        except Exception:  # noqa: BLE001
            return None

    # ── reference-data reactives ──────────────────────────────────────────────
    def region_label() -> str:
        kind = region_kind()
        if kind == "ecoregion" and region_code():
            return f"{region_name() or region_code()} (L3 {region_code()})"
        if kind == "state" and region_code():
            return f"{region_name() or region_code()} (state)"
        if kind == "polygon" and user_polygon():
            return "Custom drawn area"
        return "no region specified"

    def nrsa_in_region():
        d = _nrsa_all()
        if d is None:
            return None
        kind = region_kind()
        if kind == "ecoregion":
            if not region_code():
                return None
            return d[d["us_l3code"].astype(str) == str(region_code())]
        if kind == "state":
            if not region_name():
                return None
            return d[_state_matches(d["state"], region_name())]
        if kind == "polygon":
            # Normalised, so a region restored from a saved session (which may
            # carry a full Polygon geometry rather than bare rings) filters the
            # same as one just drawn.
            rings = _polygon_rings(user_polygon())
            if not rings:
                return None
            mask = [
                bool(point_in_polygon_rings(d["lon"].iloc[i], d["lat"].iloc[i], rings))
                for i in range(len(d))
            ]
            return d[mask]
        return None

    def upload_extra_cols() -> list[str]:
        u = upload_df()
        if u is None:
            return []
        return [c for c in u.columns if c not in (id_col(), lat_col(), lon_col())]

    # ── step-4 metric picker (unified table across all catalog sources) ───────
    def _use_nrsa() -> bool:
        # The checkbox lives in step 2's body, which never mounts when the
        # user jumps straight to a later step (stage-strip re-entry). An
        # unmounted input reads None; treat that as the checkbox's own mount
        # default (True) so the NRSA metric pool survives direct entry.
        v = _inp("use_nrsa")
        return True if v is None else bool(v)

    def _mmw_catalog() -> dict:
        try:
            return mmw_core_metrics() if mmw_available() else {}
        except Exception:  # noqa: BLE001
            return {}

    @reactive.calc
    def _picker_table():
        # Static across a session (the catalogs don't change): built once. MMW
        # rows appear only when a key is configured, so they never dead-select.
        try:
            return mpick.build_metric_picker_table(
                streamstats=ss_core_bcs(), mmw=_mmw_catalog())
        except Exception:  # noqa: BLE001
            logger.exception("metric picker table build failed")
            return mpick.build_metric_picker_table(streamstats={}, mmw={})

    def _selectable_table() -> pd.DataFrame:
        """The picker table minus sources the current run can't use (NRSA metrics
        need NRSA site data)."""
        t = _picker_table()
        if not _use_nrsa():
            t = t[t["source_key"] != "nrsa"]
        return t

    def _default_codes() -> list[str]:
        return mpick.default_selected_codes(_selectable_table())

    def _sel_now() -> set[str]:
        s = picker_sel()
        if s is None:
            return set(_default_codes())
        # keep only codes still selectable (e.g. after a source toggle)
        valid = set(_selectable_table()["code"].astype(str))
        return {c for c in s if c in valid}

    def _split_now() -> dict:
        return mpick.split_selection_by_source(_sel_now(), _picker_table())

    def n_metrics_selected() -> int:
        return len(_sel_now()) + sum(
            bool(_inp(f)) for f in ("want_da", "want_regional", "want_elev"))

    # ── persistent maps (built once, each when its own step first shows) ──────
    _rlayers: dict = {"eco": None, "state": None, "selected": None, "draw": None}
    _slayers: dict = {"region": None, "sites": None}
    # Last (kind, code) the highlight was built for, so re-running the effect
    # (it now re-runs on every _maps_built_nonce bump) does not re-scan the
    # ecoregion collection to rebuild a layer that is already correct.
    _hl_key: dict = {"v": None}
    # Bumped when a map object comes into existence, so the layer-reconciling
    # effects re-run against a map they can actually add to.
    _maps_built_nonce = reactive.value(0)

    if _HAS_MAP:

        def _base_map():
            m = Map(center=(38, -96), zoom=4, scroll_wheel_zoom=True,
                    layout=Layout(height="100%"))
            m.clear_layers()
            m.add(TileLayer(url=_USGS_TOPO, name="USGS Topo", base=True,
                            attribution=_USGS_ATTR, max_native_zoom=16, max_zoom=18))
            m.add(TileLayer(url=_CARTO_LIGHT, name="Light", base=True, max_zoom=18))
            m.add(LayersControl(position="topright"))
            m.add(ScaleControl(position="bottomright"))
            return m

        # Built lazily on first wizard activation (see _ensure_maps_built):
        # every ipywidget constructed while a session is live opens its comm
        # immediately, so building the maps at session init made the client
        # create ~a dozen models while the leaflet bundle was still loading —
        # the page-load "Could not create a model" retry noise.
        _REGION_MAP = None
        _SITES_MAP = None
        _hover_readout = None

        def _rm(store, m, key):
            if m is None:
                return
            lyr = store.get(key)
            if lyr is not None:
                try:
                    m.remove(lyr)
                except Exception:  # noqa: BLE001
                    pass
                store[key] = None

        def _add(store, m, key, layer):
            if m is None:
                return
            _rm(store, m, key)
            m.add(layer)
            store[key] = layer

        def _eco_layer():
            g = GeoJSON(data=_js_geojson(_ECOREGIONS), style=_ECO_STYLE,
                        hover_style=_HOVER_STYLE, name="Ecoregions")

            def _click(**kw):
                props = (kw.get("feature") or {}).get("properties") or {}
                code = props.get("US_L3CODE")
                if code is not None:
                    _map_click.set(("ecoregion", str(code), props.get("US_L3NAME")))

            def _hover(**kw):
                props = (kw.get("feature") or {}).get("properties") or {}
                _hover_readout.value = (
                    f"<div style='padding:2px 6px'>{props.get('US_L3NAME', '')}</div>"
                )

            g.on_click(_click)
            g.on_hover(_hover)
            return g

        def _state_layer():
            g = GeoJSON(data=_js_geojson(_STATES), style=_STATE_STYLE,
                        hover_style=_HOVER_STYLE, name="States")

            def _click(**kw):
                props = (kw.get("feature") or {}).get("properties") or {}
                ab = props.get("state")
                if ab is not None:
                    _map_click.set(("state", str(ab), props.get("name")))

            def _hover(**kw):
                props = (kw.get("feature") or {}).get("properties") or {}
                _hover_readout.value = (
                    f"<div style='padding:2px 6px'>{props.get('name', '')}</div>"
                )

            g.on_click(_click)
            g.on_hover(_hover)
            return g

        def _draw_control():
            dc = DrawControl(
                polygon={"shapeOptions": {"color": "#243a61", "fillColor": "#2f4b7c",
                                          "fillOpacity": 0.4}},
                polyline={}, rectangle={}, circle={}, circlemarker={}, marker={},
            )

            def _on_draw(target, action, geo_json):
                if action == "deleted":
                    _map_draw.set(None)
                    return
                geom = (geo_json or {}).get("geometry") or {}
                if geom.get("type") != "Polygon":
                    return
                rings = [np.asarray(ring, dtype=float) for ring in geom.get("coordinates", [])]
                _map_draw.set(rings)

            dc.on_draw(_on_draw)
            return dc

        # region_kind value -> (layer slot, builder). "none" maps to nothing.
        def _region_layer_specs():
            return (
                ("eco", "ecoregion", _eco_layer),
                ("state", "state", _state_layer),
                ("draw", "polygon", _draw_control),
            )

        def _apply_region_layers(kind) -> None:
            """Reconcile the region map's base overlay to ``kind``. Idempotent.

            Deliberately not "remove all four, add the one for this kind": that
            shape wiped the `selected` highlight on every call and left an empty
            map when kind was "none", with nothing to re-seed it. It also fired
            spuriously -- a re-mounted radio re-sends its default with force=True
            (shiny applies re-sent client values forcibly), so rebuilding the
            wizard DOM used to blank the polygons outright. Reconciling instead
            makes a repeat call a no-op.
            """
            if _REGION_MAP is None:
                return
            for slot, want_kind, builder in _region_layer_specs():
                if kind == want_kind:
                    if _rlayers.get(slot) is None:
                        _add(_rlayers, _REGION_MAP, slot, builder())
                elif _rlayers.get(slot) is not None:
                    _rm(_rlayers, _REGION_MAP, slot)

        def _apply_selected_highlight(kind, code, polygon=None) -> None:
            """Reconcile the "selected region" highlight. Idempotent.

            Deliberately NOT folded into _apply_region_layers: the base sheet and
            the highlight have different lifetimes, and owning both in one pass is
            what used to wipe the highlight on every reconcile (the separation is
            pinned by test_reconcile_never_touches_the_selected_highlight).
            """
            if _REGION_MAP is None:
                return
            # A drawn polygon always reports the same (kind, code) -- "polygon"
            # and "USER" -- so it must not take the fast path when redrawn. Its
            # geometry is a handful of vertices, so rebuilding is free anyway.
            key = (kind, code)
            if (kind != "polygon" and key == _hl_key["v"]
                    and _rlayers.get("selected") is not None):
                return
            feats = _region_features(kind, code, polygon)
            if feats:
                _add(_rlayers, _REGION_MAP, "selected",
                     GeoJSON(data={"type": "FeatureCollection", "features": feats},
                             style=_SELECTED_STYLE, name="Selected region"))
            else:
                _rm(_rlayers, _REGION_MAP, "selected")
            _hl_key["v"] = key

        # Both seeds run post-flush (registered from the builders), i.e. outside the
        # render context -- which is also why the nonce is bumped here rather than in
        # the builder: writing a reactive value from inside a @render_widget body is
        # the self-invalidation footgun documented in views/stagebar.py.
        def _seed_region_layers():
            with reactive.isolate():
                kind = region_kind()
                _apply_region_layers(kind)
                # Backfill the highlight for the same reason _seed_sites_layers
                # backfills markers: re-entry over a restored project seeds the
                # region before this map exists, so the highlight had nothing to
                # attach to. Doing it here (rather than leaving it to the nonce
                # bump below) lands it in the same flush as the base sheet.
                _apply_selected_highlight(kind, region_code(), user_polygon())
                _maps_built_nonce.set(_maps_built_nonce() + 1)

        def _seed_sites_layers():
            # The sites map is built when step 3 first shows, which is normally
            # AFTER _refresh_sites_map already computed markers for it. Re-run that
            # reconciliation now that there is a map to add them to.
            _refresh_sites_map_now()
            with reactive.isolate():
                _maps_built_nonce.set(_maps_built_nonce() + 1)

        # Each map is built when ITS OWN step first becomes visible, not both at
        # once. A Leaflet map created inside a display:none container measures its
        # viewport as 0x0 and stays that way until something calls
        # invalidateSize(); building the sites map alongside the region map (while
        # step 3 was hidden) is what used to leave it unrendered until the window
        # was resized. Its own output is suspend_when_hidden, so this runs exactly
        # when step 3 is shown -- and a map measured correctly on creation never
        # needs a resize nudge, which is what makes it safe to have none (see the
        # note on Leaflet's global trackResize listener below).
        def _ensure_region_map():
            nonlocal _REGION_MAP, _hover_readout
            if _REGION_MAP is not None:
                return
            _REGION_MAP = _base_map()
            _hover_readout = IPHTML("<div style='padding:2px 6px'>Hover a region</div>")
            _REGION_MAP.add(WidgetControl(widget=_hover_readout, position="bottomleft"))
            # The base overlay is a GeoJSON child; attaching it to the map's
            # INITIAL widget state trips a shinywidgets 0.8.1 bug (client logs
            # "Could not create a model" and the polygons never render). Every
            # later change adds its layer as a post-display update and works, so
            # seed the same way: just after this render's flush reaches the client.
            session.on_flushed(_seed_region_layers, once=True)

        def _ensure_sites_map():
            nonlocal _SITES_MAP
            if _SITES_MAP is not None:
                return
            _SITES_MAP = Map(center=(38, -96), zoom=4, scroll_wheel_zoom=True,
                             layout=Layout(height="100%"))
            _SITES_MAP.clear_layers()
            # USGS Topo is the default basemap: base layers stack in add-order, so
            # the LAST-added one renders on top. Add Imagery first, Topo last.
            _SITES_MAP.add(TileLayer(url=_USGS_IMAGERY, name="USGS Imagery", base=True,
                                     attribution=_USGS_ATTR, max_native_zoom=16,
                                     max_zoom=18))
            _SITES_MAP.add(TileLayer(url=_USGS_TOPO, name="USGS Topo", base=True,
                                     attribution=_USGS_ATTR, max_native_zoom=16,
                                     max_zoom=18))
            _SITES_MAP.add(LayersControl(position="topright"))
            # Same post-display rule as above, and it also backfills the site
            # markers/region outline that _refresh_sites_map computed before this
            # map existed.
            session.on_flushed(_seed_sites_layers, once=True)

        # reactive.isolate on the gate: `active` reads entry_view(), and taking it
        # as a dependency made shinywidgets register every widget built inside this
        # render for close() on the next invalidation (WidgetRenderContext.on_close).
        # Since the builders early-return, the closed widgets were handed straight
        # back with an OrphanedShinyComm and no new comm_open -- so Start New
        # Project -> Back to start -> Start New Project killed both maps.
        @render_widget
        def region_map():  # noqa: A001
            with reactive.isolate():
                req(active is None or active())
            _ensure_region_map()
            return _REGION_MAP

        @render_widget
        def sites_map():  # noqa: A001
            with reactive.isolate():
                req(active is None or active())
            _ensure_sites_map()
            return _SITES_MAP

    # ── region selection ──────────────────────────────────────────────────────
    def set_region_ecoregion(code):
        code = str(code)
        if not code:
            return
        region_kind.set("ecoregion")
        region_code.set(code)
        user_polygon.set(None)
        label = _region_choices().get(code, code)
        region_name.set(label.rsplit(" (", 1)[0])

    def set_region_state(abbr):
        abbr = str(abbr)
        if not abbr:
            return
        region_kind.set("state")
        region_code.set(abbr)
        user_polygon.set(None)
        region_name.set(_state_choices().get(abbr, abbr))

    @reactive.effect
    @reactive.event(_map_click)
    def _apply_map_click():
        click = _map_click()
        if not click:
            return
        kind, code, _name = click
        if kind == "ecoregion":
            set_region_ecoregion(code)
            ui.update_selectize("region_pick", selected=code, session=session)
        else:
            set_region_state(code)
            ui.update_selectize("state_pick", selected=code, session=session)
        ui.notification_show(
            f"Region of applicability: {region_label()}", type="message", duration=4
        )

    @reactive.effect
    @reactive.event(_map_draw)
    def _apply_map_draw():
        rings = _map_draw()
        if rings is None:
            if region_kind() == "polygon":
                region_code.set(None)
                region_name.set(None)
            user_polygon.set(None)
            return
        region_kind.set("polygon")
        region_code.set("USER")
        region_name.set("Custom area")
        user_polygon.set(rings)
        ui.notification_show(
            "Region of applicability: custom drawn area.", type="message", duration=4
        )

    @reactive.effect
    @reactive.event(input.region_approach, ignore_init=True)
    def _swap_approach():
        kind = _inp("region_approach") or "ecoregion"
        mapped = {"ecoregion": "ecoregion", "state": "state",
                  "draw": "polygon", "none": "none"}.get(kind, "ecoregion")
        with reactive.isolate():
            already_current = region_kind() == mapped
        if not already_current:
            # Hydration updates this radio to match a restored region; in that
            # round-trip the kind is already current and clearing here would
            # wipe the freshly seeded code/name/polygon.
            region_kind.set(mapped)
            region_code.set(None)
            region_name.set(None)
            user_polygon.set(None)
        # Layers are NOT touched here: _reconcile_region_layers below derives them
        # from region_kind, so this effect firing spuriously (see _apply_region_layers)
        # can no longer blank the map.

    @reactive.effect
    def _reconcile_region_layers():
        # Same dependency-before-guard rule as _refresh_sites_map below.
        kind = region_kind()
        _maps_built_nonce()
        if _HAS_MAP:
            _apply_region_layers(kind)

    @reactive.effect
    @reactive.event(input.region_pick, ignore_init=True)
    def _pick_eco():
        code = _inp("region_pick") or ""
        if code and str(code) != str(region_code()):
            set_region_ecoregion(code)

    @reactive.effect
    @reactive.event(input.state_pick, ignore_init=True)
    def _pick_state():
        ab = _inp("state_pick") or ""
        if ab and str(ab) != str(region_code()):
            set_region_state(ab)

    @reactive.effect
    def _highlight_selected():
        # Every dependency BEFORE the guard, and _maps_built_nonce() so this
        # re-runs once a map exists to add to -- the same rule as
        # _reconcile_region_layers above and _refresh_sites_map below. Re-entry
        # over a restored project sets region_code in a flush where _REGION_MAP is
        # still None (data_overview flips entry_view and bumps the hydrate nonce in
        # one effect, so hydration beats the map's first render), and _add silently
        # no-ops on a None map. Without the nonce the highlight was built, dropped,
        # and never rebuilt -- region_kind/region_code do not change again, because
        # the radio round-trip hits _swap_approach's already_current guard and the
        # re-sent selectize hits _pick_eco's equality guard.
        kind, code, poly = region_kind(), region_code(), user_polygon()
        _maps_built_nonce()
        if not _HAS_MAP:
            return
        _apply_selected_highlight(kind, code, poly)

    # ── step navigation ───────────────────────────────────────────────────────
    @reactive.effect
    def _mirror_step():
        # Location mirror for the workflow strip (its sub-step row and the
        # "you are here" highlight). One writer: this effect.
        state.wizard_current_step.set(int(step()))

    # NOTE: there is deliberately no "nudge leaflet to re-measure" resize here.
    # Leaflet's trackResize listener is global and unguarded, so a dispatched
    # window resize also reaches whichever map is currently display:none, where
    # getSize() reads 0x0 and invalidateSize() pans the pane by half the old
    # viewport; jupyter-leaflet then commits that bogus center into the shared
    # ipywidget model on a ~200ms-debounced moveend, which is how the region map
    # ended up in the Caribbean with its polygons gone. The nudge is unnecessary
    # now that each map is created while its own step is visible (see
    # _ensure_region_map / _ensure_sites_map) and therefore measures correctly
    # from the start.

    @render.ui
    def step_style():
        cur = step()
        css = ".wiz-step-block{display:none;} " + f".wiz-step-block-{cur}{{display:block;}}"
        return ui.tags.style(css)

    @reactive.effect
    @reactive.event(input.to_next)
    def _next():
        cur = step()
        if cur == 2:
            use_nrsa = bool(_inp("use_nrsa"))
            use_upload = bool(_inp("use_upload")) and upload_df() is not None
            if not use_nrsa and not use_upload:
                ui.notification_show(
                    "Choose at least one data source (NRSA and/or your upload).",
                    type="warning", duration=5,
                )
                return
            _assemble_sites(use_nrsa, use_upload)
            if sites() is None or len(sites()) == 0:
                ui.notification_show(
                    "No sites available from the chosen sources.", type="warning"
                )
                return
        if cur == 4:
            split = _split_now()
            metric_sel.set(split["streamcat"])
            nrsa_sel.set(split["nrsa"])
            ss_sel.set(split["streamstats"])
            mmw_sel.set(split["mmw"])
        if cur == 6:
            live = classify_assignments_from_input(input, _classify_profile())
            if int(live["is_metric"].sum()) < 1:
                ui.notification_show(
                    "Mark at least one column as Metric before continuing.",
                    type="warning", duration=5,
                )
                return
            saved_assignments.set(live)
        step.set(min(N_STEPS, cur + 1))

    @reactive.effect
    @reactive.event(input.to_back)
    def _back():
        step.set(max(1, step() - 1))

    @reactive.effect
    @reactive.event(state.wizard_step_nonce, ignore_init=True)
    def _wizard_step_from_root():
        # The stage banner routes here by requesting a wizard step (1-based).
        with reactive.isolate():
            req_step = state.wizard_step_request()
        if req_step is None:
            return
        step.set(max(1, min(N_STEPS, int(req_step))))

    @reactive.effect
    @reactive.event(state.app_reset_nonce, ignore_init=True)
    def _reset():
        step.set(1)
        # id/lat/lon were omitted here, so an uploaded table's column mapping
        # leaked into the next project.
        for rv in (region_code, region_name, user_polygon, upload_df, upload_source,
                   picker_sel, metric_sel, nrsa_sel, ss_sel, mmw_sel, compiled,
                   coverage, col_source, col_function, saved_assignments,
                   id_col, lat_col, lon_col):
            rv.set(None)
        compiled_is_fresh.set(False)
        unmatched.set(0)
        # Clear the persisted mirror too, or a later hydrate restores the
        # candidates this reset was meant to discard.
        _set_sites(None)
        region_kind.set("ecoregion")

    def _set_sites(df) -> None:
        """Set the wizard's candidate frame and mirror it into persisted state.

        The mirror is what makes re-entry work: the screening table drops lat/lon
        and reuses ``state`` for the EASI run state, so it cannot rebuild these
        candidates.
        """
        sites.set(df)
        state.candidate_sites.set(None if df is None else df.copy())

    @render.ui
    def wizard_header_title():
        if state.app_data_loaded():
            name = (state.session_name() or "").strip()
            return ui.tags.span(
                bi("geo-alt-fill"), " Editing ", ui.tags.strong(name or "this project"),
            )
        return ui.tags.span(bi("geo-alt-fill"), " Start a new project")

    @reactive.effect
    @reactive.event(state.wizard_hydrate_nonce, ignore_init=True)
    def _hydrate_from_state():
        # Re-entry over a restored project (stage-banner click or header Open):
        # seed the wizard's local widgets from the saved state so the steps show
        # the project's region and candidate sites instead of blank defaults.
        # No-op when there is nothing saved (a genuinely fresh wizard).
        with reactive.isolate():
            region = state.region_of_applicability()
            sc = state.easi_screening_sites()
            cand = state.candidate_sites()
            data = state.data()
            tables = state.input_metadata()
            saved_sources = state.column_sources()
            saved_functions = state.column_functions()
            picker_untouched = picker_sel() is None
            compile_untouched = compiled() is None
        # `data is None` too: a built project with no region/sites still needs
        # its step 4-7 seeding, which the old region-only test skipped.
        if region is None and sc is None and cand is None and data is None:
            return
        if region is not None:
            seed = wizard_seed_from_state(region)
            region_kind.set(seed["region_kind"])
            region_code.set(seed["region_code"])
            region_name.set(seed["region_name"])
            user_polygon.set(seed["user_polygon"])
            # region_control renders from the radio input; _swap_approach skips
            # its clearing when the kind is already current (see above).
            ui.update_radio_buttons(
                "region_approach", selected=seed["region_approach"], session=session
            )
        restored = _restore_candidate_sites(cand, sc)
        if restored is not None:
            # _set_sites (not sites.set): re-mirror into state.candidate_sites
            # so a project restored via the lossy screening fallback saves the
            # full-fidelity frame (with .source) from here on.
            _set_sites(restored)
        # Seed the step-4 selection from the metrics the project actually
        # pulled (its dataset columns), so Choose metrics shows the real
        # selection and coverage instead of the recommended defaults. Only
        # when untouched this session: hydrate refires on every stage click
        # and must never clobber in-session tweaks.
        if picker_untouched and data is not None:
            seeded = mpick.codes_for_columns(data.columns, _picker_table())
            if seeded:
                picker_sel.set(seeded)
                # The per-source split is otherwise only computed on the step-4
                # "Next" click (_next below), so arriving at Compile straight
                # from the stage strip left these empty -- the step reported
                # "0 StreamCAT + 0 NRSA" and, worse, re-compiling would have
                # pulled nothing. Seed them the same way _next does.
                split = mpick.split_selection_by_source(set(seeded), _picker_table())
                metric_sel.set(split["streamcat"])
                nrsa_sel.set(split["nrsa"])
                ss_sel.set(split["streamstats"])
                mmw_sel.set(split["mmw"])
        # Steps 6 and 7 key off `compiled`/`saved_assignments`, which only the
        # compile worker ever wrote -- so a reopened project had both replaced
        # by a "Nothing compiled yet" panel and no way back in short of
        # re-pulling every metric. The built dataset IS the compiled table, and
        # the restored workbook tables carry the roles that were classified, so
        # rebuild both rather than making the user re-run the pull.
        if compile_untouched and data is not None:
            compiled.set(data)
            compiled_is_fresh.set(False)  # hydrated frame: masks already applied
            col_source.set(dict(saved_sources or {}))
            col_function.set(dict(saved_functions or {}))
            if tables:
                try:
                    saved_assignments.set(current_role_membership(tables))
                except Exception:  # noqa: BLE001
                    pass  # unusable tables: leave step 7 to its blocker

    # ── upload (step 2) ────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.upload_file)
    def _upload():
        info = _inp("upload_file")
        if not info:
            return
        f = info[0]
        path, name = f["datapath"], f["name"]
        ext = name.lower().rsplit(".", 1)[-1]
        try:
            if ext in ("xlsx", "xls"):
                df = pd.read_excel(path)
            elif ext in ("tsv", "txt"):
                df = pd.read_csv(path, sep="\t")
            else:
                df = pd.read_csv(path)
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not read file: {e}", type="error")
            return
        if len(df) == 0 or df.shape[1] == 0:
            return
        cols = list(df.columns)
        prof = None
        try:
            prof = profile_columns(df)
        except Exception:  # noqa: BLE001
            prof = None
        lat_g = [c for c in cols if _re_lat(c)]
        lon_g = [c for c in cols if _re_lon(c)]
        if prof is not None and "looks_like_id" in prof.columns and prof["looks_like_id"].any():
            id_g = prof.loc[prof["looks_like_id"], "column"].iloc[0]
        else:
            id_g = cols[0]
        upload_df.set(df)
        upload_source.set(name)
        lat_col.set(lat_g[0] if lat_g else cols[0])
        lon_col.set(lon_g[0] if lon_g else cols[min(1, len(cols) - 1)])
        id_col.set(id_g)
        ui.update_checkbox("use_upload", value=True, session=session)
        ui.notification_show(
            f"Loaded {len(df)} rows x {df.shape[1]} cols from {name}.",
            type="message", duration=4,
        )

    @reactive.effect
    @reactive.event(input.id_col, ignore_init=True)
    def _set_id():
        id_col.set(_inp("id_col"))

    @reactive.effect
    @reactive.event(input.lat_col, ignore_init=True)
    def _set_lat():
        lat_col.set(_inp("lat_col"))

    @reactive.effect
    @reactive.event(input.lon_col, ignore_init=True)
    def _set_lon():
        lon_col.set(_inp("lon_col"))

    def _assemble_sites(use_nrsa, use_upload):
        upload_part = None
        nrsa_part = None
        if use_upload and upload_df() is not None:
            u = upload_df()
            std = pd.DataFrame({
                "site_id": (u[id_col()].astype(str) if id_col() in u.columns
                            else [f"U{i + 1}" for i in range(len(u))]),
                "lat": (pd.to_numeric(u[lat_col()], errors="coerce")
                        if lat_col() in u.columns else np.nan),
                "lon": (pd.to_numeric(u[lon_col()], errors="coerce")
                        if lon_col() in u.columns else np.nan),
            })
            extra = u[[c for c in u.columns if c not in ("site_id", "lat", "lon")]]
            upload_part = pd.concat([std.reset_index(drop=True), extra.reset_index(drop=True)], axis=1)
        if use_nrsa:
            nr = nrsa_in_region()
            if nr is not None and len(nr):
                site_ids = nr["site_id"].astype(str)
                # Screening delineates from the published EPA reach where one
                # exists, instead of snapping each point live. The live snap is
                # slower and its endpoint intermittently 502s, which used to
                # surface as "no NHD stream found near this point".
                seeded = _nrsa_comids()
                from_evidence = site_ids.map(seeded)
                # The archive backfills a COMID for every station, including the
                # ones EPA published none for, so fall back to it rather than to
                # the live snap the comment above warns about.
                from_archive = (nr["comid"].reset_index(drop=True)
                                if "comid" in nr.columns else None)
                comid = (from_evidence if from_archive is None
                         else from_evidence.reset_index(drop=True).fillna(from_archive))
                nrsa_part = pd.DataFrame({
                    "site_id": site_ids, "lat": nr["lat"], "lon": nr["lon"],
                    "state": nr["state"], "ag_eco9": nr["ag_eco9"], "huc8": nr["huc8"],
                    "comid": comid.to_numpy() if hasattr(comid, "to_numpy") else comid,
                })
                nrsa_part["comid_source"] = np.where(
                    nrsa_part["comid"].isna(), "live_snap",
                    np.where(pd.notna(from_evidence.to_numpy()), "epa_nrsa", "archive"))
        if upload_part is None and nrsa_part is None:
            _set_sites(None)
            return
        asm = assemble_sites(upload=upload_part, nrsa=nrsa_part)
        # No coordinate dedup: reference screening + reviewer overrides decide which
        # candidate sites continue, so every assembled row is preserved here.
        _set_sites(asm)
        # Publish the candidate count so the stage banner can report it.
        meta = rs.touch_run_meta(state.run_meta())
        meta["n_candidates"] = int(len(asm))
        state.run_meta.set(meta)

    # ── compile (step 5) ──────────────────────────────────────────────────────
    # The pull is run as a detached asyncio task (like the workspace modal +
    # summary recompute): a sync effect captures the inputs and launches the
    # coroutine, which paints a bottom-right progress toast between steps via
    # st.task_flush(). Doing the work inline in the effect would block the loop
    # (nothing paints until the end) and awaiting a flush inside the effect's
    # own flush cycle wedges the session — see views/workspace_modal.py.
    _compile_tasks: set = set()

    def _launch(coro):
        task = asyncio.create_task(coro)
        _compile_tasks.add(task)
        task.add_done_callback(_compile_tasks.discard)

    async def _run_compile(sdf, upload_kept, metric_names, nrsa_names, ss_codes,
                           mmw_codes, want_da, want_regional, want_elev, mmw_ok,
                           exclusions=None):
        n = len(sdf)
        run_mmw = bool(mmw_codes) and mmw_ok
        prog = CompileProgress.for_run(
            n,
            want_da=want_da, want_elev=want_elev,
            streamcat=bool(metric_names), nrsa=bool(nrsa_names),
            streamstats=bool(ss_codes), mmw=run_mmw,
        )
        # Per-source diagnostics feed run_stage_status["enrichment_build"] so the
        # stage banner can say which sources succeeded, retried, or were isolated.
        diag = {"sources": {}, "isolated": [], "site_failures": {}}

        async def announce(source, *, site=None, n_sites=None):
            # Name the source being fetched, repaint the toast, and yield so the
            # websocket transmits before the (blocking) call runs.
            prog.start(source, site=site, n_sites=n_sites)
            show_final_loading_notification(
                "sc_compile", prog.title(), prog.detail(), close_button=False
            )
            await st.task_flush()
            await asyncio.sleep(0)

        async def pull(label, fn, on_fail):
            # One retry, then failure isolation: a non-critical source that keeps
            # failing degrades to its fallback instead of aborting the whole build.
            for attempt in (1, 2):
                try:
                    val = await asyncio.to_thread(fn)
                    diag["sources"][label] = "ok" if attempt == 1 else "ok_retry"
                    return val
                except Exception as exc:  # noqa: BLE001 — isolate optional sources
                    if attempt == 2:
                        diag["sources"][label] = f"failed: {exc}"
                        diag["isolated"].append(label)
                        logger.warning("compile source %s failed after retry: %s", label, exc)
                        return on_fail()
                    logger.info("compile source %s failed (attempt 1), retrying: %s", label, exc)

        try:
            # NLDI comids stay a hard failure: everything downstream keys off them.
            await announce("Snapping sites to NHD flowlines")
            comids = await asyncio.to_thread(nldi_comids, list(sdf["lon"]), list(sdf["lat"]))
            sdf["comid"] = comids
            diag["sources"]["nldi_comids"] = "ok"
            prog.complete()

            if want_da:
                await announce("Drainage area (NLDI basins)")
                da = await pull("drainage_area",
                                lambda: nldi_basin_sqkm_many(comids),
                                lambda: np.full(n, np.nan))
                prog.complete()
            else:
                da = np.full(n, np.nan)

            elev = None
            if want_elev:
                await announce("Site elevation (USGS 3DEP)")
                elev = await pull(
                    "elevation_3dep",
                    lambda: [epqs_elev(sdf["lon"].iloc[i], sdf["lat"].iloc[i]) for i in range(n)],
                    lambda: None)
                prog.complete()

            sc = None
            if metric_names:
                await announce(f"StreamCAT ({len(metric_names)} metrics)")
                sc = await pull(
                    "streamcat",
                    lambda: streamcat_metrics(comids, metric_names, area="watershed"),
                    lambda: None)
                prog.complete()

            if nrsa_names:
                await announce(f"NRSA metrics ({len(nrsa_names)})")
                nrsa_res = await pull(
                    "nrsa",
                    lambda: attach_nrsa_metrics(sdf, nrsa_names, _nrsa_values_for(sdf)),
                    lambda: None)
                if nrsa_res is not None:
                    sdf = nrsa_res
                prog.complete()

            if ss_codes:
                for cc in [f"ss_{c}" for c in ss_codes]:
                    sdf[cc] = np.nan
                ss_fail = 0
                for i in range(n):
                    st_name = sdf["state"].iloc[i] if "state" in sdf.columns else None
                    if st_name and str(st_name).strip():
                        a = state_abbr_from_name(st_name)
                        state_code = a if a else state_at(sdf["lon"].iloc[i], sdf["lat"].iloc[i], str(_STATES))
                    else:
                        state_code = state_at(sdf["lon"].iloc[i], sdf["lat"].iloc[i], str(_STATES))
                    await announce(f"StreamStats ({state_code or '?'})", site=i + 1, n_sites=n)
                    try:
                        vals = await asyncio.to_thread(
                            ss_basin_characteristics, sdf["lat"].iloc[i], sdf["lon"].iloc[i], state_code, ss_codes
                        )
                        for c in ss_codes:
                            sdf.loc[sdf.index[i], f"ss_{c}"] = vals.get(c)
                    except Exception as exc:  # noqa: BLE001 — isolate one site, keep NaN
                        ss_fail += 1
                        logger.warning("StreamStats failed for site %d: %s", i + 1, exc)
                    prog.complete()
                diag["sources"]["streamstats"] = "ok" if ss_fail == 0 else f"partial ({ss_fail} failed)"
                if ss_fail:
                    diag["site_failures"]["streamstats"] = ss_fail

            if run_mmw:
                for cc in mmw_codes:
                    sdf[cc] = np.nan
                mmw_fail = 0
                for i in range(n):
                    await announce("Model My Watershed (watershed + attributes)", site=i + 1, n_sites=n)
                    try:
                        vals = await asyncio.to_thread(
                            mmw_site_metrics, sdf["lat"].iloc[i], sdf["lon"].iloc[i], mmw_codes
                        )
                        for c in mmw_codes:
                            sdf.loc[sdf.index[i], c] = vals.get(c)
                    except Exception as exc:  # noqa: BLE001 — isolate one site, keep NaN
                        mmw_fail += 1
                        logger.warning("MMW failed for site %d: %s", i + 1, exc)
                    prog.complete()
                diag["sources"]["mmw"] = "ok" if mmw_fail == 0 else f"partial ({mmw_fail} failed)"
                if mmw_fail:
                    diag["site_failures"]["mmw"] = mmw_fail

            await announce("Assembling table + regional predictions")
            comp = compile_site_table(
                sdf, lat_col="lat", lon_col="lon", comid_col="comid",
                streamcat_wide=sc, physio_path=(str(_PHYSIO) if want_regional else None),
                da_sqkm=da, bieger_geometry=bieger_geometry,
                division_abbr=bieger_division_abbr,
            )
            if not want_regional:
                comp = comp[[c for c in comp.columns
                             if c not in ("pred_BW_ft", "pred_BD_ft", "pred_BA_ft2", "bieger_division")]]
            if not want_da:
                # DA_mi2 is built unconditionally by compile_site_table; drop the empty
                # column when the user didn't request it (mirrors the regional guard above).
                comp = comp[[c for c in comp.columns if c != "DA_mi2"]]
            if want_elev:
                comp["elev_3dep_m"] = np.round(np.asarray(elev, dtype=float), 2)
            comp = comp[[c for c in comp.columns if c != ".source"]]
            csrc = build_col_provenance(comp, sc, nrsa_names, upload_kept)
            cfun = {c: metric_map_function_label(c) for c in comp.columns}
            n_unmatched = int(sum(c is None for c in comids))
            metric_cols = [c for c in comp.columns if c not in ("lat", "lon", "comid", "site_id")]
            cov = compute_coverage_table(comp, metric_cols)
            cov["source"] = [csrc.get(m, "") for m in cov["metric"]]
            cov["SFARI function"] = [cfun.get(m, "") for m in cov["metric"]]

            unmatched.set(n_unmatched)
            compiled.set(comp)
            compiled_is_fresh.set(True)
            col_source.set(csrc)
            col_function.set(cfun)
            saved_assignments.set(None)
            coverage.set(cov)
            # Legacy site_mask_config from the screening/reviewer exclusions so the
            # workbook export still carries masks (empty over a retained-only frame,
            # but with the right label column + the exclusion provenance preserved
            # separately in state.site_exclusions).
            state.site_mask_config.set(rs.site_mask_config_from_exclusions(
                comp, exclusions or [], site_id_column="site_id"))
            # Record enrichment diagnostics for the stage banner.
            stage_status = dict(state.run_stage_status() or {})
            stage_status["enrichment_build"] = {
                "status": "done",
                "n_enriched": int(len(comp)),
                "n_unmatched": n_unmatched,
                "sources": diag["sources"],
                "isolated": diag["isolated"],
                "site_failures": diag["site_failures"],
            }
            state.run_stage_status.set(stage_status)
            state.run_meta.set(rs.touch_run_meta(state.run_meta()))
            prog.complete()
            await st.task_flush()

            remove_final_loading_notification("sc_compile")
            extra = f" ({n_unmatched} couldn't snap to a flowline)" if n_unmatched > 0 else ""
            ui.notification_show(
                f"Compiled {len(comp)} sites{extra}. Continue to classify the columns.",
                type="message", duration=6,
            )
            await st.task_flush()
        except Exception as exc:  # noqa: BLE001 — surface the failure in a toast
            logger.exception("compile failed")
            remove_final_loading_notification("sc_compile")
            ui.notification_show(f"Compile failed: {exc}", type="error", duration=8)
            await st.task_flush()

    @reactive.effect
    @reactive.event(input.do_compile)
    def _compile():
        sdf = sites()
        if sdf is None or len(sdf) == 0:
            return
        sdf = sdf.copy()
        # Retained-only enrichment: when reference screening exists, enrich only the
        # sites whose final decision is "retained" (1:1 on the external site_id). The
        # Advanced (no-screening) path enriches every assembled row unchanged.
        sc = state.easi_screening_sites()
        if sc is not None and not (hasattr(sc, "empty") and sc.empty):
            tables = {"easi_screening_sites":
                      (sc if hasattr(sc, "columns") else pd.DataFrame(sc)).to_dict("records")}
            retained = set(easi_screening.retained_site_ids(tables))
            if not retained:
                ui.notification_show(
                    "No sites are retained after screening; retain at least one "
                    "before compiling.", type="warning", duration=7)
                return
            sdf = sdf[sdf["site_id"].astype(str).isin(retained)].reset_index(drop=True)
            if len(sdf) == 0:
                ui.notification_show(
                    "None of the retained site ids match the assembled sites.",
                    type="warning", duration=7)
                return
        upload_all = upload_extra_cols()
        keep = _inp("upload_cols")
        upload_kept = (
            [c for c in upload_all if c in (keep or upload_all)]
            if upload_df() is not None else []
        )
        if upload_df() is not None and keep is not None:
            drop = [c for c in upload_all if c not in keep]
            sdf = sdf[[c for c in sdf.columns if c not in drop]]
        # Read every input up front (still in the reactive/event context); the
        # detached worker then touches no reactive reads, only setters.
        metric_names = metric_sel() or []
        nrsa_names = nrsa_sel() or []
        ss_codes = ss_sel() or []
        mmw_codes = mmw_sel() or []
        want_da = bool(_inp("want_da"))
        want_regional = bool(_inp("want_regional"))
        want_elev = bool(_inp("want_elev"))
        mmw_ok = mmw_available()
        exclusions = list(state.site_exclusions() or [])
        _launch(_run_compile(
            sdf, upload_kept, metric_names, nrsa_names, ss_codes, mmw_codes,
            want_da, want_regional, want_elev, mmw_ok, exclusions,
        ))

    # ── classify + build ──────────────────────────────────────────────────────
    def _classify_profile():
        req(compiled() is not None)
        prof = profile_and_suggest(compiled())
        asg = saved_assignments()
        if asg is None or "column" not in getattr(asg, "columns", []):
            return prof
        # classify_selected_role_set() honors explicit role_* columns and only
        # falls back to fresh suggestions without them. A reopened project has
        # real roles, so seed them: otherwise Classify proposes roles the
        # project never had (a categorical column offered as a stratifier) and
        # stepping past it would bake the suggestion into the rebuild.
        for role in ("metric", "predictor", "stratifier"):
            col = f"is_{role}"
            known = (
                {str(c): bool(v) for c, v in zip(asg["column"], asg[col])}
                if col in asg.columns else {}
            )
            prof[f"role_{role}"] = [known.get(str(c), False) for c in prof["column"]]
        return prof

    def _built_tables():
        req(compiled() is not None and saved_assignments() is not None)
        prev = state.input_metadata()
        if prev:
            # Loaded project: reconcile the existing tables onto the new frame
            # instead of regenerating from roles, which silently discarded
            # derived predictors, factor recodes, site masks, custom/paired
            # stratifications and curated allow-lists. The screening-derived
            # mask config is only valid when the frame was compiled this
            # session -- a hydrated frame already has its masks applied.
            tables = reconcile_tables_with_new_data(
                prev, compiled(), saved_assignments(),
                site_mask_config=(
                    state.site_mask_config() if compiled_is_fresh() else None
                ),
            )
        else:
            tables = build_config_tables_from_roles(compiled(), saved_assignments())
        # build_config_tables_from_roles only knows which columns are metrics, so
        # it fills every setting with a default -- higher_is_better TRUE for all.
        # Rebuilding a restored project that way inverts every "more is worse"
        # metric it already had, so put the project's own settings back first.
        # (A no-op over rows the reconcile branch preserved.)
        return wb.overlay_metric_settings(tables, state.metric_config())

    def _region_of_applicability():
        """Snapshot the wizard's region selection so it can ride into the
        session, the DEEP bundle meta, and the assessment library. None when the
        user chose no region. Shape mirrors apps/library/README.md's region block."""
        kind = region_kind()
        if not kind or kind == "none":
            return None
        roa = {"kind": kind, "code": region_code(), "name": region_name()}
        if kind == "polygon" and user_polygon() is not None:
            roa["polygon"] = user_polygon()
        return roa

    @reactive.effect
    def _publish_region_selection():
        # Commit the region choice to AppState as soon as it is complete so the
        # stage banner (and the Publish page) track the wizard live;
        # wiz_build re-writes the same value at the end. Writes a value this
        # effect never reads, so it cannot self-invalidate.
        roa = _region_of_applicability()
        if roa is not None and not (roa.get("code") or roa.get("name")):
            roa = None  # region type picked but nothing selected yet
        state.region_of_applicability.set(roa)

    @reactive.effect
    @reactive.event(input.wiz_build)
    def _build():
        if compiled() is None or saved_assignments() is None:
            return
        if int(saved_assignments()["is_metric"].sum()) < 1:
            ui.notification_show("Mark at least one column as Metric before building.", type="warning")
            return
        ok = rebuild_app_from_tables(
            state, _built_tables(),
            success_text=f"Dataset built from import ({region_label()}).",
            error_prefix="Could not build dataset",
        )
        if ok:
            state.column_sources.set(dict(col_source() or {}))
            state.column_functions.set(dict(col_function() or {}))
            state.region_of_applicability.set(_region_of_applicability())
            ui.notification_show(
                "Dataset built. Continue to Reference curves in the workflow "
                "strip, or refine in the Workbook below.",
                type="message", duration=7,
            )

    # ── nav (the workflow strip owns the stepper; Back/Next live here) ───────
    @render.ui
    def nav():
        cur = step()
        left = (
            ui.input_action_button("to_back", ui.TagList(fa("arrow-left"), " Back"),
                                   class_="btn btn-outline-secondary")
            if cur > 1 else ui.tags.span()
        )
        # A step whose prerequisite is missing must not offer an enabled button that
        # silently does nothing: wiz_build, the step-6 Next, and do_compile all used
        # to return early with no feedback at all.
        blocker = _step_blocker(cur)
        blocked_kwargs = (
            {"disabled": "disabled", "title": blocker["message"]} if blocker else {}
        )
        if cur == 7:
            right = ui.input_action_button(
                "wiz_build", ui.TagList(fa("check"), " Build dataset"),
                class_="btn btn-success", **blocked_kwargs)
        elif cur == 5:
            kwargs = {"class_": "btn btn-primary"}
            if compiled() is None:
                kwargs["disabled"] = "disabled"
                kwargs["title"] = "Pull and compile the data first."
            right = ui.input_action_button("to_next", ui.TagList("Next: Classify ", fa("arrow-right")), **kwargs)
        else:
            right = ui.input_action_button(
                "to_next", ui.TagList(f"Next: {_STEP_LABELS[cur]} ", fa("arrow-right")),
                class_="btn btn-primary", **blocked_kwargs,
            )
        return ui.div(left, right, class_="d-flex justify-content-between")

    # ── prerequisites ─────────────────────────────────────────────────────────
    def _has_sites() -> bool:
        s = sites()
        return s is not None and len(s) > 0

    def _step_blocker(cur: int) -> dict | None:
        """What step ``cur`` is missing, or None when it is ready to use.

        Declared once so the body renderers and the nav/action buttons agree: a step
        that shows a not-ready panel must never also offer a button that silently
        does nothing. Steps 1 and 2 have no prerequisite -- a new project starts
        there.
        """
        if cur in (3, 4) and not _has_sites():
            return {
                "title": "No sites yet",
                "message": (
                    "Add candidate sites first: choose a region, then pick published "
                    "NRSA sites and/or upload your own site table."
                ),
                "action_label": "Go to Add data",
                "goto_step": 2,
            }
        if cur == 5:
            if not _has_sites():
                return {
                    "title": "No sites yet",
                    "message": ("Add candidate sites before compiling: choose a region, "
                                "then pick NRSA sites and/or upload your own table."),
                    "action_label": "Go to Add data", "goto_step": 2,
                }
            if n_metrics_selected() < 1:
                return {
                    "title": "No metrics selected",
                    "message": "Choose at least one metric to pull for each site.",
                    "action_label": "Go to Choose metrics", "goto_step": 4,
                }
        if cur in (6, 7) and compiled() is None:
            return {
                "title": "Nothing compiled yet",
                "message": ("Pull and compile the data for your sites first; the columns "
                            "it returns are what you classify and build from."),
                "action_label": "Go to Compile", "goto_step": 5,
            }
        if cur == 7 and saved_assignments() is None:
            return {
                "title": "Columns not classified yet",
                "message": "Mark at least one column as a Metric before reviewing the build.",
                "action_label": "Go to Classify", "goto_step": 6,
            }
        return None

    def _blocker_panel(blocker: dict):
        return not_ready_panel(
            blocker["title"], blocker["message"],
            action_label=blocker.get("action_label"),
            goto_nav="data", goto_step=blocker.get("goto_step"),
        )

    # ── body (steps 2, 4, 5, 6, 7) ────────────────────────────────────────────
    @render.ui
    def body():
        cur = step()
        if cur == 2:
            return _body_step2()
        if cur not in (4, 5, 6, 7):
            return None
        blocker = _step_blocker(cur)
        if blocker is not None:
            # Replace the step body entirely. Rendering the heading over an empty
            # area (what a bare req() produced) reads as a broken screen, and the
            # step-4 picker rendered with its recommended defaults pre-checked made
            # an empty project look finished.
            return ui.TagList(
                ui.tags.h5(_STEP_LABELS[cur - 1]),
                _blocker_panel(blocker),
            )
        if cur == 4:
            return _body_step4()
        if cur == 5:
            return _body_step5()
        if cur == 6:
            return _body_step6()
        return _body_step7()

    def _body_step2():
        nr = nrsa_in_region()
        n_nrsa = 0 if nr is None else len(nr)
        return ui.TagList(
            ui.tags.h5(f"Add data{' — ' + region_label() if region_code() else ''}"),
            ui.tags.p("Where should the site data come from? You can combine both.", class_="text-muted"),
            ui.input_checkbox(
                "use_nrsa",
                f"Published NRSA monitoring sites in this region ({n_nrsa} available"
                + (" across the 2013-14, 2018-19 and 2023-24 surveys)"
                   if nds.multi_cycle_available() else ", 2018-19 survey)"),
                value=True),
            ui.tags.hr(class_="my-2"),
            ui.input_checkbox("use_upload", "Import additional data — my own site table (CSV / Excel)", value=upload_df() is not None),
            ui.input_file("upload_file", None, accept=[".csv", ".tsv", ".txt", ".xlsx", ".xls"],
                          button_label="Choose file", placeholder="Optional upload"),
            ui.output_ui("upload_colmap"),
        )

    def _body_step4():
        src_choices = {"": "All sources", "NRSA": "NRSA", "StreamCat": "StreamCat",
                       "StreamStats": "StreamStats", "MMW": "Model My Watershed"}
        disc_choices = {"": "All disciplines"}
        for d in _DISCIPLINE_ORDER:
            disc_choices[d] = d
        disc_choices["Unmapped"] = "Unmapped (no function)"
        sort_choices = {"recommended": "Sort: recommended", "name": "Sort: name",
                        "source": "Sort: source", "discipline": "Sort: discipline",
                        "function": "Sort: function"}
        return ui.TagList(
            ui.tags.h5("Choose metrics"),
            ui.tags.p("Pick the metrics to pull for each site. Every metric shows the STAF "
                      "function it informs, and the matrix tracks how many of the 20 functions "
                      "your selection covers.", class_="text-muted"),
            ui.output_ui("coverage_panel"),
            ui.output_ui("metric_selected_count"),
            ui.div(
                ui.input_text("pick_search", None, placeholder="Search name or code…"),
                ui.input_select("pick_source", None, choices=src_choices),
                ui.input_select("pick_discipline", None, choices=disc_choices),
                ui.input_select("pick_sort", None, choices=sort_choices),
                ui.div(ui.input_switch("pick_rec_only", "Recommended only", value=False),
                       class_="metric-filter-switch"),
                ui.input_action_button("pick_select_all", "Select shown",
                                       class_="btn btn-outline-secondary btn-sm"),
                ui.input_action_button("pick_clear", "Clear shown",
                                       class_="btn btn-outline-secondary btn-sm"),
                class_="metric-filter-bar d-flex flex-wrap gap-2 align-items-center mb-2",
            ),
            ui.div(ui.output_ui("metric_table"), class_="metric-table-scroll"),
            ui.accordion(
                ui.accordion_panel(
                    ui.TagList(bi("table"), " Advanced: all NRSA metrics"),
                    # The search input is static (outside the reactive output) so
                    # typing does not re-render and clear it.
                    ui.input_text("adv_search", None,
                                  placeholder="Search NRSA name, code, or category…", width="100%"),
                    ui.output_ui("advanced_nrsa"), value="adv",
                ),
                ui.accordion_panel(
                    ui.TagList(bi("rulers"), " Computed — USGS NLDI / 3DEP / regional curves"),
                    ui.input_checkbox("want_da", "Drainage area (NLDI basin)", value=False),
                    ui.input_checkbox("want_regional", "Regional bankfull predictions (Bieger curves)", value=False),
                    ui.input_checkbox("want_elev", "Site elevation (USGS 3DEP point)", value=False),
                    value="computed",
                ),
                ui.accordion_panel(
                    ui.TagList(bi("file-earmark-arrow-up"), " From your uploaded file"),
                    ui.output_ui("uploaded_cols_picker"), value="upload",
                ),
                id="metric_extra", open=False, multiple=True,
            ),
        )

    def _body_step5():
        return ui.TagList(
            ui.tags.h5("Compile"),
            ui.output_ui("compile_intro"),
            ui.div(
                ui.input_action_button("do_compile", ui.TagList(fa("cloud-arrow-down"), " Pull & compile data"),
                                       class_="btn btn-primary"),
                class_="d-flex gap-2 my-2",
            ),
            ui.output_ui("coverage_summary"),
            ui.output_ui("compile_view_toggle"),
            ui.output_ui("coverage_body"),
        )

    def _body_step6():
        return ui.TagList(
            ui.tags.h5("Classify columns"),
            ui.tags.p("We guessed a role for each column. A column can have more than one role. "
                      "Mark what you want to build curves for as Metric.", class_="text-muted"),
            ui.output_ui("role_summary"),
            ui.div(ui.output_ui("classify_table"), class_="wizard-classify-scroll"),
        )

    def _body_step7():
        return ui.TagList(
            ui.tags.h5("Review & build"),
            ui.tags.p("Here's the setup we'll create; fine-tune later in the Workbook.", class_="text-muted"),
            ui.output_ui("build_summary"),
            ui.navset_pill(
                ui.nav_panel("Metrics", ui.div(ui.output_ui("review_metrics"), class_="mt-3")),
                ui.nav_panel("Predictors", ui.div(ui.output_ui("review_predictors"), class_="mt-3")),
                ui.nav_panel("Stratifications", ui.div(ui.output_ui("review_strats"), class_="mt-3")),
            ),
        )

    # ── region controls + readout ─────────────────────────────────────────────
    @render.ui
    def region_control():
        kind = _inp("region_approach") or "ecoregion"
        if kind == "ecoregion":
            choices = {"": "Type or pick a region..."}
            choices.update({k: v for k, v in _region_choices().items()})
            return ui.input_selectize("region_pick", "Ecoregion", choices=choices,
                                      selected=region_code() or "", width="100%")
        if kind == "state":
            choices = {"": "Type or pick a state..."}
            for ab, nm in sorted(_state_choices().items(), key=lambda kv: kv[1]):
                choices[ab] = nm
            return ui.input_selectize("state_pick", "State", choices=choices,
                                      selected=region_code() or "", width="100%")
        if kind == "draw":
            return ui.div(
                ui.div("Use the polygon tool (top-left of the map) to outline your area — "
                       "click to add points, then click the first point to finish.",
                       class_="small text-muted mb-2"),
            )
        return ui.div(
            "No region of applicability — the analysis won't be tied to an ecoregion, state, "
            "or area. Bring your own site data in the next step.",
            class_="small text-muted",
        )

    @render.ui
    def region_readout():
        kind = region_kind()
        has_sel = region_code() is not None or (kind == "polygon" and user_polygon() is not None)
        if kind == "none":
            return ui.div(fa("check"), " No region of applicability (analysis not tied to a region).",
                          class_="alert alert-info py-2 mt-2 mb-0")
        if has_sel:
            return ui.div(fa("check"), " ", region_label(), class_="alert alert-success py-2 mt-2 mb-0")
        msg = {
            "ecoregion": "No ecoregion selected yet — pick one or click the map.",
            "state": "No state selected yet — pick one or click the map.",
            "polygon": "No area drawn yet — use the polygon tool on the map.",
        }.get(kind, "No region selected yet.")
        return ui.div(msg, class_="text-muted small mt-2")

    @render.ui
    def upload_colmap():
        u = upload_df()
        if u is None:
            return None
        cols = list(u.columns)
        return ui.TagList(
            ui.div(f"{upload_source() or 'upload'}: {len(u)} rows x {u.shape[1]} cols. "
                   "Map its identifier & coordinate columns:", class_="alert alert-info py-2"),
            ui.div(
                ui.div(ui.input_select("id_col", "Site name / ID", choices=cols, selected=id_col()), class_="col-sm-4"),
                ui.div(ui.input_select("lat_col", "Latitude", choices=cols, selected=lat_col()), class_="col-sm-4"),
                ui.div(ui.input_select("lon_col", "Longitude", choices=cols, selected=lon_col()), class_="col-sm-4"),
                class_="row g-2",
            ),
        )

    # ── sites (step 3) ─────────────────────────────────────────────────────────
    @render.ui
    def step3_blocker():
        blocker = _step_blocker(3)
        return None if blocker is None else _blocker_panel(blocker)

    @render.ui
    def step3_style():
        """Show the live body or the not-ready panel, and pick the Map/Table view.

        Both are CSS toggles rather than swapped outputs: the sites map must never
        leave the DOM, or shinywidgets creates a second view over the same model and
        the abandoned 0x0 Leaflet map keeps writing center/zoom into it.
        """
        blocked = _step_blocker(3) is not None
        table = _inp("sites_view") == "table"
        css = [".step3-live{display:block;}"]
        if blocked:
            css = [".step3-live{display:none;}"]
        css.append(".sites-view-map{display:%s;}" % ("none" if table else "block"))
        css.append(".sites-view-table{display:%s;}" % ("block" if table else "none"))
        return ui.tags.style(" ".join(css))

    @render.ui
    def sites_summary():
        s = sites()
        if s is None or len(s) == 0:
            return ui.div("No sites assembled.", class_="text-muted")
        n_geo = int((np.isfinite(s["lat"]) & np.isfinite(s["lon"])).sum())
        src = s[".source"] if ".source" in s.columns else pd.Series([None] * len(s))
        n_up = int((src == "upload").sum())
        n_nrsa = int((src == "nrsa").sum())
        warn = (ui.tags.span(" Rows without coordinates can't be enriched from public sources.",
                             class_="text-danger ms-1") if n_geo < len(s) else None)
        return ui.div(
            f"{len(s)} sites ({n_nrsa} NRSA, {n_up} uploaded; {n_geo} with coordinates).",
            warn, class_="alert alert-success py-2",
        )

    @render.data_frame
    def sites_table():
        s = sites()
        req(s is not None)
        show = s[[c for c in ("site_id", "lat", "lon", ".source", "state") if c in s.columns]]
        return render.DataGrid(show.head(100).reset_index(drop=True), height="360px")

    # ---- EASI reference-condition screening ---------------------------------
    # Two paths: run the vendored engine in-process (local/desktop, direct) or
    # import a finalized batch ZIP (cloud-safe fallback). Either way we persist
    # the three screening tables and re-derive the retained set + site_exclusions.
    _screen_prog = {"done": 0, "total": 0, "stage": ""}
    _screen_cancel = {"flag": False}

    def _screening_site_rows(sdf) -> list[dict]:
        rows: list[dict] = []
        for _, r in sdf.iterrows():
            lat, lon = r.get("lat"), r.get("lon")
            if lat is None or lon is None or not (np.isfinite(lat) and np.isfinite(lon)):
                continue
            row = {"site_id": str(r.get("site_id")), "lat": float(lat), "lon": float(lon)}
            if "comid" in sdf.columns and pd.notna(r.get("comid")):
                row["comid"] = int(r["comid"])
            rows.append(row)
        return rows

    def _sync_screening_derivations(sites_df, *, method: str | None = None) -> None:
        """Re-derive the retained set, site_exclusions, and the screening_run
        summary from the current sites table (called after every screen or
        reviewer override)."""
        df = sites_df if hasattr(sites_df, "columns") else pd.DataFrame(sites_df)
        rows = df.to_dict("records")
        # Everything not retained still stays out of enrichment, but a site the
        # engine never assessed is recorded as unresolved rather than as a
        # deliberate screen-out.
        state.site_exclusions.set(easi_screening.exclusion_records(rows))
        # Isolate the reads: reading a value this function also rewrites would
        # register a self-invalidating dependency on any plain-effect caller.
        with reactive.isolate():
            run = dict(state.screening_run() or {})
            prev_meta = state.run_meta()
        run.update({
            **easi_screening.summarize_screening_rows(rows),
            "method": method or run.get("method") or "zip_import",
            "method_version": rs.SCREENING_METHOD_VERSION,
        })
        state.screening_run.set(run)
        state.run_meta.set(rs.touch_run_meta(prev_meta))

    def _persist_screening(tables: dict, *, method: str | None = None) -> None:
        sites_df = pd.DataFrame(tables["easi_screening_sites"])
        # The engine does not know where a reach came from, but we do: record it
        # per site so a reviewer can tell a published EPA reach from a live snap.
        s = sites()
        if (s is not None and len(sites_df) and "comid_source" in getattr(s, "columns", [])
                and "site_id" in sites_df.columns):
            src = dict(zip(s["site_id"].astype(str), s["comid_source"]))
            sites_df["comid_source"] = sites_df["site_id"].astype(str).map(src)
        state.easi_screening_sites.set(sites_df)
        state.easi_screening_metrics.set(pd.DataFrame(tables["easi_screening_metrics"]))
        state.easi_screening_criteria.set(tables["easi_screening_criteria"])
        _sync_screening_derivations(sites_df, method=method)

    @reactive.extended_task
    async def screen_task(site_rows: list[dict], criteria, progress: dict,
                          cancel: dict) -> dict:
        def on_event(stage, site_id, info):
            progress["stage"] = stage
            progress["site"] = site_id or ""
            if stage == "site_done":
                progress["done"] = progress.get("done", 0) + 1

        batch = await easi_screening.screen_sites_direct_async(
            site_rows, criteria, on_event=on_event,
            cancel=lambda: bool(cancel.get("flag")))
        return easi_screening.to_screening_tables(batch)

    @reactive.effect
    @reactive.event(input.screening_run_direct)
    def _run_screening_direct():
        s = sites()
        if s is None or len(s) == 0:
            return
        rows = _screening_site_rows(s)
        if not rows:
            ui.notification_show("No candidate sites have coordinates to screen.",
                                 type="warning", duration=6)
            return
        preset = (_inp("screening_preset")
                  or easi_screening.DEFAULT_SCREENING_PRESET)
        _screen_prog.update({"done": 0, "total": len(rows), "stage": "queued",
                             "site": ""})
        _screen_cancel["flag"] = False
        screen_task(rows, preset, _screen_prog, _screen_cancel)
        with reactive.isolate():
            tasks = dict(state.tasks_running() or {})
        tasks["candidate_screening"] = True
        state.tasks_running.set(tasks)
        ui.notification_show(f"Screening {len(rows)} candidate sites…", id="sc_screen",
                             type="message", duration=None)

    @reactive.effect
    @reactive.event(input.screening_cancel)
    def _cancel_screening():
        _screen_cancel["flag"] = True
        ui.notification_show("Cancelling screening…", id="sc_screen",
                             type="warning", duration=None)

    @reactive.effect
    def _screen_poll():
        if screen_task.status() != "running":
            return
        reactive.invalidate_later(0.4)
        done, total = _screen_prog.get("done", 0), _screen_prog.get("total", 0)
        site = _screen_prog.get("site") or ""
        extra = f" ({site})" if site else ""
        ui.notification_show(f"Screening candidate sites… {done} of {total}{extra}",
                             id="sc_screen", type="message", duration=None)

    @reactive.effect
    @reactive.event(screen_task.status)
    def _screen_done():
        # Event-guarded on the task status: the body persists to reactives it
        # would otherwise depend on (run_meta gets a fresh dict every write),
        # so an unguarded effect re-fires itself forever after success.
        status = screen_task.status()
        if status in ("initial", "running"):
            return
        ui.notification_remove("sc_screen")
        with reactive.isolate():
            tasks = dict(state.tasks_running() or {})
        tasks.pop("candidate_screening", None)
        state.tasks_running.set(tasks)
        try:
            tables = screen_task.result()
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Screening failed: {exc}", type="error", duration=8)
            return
        _persist_screening(tables, method="direct_engine")
        c = easi_screening.summarize_screening_rows(tables["easi_screening_sites"])
        note = " (cancelled early)" if _screen_cancel.get("flag") else ""
        msg = (f"Screened {c['n_screened']} sites, {c['n_retained']} retained, "
               f"{c['n_excluded']} excluded")
        if c["n_unresolved"]:
            msg += f", {c['n_unresolved']} not assessed"
        ui.notification_show(
            f"{msg}{note}.", id="sc_screen_done",
            type="warning" if c["n_unresolved"] else "message", duration=6)

    @reactive.effect
    @reactive.event(input.screening_zip)
    def _import_screening_zip():
        finfo = input.screening_zip()
        if not finfo:
            return
        try:
            data = Path(finfo[0]["datapath"]).read_bytes()
            tables = easi_screening.to_screening_tables(
                easi_screening.screen_result_from_zip(data))
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not read EASI screening ZIP: {exc}",
                                 type="error", duration=6)
            return
        _persist_screening(tables, method="zip_import")
        n = len(tables["easi_screening_sites"])
        keep = len(easi_screening.retained_site_ids(tables))
        ui.notification_show(f"Imported EASI screening: {n} sites, {keep} retained.",
                             type="message", duration=5)

    @reactive.effect
    @reactive.event(input.screening_clear)
    def _clear_screening():
        state.easi_screening_sites.set(None)
        state.easi_screening_metrics.set(None)
        state.easi_screening_criteria.set(None)
        state.site_exclusions.set([])
        state.screening_run.set(None)

    def _apply_reviewer_override(decision: str) -> None:
        sc = state.easi_screening_sites()
        if sc is None:
            return
        df = (sc if hasattr(sc, "columns") else pd.DataFrame(sc)).reset_index(drop=True)
        sel = screening_table.cell_selection()
        rows0 = list((sel or {}).get("rows") or [])
        if not rows0:
            ui.notification_show("Select one or more rows first.",
                                 type="warning", duration=5)
            return
        note = (_inp("screening_note") or "").strip()
        if not note:
            ui.notification_show("Add a short reviewer note before overriding.",
                                 type="warning", duration=5)
            return
        for col in ("final_decision", "reviewer", "reviewer_note", "reason"):
            if col not in df.columns:
                df[col] = None
        df = df.astype({"final_decision": object, "reviewer": object,
                        "reviewer_note": object, "reason": object})
        for i in rows0:
            if 0 <= i < len(df):
                df.at[i, "final_decision"] = decision
                df.at[i, "reviewer"] = "reviewer"
                df.at[i, "reviewer_note"] = note
                df.at[i, "reason"] = f"reviewer {decision}: {note}"
        state.easi_screening_sites.set(df)
        _sync_screening_derivations(df)
        ui.notification_show(
            f"{len(rows0)} site(s) marked {decision}.", type="message", duration=4)

    @reactive.effect
    @reactive.event(input.screening_retain_sel)
    def _retain_selected():
        _apply_reviewer_override("retained")

    @reactive.effect
    @reactive.event(input.screening_exclude_sel)
    def _exclude_selected():
        _apply_reviewer_override("excluded")

    @render.data_frame
    def screening_table():
        sc = state.easi_screening_sites()
        req(sc is not None)
        df = sc if hasattr(sc, "columns") else pd.DataFrame(sc)
        cols = [c for c in ("site_id", "state", "eci", "condition", "auto_decision",
                            "final_decision", "reviewer", "comid_source", "reason")
                if c in df.columns]
        return render.DataGrid(df[cols].reset_index(drop=True), height="260px",
                               selection_mode="rows", width="100%")

    def _screening_failure_note(df, counts: dict):
        """Say what actually broke when sites could not be assessed.

        Without this the table only shows the criteria predicate, which for a site
        that never scored reads "skip (no data)" and explains nothing.
        """
        if not counts["n_failed"] and not counts["n_cancelled"]:
            return None
        msgs, seen = [], set()
        if "issue" in df.columns:
            for m in df.loc[df["state"] == "failed", "issue"].tolist():
                if m and m not in seen:
                    seen.add(m)
                    msgs.append(m)
        bits = []
        if counts["n_failed"]:
            head = f"{counts['n_failed']} site(s) could not be assessed"
            bits.append(f"{head}: {msgs[0]}" if msgs else head)
            if len(msgs) > 1:
                bits.append(f"(+{len(msgs) - 1} other error(s))")
        if counts["n_cancelled"]:
            bits.append(f"{counts['n_cancelled']} cancelled")
        diag = (state.easi_screening_criteria() or {}).get("diagnostics") or {}
        stats = [f"{k} {diag[k]}" for k in ("retries", "timeouts", "throttled",
                                            "server_errors") if diag.get(k)]
        if stats:
            bits.append("Service issues: " + ", ".join(stats) + ".")
        return ui.tags.p(" ".join(bits), class_="text-danger small mb-2")

    @render.ui
    def easi_screening_panel():
        s = sites()
        if s is None or len(s) == 0:
            return None
        engine_ok = easi_screening.engine_available()
        sc = state.easi_screening_sites()
        if sc is None or (hasattr(sc, "empty") and sc.empty):
            run_controls = (
                ui.TagList(
                    ui.div(
                        ui.input_select(
                            ns("screening_preset"), None,
                            choices=easi_screening.SCREENING_PRESET_CHOICES,
                            selected=easi_screening.DEFAULT_SCREENING_PRESET,
                            width="320px"),
                        ui.input_action_button(
                            ns("screening_run_direct"), "Run screening",
                            class_="btn btn-sm btn-primary ms-2"),
                        ui.input_action_button(
                            ns("screening_cancel"), "Cancel",
                            class_="btn btn-sm btn-outline-secondary ms-1"),
                        class_="d-flex align-items-center mb-2"),
                    ui.tags.p("Scores each candidate site with the EASI batch engine. "
                              "Only Functioning keeps sites scoring above 0.69; "
                              "Functioning or Functioning-at-Risk keeps those above 0.39; "
                              "All sites keeps every site that scored, so you can exclude "
                              "them yourself below. Large batches take a while, so try a "
                              "few sites first.",
                              class_="text-muted small mb-2"),
                )
                if engine_ok else
                ui.div("The EASI engine cannot run here: "
                       f"{', '.join(easi_screening.missing_engine_requirements())} "
                       "is not installed. Import a finalized batch ZIP produced "
                       "somewhere that has it.",
                       class_="text-muted small mb-2")
            )
            return ui.div(
                ui.tags.h6("EASI reference-condition screening", class_="mb-1"),
                run_controls,
                ui.tags.hr(class_="my-2"),
                ui.tags.p("Or import a finalized EASI batch ZIP (cloud-safe, no engine):",
                          class_="text-muted small mb-1"),
                ui.input_file(ns("screening_zip"), None, accept=[".zip"]),
                class_="easi-screening-panel border rounded p-2 mt-3")
        df = sc if hasattr(sc, "columns") else pd.DataFrame(sc)
        c = easi_screening.summarize_screening_rows(df.to_dict("records"))
        unresolved = c["n_unresolved"]
        summary = (f"{c['n_screened']} screened · {c['n_retained']} retained · "
                   f"{c['n_excluded']} excluded")
        if unresolved:
            summary += f" · {unresolved} not assessed"
        return ui.div(
            ui.tags.h6("EASI screening results", class_="mb-1"),
            ui.div(summary,
                   class_=("alert alert-warning py-2 mb-2" if unresolved
                           else "alert alert-info py-2 mb-2")),
            _screening_failure_note(df, c),
            ui.output_data_frame("screening_table"),
            ui.div(
                ui.input_text(ns("screening_note"), None,
                              placeholder="Reviewer note (required to override)",
                              width="320px"),
                ui.input_action_button(ns("screening_retain_sel"), "Retain selected",
                                       class_="btn btn-sm btn-outline-success ms-2"),
                ui.input_action_button(ns("screening_exclude_sel"), "Exclude selected",
                                       class_="btn btn-sm btn-outline-danger ms-1"),
                class_="d-flex align-items-center mt-2"),
            ui.input_action_button(ns("screening_clear"), "Clear screening",
                                   class_="btn btn-sm btn-outline-secondary mt-2"),
            class_="easi-screening-panel border rounded p-2 mt-3")

    def _refresh_sites_map_now():
        """Reconcile the sites map now, outside the reactive graph.

        Called from _seed_sites_layers once the map exists: it is built when step 3
        first shows, which is normally after _refresh_sites_map already computed
        markers there was no map to attach.
        """
        if not _HAS_MAP or _SITES_MAP is None:
            return
        with reactive.isolate():
            _apply_sites_layers(sites(), region_code(), region_kind(), user_polygon())

    @reactive.effect
    def _refresh_sites_map():
        # Read every dependency BEFORE the existence check: bailing first would
        # register no dependencies at all and leave this effect permanently dead
        # for the (normal) case where sites arrive before step 3 is ever shown.
        s, code, kind, poly = sites(), region_code(), region_kind(), user_polygon()
        _maps_built_nonce()
        if not _HAS_MAP or _SITES_MAP is None:
            return
        _apply_sites_layers(s, code, kind, poly)

    def _apply_sites_layers(s, code, kind, polygon=None):
        _rm(_slayers, _SITES_MAP, "sites")
        _rm(_slayers, _SITES_MAP, "region")
        # region outline for context -- fainter than the region map's highlight
        feats = _region_features(kind, code, polygon)
        if feats:
            _add(_slayers, _SITES_MAP, "region",
                 GeoJSON(data={"type": "FeatureCollection", "features": feats},
                         style={"weight": 2, "color": "#243a61", "fillColor": "#2f4b7c", "fillOpacity": 0.12}))
        if s is None or len(s) == 0:
            return
        g = s[np.isfinite(s["lat"]) & np.isfinite(s["lon"])]
        if len(g) == 0:
            return
        pts = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "properties": {"site_id": str(r.get("site_id", "")), "source": str(r.get(".source", ""))},
                 "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]}}
                for _, r in g.iterrows()
            ],
        }
        _add(_slayers, _SITES_MAP, "sites",
             GeoJSON(data=pts, point_style=_SITE_POINT_STYLE, name="Sites"))
        try:
            _SITES_MAP.center, _SITES_MAP.zoom = _view_for_extent(
                float(g["lat"].min()), float(g["lat"].max()),
                float(g["lon"].min()), float(g["lon"].max()))
        except Exception:  # noqa: BLE001
            pass

    # ── metric picker outputs (step 4) ────────────────────────────────────────
    def _filtered_named() -> pd.DataFrame:
        """Named metrics passing the current search / source / discipline / rec
        filters (unsorted). Shared by the table render and Select/Clear shown."""
        tbl = _selectable_table()
        view = tbl[tbl["named"]]
        if len(view) == 0:
            return view
        q = (_inp("pick_search") or "").strip().lower()
        src = _inp("pick_source") or ""
        disc = _inp("pick_discipline") or ""
        if q:
            view = view[view.apply(
                lambda r: q in str(r["name"]).lower() or q in str(r["code"]).lower(), axis=1)]
        if src:
            view = view[view["source"] == src]
        if disc == "Unmapped":
            view = view[view["disciplines"].map(len) == 0]
        elif disc:
            view = view[view["disciplines"].map(lambda ds: disc in ds)]
        if bool(_inp("pick_rec_only")):
            view = view[view["recommended"]]
        return view

    def _sort_named(view: pd.DataFrame, sort_by: str) -> pd.DataFrame:
        v = view.copy()
        v["_disc0"] = v["disciplines"].map(
            lambda ds: _DISCIPLINE_ORDER.index(ds[0])
            if ds and ds[0] in _DISCIPLINE_ORDER else 99)
        v["_fn0"] = v["functions"].map(lambda fs: fs[0] if fs else "~")
        v["_name"] = v["name"].astype(str).str.lower()
        if sort_by == "name":
            return v.sort_values(["_name"])
        if sort_by == "source":
            return v.sort_values(["source", "_name"])
        if sort_by == "discipline":
            return v.sort_values(["_disc0", "_fn0", "_name"])
        if sort_by == "function":
            return v.sort_values(["_fn0", "_name"])
        v["_rec"] = ~v["recommended"]  # recommended first
        return v.sort_values(["_rec", "_disc0", "_fn0", "_name"])

    def _pick_checkbox(code: str, checked: bool, extra_class: str = ""):
        oc = (f"Shiny.setInputValue('{ns('pick_toggle')}',"
              f"{{code:'{_js1(code)}',on:this.checked}},{{priority:'event'}})")
        attrs = {"checked": "checked"} if checked else {}
        return ui.tags.input(
            type="checkbox", class_=f"form-check-input {extra_class}".strip(),
            onchange=oc, **attrs)

    def _fn_chips(funcs):
        if not funcs:
            return ui.tags.span("—", class_="text-muted")
        return ui.TagList(*[ui.tags.span(f, class_="metric-fn-chip") for f in funcs])

    def _metric_row(r, checked: bool):
        code = str(r["code"])
        star = (ui.tags.span("★", class_="metric-rec-star", title="Recommended default")
                if bool(r["recommended"]) else "")
        disc = ", ".join(r["disciplines"]) if r["disciplines"] else "—"
        return ui.tags.tr(
            ui.tags.td(_pick_checkbox(code, checked, "metric-pick-cb"), class_="metric-cb-cell"),
            ui.tags.td(ui.tags.span(r["name"], class_="metric-name"), " ", star),
            ui.tags.td(ui.tags.code(code, class_="metric-code")),
            ui.tags.td(r["source"], class_="metric-src"),
            ui.tags.td(r["units"] or "", class_="metric-units"),
            ui.tags.td(_fn_chips(r["functions"]), class_="metric-fns"),
            ui.tags.td(disc, class_="metric-disc"),
            class_="metric-row" + (" metric-row-on" if checked else ""),
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def coverage_panel():
        tbl = _picker_table()
        label_of = dict(zip(tbl["code"].astype(str), tbl["name"].astype(str)))
        codes = sorted(_sel_now())
        fn_metrics = mpick.coverage_by_function(codes, label_of=lambda c: label_of.get(c, c))
        summ = mpick.coverage_summary(codes)
        n, total = summ["n_covered"], summ["total"]
        badges = []
        for d in _DISCIPLINE_ORDER:
            cov, tot = summ["per_discipline"].get(d, (0, 0))
            mod = " is-full" if tot and cov == tot else (" is-none" if cov == 0 else "")
            badges.append(ui.tags.span(f"{d} {cov}/{tot}", class_="metric-disc-badge" + mod))
        pct = int(round(100 * n / total)) if total else 0
        header = ui.div(
            ui.div(
                ui.tags.strong(f"{n} / {total} STAF functions covered"),
                ui.div(ui.div(class_="metric-cov-fill", style=f"width:{pct}%"),
                       class_="metric-cov-track"),
                class_="metric-cov-headline",
            ),
            ui.div(*badges, class_="metric-disc-badges"),
            class_="metric-cov-summary mb-2",
        )
        return ui.div(header, _coverage_table_ui(fn_metrics),
                      class_="metric-coverage-panel border rounded p-2 mb-3")

    @output(suspend_when_hidden=False)
    @render.ui
    def metric_table():
        notes = []
        if not _use_nrsa():
            notes.append(ui.div(
                bi("info-circle"), " NRSA field metrics are hidden — NRSA was not chosen as a "
                "data source in step 2.", class_="text-muted small mb-1"))
        if not mmw_available():
            notes.append(ui.div(
                bi("info-circle"), " Model My Watershed metrics need an API key and are omitted.",
                class_="text-muted small mb-1"))
        view = _filtered_named()
        if view is None or len(view) == 0:
            return ui.TagList(*notes, ui.div("No metrics match your filters.",
                                             class_="text-muted p-2"))
        sel = _sel_now()
        view = _sort_named(view, _inp("pick_sort") or "recommended")
        header = ui.tags.thead(ui.tags.tr(
            ui.tags.th(""), ui.tags.th("Metric"), ui.tags.th("Code"), ui.tags.th("Source"),
            ui.tags.th("Units"), ui.tags.th("Function(s)"), ui.tags.th("Discipline"),
        ))
        body = ui.tags.tbody(*[_metric_row(r, str(r["code"]) in sel) for _, r in view.iterrows()])
        return ui.TagList(
            *notes,
            ui.tags.table(header, body, class_="table table-sm align-middle metric-pick-table"),
        )

    @render.ui
    def advanced_nrsa():
        tbl = _picker_table()
        un = tbl[(~tbl["named"]) & (tbl["source_key"] == "nrsa")]
        note = ui.div(
            f"{len(un)} further NRSA metrics, named from EPA's own field metadata. The list "
            "above holds the ones the STAF function crosswalk maps; search here for any other.",
            class_="text-muted small mb-1")
        q = (_inp("adv_search") or "").strip().lower()
        if not q:
            return note
        hit = un[un.apply(
            lambda r: q in str(r["name"]).lower() or q in str(r["code"]).lower()
            or q in str(r["category"]).lower(), axis=1)]
        if len(hit) == 0:
            return ui.TagList(note, ui.div("Nothing matches.", class_="text-muted small"))
        sel = _sel_now()
        cap = 200
        rows = []
        for _, r in hit.head(cap).iterrows():
            code = str(r["code"])
            units = str(r["units"] or "").strip()
            rows.append(ui.tags.label(
                _pick_checkbox(code, code in sel, "me-2"),
                ui.tags.span(str(r["name"] or code), class_="me-2"),
                ui.tags.span(f"({units})", class_="metric-units me-2") if units else None,
                ui.tags.code(code, class_="metric-code me-2"),
                ui.tags.span(r["category"], class_="text-muted small"),
                class_="metric-adv-row d-flex align-items-center gap-1",
            ))
        more = (ui.div(f"Showing {cap} of {len(hit)} matches — refine your search.",
                       class_="text-muted small mt-1") if len(hit) > cap else None)
        return ui.TagList(note, ui.div(*rows, class_="metric-adv-list"), more)

    @output(suspend_when_hidden=False)
    @render.ui
    def uploaded_cols_picker():
        cols = upload_extra_cols()
        if not cols:
            return ui.div("No uploaded file (optional).", class_="text-muted small")
        return ui.TagList(
            ui.tags.div("Columns from your table to keep in the dataset:", class_="small text-muted mb-1"),
            ui.input_checkbox_group("upload_cols", None, choices=cols, selected=cols, inline=True),
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def metric_selected_count():
        return ui.div(ui.tags.strong(str(n_metrics_selected())), " metric(s) selected across sources.",
                      class_="alert alert-light border py-1 px-2 small mb-2")

    # ── metric picker events ──────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.pick_toggle)
    def _pick_toggle():
        p = input.pick_toggle() or {}
        code = str(p.get("code") or "")
        if not code:
            return
        cur = set(_sel_now())
        if bool(p.get("on")):
            cur.add(code)
        else:
            cur.discard(code)
        picker_sel.set(cur)

    @reactive.effect
    @reactive.event(input.pick_select_all)
    def _pick_select_all():
        view = _filtered_named()
        codes = [] if view is None else list(view["code"].astype(str))
        picker_sel.set(set(_sel_now()) | set(codes))

    @reactive.effect
    @reactive.event(input.pick_clear)
    def _pick_clear():
        view = _filtered_named()
        codes = set() if view is None else set(view["code"].astype(str))
        picker_sel.set(set(_sel_now()) - codes)

    # ── compile outputs (step 5) ──────────────────────────────────────────────
    def _compile_site_count() -> int:
        """How many sites the pull would actually touch.

        _compile filters to the screening-retained ids, so counting the whole
        candidate frame here told users "71 sites" for a run that would compile
        33. Mirror the handler's own filter instead.
        """
        s = sites()
        if s is None:
            return 0
        sc = state.easi_screening_sites()
        if sc is None or (hasattr(sc, "empty") and sc.empty):
            return len(s)
        try:
            rows = (sc if hasattr(sc, "columns") else pd.DataFrame(sc)).to_dict("records")
            retained = set(easi_screening.retained_site_ids({"easi_screening_sites": rows}))
        except Exception:  # noqa: BLE001
            return len(s)
        if not retained:
            return 0
        return int(s["site_id"].astype(str).isin(retained).sum())

    @render.ui
    def compile_intro():
        computed = [lbl for flag, lbl in (("want_da", "drainage area"),
                                          ("want_regional", "regional bankfull"),
                                          ("want_elev", "3DEP elevation")) if _inp(flag)]
        n_ss, n_mmw = len(ss_sel() or []), len(mmw_sel() or [])
        return ui.div(
            f"Ready to compile {_compile_site_count()} sites with "
            f"{len(metric_sel() or [])} StreamCAT + {len(nrsa_sel() or [])} NRSA"
            + (f" + {n_ss} StreamStats" if n_ss else "")
            + (f" + {n_mmw} MMW" if n_mmw else "")
            + " metric(s)"
            + (" + " + ", ".join(computed) if computed else "") + ".",
            class_="alert alert-info py-2",
        )

    @render.ui
    def coverage_summary():
        cov = coverage()
        if cov is None:
            return None
        return ui.div(
            f"Compiled. {int((cov['n_available'] > 0).sum())} of {len(cov)} columns "
            "have data for at least one site.",
            class_="alert alert-success py-2",
        )

    @render.ui
    def compile_view_toggle():
        # Only offer the Table | Mapping switch once there's something compiled.
        if coverage() is None:
            return None
        return ui.input_radio_buttons(
            "compile_view", None,
            {"table": "Table", "map": "Discipline → Function → Metric"},
            selected="table", inline=True,
        )

    @render.ui
    def coverage_body():
        cov = coverage()
        req(cov is not None)
        # _inp() yields None until the toggle renders + the client echoes its default;
        # treat that as the default table view.
        if _inp("compile_view") == "map":
            return _compile_coverage_mapping(cov)
        return _plain_table(cov)

    # ── classify + review outputs ─────────────────────────────────────────────
    @render.ui
    def role_summary():
        req(compiled() is not None)
        return classify_role_summary_html(classify_assignments_from_input(input, _classify_profile()))

    @render.ui
    def classify_table():
        req(compiled() is not None)
        return classify_table_html(ns, _classify_profile())

    @render.ui
    def build_summary():
        t = _built_tables()
        return ui.div(
            f"Will create {len(t['metrics'])} metric(s), {len(t['predictors'])} predictor(s), "
            f"and {len(t['stratifications'])} stratification(s) from {len(t['data'])} rows.",
            class_="alert alert-info py-2",
        )

    @render.ui
    def review_metrics():
        t = _built_tables()
        # column_name is dropped: metric_key is sanitize_keys(column_name), and
        # every NRSA and StreamCat column is already a valid identifier, so the
        # two read the same. The workbook table still carries it.
        cols = [c for c in ("metric_key", "display_name", "metric_family", "min_sample_size")
                if c in t["metrics"].columns]
        return _plain_table(t["metrics"][cols])

    @render.ui
    def review_predictors():
        t = _built_tables()
        cols = [c for c in ("predictor_key", "display_name", "type")
                if c in t["predictors"].columns]
        return _plain_table(t["predictors"][cols])

    @render.ui
    def review_strats():
        t = _built_tables()
        # source_column stays: unlike the two tables above, a numeric stratifier
        # is keyed on its derived binned column, so strat_key elevws_grp comes
        # from source_column elevws. The pairing is never redundant.
        cols = [c for c in ("strat_key", "display_name", "source_column", "source_data_type", "min_group_size")
                if c in t["stratifications"].columns]
        return _plain_table(t["stratifications"][cols])


# --------------------------------------------------------------------------- #
# Small helpers.
# --------------------------------------------------------------------------- #

import re as _re


def _re_lat(c: str) -> bool:
    return bool(_re.search(r"^lat|lat$|latitude", str(c), _re.I))


def _re_lon(c: str) -> bool:
    return bool(_re.search(r"^lon|lon$|long|longitude", str(c), _re.I))


def _coverage_table_ui(fn_metrics: dict):
    """Render a ``{function_name: [metric labels]}`` map as the shared ``wb-table``
    Discipline -> Function -> Metric matrix (dash for uncovered functions)."""
    by_disc = staf_functions_by_discipline()

    def fn_cell(fn: str):
        return ui.tags.td(ui.tags.span(fn), class_="wb-fn")

    def metrics_cell(fn: str):
        mets = fn_metrics.get(fn) or []
        if not mets:
            return ui.tags.td(
                ui.tags.span("—", class_="wb-empty text-muted"), class_="wb-metrics"
            )
        chips = [
            ui.tags.span(
                ui.tags.span(m, class_="wb-chip-label"), class_="wb-chip wb-chip-data"
            )
            for m in mets
        ]
        return ui.tags.td(*chips, class_="wb-metrics")

    return render_wb_table(by_disc, fn_cell=fn_cell, metrics_cell=metrics_cell)


def _compile_coverage_mapping(cov):
    """Read-only Discipline -> Function -> Metric coverage for the compiled metrics,
    rendered with the same ``wb-table`` markup as the workbench and the Choose-metrics
    coverage panel (all three share ``coverage_by_function`` + ``_coverage_table_ui``)."""
    metric_cols = [
        str(m)
        for m in (cov["metric"].tolist() if cov is not None else [])
        if str(m) not in ("lat", "lon", "comid", "site_id")
    ]
    return _coverage_table_ui(mpick.coverage_by_function(metric_cols))


def _plain_table(df: pd.DataFrame):
    if df is None or len(df) == 0:
        return ui.div("No rows.", class_="text-muted")
    return ui.tags.div(
        ui.tags.table(
            ui.tags.thead(ui.tags.tr(*[ui.tags.th(str(c)) for c in df.columns])),
            ui.tags.tbody(*[
                ui.tags.tr(*[ui.tags.td("" if pd.isna(r[c]) else str(r[c])) for c in df.columns])
                for _, r in df.head(200).iterrows()
            ]),
            class_="table table-sm table-striped compact",
        ),
        style="overflow-x:auto;",
    )
