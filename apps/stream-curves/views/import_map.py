"""Map-first data-import wizard — port of app/modules/mod_import_map.R.

A single self-contained flow for building a new dataset: 1 Region → 2 Add data
→ 3 Confirm sites → 4 Choose metrics → 5 Compile → 6 Classify → 7 Review & build.
Step 1 defines the region of applicability (EPA Level III ecoregion, state, a
drawn area, or none); all data entry, metric selection, classification and the
build happen inside the wizard. Classify/Build reuse the shared classify UI and
the same build path as everything else (build_config_tables_from_roles →
rebuild_app_from_tables). Compile is synchronous (ui.Progress); sources fail to NA.

Maps use ipyleaflet (the DEEP persistent-map pattern): built once, mutated in
place via an ``_layers`` dict, and rendered through shinywidgets. Region
selection is per-feature ``GeoJSON.on_click`` (replacing R's layerId-prefix
routing); polygon draw uses ipyleaflet's native ``DrawControl`` (no
leaflet.extras gap). The one visible behaviour change from R: hover shows a
``WidgetControl`` name readout rather than a sticky leaflet tooltip.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import numpy as np
import pandas as pd
from shiny import module, reactive, render, req, ui

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
from streamcurves.metric_map import metric_map_default_codes, metric_map_function_label
from streamcurves.nrsa import (
    attach_nrsa_metrics,
    load_nrsa_catalog,
    load_nrsa_values,
    nrsa_source_for,
)
from streamcurves.paths import DATA_DIR
from streamcurves.profiler import (
    build_config_tables_from_roles,
    profile_and_suggest,
    profile_columns,
)
from streamcurves.sites import (
    assemble_sites,
    compile_site_table,
    dedup_sites,
)
from streamcurves.sites import coverage_table as compute_coverage_table
from streamcurves.terrain import nldi_basin_sqkm_many
from views.classify_ui import (
    classify_assignments_from_input,
    classify_role_summary_html,
    classify_table_html,
)
from views.rebuild import rebuild_app_from_tables
from views.state import AppState
from views.theme import bi, fa

logger = logging.getLogger("streamcurves")

_ECOREGIONS = DATA_DIR / "ecoregions_l3.geojson"
_CROSSWALK = DATA_DIR / "ecoregion_code_crosswalk.csv"
_NRSA_SITES = DATA_DIR / "nrsa_sites.csv"
_STREAMCAT_CATALOG = DATA_DIR / "streamcat_metrics.csv"
_PHYSIO = DATA_DIR / "physio_divisions.geojson"
_STATES = DATA_DIR / "us_states.geojson"

N_STEPS = 7
_STEP_LABELS = [
    "Region", "Add data", "Confirm sites", "Choose metrics",
    "Compile", "Classify", "Review & build",
]
_NRSA_SECTIONS = [
    ("chem", "Water chemistry"),
    ("phab", "Physical habitat"),
    ("bent", "Benthic macroinvertebrates"),
    ("fish", "Fish"),
    ("land", "Landscape"),
]

_USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
_USGS_IMAGERY = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
_CARTO_LIGHT = "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
_USGS_ATTR = "Basemaps © USGS National Map"

_ECO_STYLE = {"weight": 1, "color": "#2f4b7c", "opacity": 0.7, "fillColor": "#4a7fb5", "fillOpacity": 0.15}
_STATE_STYLE = {"weight": 1, "color": "#2f4b7c", "opacity": 0.7, "fillColor": "#4a7fb5", "fillOpacity": 0.12}
_HOVER_STYLE = {"weight": 2, "color": "#243a61", "fillColor": "#8fb4dd", "fillOpacity": 0.50}
_SELECTED_STYLE = {"weight": 3, "color": "#243a61", "opacity": 1, "fillColor": "#2f4b7c", "fillOpacity": 0.55}
_SITE_POINT_STYLE = {"radius": 6, "weight": 1.5, "color": "#7a2a12", "fillColor": "#e2603a", "fillOpacity": 0.9}


# --------------------------------------------------------------------------- #
# Reference data (loaded once).
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=1)
def _nrsa_all() -> pd.DataFrame | None:
    try:
        return pd.read_csv(_NRSA_SITES)
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=1)
def _streamcat_catalog() -> pd.DataFrame:
    try:
        return pd.read_csv(_STREAMCAT_CATALOG)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["name", "label", "domain", "default"])


def _default_streamcat() -> list[str]:
    cdf = _streamcat_catalog()
    if len(cdf) == 0:
        return []
    return [n for n in cdf["name"] if n in set(metric_map_default_codes("streamcat"))]


def _nrsa_default_sel() -> list[str]:
    cdf = load_nrsa_catalog()
    if cdf is None or len(cdf) == 0:
        return []
    rec = set(metric_map_default_codes("nrsa"))
    return [n for n in cdf["name"] if n in rec]


def _js_geojson(path) -> dict:
    return _load_geojson(str(path))


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
            ui.tags.span(bi("geo-alt-fill"), " Start a new project"),
            ui.tags.span(
                "Define the region, gather sites, pull metrics, build",
                class_="text-muted small",
            ),
            class_="card-header data-setup-card-header d-flex justify-content-between align-items-center",
        ),
        ui.div(
            ui.output_ui("stepper"),
            ui.tags.hr(class_="mt-2 mb-3"),
            ui.output_ui("step_style"),
            # Region block (step 1) — persistent so the map survives step nav.
            ui.div(
                ui.tags.h5("1. Region of applicability"),
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
            ui.div(
                ui.tags.h5("3. Confirm sites"),
                ui.tags.p("These are the sites we'll pull data for.", class_="text-muted"),
                ui.output_ui("sites_summary"),
                ui.div(
                    ui.input_radio_buttons(
                        "sites_view", None, choices={"map": "Map", "table": "Table"},
                        selected="map", inline=True,
                    ),
                    class_="sites-view-toggle d-flex justify-content-end mb-2",
                ),
                ui.output_ui("sites_view_body"),
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
    compiled = reactive.value(None)
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
            return d[d["state"].astype(str) == str(region_name())]
        if kind == "polygon":
            rings = user_polygon()
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

    def collect_metric_selection() -> list[str]:
        cdf = _streamcat_catalog()
        doms = list(dict.fromkeys(cdf["domain"])) if len(cdf) else []
        out: list[str] = []
        for k in range(len(doms)):
            out += list(_inp(f"metric_dom_{k}") or [])
        return out

    def collect_nrsa_selection() -> list[str]:
        out: list[str] = []
        for code, _ in _NRSA_SECTIONS:
            out += list(_inp(f"nrsa_core_{code}") or [])
            out += list(_inp(f"nrsa_more_{code}") or [])
        return out

    def n_metrics_selected() -> int:
        return (
            len(collect_metric_selection())
            + len(collect_nrsa_selection())
            + len(_inp("ss_sel") or [])
            + len(_inp("mmw_sel") or [])
            + sum(bool(_inp(f)) for f in ("want_da", "want_regional", "want_elev"))
        )

    # ── persistent maps (built once) ──────────────────────────────────────────
    _rlayers: dict = {"eco": None, "state": None, "selected": None, "draw": None}
    _slayers: dict = {"region": None, "sites": None}

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

        def _add_default_region_overlay():
            # Seed the default ecoregion polygons AFTER the region map's first
            # flush (registered from _ensure_maps_built). Skip if the user has
            # already switched approach in the brief interim.
            with reactive.isolate():
                kind = region_kind()
            if kind == "ecoregion" and _rlayers.get("eco") is None:
                _add(_rlayers, _REGION_MAP, "eco", _eco_layer())

        def _ensure_maps_built():
            # Run-once assembly, inside a live session but only when the wizard
            # is actually shown — the leaflet bundle (loaded eagerly at page
            # parse, views/widget_deps.py) is ready long before this.
            nonlocal _REGION_MAP, _SITES_MAP, _hover_readout
            if _REGION_MAP is not None:
                return
            _REGION_MAP = _base_map()
            _hover_readout = IPHTML("<div style='padding:2px 6px'>Hover a region</div>")
            _REGION_MAP.add(WidgetControl(widget=_hover_readout, position="bottomleft"))

            _SITES_MAP = Map(center=(38, -96), zoom=4, scroll_wheel_zoom=True,
                             layout=Layout(height="100%"))
            _SITES_MAP.clear_layers()
            _SITES_MAP.add(TileLayer(url=_USGS_TOPO, name="USGS Topo", base=True,
                                     attribution=_USGS_ATTR, max_native_zoom=16,
                                     max_zoom=18))
            _SITES_MAP.add(TileLayer(url=_USGS_IMAGERY, name="USGS Imagery", base=True,
                                     attribution=_USGS_ATTR, max_native_zoom=16,
                                     max_zoom=18))
            _SITES_MAP.add(LayersControl(position="topright"))
            # The default ecoregion overlay is a GeoJSON child; attaching it to
            # the region map's INITIAL widget state trips a shinywidgets 0.8.1
            # bug (client logs "Could not create a model" and the polygons never
            # render). Every later approach/selection change adds its layer as a
            # post-display update and works, so seed the default the same way:
            # just after this render's flush reaches the client.
            session.on_flushed(_add_default_region_overlay, once=True)

        @render_widget
        def region_map():  # noqa: A001
            req(active is None or active())
            _ensure_maps_built()
            return _REGION_MAP

        @render_widget
        def sites_map():  # noqa: A001
            req(active is None or active())
            _ensure_maps_built()
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
        region_kind.set({"ecoregion": "ecoregion", "state": "state",
                         "draw": "polygon", "none": "none"}.get(kind, "ecoregion"))
        region_code.set(None)
        region_name.set(None)
        user_polygon.set(None)
        if not _HAS_MAP:
            return
        for key in ("eco", "state", "selected", "draw"):
            _rm(_rlayers, _REGION_MAP, key)
        if kind == "ecoregion":
            _add(_rlayers, _REGION_MAP, "eco", _eco_layer())
        elif kind == "state":
            _add(_rlayers, _REGION_MAP, "state", _state_layer())
        elif kind == "draw":
            _add(_rlayers, _REGION_MAP, "draw", _draw_control())

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
        code, kind = region_code(), region_kind()
        if not _HAS_MAP:
            return
        _rm(_rlayers, _REGION_MAP, "selected")
        if not code:
            return
        if kind == "ecoregion":
            feats = _js_geojson(_ECOREGIONS)["features"]
            sel = [f for f in feats if str((f.get("properties") or {}).get("US_L3CODE")) == str(code)]
        elif kind == "state":
            feats = _js_geojson(_STATES)["features"]
            sel = [f for f in feats if str((f.get("properties") or {}).get("state")) == str(code)]
        else:
            return
        if sel:
            layer = GeoJSON(
                data={"type": "FeatureCollection", "features": sel},
                style=_SELECTED_STYLE, name="Selected region",
            )
            _add(_rlayers, _REGION_MAP, "selected", layer)

    # ── step navigation ───────────────────────────────────────────────────────
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
            metric_sel.set(collect_metric_selection())
            nrsa_sel.set(collect_nrsa_selection())
            ss_sel.set(list(_inp("ss_sel") or []))
            mmw_sel.set(list(_inp("mmw_sel") or []))
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
    @reactive.event(state.app_reset_nonce, ignore_init=True)
    def _reset():
        step.set(1)
        for rv in (region_code, region_name, user_polygon, upload_df, upload_source,
                   sites, metric_sel, nrsa_sel, ss_sel, mmw_sel, compiled, coverage,
                   col_source, col_function, saved_assignments):
            rv.set(None)
        region_kind.set("ecoregion")

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
                nrsa_part = pd.DataFrame({
                    "site_id": nr["site_id"].astype(str), "lat": nr["lat"], "lon": nr["lon"],
                    "state": nr["state"], "ag_eco9": nr["ag_eco9"], "huc8": nr["huc8"],
                })
        if upload_part is None and nrsa_part is None:
            sites.set(None)
            return
        asm = assemble_sites(upload=upload_part, nrsa=nrsa_part)
        sites.set(dedup_sites(asm, "lon", "lat", tol_m=50))

    # ── compile (step 5) ──────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.do_compile)
    def _compile():
        sdf = sites()
        if sdf is None or len(sdf) == 0:
            return
        sdf = sdf.copy()
        upload_all = upload_extra_cols()
        keep = _inp("upload_cols")
        upload_kept = (
            [c for c in upload_all if c in (keep or upload_all)]
            if upload_df() is not None else []
        )
        if upload_df() is not None and keep is not None:
            drop = [c for c in upload_all if c not in keep]
            sdf = sdf[[c for c in sdf.columns if c not in drop]]
        metric_names = metric_sel() or []
        nrsa_names = nrsa_sel() or []
        want_da = bool(_inp("want_da"))
        want_regional = bool(_inp("want_regional"))
        want_elev = bool(_inp("want_elev"))
        n = len(sdf)

        with ui.Progress(min=0, max=1) as p:
            p.set(0.05, message="Compiling site data", detail="Snapping sites to NHD flowlines")
            comids = nldi_comids(list(sdf["lon"]), list(sdf["lat"]))
            sdf["comid"] = comids
            p.set(0.25, detail="Drainage area (NLDI basins)")
            da = nldi_basin_sqkm_many(comids) if want_da else np.full(n, np.nan)
            elev = None
            if want_elev:
                p.set(0.35, detail="Site elevation (USGS 3DEP)")
                elev = [epqs_elev(sdf["lon"].iloc[i], sdf["lat"].iloc[i]) for i in range(n)]
            sc = None
            if metric_names:
                p.set(0.45, detail=f"StreamCAT ({len(metric_names)} metrics)")
                sc = streamcat_metrics(comids, metric_names, area="watershed")
            if nrsa_names:
                p.set(0.55, detail=f"NRSA metrics ({len(nrsa_names)})")
                sdf = attach_nrsa_metrics(sdf, nrsa_names, load_nrsa_values())
            ss_codes = ss_sel() or []
            if ss_codes:
                for cc in [f"ss_{c}" for c in ss_codes]:
                    sdf[cc] = np.nan
                for i in range(n):
                    st_name = sdf["state"].iloc[i] if "state" in sdf.columns else None
                    if st_name and str(st_name).strip():
                        a = state_abbr_from_name(st_name)
                        st = a if a else state_at(sdf["lon"].iloc[i], sdf["lat"].iloc[i], str(_STATES))
                    else:
                        st = state_at(sdf["lon"].iloc[i], sdf["lat"].iloc[i], str(_STATES))
                    p.set(0.55 + 0.15 * (i + 1) / n, detail=f"StreamStats {i + 1}/{n} ({st or '?'})")
                    vals = ss_basin_characteristics(sdf["lat"].iloc[i], sdf["lon"].iloc[i], st, ss_codes)
                    for c in ss_codes:
                        sdf.loc[sdf.index[i], f"ss_{c}"] = vals.get(c)
            mmw_codes = mmw_sel() or []
            if mmw_codes and mmw_available():
                for cc in mmw_codes:
                    sdf[cc] = np.nan
                for i in range(n):
                    p.set(0.7 + 0.15 * (i + 1) / n, detail=f"Model My Watershed {i + 1}/{n}")
                    vals = mmw_site_metrics(sdf["lat"].iloc[i], sdf["lon"].iloc[i], mmw_codes)
                    for c in mmw_codes:
                        sdf.loc[sdf.index[i], c] = vals.get(c)
            p.set(0.9, detail="Assembling table + regional predictions")
            comp = compile_site_table(
                sdf, lat_col="lat", lon_col="lon", comid_col="comid",
                streamcat_wide=sc, physio_path=(str(_PHYSIO) if want_regional else None),
                da_sqkm=da, bieger_geometry=bieger_geometry,
                division_abbr=bieger_division_abbr,
            )
            if not want_regional:
                comp = comp[[c for c in comp.columns
                             if c not in ("pred_BW_ft", "pred_BD_ft", "pred_BA_ft2", "bieger_division")]]
            if want_elev:
                comp["elev_3dep_m"] = np.round(np.asarray(elev, dtype=float), 2)
            comp = comp[[c for c in comp.columns if c != ".source"]]
            csrc = build_col_provenance(comp, sc, nrsa_names, upload_kept)
            cfun = {c: metric_map_function_label(c) for c in comp.columns}
            unmatched.set(int(sum(c is None for c in comids)))
            compiled.set(comp)
            col_source.set(csrc)
            col_function.set(cfun)
            saved_assignments.set(None)
            metric_cols = [c for c in comp.columns if c not in ("lat", "lon", "comid", "site_id")]
            cov = compute_coverage_table(comp, metric_cols)
            cov["source"] = [csrc.get(m, "") for m in cov["metric"]]
            cov["SFARI function"] = [cfun.get(m, "") for m in cov["metric"]]
            coverage.set(cov)
            p.set(1.0, detail="Done")

        extra = f" ({unmatched()} couldn't snap to a flowline)" if unmatched() > 0 else ""
        ui.notification_show(
            f"Compiled {len(compiled())} sites{extra}. Continue to classify the columns.",
            type="message", duration=6,
        )

    # ── classify + build ──────────────────────────────────────────────────────
    def _classify_profile():
        req(compiled() is not None)
        return profile_and_suggest(compiled())

    def _built_tables():
        req(compiled() is not None and saved_assignments() is not None)
        return build_config_tables_from_roles(compiled(), saved_assignments())

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
            ui.notification_show(
                "Dataset built. Open Reference Curves, or refine in the Workbook below.",
                type="message", duration=7,
            )

    # ── stepper + nav ─────────────────────────────────────────────────────────
    @render.ui
    def stepper():
        cur = step()
        chips = []
        for i, label in enumerate(_STEP_LABELS, start=1):
            cls = "wizard-step active" if i == cur else "wizard-step done" if i < cur else "wizard-step"
            chips.append(ui.div(
                ui.tags.span(str(i), class_="wizard-step-num"),
                ui.tags.span(label, class_="wizard-step-label"),
                class_=cls,
            ))
        return ui.div(*chips, class_="wizard-stepper d-flex flex-wrap gap-3")

    @render.ui
    def nav():
        cur = step()
        left = (
            ui.input_action_button("to_back", ui.TagList(fa("arrow-left"), " Back"),
                                   class_="btn btn-outline-secondary")
            if cur > 1 else ui.tags.span()
        )
        if cur == 7:
            right = ui.input_action_button("wiz_build", ui.TagList(fa("check"), " Build dataset"),
                                           class_="btn btn-success")
        elif cur == 5:
            kwargs = {"class_": "btn btn-primary"}
            if compiled() is None:
                kwargs["disabled"] = "disabled"
            right = ui.input_action_button("to_next", ui.TagList("Next: Classify ", fa("arrow-right")), **kwargs)
        else:
            right = ui.input_action_button(
                "to_next", ui.TagList(f"Next: {_STEP_LABELS[cur]} ", fa("arrow-right")),
                class_="btn btn-primary",
            )
        return ui.div(left, right, class_="d-flex justify-content-between")

    # ── body (steps 2, 4, 5, 6, 7) ────────────────────────────────────────────
    @render.ui
    def body():
        cur = step()
        if cur == 2:
            return _body_step2()
        if cur == 4:
            return _body_step4()
        if cur == 5:
            return _body_step5()
        if cur == 6:
            return _body_step6()
        if cur == 7:
            return _body_step7()
        return None

    def _body_step2():
        nr = nrsa_in_region()
        n_nrsa = 0 if nr is None else len(nr)
        return ui.TagList(
            ui.tags.h5(f"2. Add data{' — ' + region_label() if region_code() else ''}"),
            ui.tags.p("Where should the site data come from? You can combine both.", class_="text-muted"),
            ui.input_checkbox("use_nrsa", f"Published NRSA monitoring sites in this region ({n_nrsa} available)", value=True),
            ui.tags.hr(class_="my-2"),
            ui.input_checkbox("use_upload", "Import additional data — my own site table (CSV / Excel)", value=upload_df() is not None),
            ui.input_file("upload_file", None, accept=[".csv", ".tsv", ".txt", ".xlsx", ".xls"],
                          button_label="Choose file", placeholder="Optional upload"),
            ui.output_ui("upload_colmap"),
        )

    def _body_step4():
        return ui.TagList(
            ui.tags.h5("4. Choose metrics"),
            ui.tags.p("Metrics are grouped by data source. A recommended set is pre-selected; "
                      "expand any source to add more.", class_="text-muted"),
            ui.output_ui("metric_selected_count"),
            ui.accordion(
                ui.accordion_panel(
                    ui.TagList(bi("clipboard2-data"), " NRSA field & lab data ",
                               ui.tags.span(ui.output_text("cnt_nrsa", inline=True), class_="metric-count-badge")),
                    ui.output_ui("nrsa_section"), value="nrsa",
                ),
                ui.accordion_panel(
                    ui.TagList(bi("layers"), " EPA StreamCAT ",
                               ui.tags.span(ui.output_text("cnt_streamcat", inline=True), class_="metric-count-badge")),
                    ui.output_ui("metric_catalog"), value="streamcat",
                ),
                ui.accordion_panel(
                    ui.TagList(bi("water"), " USGS StreamStats ",
                               ui.tags.span(ui.output_text("cnt_streamstats", inline=True), class_="metric-count-badge")),
                    ui.output_ui("streamstats_section"), value="streamstats",
                ),
                ui.accordion_panel(
                    ui.TagList(bi("globe-americas"), " Model My Watershed ",
                               ui.tags.span(ui.output_text("cnt_mmw", inline=True), class_="metric-count-badge")),
                    ui.output_ui("mmw_section"), value="mmw",
                ),
                ui.accordion_panel(
                    ui.TagList(bi("rulers"), " Computed — USGS NLDI / 3DEP / regional curves ",
                               ui.tags.span(ui.output_text("cnt_computed", inline=True), class_="metric-count-badge")),
                    ui.input_checkbox("want_da", "Drainage area (NLDI basin)", value=False),
                    ui.input_checkbox("want_regional", "Regional bankfull predictions (Bieger curves)", value=False),
                    ui.input_checkbox("want_elev", "Site elevation (USGS 3DEP point)", value=False),
                    value="computed",
                ),
                ui.accordion_panel(
                    ui.TagList(bi("file-earmark-arrow-up"), " From your uploaded file ",
                               ui.tags.span(ui.output_text("cnt_upload", inline=True), class_="metric-count-badge")),
                    ui.output_ui("uploaded_cols_picker"), value="upload",
                ),
                id="metric_sources", open=False, multiple=True,
            ),
        )

    def _body_step5():
        return ui.TagList(
            ui.tags.h5("5. Compile"),
            ui.output_ui("compile_intro"),
            ui.div(
                ui.input_action_button("do_compile", ui.TagList(fa("cloud-arrow-down"), " Pull & compile data"),
                                       class_="btn btn-primary"),
                class_="d-flex gap-2 my-2",
            ),
            ui.output_ui("coverage_summary"),
            ui.output_ui("coverage_table"),
        )

    def _body_step6():
        return ui.TagList(
            ui.tags.h5("6. Classify columns"),
            ui.tags.p("We guessed a role for each column. A column can have more than one role. "
                      "Mark what you want to build curves for as Metric.", class_="text-muted"),
            ui.output_ui("role_summary"),
            ui.div(ui.output_ui("classify_table"), class_="wizard-classify-scroll"),
        )

    def _body_step7():
        return ui.TagList(
            ui.tags.h5("7. Review & build"),
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
    def sites_view_body():
        if _inp("sites_view") == "table":
            return ui.output_data_frame("sites_table")
        return output_widget("sites_map", height="460px") if _HAS_MAP else ui.div(
            "Map requires ipyleaflet.", class_="text-muted"
        )

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

    @reactive.effect
    def _refresh_sites_map():
        if not _HAS_MAP:
            return
        s = sites()
        _rm(_slayers, _SITES_MAP, "sites")
        _rm(_slayers, _SITES_MAP, "region")
        # region polygon for context
        code, kind = region_code(), region_kind()
        if code and kind in ("ecoregion", "state"):
            feats = _js_geojson(_ECOREGIONS if kind == "ecoregion" else _STATES)["features"]
            keyp = "US_L3CODE" if kind == "ecoregion" else "state"
            sel = [f for f in feats if str((f.get("properties") or {}).get(keyp)) == str(code)]
            if sel:
                _add(_slayers, _SITES_MAP, "region",
                     GeoJSON(data={"type": "FeatureCollection", "features": sel},
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
            _SITES_MAP.fit_bounds([[float(g["lat"].min()), float(g["lon"].min())],
                                   [float(g["lat"].max()), float(g["lon"].max())]])
        except Exception:  # noqa: BLE001
            pass

    # ── metric selection outputs (step 4) ─────────────────────────────────────
    # These render even while their accordion panel is collapsed (all panels
    # start closed) — otherwise the checkbox inputs never initialise, so the
    # default selections + header count badges read 0 (R's outputOptions
    # suspendWhenHidden=FALSE).
    @output(suspend_when_hidden=False)
    @render.ui
    def metric_catalog():
        cdf = _streamcat_catalog()
        if len(cdf) == 0:
            return ui.div("No metric catalog available.", class_="text-muted")
        doms = list(dict.fromkeys(cdf["domain"]))
        sel = set(metric_sel() or _default_streamcat())
        panels = []
        for k, dom in enumerate(doms):
            sub = cdf[cdf["domain"] == dom]
            ch = {r["name"]: r["label"] for _, r in sub.iterrows()}
            panels.append(ui.div(
                ui.tags.div(str(dom), class_="small text-uppercase text-muted fw-semibold"),
                ui.input_checkbox_group(f"metric_dom_{k}", None, choices=ch,
                                        selected=[n for n in sub["name"] if n in sel], inline=True),
                class_="mb-2",
            ))
        return ui.TagList(*panels)

    @output(suspend_when_hidden=False)
    @render.ui
    def nrsa_section():
        if not bool(_inp("use_nrsa")):
            return ui.div("Choose NRSA as a data source in step 2 to include its measured metrics.",
                          class_="text-muted small")
        cdf = load_nrsa_catalog()
        if cdf is None or len(cdf) == 0:
            return ui.div("NRSA metric catalog not available.", class_="text-muted small")
        sel0 = set(nrsa_sel() or _nrsa_default_sel())
        rec = set(metric_map_default_codes("nrsa"))
        blocks = []
        for code, label in _NRSA_SECTIONS:
            sub = cdf[cdf["name"].str.startswith(f"{code}_")]
            if len(sub) == 0:
                continue
            core = sub[sub["name"].isin(rec)]
            more = sub[~sub["name"].isin(rec)]
            core_ch = {
                r["name"]: (f"{r['label']} ({r['units']})" if r.get("units") else r["label"])
                for _, r in core.iterrows()
            }
            more_ch = {r["name"]: r["label"] for _, r in more.iterrows()}
            parts = [ui.tags.div(label, ui.tags.span(f" — {len(sub)} metrics", class_="text-muted"),
                                 class_="fw-semibold small")]
            if len(core):
                parts.append(ui.input_checkbox_group(
                    f"nrsa_core_{code}", None, choices=core_ch,
                    selected=[n for n in core["name"] if n in sel0], inline=True))
            if len(more):
                parts.append(ui.input_selectize(
                    f"nrsa_more_{code}", "Add more from this category", choices=more_ch,
                    selected=[n for n in more["name"] if n in sel0], multiple=True, width="100%"))
            blocks.append(ui.div(*parts, class_="mb-3 pb-2 border-bottom"))
        return ui.TagList(*blocks)

    @output(suspend_when_hidden=False)
    @render.ui
    def streamstats_section():
        ch = ss_core_bcs()
        return ui.TagList(
            ui.div("Basin characteristics, computed per site. Pulled only if selected (slower). "
                   "Coverage varies by state.", class_="small text-muted mb-1"),
            ui.input_checkbox_group("ss_sel", None, choices=ch, selected=list(ss_sel() or [])),
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def mmw_section():
        if not mmw_available():
            return ui.div(ui.TagList("Model My Watershed requires an API key (",
                                     ui.tags.code("MMW_API_KEY"),
                                     "). Set it to enable land cover, soils, terrain and climate metrics."),
                          class_="text-muted small")
        mc = mmw_core_metrics()
        ch = {k: v["label"] for k, v in mc.items()}
        return ui.TagList(
            ui.div("Per-site watershed metrics (NLCD land cover, soils, terrain, climate), CONUS only. "
                   "Slow for large watersheds.", class_="small text-muted mb-1"),
            ui.input_checkbox_group("mmw_sel", None, choices=ch, selected=list(mmw_sel() or [])),
        )

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

    @render.ui
    def metric_selected_count():
        return ui.div(ui.tags.strong(str(n_metrics_selected())), " metric(s) selected across sources.",
                      class_="alert alert-light border py-1 px-2 small mb-2")

    @render.text
    def cnt_nrsa():
        return f"{len(collect_nrsa_selection())} selected"

    @render.text
    def cnt_streamcat():
        return f"{len(collect_metric_selection())} selected"

    @render.text
    def cnt_streamstats():
        return f"{len(_inp('ss_sel') or [])} selected"

    @render.text
    def cnt_mmw():
        return f"{len(_inp('mmw_sel') or [])} selected"

    @render.text
    def cnt_computed():
        return f"{sum(bool(_inp(f)) for f in ('want_da', 'want_regional', 'want_elev'))} selected"

    @render.text
    def cnt_upload():
        return f"{len(_inp('upload_cols') or [])} selected"

    # ── compile outputs (step 5) ──────────────────────────────────────────────
    @render.ui
    def compile_intro():
        s = sites()
        computed = [lbl for flag, lbl in (("want_da", "drainage area"),
                                          ("want_regional", "regional bankfull"),
                                          ("want_elev", "3DEP elevation")) if _inp(flag)]
        n_ss, n_mmw = len(ss_sel() or []), len(mmw_sel() or [])
        return ui.div(
            f"Ready to compile {0 if s is None else len(s)} sites with "
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
    def coverage_table():
        cov = coverage()
        req(cov is not None)
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
        cols = [c for c in ("metric_key", "display_name", "column_name", "metric_family", "min_sample_size")
                if c in t["metrics"].columns]
        return _plain_table(t["metrics"][cols])

    @render.ui
    def review_predictors():
        t = _built_tables()
        cols = [c for c in ("predictor_key", "display_name", "column_name", "type")
                if c in t["predictors"].columns]
        return _plain_table(t["predictors"][cols])

    @render.ui
    def review_strats():
        t = _built_tables()
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
