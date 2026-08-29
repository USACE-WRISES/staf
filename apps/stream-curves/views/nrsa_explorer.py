"""NRSA explorer: browse every NRSA station across the three survey cycles.

A side analysis, like Regional Curves and Cross-Sections, but unlike those it
needs no built dataset: it reads the archive under ``data/nrsa/`` directly.

Read-only by design. Nothing here writes to the session or influences a run, so
browsing cannot perturb a result or its provenance. The pure logic (filtering,
the map payload, one station's record) lives in ``streamcurves.nrsa_explorer``
where the suite can reach it.
"""

from __future__ import annotations

import pandas as pd
from shiny import module, reactive, render, ui

try:
    from ipyleaflet import GeoJSON, LayersControl, Map, ScaleControl, TileLayer
    from ipywidgets import Layout
    from shinywidgets import output_widget, render_widget

    _HAS_MAP = True
except Exception:  # noqa: BLE001
    _HAS_MAP = False

from streamcurves import nrsa_dataset as nds
from streamcurves import nrsa_explorer as nx
from views.state import AppState
from views.theme import bi
from views.uihelpers import no_data_alert

# the same basemap the import wizard uses
_USGS_TOPO = ("https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/"
              "MapServer/tile/{z}/{y}/{x}")
_USGS_ATTR = "USGS The National Map"

_POINT_STYLE = {"radius": 5, "weight": 1, "fillOpacity": 0.85, "opacity": 1}

# a small, recognisable set to show as a per-cycle time series in the panel
_PANEL_METRICS = [
    "phab_XEMBED", "phab_PCT_SAFN", "phab_BFWD_RAT", "phab_SINU",
    "chem_PTL", "chem_COND", "bent_EPT_NTAX",
]

CYCLE_CHOICES = {c: nds.CYCLE_LABELS[c] for c in ("1314", "1819", "2324")}
MATCH_CHOICES = {"any": "Sampled in any of them", "all": "Sampled in all of them"}


# --------------------------------------------------------------------------- #
# Panel builders, at module level on purpose.
#
# They were closures inside the server first, where no test can reach them, and
# an accordion_panel arity mistake shipped straight past the suite: only clicking
# a station in the running app surfaced it. Same reasoning as the module-level
# builders in views/summary_page.py. Nothing here touches ``input`` or the
# session, so tests can render them straight to HTML.
# --------------------------------------------------------------------------- #

def _format_value(value) -> str:
    return "" if value is None else f"{value:,.3g}"


def metric_name_cell(metric):
    """The name cell: readable name, units, and the code underneath it."""
    return ui.tags.td(
        ui.TagList(
            metric["name"],
            ui.tags.span(f" ({metric['units']})", class_="text-muted small")
            if metric.get("units") else None,
        ),
        ui.tags.div(ui.tags.code(metric["metric"]), class_="nrsa-metric-code"),
        title=metric.get("description") or None,
    )


def metric_group_table(group, cycles_present):
    """One category's table: a column per cycle, or a single Value column when
    the category does not vary by cycle."""
    if group["varies_by_cycle"]:
        head = ui.tags.tr(ui.tags.th("Metric"),
                          *[ui.tags.th(nds.CYCLE_LABELS[c]) for c in cycles_present])
        rows = [
            ui.tags.tr(metric_name_cell(m),
                       *[ui.tags.td(_format_value(m["by_cycle"].get(c)))
                         for c in cycles_present])
            for m in group["metrics"]
        ]
    else:
        head = ui.tags.tr(ui.tags.th("Metric"), ui.tags.th("Value"))
        rows = [
            ui.tags.tr(metric_name_cell(m), ui.tags.td(_format_value(m["value"])))
            for m in group["metrics"]
        ]
    return ui.div(
        ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*rows),
                      class_="table table-sm nrsa-station-table"),
        class_="nrsa-metric-group-scroll",
    )


