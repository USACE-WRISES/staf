"""EASI National Builder: the local control panel over the checkpointed worker.

    cd tools/easi-national && shiny run app.py --port 8020

Chunks (a HUC8, a HUC4, a state, or a region) are queued as jobs; the worker
subprocess drains the queue with a heartbeat file the panel reads at the chosen
cadence (every minute by default) into a timestamped Phase / Task / Step line.
Pause finishes the current request and exits cleanly; Resume relaunches the
worker, which skips everything already done.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from shiny import App, reactive, render, ui

import builder  # noqa: F401  (puts apps/easi on sys.path)
from builder import config, inventory, pipeline, publish
from builder.paths import DataRoot
from builder.state import (Control, Queue, Rates, UnitStates, read_progress, status_line,
                           worker_alive, worker_state)
from builder.stages import tiles as tiles_stage
from builder.units import Chunk, list_chunks, load_huc8_index, make_chunk

STATE_CHOICES = {abbr: abbr for abbr in inventory.CONUS_STATES}


def _controls(suffix: str):
    """The worker buttons, repeated on the Data and Cross-sections tabs."""
    return ui.div(ui.input_action_button(f"start{suffix}", "Start", class_="btn-primary btn-sm"),
                  ui.input_action_button(f"pause{suffix}", "Pause", class_="btn-outline-secondary btn-sm"),
                  ui.input_action_button(f"resume{suffix}", "Resume", class_="btn-outline-secondary btn-sm"),
                  ui.input_action_button(f"stop_now{suffix}", "Stop now", class_="btn-outline-danger btn-sm"),
                  ui.input_action_link(f"refresh_now{suffix}", "Refresh now", style="align-self:center;margin-left:8px"),
                  style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px")


def _status_color(status: str) -> str:
    return {"done": "#1a7f37", "downloaded": "#1a7f37", "sampled": "#1a7f37", "running": "#1f6feb",
            "sampling": "#1f6feb", "failed": "#b42318", "paused": "#9a6a00", "partial": "#9a6a00",
            "stale": "#9a6a00"}.get(status, "#55607a")

try:
    from ipyleaflet import GeoJSON, Map, TileLayer, basemaps
    from shinywidgets import output_widget, render_widget
    _HAS_MAP = True
except Exception:  # noqa: BLE001
    _HAS_MAP = False

ROOT = DataRoot.default().ensure()
TOOL_ROOT = Path(__file__).resolve().parent
WORKER_LOG = ROOT.state / "worker.log"
_WORKER: dict = {"proc": None}

STATUS_COLORS = {"complete": "#c8d9f2", "partial": "#f5e7a6", "not_started": "#eef1f5"}


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,.0f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"


def _worker_running() -> bool:
    """A worker is running: one this panel launched, or any other whose
    heartbeat pid is alive (a terminal or scheduled-task worker)."""
    proc = _WORKER.get("proc")
    if proc is not None and proc.poll() is None:
        return True
    return worker_alive(ROOT)


def _start_worker() -> None:
    if _worker_running():
        return
    Control(ROOT).clear()
    log = open(WORKER_LOG, "a", encoding="utf-8")
    _WORKER["proc"] = subprocess.Popen(
        [sys.executable, "-u", "-m", "builder.worker", "--root", str(ROOT.root), "queue"],
        cwd=str(TOOL_ROOT), stdout=log, stderr=subprocess.STDOUT)


def _tool_checks() -> list[tuple[str, bool, str]]:
    ok_gh, note_gh = publish.gh_ready()
    ok_docker, note_docker = tiles_stage.docker_ready()
    usage = shutil.disk_usage(ROOT.root)
    national = ROOT.vaa.exists() and ROOT.index.exists()
    return [
        ("Data root", True, f"{ROOT.root}  ({_fmt_bytes(usage.free)} free)"),
        ("National index", national, "VAA, COMID index" + (", dams" if ROOT.nid.exists() else ", dams missing")
         + (", HUC4 polygons" if ROOT.huc4_geojson.exists() else ", HUC4 polygons missing")
         + (", StreamCat cache" if (ROOT.national / "streamcat.parquet").exists()
            else ", StreamCat cache missing (Run national pulls)")
         + (", NAS cache" if (ROOT.national / "nas.parquet").exists() else ", NAS cache missing")
         + (", flowlines" if (ROOT.national / "flowlines.parquet").exists() else ", flowlines from the fabric API")
         + (", HUC12" if (ROOT.national / "huc12.parquet").exists() else ", HUC12 from the fabric API")
         + (", ATTAINS" if (ROOT.national / "attains.parquet").exists() else ", ATTAINS paged from the service")
         if national else "run the national job first"),
        ("3DEP catalogs", ROOT.dem1m_catalog.exists() and ROOT.dem19_catalog.exists(),
         ("1 m tiles" if ROOT.dem1m_catalog.exists() else "1 m tile catalog missing")
         + (", 1/9 arc-second quads" if ROOT.dem19_catalog.exists() else ", 1/9 arc-second quad catalog missing")
         + " (Cross-sections tab: Rebuild catalogs)"),
        ("gh", ok_gh, note_gh),
        ("Docker (tiles)", ok_docker, note_docker),
    ]


app_ui = ui.page_navbar(
    ui.nav_panel(
        "Overview",
        ui.layout_columns(
            ui.card(ui.card_header("Setup"), ui.output_ui("checks"),
                    ui.input_action_button("run_national", "Run national pulls",
                                           class_="btn-outline-secondary btn-sm")),
            ui.card(ui.card_header("Worker"), ui.output_ui("worker_status"),
                    ui.div(ui.input_action_button("start", "Start", class_="btn-primary btn-sm"),
                           ui.input_action_button("pause", "Pause", class_="btn-outline-secondary btn-sm"),
                           ui.input_action_button("resume", "Resume", class_="btn-outline-secondary btn-sm"),
                           ui.input_action_button("cancel", "Cancel", class_="btn-outline-danger btn-sm"),
                           ui.input_action_button("stop_now", "Stop now", class_="btn-outline-danger btn-sm",
                                                  title="Kill the worker at once; the job goes back in the queue "
                                                        "and only the request in flight repeats on Resume"),
                           class_="d-flex gap-2"),
                    ui.output_ui("progress_bar"),
                    ui.div(ui.input_select("refresh", "Status refresh",
                                           {"60": "every minute", "300": "every 5 minutes",
                                            "900": "every 15 minutes", "3600": "every hour"},
                                           selected="3600", width="180px"),
                           ui.input_action_link("refresh_now", "Refresh now"),
                           class_="d-flex gap-3 align-items-end"),
                    ui.output_ui("status_log"), ui.output_ui("log_tail")),
            ui.card(ui.card_header("Coverage"),
                    output_widget("cover_map", height="420px") if _HAS_MAP else ui.p("ipyleaflet missing"),
                    ui.output_ui("coverage_summary")),
            col_widths=(4, 4, 4)),
    ),
    ui.nav_panel(
        "Units",
        ui.layout_columns(
            ui.card(ui.card_header("Add a chunk"),
                    ui.input_select("kind", "Kind", {"huc8": "HUC8", "huc4": "HUC4",
                                                     "state": "State", "vpu": "Region (VPU)"}),
                    ui.input_text("value", "Value", placeholder="02080204, 0208, VA, 02"),
                    ui.input_checkbox_group("stages", "Stages", {s: s for s in pipeline.ORDER},
                                            selected=list(pipeline.ORDER)),
                    ui.input_checkbox("with_tiles", "Queue tiles for the chunk's regions", value=True),
                    ui.input_checkbox("with_stage", "Queue staging afterwards", value=True),
                    ui.input_action_button("add_chunk", "Queue chunk", class_="btn-primary btn-sm"),
                    ui.output_ui("add_msg")),
            ui.card(ui.card_header("Chunks"), ui.output_ui("chunk_table"),
                    ui.card_header("Queue"), ui.output_ui("queue_table"),
                    ui.input_action_button("clear_queue", "Clear queue", class_="btn-outline-secondary btn-sm")),
            col_widths=(4, 8)),
    ),
    ui.nav_panel(
        "Data",
        ui.layout_columns(
            ui.card(ui.card_header("National datasets"), ui.output_ui("national_table")),
            ui.card(ui.card_header("Worker"), ui.output_ui("worker_status_data"), _controls("_d"),
                    ui.output_ui("disk_summary")),
            col_widths=(8, 4)),
        ui.card(ui.card_header("Downloads by chunk"),
                ui.div(ui.input_select("dl_state", "State", STATE_CHOICES, selected="VA"),
                       ui.input_action_button("dl_queue", "Download this state",
                                              class_="btn-primary btn-sm"),
                       ui.output_ui("dl_msg"), style="display:flex;gap:10px;align-items:end;flex-wrap:wrap"),
                ui.output_ui("downloads_table")),
    ),
    ui.nav_panel(
        "Cross-sections",
        ui.layout_columns(
            ui.card(ui.card_header("3DEP catalogs"), ui.output_ui("catalog_card")),
            ui.card(ui.card_header("Bandwidth budget"), ui.output_ui("bandwidth_card")),
            ui.card(ui.card_header("Worker"), ui.output_ui("worker_status_xs"), _controls("_x")),
            col_widths=(4, 4, 4)),
        ui.card(ui.card_header("Archive by state"),
                ui.div(ui.input_select("xs_state", "State", STATE_CHOICES, selected="VA"),
                       ui.input_checkbox("xs_with_score", "then re-score", value=True),
                       ui.input_checkbox("xs_with_publish", "then tiles, staging and publish", value=False),
                       ui.input_checkbox("xs_keep_windows", "keep each reach's DEM window (1 to 3 MB per reach)",
                                         value=False),
                       ui.input_action_button("xs_queue", "Sample this state", class_="btn-primary btn-sm"),
                       ui.output_ui("xs_msg"), style="display:flex;gap:10px;align-items:end;flex-wrap:wrap"),
                ui.output_ui("xs_progress"),
                ui.output_ui("xs_table")),
    ),
    ui.nav_panel(
        "Publish",
        ui.layout_columns(
            ui.card(ui.card_header("Staged changes"), ui.output_ui("publish_plan"),
                    ui.div(ui.input_action_button("stage_now", "Rebuild staging", class_="btn-outline-secondary btn-sm"),
                           ui.input_action_button("publish_now", "Publish now", class_="btn-primary btn-sm"),
                           class_="d-flex gap-2")),
            ui.card(ui.card_header("History"), ui.output_ui("publish_history")),
            col_widths=(7, 5)),
    ),
    ui.nav_panel(
        "QA",
        ui.card(ui.card_header("Parity: live EASI vs precomputed"),
                ui.input_text("qa_huc8", "HUC8", placeholder="02080204"),
                ui.input_numeric("qa_n", "Reaches", value=10, min=1, max=200),
                ui.input_select("qa_xs", "Cross-sections", {"off": "not compared", "10m": "live forced to 10 m (exact check)",
                                                            "best": "live as the app runs it (1 m where available)"},
                                selected="off"),
                ui.input_action_button("qa_run", "Run parity check", class_="btn-primary btn-sm"),
                ui.output_ui("qa_result")),
    ),
    # No page-wide pulse or output spinners: the timed refresh would otherwise
    # animate the whole page every interval; the status log is the indicator.
    header=ui.busy_indicators.use(spinners=False, pulse=False),
    title="EASI National Builder",
    id="nav",
)


def server(input, output, session):
    tick = reactive.value(0)
    _add_msg = reactive.value("")
    qa_msg = reactive.value("")
    publish_msg = reactive.value("")

    _status_lines = reactive.value([])        # timestamped one-liners, newest last, bounded

    def _bump():
        with reactive.isolate():
            tick.set(tick() + 1)
            lines = list(_status_lines())
            lines.append(status_line(read_progress(ROOT)))
            _status_lines.set(lines[-120:])

    @reactive.effect
    def _poll():
        # the panel re-reads the heartbeat file at the chosen cadence (a few KB
        # each time); every button press refreshes at once
        try:
            interval = float(input.refresh() or 3600)
        except Exception:  # noqa: BLE001 - before the input exists
            interval = 3600.0
        reactive.invalidate_later(max(10.0, interval))
        _bump()

    @reactive.effect
    @reactive.event(input.refresh_now)
    def _refresh_now():
        _bump()

    @render.ui
    def status_log():
        lines = _status_lines()
        if not lines:
            return None
        return ui.tags.pre("\n".join(reversed(lines[-40:])),
                           style="font-size:11px;max-height:180px;overflow:auto;background:#eef2f8;padding:6px;margin-top:6px")

    # ------------------------------------------------------------ overview
    @render.ui
    def checks():
        tick()
        rows = [ui.div(ui.span("OK " if ok else "!! ", style="font-weight:600;color:%s" % ("#1a7f37" if ok else "#b42318")),
                       ui.tags.b(name + ": "), ui.span(note), style="font-size:13px;margin:2px 0")
                for name, ok, note in _tool_checks()]
        return ui.div(*rows)

    @render.ui
    def worker_status():
        tick()
        p = read_progress(ROOT)
        ws = worker_state(ROOT)
        color = {"running": "#1f6feb", "pausing": "#9a6a00", "cancelling": "#9a6a00",
                 "failed": "#b42318"}.get(ws["phase"], "#1a7f37")
        safe = ("Safe to disconnect or shut down." if ws["safe"]
                else "Not yet safe to shut down: wait for the paused state.")
        return ui.div(ui.div(ui.tags.b("Worker: "), ui.span(ws["phase"], style=f"color:{color};font-weight:600"),
                             style="font-size:13px"),
                      ui.div(ws["text"], style="font-size:12px;color:#33415c;margin:2px 0"),
                      ui.div(safe, style=f"font-size:12px;font-weight:600;color:{'#1a7f37' if ws['safe'] else '#9a6a00'}"),
                      ui.div(status_line(p), style="font-size:12px;color:#33415c;margin-top:6px;font-weight:600"),
                      ui.div(f"queue: {len(Queue(ROOT).read())} jobs", style="font-size:12px;color:#55607a"))

    @render.ui
    def progress_bar():
        tick()
        p = read_progress(ROOT)
        total, done = int(p.get("total") or 0), int(p.get("done") or 0)
        if not total:
            return None
        pct = min(100, int(100 * done / total))
        eta = p.get("eta_s")
        eta_txt = f" · about {int(eta // 60)} min left" if eta else ""
        return ui.div(ui.div(ui.div(style=f"width:{pct}%;height:8px;background:#1f6feb;border-radius:4px"),
                             style="background:#e5e9f0;border-radius:4px;margin:6px 0"),
                      ui.div(f"{done:,} / {total:,}{eta_txt}", style="font-size:12px;color:#55607a"))

    @render.ui
    def log_tail():
        tick()
        p = read_progress(ROOT)
        lines = p.get("log") or []
        return ui.tags.pre("\n".join(lines[-12:]), style="font-size:11px;max-height:220px;overflow:auto;background:#f6f8fa;padding:6px")

    @reactive.effect
    @reactive.event(input.start)
    def _start():
        _start_worker()
        _bump()

    @reactive.effect
    @reactive.event(input.resume)
    def _resume():
        _start_worker()
        _bump()

    @reactive.effect
    @reactive.event(input.stop_now)
    def _stop_now():
        from builder import worker as worker_mod
        worker_mod.hard_stop(ROOT)
        _WORKER["proc"] = None
        _bump()

    @reactive.effect
    @reactive.event(input.pause)
    def _pause():
        Control(ROOT).request("pause")
        _bump()

    @reactive.effect
    @reactive.event(input.cancel)
    def _cancel():
        Control(ROOT).request("cancel")
        proc = _WORKER.get("proc")
        if proc is not None and proc.poll() is None:
            for _ in range(20):
                time.sleep(0.5)
                if proc.poll() is not None:
                    break
            else:
                proc.terminate()
        _bump()

    @reactive.effect
    @reactive.event(input.run_national)
    def _national():
        Queue(ROOT).append([{"job": "national"}])
        _start_worker()

    # ---------------------------------------------------------------- map
    if _HAS_MAP:
        _MAP = Map(center=(38.5, -96), zoom=4, scroll_wheel_zoom=True)
        _layers: dict = {"cover": None}

        def _coverage_geojson() -> dict | None:
            for path in (ROOT.staging / "coverage.geojson", ROOT.huc4_geojson):
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
            return None

        def _style(feature):
            status = (feature.get("properties") or {}).get("status", "not_started")
            return {"color": "#8a93a3", "weight": 1, "fillColor": STATUS_COLORS.get(status, "#eef1f5"),
                    "fillOpacity": 0.55}

        def _refresh_cover():
            data = _coverage_geojson()
            if data is None:
                return
            if _layers["cover"] is not None:
                try:
                    _MAP.remove(_layers["cover"])
                except Exception:  # noqa: BLE001
                    pass
            layer = GeoJSON(data=data, style_callback=_style, name="Coverage")
            _MAP.add(layer)
            _layers["cover"] = layer

        @render_widget
        def cover_map():
            session.on_flushed(_refresh_cover, once=True)
            return _MAP

        @reactive.effect
        @reactive.event(input.stage_now, input.publish_now)
        def _map_refresh():
            _refresh_cover()

    @render.ui
    def coverage_summary():
        tick()
        try:
            manifest = json.loads((ROOT.staging / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ui.div("nothing staged yet", style="font-size:12px;color:#55607a")
        units = manifest.get("units") or {}
        complete = sum(1 for u in units.values() if u.get("status") == "complete")
        return ui.div(f"{len(units)} HUC4 units staged ({complete} complete), "
                      f"{manifest.get('reaches_scored', 0):,} reaches, "
                      f"{len(manifest.get('tiles') or {})} tile archives, updated {manifest.get('updated')}",
                      style="font-size:12px;color:#55607a")

    # -------------------------------------------------------------- units
    @reactive.effect
    @reactive.event(input.add_chunk)
    def _add_chunk():
        kind, value = input.kind(), (input.value() or "").strip()
        if not value:
            _add_msg.set("enter a value")
            return
        try:
            chunk = make_chunk(ROOT, kind, value)
        except Exception as exc:  # noqa: BLE001
            _add_msg.set(f"could not build the chunk: {exc}")
            return
        jobs = [{"job": "chunk", "kind": kind, "value": value, "stages": list(input.stages())}]
        if input.with_tiles():
            jobs += [{"job": "tiles", "vpu": vpu} for vpu in chunk.vpus]
        if input.with_stage():
            jobs.append({"job": "stage"})
        Queue(ROOT).append(jobs)
        _add_msg.set(f"queued {chunk.label}: {len(chunk.huc8s)} HUC8s, {chunk.n_comids:,} reaches, "
                     f"regions {', '.join(chunk.vpus)}")

    @render.ui
    def add_msg():
        return ui.div(_add_msg(), style="font-size:12px;color:#55607a;margin-top:6px")

    @render.ui
    def chunk_table():
        tick()
        states = UnitStates(ROOT)
        chunks = list_chunks(ROOT)
        if not chunks:
            return ui.div("no chunks yet", style="font-size:12px;color:#55607a")
        head = ui.tags.tr(ui.tags.th("Chunk"), ui.tags.th("HUC8s"), ui.tags.th("Reaches"),
                          *[ui.tags.th(s) for s in pipeline.ORDER])
        rows = []
        for chunk in chunks:
            status = pipeline.chunk_status(states, chunk)
            cells = []
            for s in pipeline.ORDER:
                v = status.get(s, "pending")
                color = {"done": "#1a7f37", "running": "#1f6feb", "failed": "#b42318"}.get(v, "#55607a")
                cells.append(ui.tags.td(v, style=f"color:{color}"))
            rows.append(ui.tags.tr(ui.tags.td(chunk.label), ui.tags.td(str(len(chunk.huc8s))),
                                   ui.tags.td(f"{chunk.n_comids:,}"), *cells))
        return ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*rows),
                             class_="table table-sm", style="font-size:12px")

    @render.ui
    def queue_table():
        tick()
        items = Queue(ROOT).read()
        if not items:
            return ui.div("queue empty", style="font-size:12px;color:#55607a")
        return ui.tags.ol(*[ui.tags.li(json.dumps({k: v for k, v in i.items() if v}))
                            for i in items], style="font-size:12px")

    @reactive.effect
    @reactive.event(input.clear_queue)
    def _clear_queue():
        Queue(ROOT).clear()

    # ------------------------------------------------------------ publish
    @render.ui
    def publish_plan():
        tick()
        items = publish.plan(ROOT)
        if not items:
            return ui.div("nothing staged", style="font-size:12px;color:#55607a")
        changed = [i for i in items if i["changed"]]
        total = sum(i["bytes"] for i in changed)
        rows = [ui.tags.tr(ui.tags.td(i["asset"]), ui.tags.td(_fmt_bytes(i["bytes"])),
                           ui.tags.td("upload" if i["changed"] else "unchanged"))
                for i in items]
        return ui.div(ui.div(f"{len(changed)} files to upload, {_fmt_bytes(total)}; "
                             f"{publish_msg()}", style="font-size:12px;color:#55607a"),
                      ui.tags.table(ui.tags.tbody(*rows), class_="table table-sm", style="font-size:12px"))

    @reactive.effect
    @reactive.event(input.stage_now)
    def _stage_now():
        Queue(ROOT).append([{"job": "stage"}])
        _start_worker()
        publish_msg.set("staging queued")

    @reactive.effect
    @reactive.event(input.publish_now)
    def _publish_now():
        Queue(ROOT).append([{"job": "stage"}, {"job": "publish"}])
        _start_worker()
        publish_msg.set("staging + publish queued")

    @render.ui
    def publish_history():
        tick()
        path = ROOT.state / publish.LOG
        if not path.exists():
            return ui.div("no publishes yet", style="font-size:12px;color:#55607a")
        lines = path.read_text(encoding="utf-8").splitlines()[-30:]
        return ui.tags.pre("\n".join(lines), style="font-size:11px;max-height:400px;overflow:auto")

    # --------------------------------------------------------------- data
    dl_msg_v = reactive.value("")
    xs_msg_v = reactive.value("")

    for _suffix in ("_d", "_x"):
        def _make(suffix):
            @reactive.effect
            @reactive.event(getattr(input, f"start{suffix}"))
            def _start_again():
                _start_worker()
                _bump()

            @reactive.effect
            @reactive.event(getattr(input, f"resume{suffix}"))
            def _resume_again():
                _start_worker()
                _bump()

            @reactive.effect
            @reactive.event(getattr(input, f"refresh_now{suffix}"))
            def _refresh_again():
                _bump()

            @reactive.effect
            @reactive.event(getattr(input, f"pause{suffix}"))
            def _pause_again():
                Control(ROOT).request("pause")
                _bump()

            @reactive.effect
            @reactive.event(getattr(input, f"stop_now{suffix}"))
            def _stop_again():
                from builder import worker as worker_mod
                worker_mod.hard_stop(ROOT)
                _WORKER["proc"] = None
                _bump()
        _make(_suffix)

    def _worker_card():
        p = read_progress(ROOT)
        ws = worker_state(ROOT)
        color = {"running": "#1f6feb", "pausing": "#9a6a00", "cancelling": "#9a6a00",
                 "failed": "#b42318"}.get(ws["phase"], "#1a7f37")
        return ui.div(ui.div(ui.tags.b("Worker: "), ui.span(ws["phase"], style=f"color:{color};font-weight:600"),
                             style="font-size:13px"),
                      ui.div(status_line(p), style="font-size:12px;color:#33415c;margin-top:4px"),
                      ui.div(f"queue: {len(Queue(ROOT).read())} jobs", style="font-size:12px;color:#55607a"))

    @render.ui
    def worker_status_data():
        tick()
        return _worker_card()

    @render.ui
    def worker_status_xs():
        tick()
        return _worker_card()

    @render.ui
    def national_table():
        tick()
        rows = []
        for r in inventory.national_datasets(ROOT):
            rows.append(ui.tags.tr(
                ui.tags.td(r["label"]), ui.tags.td(r["status"], style=f"color:{_status_color(r['status'])};font-weight:600"),
                ui.tags.td(_fmt_bytes(r["size"]) if r["size"] else ""),
                ui.tags.td(f"{r['rows']:,}" if r.get("rows") else ""),
                ui.tags.td(str(r.get("updated") or "")[:16]), ui.tags.td(r["origin"], style="color:#55607a"),
                ui.tags.td(ui.input_action_button(f"fetch_{r['step']}", "Fetch", class_="btn-outline-secondary btn-sm"))))
        head = ui.tags.tr(*[ui.tags.th(h) for h in ("Dataset", "Status", "Size", "Rows", "Updated", "Origin", "")])
        return ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*rows), class_="table table-sm", style="font-size:12px")

    for _step, _label, _files, _origin in inventory.NATIONAL_STEPS:
        def _make_fetch(step, label):
            @reactive.effect
            @reactive.event(getattr(input, f"fetch_{step}"))
            def _fetch():
                Queue(ROOT).append([{"job": "national", "steps": [step]}])
                dl_msg_v.set(f"queued the national step: {label}")
                _bump()
        _make_fetch(_step, _label)

    @reactive.effect
    @reactive.event(input.dl_queue)
    def _dl_queue():
        abbr = (input.dl_state() or "").strip().upper()
        try:
            chunk = make_chunk(ROOT, "state", abbr)
        except Exception as exc:  # noqa: BLE001
            dl_msg_v.set(f"could not build the chunk: {exc}")
            return
        Queue(ROOT).append([{"job": "chunk", "kind": "state", "value": abbr, "stages": list(pipeline.CHUNK_STAGES)}])
        dl_msg_v.set(f"queued the downloads for {chunk.label}: {len(chunk.huc8s)} HUC8s, {chunk.n_comids:,} reaches")
        _bump()

    @render.ui
    def dl_msg():
        return ui.div(dl_msg_v(), style="font-size:12px;color:#55607a")

    @render.ui
    def downloads_table():
        tick()
        rows = []
        for r in inventory.chunk_downloads(ROOT):
            cells = [ui.tags.td(r["label"]), ui.tags.td(r["status"], style=f"color:{_status_color(r['status'])};font-weight:600"),
                     ui.tags.td(f"{r['huc8s']}" if r["huc8s"] else ""),
                     ui.tags.td(f"{r['reaches']:,}" if r["reaches"] else "")]
            for stage, _names in inventory.CHUNK_SOURCES:
                src = r["sources"].get(stage)
                if not src:
                    cells.append(ui.tags.td(""))
                    continue
                cells.append(ui.tags.td(f"{src['status']} {_fmt_bytes(src['size']) if src['size'] else ''}".strip(),
                                        style=f"color:{_status_color(src['status'])}"))
            for stage in pipeline.HUC8_STAGES:
                st = r["huc8_stages"].get(stage)
                cells.append(ui.tags.td(f"{st['done']}/{st['total']}" if st else "",
                                        style=f"color:{_status_color(st['status']) if st else '#55607a'}"))
            cells.append(ui.tags.td(f"{r['published_huc4s']}/{r['huc4s']}" if r["huc4s"] else ""))
            cells.append(ui.tags.td(_fmt_bytes(r["raw_size"]) if r["raw_size"] else ""))
            rows.append(ui.tags.tr(*cells))
        head = ui.tags.tr(*[ui.tags.th(h) for h in ("Chunk", "Status", "HUC8s", "Reaches")],
                          *[ui.tags.th(stage) for stage, _n in inventory.CHUNK_SOURCES],
                          *[ui.tags.th(stage) for stage in pipeline.HUC8_STAGES],
                          ui.tags.th("HUC4s published"), ui.tags.th("Raw size"))
        return ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*rows), class_="table table-sm", style="font-size:11px")

    @render.ui
    def disk_summary():
        tick()
        d = inventory.disk_status(ROOT)
        parts = ", ".join(f"{k} {_fmt_bytes(v)}" for k, v in d["folders"].items())
        return ui.div(f"free {_fmt_bytes(d['free'])} of {_fmt_bytes(d['total'])}; {parts}",
                      style="font-size:12px;color:#55607a;margin-top:8px")

    # ------------------------------------------------------- cross-sections
    @render.ui
    def catalog_card():
        tick()
        c = inventory.catalog_status(ROOT)
        one, nine = c["1m"], c["19"]
        return ui.div(
            ui.div(ui.tags.b("1 m lidar tiles: "), (f"{one['tiles']:,} tiles in {one['projects']} projects, built "
                                                       f"{str(one['built_at'])[:10]}" if one["present"] else "not built"),
                   style="font-size:12px"),
            ui.div(ui.tags.b("1/9 arc-second quads: "), (f"{nine['tiles']:,} quads, built {str(nine['built_at'])[:10]}"
                                                           if nine["present"] else "not built"), style="font-size:12px"),
            ui.div("10 m seamless: read through the USGS VRT, no catalog needed", style="font-size:12px;color:#55607a"),
            ui.div(ui.input_action_button("fetch_catalogs", "Rebuild catalogs", class_="btn-outline-secondary btn-sm"),
                   style="margin-top:6px"))

    @reactive.effect
    @reactive.event(input.fetch_catalogs)
    def _fetch_catalogs():
        Queue(ROOT).append([{"job": "national", "steps": ["dem1m_index", "dem19_index"]}])
        xs_msg_v.set("queued the catalog rebuild")
        _bump()

    @render.ui
    def bandwidth_card():
        tick()
        b = inventory.bandwidth_status(ROOT)
        pct = int(100 * b["fraction"])
        return ui.div(
            ui.div(f"{b['month']}: {_fmt_bytes(b['bytes'])} of the {config.XS_BYTE_BUDGET_GB:g} GB budget "
                   f"({b['reaches']:,} reaches sampled)", style="font-size:12px"),
            ui.div(ui.div(style=f"width:{pct}%;height:8px;background:{'#b42318' if pct >= 90 else '#1f6feb'};border-radius:4px"),
                   style="background:#e5e9f0;border-radius:4px;margin:6px 0"),
            ui.div("estimated from the tile blocks each reach window touches; the sampling pauses at the budget "
                   "(EASI_NATIONAL_XS_BYTE_BUDGET_GB)", style="font-size:11px;color:#55607a"))

    @reactive.effect
    @reactive.event(input.xs_queue)
    def _xs_queue():
        abbr = (input.xs_state() or "").strip().upper()
        try:
            chunk = make_chunk(ROOT, "state", abbr)
        except Exception as exc:  # noqa: BLE001
            xs_msg_v.set(f"could not build the chunk: {exc}")
            return
        stages = ["derive", "xs_sample", "xs_derive"]
        if input.xs_with_score():
            stages += ["joins", "score"]
        jobs = [{"job": "chunk", "kind": "state", "value": abbr, "stages": stages,
                 "keep_dem_windows": bool(input.xs_keep_windows())}]
        if input.xs_with_publish():
            jobs += [{"job": "tiles", "vpu": vpu} for vpu in chunk.vpus]
            jobs += [{"job": "stage"}, {"job": "publish"}]
        Queue(ROOT).append(jobs)
        xs_msg_v.set(f"queued cross-section sampling for {chunk.label}: {len(chunk.huc8s)} HUC8s, "
                     f"{chunk.n_comids:,} reaches")
        _bump()

    @render.ui
    def xs_msg():
        return ui.div(xs_msg_v(), style="font-size:12px;color:#55607a")

    @render.ui
    def xs_progress():
        tick()
        p = read_progress(ROOT)
        if p.get("stage") != "xs_sample":
            return None
        done, total = 0, 0
        n_1m = n_3m = n_10m = 0
        for folder in ROOT.huc8.iterdir() if ROOT.huc8.exists() else []:
            path = folder / "xs_sample.progress.json"
            if path.exists():
                try:
                    j = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                done += int(j.get("done") or 0)
                total += int(j.get("total") or 0)
                n_1m += int(j.get("n_1m") or 0)
                n_3m += int(j.get("n_3m") or 0)
                n_10m += int(j.get("n_10m") or 0)
        pct = int(100 * done / total) if total else 0
        return ui.div(ui.div(ui.div(style=f"width:{pct}%;height:8px;background:#1f6feb;border-radius:4px"),
                             style="background:#e5e9f0;border-radius:4px;margin:6px 0"),
                      ui.div(f"sampling now: {done:,} of {total:,} reaches in the HUC8s in flight; "
                             f"1 m {n_1m:,}, 3 m {n_3m:,}, 10 m {n_10m:,}", style="font-size:12px;color:#55607a"))

    @render.ui
    def xs_table():
        tick()
        rows = []
        for r in inventory.xs_inventory(ROOT):
            rows.append(ui.tags.tr(
                ui.tags.td(r["label"]), ui.tags.td(r["status"], style=f"color:{_status_color(r['status'])};font-weight:600"),
                ui.tags.td(f"{r['sampled']}/{r['huc8s']}" if r["huc8s"] else ""),
                ui.tags.td(f"{r['derived']}/{r['huc8s']}" if r["huc8s"] else ""),
                ui.tags.td(f"{r['reaches_sampled']:,}" + (f" of {r['reaches']:,}" if r["reaches"] else "")
                           if r["reaches_sampled"] or r["reaches"] else ""),
                ui.tags.td(f"{r['n_1m']:,} / {r['n_3m']:,} / {r['n_10m']:,}" if r["reaches_sampled"] else ""),
                ui.tags.td(_fmt_bytes(r["archive_bytes"]) if r["archive_bytes"] else ""),
                ui.tags.td(_fmt_bytes(r["bytes_est"]) if r["bytes_est"] else ""),
                ui.tags.td(r.get("last_activity") or "")))
        head = ui.tags.tr(*[ui.tags.th(h) for h in ("State", "Status", "HUC8s sampled", "HUC8s derived", "Reaches",
                                                   "1 m / 3 m / 10 m", "Archive", "Downloaded (est.)", "Last activity")])
        return ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*rows), class_="table table-sm", style="font-size:12px")

    # ----------------------------------------------------------------- QA
    @reactive.effect
    @reactive.event(input.qa_run)
    def _qa():
        from builder import qa
        huc8 = (input.qa_huc8() or "").strip()
        if not huc8:
            qa_msg.set("enter a HUC8")
            return
        qa_msg.set("running (live EASI calls, a few seconds per reach) ...")
        try:
            report = qa.parity(ROOT, huc8, n=int(input.qa_n() or 10), xs_mode=str(input.qa_xs() or "off"))
        except Exception as exc:  # noqa: BLE001
            qa_msg.set(f"failed: {exc}")
            return
        qa_msg.set(qa.format_report(report))

    @render.ui
    def qa_result():
        return ui.tags.pre(qa_msg(), style="font-size:11px;white-space:pre-wrap")


app = App(app_ui, server)