def metric_groups_ui(groups, cycles_present, *, search: str = ""):
    """The "All metrics by category" section: one collapsed panel per category."""
    heading = ui.tags.h6("All metrics by category", class_="mt-3")
    if not groups:
        message = (f'No metric name or code matches "{str(search).strip()}".'
                   if str(search or "").strip() else
                   "The archive holds no metric values for this station.")
        return ui.TagList(heading, ui.div(message, class_="text-muted small"))

    panels = []
    for group in groups:
        note = (ui.div("Watershed attributes from the flowline. The same in every "
                       "cycle, so they are shown once.",
                       class_="text-muted small mb-2")
                if not group["varies_by_cycle"] else None)
        panels.append(ui.accordion_panel(
            ui.TagList(group["category"],
                       ui.tags.span(f"{group['n']:,}", class_="metric-count-badge")),
            note,
            metric_group_table(group, cycles_present),
            # a non-string title needs an explicit value, or accordion_panel
            # raises "If `title` is not a string, `value` must be provided"
            value=group["category"],
        ))
    # deliberately no id: an id would register the accordion as a Shiny input,
    # and the panel around it is a reactive output, so every station click would
    # reset and re-transmit that state for nothing
    return ui.TagList(
        heading,
        ui.div(f"{sum(g['n'] for g in groups):,} metrics with a value for this station.",
               class_="text-muted small mb-2"),
        ui.accordion(*panels, open=False, multiple=True),
    )


@module.ui
def nrsa_explorer_ui():
    return ui.output_ui("explorer_page")


@module.server
def nrsa_explorer_server(input, output, session, state: AppState, active=None):
    """``active``: optional reactive callable, True while this tool is showing.

    The map widget gates on it for the same reason the wizard's maps do: an
    ipywidget built at session init races the leaflet bundle fetch.
    """
    ns = session.ns
    selected_station = reactive.value(None)
    map_holder: dict[str, object] = {}

    @reactive.calc
    def dataset():
        if not nds.multi_cycle_available():
            return None
        return nds.load_dataset(nds.MULTI_CYCLE_DATASET_ID)

    @reactive.calc
    def filtered() -> pd.DataFrame:
        ds = dataset()
        if ds is None:
            return pd.DataFrame()
        # the controls only exist once explorer_page has rendered, so read them
        # defensively the way the wizard does
        def _inp(name, default=None):
            try:
                value = input[name]()
            except Exception:  # noqa: BLE001
                return default
            return default if value is None else value

        return nx.filter_stations(
            ds.stations,
            ecoregion=_inp("eco_filter") or None,
            cycles=list(_inp("cycle_filter", ()) or ()),
            match=_inp("match_filter", "any"),
        )

    # ── page ────────────────────────────────────────────────────────────────
    @output
    @render.ui
    def explorer_page():
        ds = dataset()
        if ds is None:
            return no_data_alert(
                "The multi-cycle NRSA archive is not built in this checkout. Run "
                "scripts/nrsa/build_values_table.py to create data/nrsa/."
            )
        choices = {"": "All ecoregions"} | nx.ecoregion_choices(ds.stations)
        controls = ui.div(
            ui.div(
                ui.input_select(ns("eco_filter"), "Ecoregion", choices=choices,
                                selected="", width="320px"),
                ui.input_checkbox_group(
                    ns("cycle_filter"), "Survey cycles", choices=CYCLE_CHOICES,
                    selected=list(CYCLE_CHOICES), inline=True),
                ui.input_radio_buttons(
                    ns("match_filter"), None, choices=MATCH_CHOICES,
                    selected="any", inline=True),
                class_="d-flex flex-wrap align-items-end gap-4",
            ),
            class_="nrsa-explorer-controls mb-2",
        )
        body = (
            ui.div(output_widget(ns("station_map"), height="560px"),
                   class_="nrsa-explorer-map")
            if _HAS_MAP else
            ui.div("Map requires ipyleaflet.", class_="text-muted")
        )
        return ui.TagList(
            ui.h4(ui.TagList(bi("globe-americas"), " NRSA explorer"), class_="mb-1"),
            ui.div(
                "Every NRSA station across the 2013-14, 2018-19 and 2023-24 "
                "surveys. Read-only.",
                class_="text-muted small mb-3",
                title="EPA renames sites each cycle, so a station here is a "
                      "physical location, matched across cycles by the "
                      "persistent NARS id where it exists and by flowline and "
                      "position otherwise.",
            ),
            controls,
            ui.output_ui(ns("summary_line")),
            ui.output_ui(ns("legend")),
            ui.layout_column_wrap(
                ui.card(ui.card_header("Stations"), body),
                ui.card(
                    ui.card_header("Station record"),
                    # Static, NOT inside station_panel: a text input created
                    # inside a reactive output is rebuilt on every keystroke and
                    # loses what you typed (the trap import_map documents for its
                    # own adv_search). explorer_page only re-renders when the
                    # dataset changes, so inputs declared here are stable.
                    ui.input_text(ns("metric_search"), None, width="100%",
                                  placeholder="Filter metrics by name or code..."),
                    ui.output_ui(ns("station_panel")),
                ),
                width=1 / 2,
            ),
        )

    @output
    @render.ui
    def summary_line():
        shown = nx.coverage_summary(filtered())
        total = nx.coverage_summary(dataset().stations if dataset() else pd.DataFrame())
        return ui.div(
            ui.tags.strong(f"{shown['stations']:,}"), " stations shown",
            f" of {total['stations']:,}. ",
            f"{shown['multi_cycle']:,} sampled in more than one cycle, ",
            f"{shown['with_comid']:,} carry a flowline id.",
            class_="text-muted small mb-2",
        )

    @output
    @render.ui
    def legend():
        rows = nx.legend_rows(filtered())
        if not rows:
            return None
        chips = [
            ui.tags.span(
                ui.tags.span(class_="nrsa-legend-dot",
                             style=f"background:{row['color']}"),
                f"{row['label']} ({row['n']:,})",
                class_="nrsa-legend-chip",
            )
            for row in rows
        ]
        return ui.div(*chips, class_="nrsa-legend mb-2")

    # ── the map ─────────────────────────────────────────────────────────────
    if _HAS_MAP:
        @output
        @render_widget
        def station_map():
            with reactive.isolate():
                gate = None if active is None else bool(active())
            if gate is False:
                return None
            existing = map_holder.get("map")
            if existing is not None:
                return existing
            m = Map(center=(38.5, -96), zoom=4, scroll_wheel_zoom=True,
                    layout=Layout(height="100%"))
            m.clear_layers()
            m.add(TileLayer(url=_USGS_TOPO, name="USGS Topo", base=True,
                            attribution=_USGS_ATTR, max_native_zoom=16, max_zoom=18))
            m.add(LayersControl(position="topright"))
            m.add(ScaleControl(position="bottomright"))
            map_holder["map"] = m
            # Attaching the station layer to the map's INITIAL widget state trips
            # the shinywidgets bug the wizard documents ("Could not create a
            # model", polygons never render). Seed it after this render flushes.
            session.on_flushed(_seed_stations, once=True)
            return m

    def _station_layer(stations: pd.DataFrame):
        payload = nx.station_geojson(stations)
        layer = GeoJSON(data=payload, point_style=_POINT_STYLE, name="NRSA stations")

        def _click(**kw):
            props = (kw.get("feature") or {}).get("properties") or {}
            key = props.get("station_key")
            if key:
                selected_station.set(str(key))

        layer.on_click(_click)
        return layer

    def _seed_stations():
        _refresh_layer()

    def _refresh_layer():
        m = map_holder.get("map")
        if m is None:
            return
        old = map_holder.get("layer")
        if old is not None:
            try:
                m.remove(old)
            except Exception:  # noqa: BLE001  (already detached)
                pass
        with reactive.isolate():
            stations = filtered()
        layer = _station_layer(stations)
        m.add(layer)
        map_holder["layer"] = layer

    @reactive.effect
    def _redraw_on_filter_change():
        filtered()          # take the dependency
        if map_holder.get("map") is not None:
            _refresh_layer()

    # ── the panel ───────────────────────────────────────────────────────────
    @output
    @render.ui
    def station_panel():
        key = selected_station()
        ds = dataset()
        if ds is None:
            return None
        if not key:
            return ui.div("Click a station on the map to see its record across cycles.",
                          class_="text-muted")
        detail = nx.station_detail(key, stations=ds.stations, visits=ds.visits,
                                   values=ds.values, metrics=_PANEL_METRICS)
        if not detail:
            return ui.div("That station is no longer in the archive.", class_="text-muted")

        visits = ui.tags.table(
            ui.tags.thead(ui.tags.tr(
                ui.tags.th("Survey"), ui.tags.th("Site id"), ui.tags.th("Visit"),
                ui.tags.th("Year"), ui.tags.th("Date"))),
            ui.tags.tbody(*[
                ui.tags.tr(
                    ui.tags.td(v["cycle_label"]),
                    ui.tags.td(ui.tags.code(v["site_id"])),
                    ui.tags.td(v["visit_no"]), ui.tags.td(v["year"]),
                    ui.tags.td(v["date"]))
                for v in detail["visits"]
            ]),
            class_="table table-sm nrsa-station-table",
        )

        cycles_present = [c for c in ("1314", "1819", "2324")
                          if c in str(detail["cycles"]).split(",")]
        metric_rows = []
        for metric in detail["metrics"]:
            if not metric["by_cycle"]:
                continue
            cells = []
            for cycle in cycles_present:
                value = metric["by_cycle"].get(cycle)
                cells.append(ui.tags.td("" if value is None else f"{value:,.3g}"))
            metric_rows.append(ui.tags.tr(
                ui.tags.td(ui.TagList(
                    metric["name"],
                    ui.tags.span(f" ({metric['units']})", class_="text-muted small")
                    if metric["units"] else None)),
                *cells))
        metrics_table = ui.tags.table(
            ui.tags.thead(ui.tags.tr(
                ui.tags.th("Metric"),
                *[ui.tags.th(nds.CYCLE_LABELS[c]) for c in cycles_present])),
            ui.tags.tbody(*metric_rows),
            class_="table table-sm nrsa-station-table",
        ) if metric_rows else ui.div("No values for the sampled metrics.",
                                     class_="text-muted small")

        return ui.TagList(
            ui.div(ui.tags.strong(detail["station_key"]),
                   ui.tags.span(f"  {detail['us_l3code']} {detail['us_l3name']}",
                                class_="text-muted"),
                   class_="mb-1"),
            ui.div(
                f"{detail['cycle_label']}. {detail['n_visits']} visit"
                f"{'' if detail['n_visits'] == 1 else 's'}. "
                f"{detail['state']}. "
                + (f"Flowline {detail['comid']}." if detail["comid"] else
                   "No flowline id in the archive."),
                class_="text-muted small mb-2",
            ),
            ui.tags.h6("Visits"), visits,
            ui.tags.h6("Key metrics by cycle"), metrics_table,
            _all_metrics_section(key, ds, cycles_present),
        )

    def _all_metrics_section(station_key, ds, cycles_present):
        try:
            search = input.metric_search() or ""
        except Exception:  # noqa: BLE001  (not yet in the DOM)
            search = ""
        groups = nx.station_metric_groups(station_key, values=ds.values, search=search)
        return metric_groups_ui(groups, cycles_present, search=search)

    @reactive.effect
    @reactive.event(input.eco_filter, ignore_init=True)
    def _clear_selection_on_region_change():
        selected_station.set(None)
