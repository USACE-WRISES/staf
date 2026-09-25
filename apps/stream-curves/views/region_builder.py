"""Region builder: an ecoregion project's Build step (stage 3).

The batch runner (``scripts/run_region_batch.py stage``) already does the work: six
stages unattended, the standing-decision policy applied to the review queue, a staged
publish and a review packet. This module is the surface for it inside the workflow:
the region comes from stage 1, the build runs in a subprocess, and when it finishes the
staged assessment opens itself on Reference curves > Select final curves, the one place
every decision the build left open is answered (REF-15 curve decisions, CURVE-07
answers, the other queue items, documented gaps and SELECT-01 approvals).

It shells out rather than calling the agent in-process, for two reasons. The run takes
about half an hour, which must not sit on the event loop; and ``cmd_stage`` owns the
fixpoint loop, the refusal gates, the staged publish and the packet, so a second
implementation here could produce a different assessment from the same inputs. The
run folder on disk is the state, so a refresh mid-build recovers by reading it.

``stage`` never reaches the canonical library: it writes into ``<out>/library`` and
refuses the canonical root. Publishing is a separate button, behind a confirmation and
``library.publish_gate_reason``, and it shells out to the same script's ``promote`` so
the build's own provenance record is the one that lands rather than the thin
interactive one views/publish.py writes.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import pandas as pd
from shiny import module, reactive, render, ui

from streamcurves import curve_basis
from streamcurves import library as lib
from streamcurves import methodology
from streamcurves import engine_names
from streamcurves import nrsa_dataset, region_build as rb
from streamcurves import owner_curves as oc
from streamcurves import pressure_evidence as pe
from streamcurves import run_state as rs
from streamcurves import rules_view
from streamcurves import session_io as sio
from streamcurves import regional_agent as ra
from views import state as st
from views.state import AppState
from views.theme import bi
from views.uihelpers import (
    _rules_goto_onclick,
    count_text,
    guard,
    not_ready_panel,
    rule_chip,
)

#: Working folder for runs. notes/ is gitignored, which is where the pilots' runs
#: live, so a build leaves nothing in the tracked tree until it is promoted.
DEFAULT_OUT_ROOT = rb.default_runs_root()

#: The Reference curves section a finished build lands on
#: (views.final_selection.SECTION; test_region_builder pins the two agree).
FINAL_SECTION = "final"

#: (session path, mtime) -> what the run panel reads of that staged session, so
#: it does not re-read a megabyte of JSON on every repaint
_SUMMARY_CACHE: dict = {}


def _staged_build(session_path) -> Optional[dict]:
    """``{build, built, decisions}`` of a staged session: its reference build, the
    metrics it fitted and the curve decisions it carries, read from its file."""
    if session_path is None:
        return None
    path = Path(session_path)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key not in _SUMMARY_CACHE:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        fields = (doc.get("fields") if isinstance(doc.get("fields"), dict) else doc) or {}
        _SUMMARY_CACHE.clear()
        _SUMMARY_CACHE[key] = {"build": fields.get("reference_build"),
                               "built": set(fields.get("completed_metrics") or {}),
                               "decisions": list(fields.get("owner_curve_decisions") or [])}
    return _SUMMARY_CACHE[key]


def _staged_reference_summary(session_path) -> dict:
    """What the staged session scores without having fitted it
    (``pressure_evidence.reference_summary``), under the curve decisions the
    staged version carries (REF-15)."""
    got = _staged_build(session_path)
    if not got or not got["build"]:
        return {}
    return pe.reference_summary(oc.effective_build(got["build"], got["decisions"],
                                                   built=got["built"]), built=got["built"])

_TASK_KEY = "region_build"


def _register_file(run_dir) -> Optional[Path]:
    """The region's saved candidate register (curves a person added for comparison and
    the reasons recorded in Select final curves), when one exists; a build reads it
    through ``--candidate-register`` so the staged register is complete."""
    path = Path(run_dir) / rb.CANDIDATE_REGISTER_FILE
    return path if path.exists() else None


def _maintainer(state=None) -> str:
    """Who to record, derived rather than asked for: the initials views/publish.py records
    (``views.state.recorded_by``; ``n/a`` when none are set, never the login)."""
    return st.recorded_by(state)


def _sites_for(dataset_id: str) -> pd.DataFrame:
    """The candidate site table for a dataset, empty when it is not built here."""
    try:
        if dataset_id == nrsa_dataset.LEGACY_DATASET_ID:
            return pd.read_csv(ra._DATA_DIR / "nrsa_sites.csv", dtype={"us_l3code": str})
        ds = nrsa_dataset.load_dataset(dataset_id)
        return ds.sites if hasattr(ds, "sites") else pd.DataFrame()
    except Exception:  # noqa: BLE001 - a missing archive is a UI state, not a crash
        return pd.DataFrame()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def left_for_you_text(packet: Optional[dict], n_gaps: int = 0) -> str:
    """One sentence on what the build left for the owner, all of it answered in
    Select final curves (the one decision authority)."""
    items = list((packet or {}).get("open_items") or [])
    n = len(items) + int(n_gaps or 0)
    if not n:
        return ("Every queue item received a standing decision and every STAF function "
                "is covered. Nothing is left for you to answer.")
    blocking = sum(1 for i in items if i.get("blocking"))
    head = count_text(n, "item") + " left for you"
    if blocking:
        head += f" ({count_text(blocking, 'blocking item')})"
    return head + ". Answer them in Reference curves, Select final curves."


# --------------------------------------------------------------------------- #
# the campaign index: every region a runs root holds, with filters
# --------------------------------------------------------------------------- #
#: the filters, in the order the card shows them: (input name, label)
INDEX_FILTERS = (("ci_region", "Region"), ("ci_function", "Function"), ("ci_metric", "Metric"),
                 ("ci_source", "Source type"), ("ci_evidence", "Evidence"), ("ci_open", "Open items"))
EVIDENCE_CHOICES = {"": "Any", "ready": "Ready", "missing": "Missing", "unreadable": "Unreadable"}
OPEN_CHOICES = {"": "Any", "none": "None open", "some": "Items open"}
#: the source types a packet's curves rest on (its curve basis), in words
SOURCE_TYPE_LABELS = {"fitted": "Built here", "carried": "Carried forward", "fixed": "Fixed criteria",
                      **{t: curve_basis.label_for(t) for t in (*curve_basis.ORDER, curve_basis.OWNER)}}


def packet_index_detail(packet: Optional[dict]) -> dict:
    """What the campaign index filters on, read from a region's review packet: the
    functions its portfolio covers (``{id: name}``), the metrics its curves and sources
    name, the source types its curves rest on (the packet's curve basis: built here,
    carried, a basis of the ladder, fixed criteria) and ``promotion`` (``{eligible,
    blockers}``) when the packet records it."""
    p = packet if isinstance(packet, dict) else {}
    functions: dict[str, str] = {}
    for row in p.get("portfolio") or []:
        fid = str(row.get("function_id") or "")
        if fid:
            functions[fid] = str(row.get("function") or fid)
    for fid in (p.get("coverage") or {}).get("coveredFunctionIds") or []:
        functions.setdefault(str(fid), str(fid))
    metrics: set[str] = {str(c.get("metric")) for c in p.get("curves") or [] if c.get("metric")}
    ref = p.get("reference") or {}
    h = ref.get("hierarchy") or {}
    metrics |= {str(s.get("metric")) for s in h.get("sources") or [] if s.get("metric")}
    metrics |= {str(m) for m in h.get("carried") or []}
    metrics |= {str(f.get("metric")) for f in ref.get("fixed") or [] if f.get("metric")}
    for row in p.get("portfolio") or []:
        metrics |= {str(m) for m in row.get("compact_metrics") or []}
    sources: set[str] = set()
    if p.get("curves"):
        sources.add("fitted")
    for s in h.get("sources") or []:
        sources.add(str(s.get("basis") or curve_basis.REGIONAL))
    if h.get("carried") or h.get("carried_curves"):
        sources.add("carried")
    if ref.get("fixed"):
        sources.add("fixed")
    promotion = p.get("promotion")
    return {"functions": functions, "metrics": sorted(metrics), "source_types": sorted(sources),
            "promotion": dict(promotion) if isinstance(promotion, dict) else None}


def with_packet_detail(rows: list[dict]) -> list[dict]:
    """``region_build.campaign_rows`` rows with what each region's packet adds for the
    filters and the promote column (:func:`packet_index_detail`)."""
    out = []
    for r in rows:
        packet = _read_json(Path(r["run_dir"]) / "review_packet.json") if r.get("run_dir") else None
        out.append({**r, **packet_index_detail(packet)})
    return out


def index_rows(out_root) -> list[dict]:
    """The campaign index over a runs root (:func:`with_packet_detail` over
    ``region_build.campaign_rows``)."""
    return with_packet_detail(rb.campaign_rows(out_root))


def index_choices(rows: list[dict]) -> dict:
    """The filters' options over the rows: regions (code and name), functions (id and
    name), metrics and source types (token and words)."""
    regions = {str(r["region"]): f'{r["region"]}  {r.get("name") or ""}'.strip() for r in rows}
    functions: dict[str, str] = {}
    metrics: set[str] = set()
    sources: set[str] = set()
    for r in rows:
        for fid, name in (r.get("functions") or {}).items():
            functions.setdefault(fid, name)
        metrics |= set(r.get("metrics") or [])
        sources |= set(r.get("source_types") or [])
    return {"region": dict(sorted(regions.items(), key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else 0, kv[0]))),
            "function": dict(sorted(functions.items(), key=lambda kv: kv[1])),
            "metric": {m: m for m in sorted(metrics)},
            "source_type": {t: SOURCE_TYPE_LABELS.get(t, t) for t in sorted(sources)}}


def filter_index_rows(rows: list[dict], *, region: str = "", function: str = "", metric: str = "",
                      source_type: str = "", evidence: str = "", open_items: str = "") -> list[dict]:
    """The rows a filter setting keeps. ``region``: a code, or text matched against the
    code and the name; ``open_items``: ``none`` or ``some``; the others are exact."""
    q = str(region or "").strip().lower()
    out = []
    for r in rows:
        if q and q != str(r.get("region")).lower() and q not in f'{r.get("region")} {r.get("name") or ""}'.lower():
            continue
        if function and str(function) not in (r.get("functions") or {}):
            continue
        if metric and str(metric) not in (r.get("metrics") or []):
            continue
        if source_type and str(source_type) not in (r.get("source_types") or []):
            continue
        if evidence and str(r.get("evidence") or "") != str(evidence):
            continue
        n_open = r.get("open_items")
        if open_items == "none" and (n_open is None or int(n_open or 0) > 0):
            continue
        if open_items == "some" and not int(n_open or 0):
            continue
        out.append(r)
    return out


def promote_words(row: dict) -> str:
    """The promote column: the packet's own eligibility (``review_packet.promotion``) with
    its blockers when it records one, else what the index derives."""
    promotion = row.get("promotion")
    if isinstance(promotion, dict):
        if promotion.get("eligible"):
            return "eligible"
        blockers = [str(b) for b in promotion.get("blockers") or [] if str(b).strip()]
        return "not yet: " + "; ".join(blockers) if blockers else "not yet"
    return "eligible" if row.get("promote_eligible") else "not yet"


def campaign_filters_ui(choices: dict, *, ns, values: Optional[dict] = None):
    """The six filters over the index (region, function, metric, source type, evidence
    status, open items), each keeping its current value across a repaint."""
    values = values or {}
    opts = {"ci_region": {"": "Any region", **choices.get("region", {})},
            "ci_function": {"": "Any function", **choices.get("function", {})},
            "ci_metric": {"": "Any metric", **choices.get("metric", {})},
            "ci_source": {"": "Any source type", **choices.get("source_type", {})},
            "ci_evidence": dict(EVIDENCE_CHOICES), "ci_open": dict(OPEN_CHOICES)}
    controls = []
    for name, label in INDEX_FILTERS:
        cur = str(values.get(name) or "")
        controls.append(ui.input_select(ns(name), label, opts[name],
                                        selected=cur if cur in opts[name] else "", width="100%"))
    return ui.div(*controls, class_="rb-campaign-filters")


def campaign_table_ui(rows: list[dict], *, ns, total: int):
    """The index table: one row per region, Open on the rows that have a run."""
    if not rows:
        return ui.div(f"No region matches these filters ({total} in this folder).",
                      class_="text-muted small")
    head = ["Region", "Version", "Curves", "Decisions applied", "Open items",
            "Hard stops", "Promote", "Evidence", ""]
    body = []
    for r in rows:
        label = f'{r["region"]}  {r.get("name") or ""}'.strip()
        body.append(ui.tags.tr(
            ui.tags.td(label),
            ui.tags.td("" if r.get("version") is None else f'v{r["version"]}'),
            ui.tags.td("" if r.get("curves") is None else str(r["curves"])),
            ui.tags.td("" if r.get("decisions_applied") is None else str(r["decisions_applied"])),
            ui.tags.td("" if r.get("open_items") is None else str(r["open_items"])),
            ui.tags.td("" if r.get("hard_stops") is None else str(r["hard_stops"])),
            ui.tags.td(promote_words(r)),
            ui.tags.td(str(r.get("evidence") or "")),
            ui.tags.td(ui.tags.button(
                "Open", type="button", class_="btn btn-link btn-sm p-0",
                title="Open this region's staged assessment on Select final curves",
                onclick=(f"Shiny.setInputValue('{ns('open_region')}',"
                         f"{json.dumps(str(r['region']))},{{priority:'event'}})"))
                if r.get("run_dir") else "")))
    return ui.TagList(
        ui.div(f"{len(rows)} of {total} regions", class_="text-muted small mb-1"),
        ui.tags.table(ui.tags.thead(ui.tags.tr(*[ui.tags.th(h) for h in head])),
                      ui.tags.tbody(*body), class_="table table-sm rb-facts"))


@module.ui
def region_builder_ui():
    return ui.output_ui("builder_page")


@module.server
def region_builder_server(input, output, session, state: AppState, active=None, region=None):
    """``active``: a reactive callable, True while the page shows. ``region``: a
    reactive callable returning ``(code, name)`` of the ecoregion chosen in stage 1;
    given, the form carries no region select and builds that region."""
    ns = session.ns

    out_root = reactive.value(DEFAULT_OUT_ROOT)
    run_dir = reactive.value(None)          # Path of the run being shown
    log_text = reactive.value("")
    running = reactive.value(False)
    finished = reactive.value(None)          # exit code of the last run
    _tasks: set = set()

    def _inp(name: str):
        """An input's value, or None while the control is not on the page (a
        control rendered only under some setting reads None, never raises)."""
        try:
            return input[name]()
        except Exception:  # noqa: BLE001 - absent control
            return None

    def _region_code() -> str:
        """The ecoregion to build: stage 1's, else the page's own select."""
        if region is not None:
            code, _name = region()
            return str(code or "").strip()
        return str(_inp("build_region") or "").strip()

    def _region_name() -> Optional[str]:
        if region is not None:
            _code, name = region()
            return str(name) if name else None
        return None

    def _launch(coro):
        task = asyncio.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return task

    def _set_running(value: bool) -> None:
        """Publish on the channel the workflow strip reads, so it refuses to
        navigate mid-build the way it does for a curve recompute."""
        with reactive.isolate():
            tasks = dict(state.tasks_running() or {})
        if value:
            tasks[_TASK_KEY] = True
        else:
            tasks.pop(_TASK_KEY, None)
        state.tasks_running.set(tasks)
        running.set(bool(value))

    # ── the run ──────────────────────────────────────────────────────────────
    async def run_stage(argv: list[str], out_dir: Path, *, log_name: str = "stage.log"):
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / log_name
        run_dir.set(out_dir)
        log_text.set("")
        finished.set(None)
        _set_running(True)
        await st.task_flush()
        code = -1
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(rb.repo_root()),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            buf: list[str] = []
            with open(log_path, "w", encoding="utf-8") as fh:
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace")
                    fh.write(line)
                    fh.flush()
                    buf.append(line)
                    # The runner narrates itself with [batch] lines; painting only on
                    # those keeps a 35-minute build from flushing thousands of times.
                    if line.startswith("[batch]"):
                        log_text.set("".join(buf))
                        await st.task_flush()
            code = await proc.wait()
            log_text.set("".join(buf))
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not start the build: {exc}",
                                 type="error", duration=10)
        finally:
            # All of it inside finally: a detached task that raises on the way out
            # leaves the strip blocked forever with nothing on screen to explain it.
            finished.set(code)
            _set_running(False)
        await st.task_flush()
        # One build path: a finished build opens its own assessment and lands on
        # Select final curves, where what it left open is answered. A promote, a
        # failed run or a run that wrote nothing to open stays on this page.
        if log_name == "stage.log" and code == 0:
            with reactive.isolate():
                opened = _open_staged_now(land=True)
            if opened:
                await st.task_flush()

    @reactive.effect
    @reactive.event(input.build_run)
    @guard("start the build")
    def _build():
        code = _region_code()
        if not code:
            ui.notification_show("Choose an ecoregion first.", type="warning", duration=4)
            return
        with reactive.isolate():
            if running():
                ui.notification_show("A build is already running.", type="message",
                                     duration=4)
                return
            dataset = _inp("build_dataset") or nrsa_dataset.default_build_dataset_id()
            rows = {r["code"]: r for r in rb.region_choices(_sites_for(dataset))}
        row = rows.get(code) or {}
        name = (row.get("name") or _region_name() or ra.region_name_for(code)
                or f"Ecoregion {code}")
        out_dir = rb.run_folder(out_root(), code)
        decisions = out_dir / rb.OWNER_DECISIONS_FILE
        gaps = out_dir / rb.COVERAGE_EXCEPTIONS_FILE
        method = _inp("build_reference_method") or None
        argv = rb.stage_command(
            code, name, out_dir,
            maintainer=_maintainer(state),
            n_boot=int(_inp("build_nboot") or 1000),
            # The Rules page owns the opt-in selection; validate so a stale id
            # can never reach --enable-policy (the script would refuse the run).
            enable_policies=rules_view.validate_selections(
                state.rule_selections())[0],
            # Always explicit, so every recorded argv says which data it read.
            dataset_id=dataset,
            # The predictor-source control renders only under the legacy method
            # (the pressure screen computes no predictors), so it reads None,
            # the StreamCat default, whenever it is off the page.
            predictor_source=((_inp("build_predictor_source") or "streamcat")
                              if method == rs.REFERENCE_METHOD_EASI else "streamcat"),
            # The reference frame is recorded like the dataset: an explicit flag,
            # so the run's own argv says which stations it could draw from.
            reference_frame=(_inp("build_reference_frame")
                             or rb.REFERENCE_FRAME_DEFAULT),
            # How reference condition is defined (methodology 0.12), explicit in
            # the recorded argv like the frame and the dataset.
            reference_method=method,
            # the owner's answers and documented gaps, recorded in Select final
            # curves into the region's own files
            reviewer_decisions=decisions if decisions.exists() else None,
            coverage_exceptions=gaps if gaps.exists() else None,
            # the owner's standing curve decisions (REF-15), seeded from the
            # published version when the region has recorded none here
            curve_decisions=rb.curve_decisions_path(out_dir, code),
            # curves a person added for comparison and the reasons recorded in
            # Select final curves, so the staged register is complete
            candidate_register=_register_file(out_dir))
        _launch(run_stage(argv, out_dir))

    @reactive.effect
    def _poll():
        """Repaint the log tail while the build runs (the _screen_poll idiom)."""
        if not running():
            return
        reactive.invalidate_later(1.0)
        d = run_dir()
        if d is not None:
            log_text.set(_read(Path(d) / "stage.log"))

    @reactive.effect
    @reactive.event(input.restage_ref02)
    @guard("build again with REF-02")
    def _restage_ref02():
        """Re-stage the shown run with exactly one more flag. The dataset and
        resamples come from the run's own manifest, so the record differs from
        the refused run only by the enabled entry."""
        with reactive.isolate():
            if running():
                ui.notification_show("A build is already running.", type="message",
                                     duration=4)
                return
            packet = _packet() or {}
        run_folder = _active_dir()
        if run_folder is None:
            ui.notification_show("No run to build again.", type="warning", duration=4)
            return
        manifest = _read_json(Path(run_folder) / "run_manifest.json")
        if manifest is None:
            doc = _provenance()
            manifest = (doc or {}).get("manifest") if isinstance(doc, dict) else None
        kw = rb.restage_args(packet, manifest)
        if not kw["l3_code"]:
            ui.notification_show("The packet names no region.", type="warning",
                                 duration=5)
            return
        # Reflect the enable in the app-wide selection, so the Rules page and
        # the builder's summary line agree with what this run will record.
        with reactive.isolate():
            current = list(state.rule_selections() or [])
        if rb.REF02_POLICY_ID not in current:
            state.rule_selections.set(current + [rb.REF02_POLICY_ID])
        out_dir = Path(run_folder)
        decisions = out_dir / rb.OWNER_DECISIONS_FILE
        gaps = out_dir / rb.COVERAGE_EXCEPTIONS_FILE
        argv = rb.stage_command(
            kw["l3_code"], kw["name"], out_dir,
            maintainer=_maintainer(state),
            n_boot=kw["n_boot"],
            enable_policies=kw["enable_policies"],
            dataset_id=kw["dataset_id"],
            # recovered from the run's manifest by restage_args; without it a
            # re-stage of an engine build silently reverted to StreamCat, and a
            # re-stage of an all-streams run came back framed
            predictor_source=kw.get("predictor_source"),
            reference_frame=kw.get("reference_frame"),
            reference_method=kw.get("reference_method"),
            reviewer_decisions=decisions if decisions.exists() else None,
            coverage_exceptions=gaps if gaps.exists() else None,
            curve_decisions=rb.curve_decisions_path(out_dir, kw["l3_code"]),
            candidate_register=_register_file(out_dir))
        _launch(run_stage(argv, out_dir))

    # ── the run this page shows ─────────────────────────────────────────────
    def _active_dir():
        """The run this page is showing.

        The one that just ran, else the region's folder if it already holds a
        packet. A build is a folder on disk, so returning to a region later reads
        it back rather than asking for another half hour.
        """
        d = run_dir()
        if d is not None:
            return Path(d)
        code = _region_code()
        if not code:
            return None
        cand = rb.run_folder(out_root(), code)
        return cand if (cand / "review_packet.json").is_file() else None

    def _packet():
        d = _active_dir()
        return _read_json(d / "review_packet.json") if d else None

    def _provenance():
        d = _active_dir()
        if not d:
            return None
        doc = _read_json(d / "decision_provenance_log.json")
        if doc is None:
            return None
        # The log points at its manifest by name (``manifestRef``), which resolves
        # inside the run folder and nowhere else. A published version has to carry
        # the manifest itself, or its inputsDigest cannot be re-derived from it and
        # the region, configs and inputs of the build are simply absent from the
        # record (tests/test_screening_engine_pin.py checks exactly this). Promote
        # inlines it the same way.
        ref = doc.get("manifestRef")
        if isinstance(ref, str) and ref and "manifest" not in doc:
            manifest = _read_json(d / ref)
            if manifest is not None:
                doc["manifest"] = manifest
        return doc

    def _session_path():
        """The assessment to open: the staged version if there is one, else the run
        folder's own copy. Every build writes the second, so a gate refusing to stage
        never costs you the ability to look at what was built."""
        packet = _packet() or {}
        staged = (packet.get("staged") or {}).get("path")
        if staged:
            p = Path(staged) / "session.streamcurves.json"
            if p.is_file():
                return p
        d = _active_dir()
        p = d / "assessment.streamcurves.json" if d else None
        return p if (p and p.is_file()) else None

    def _land_on_final_selection() -> None:
        """Ask the shell for Reference curves and its Select final curves section.

        The section is written two ways on purpose: the request channel switches a
        page already on screen, and the mirror value seeds the section a page not
        yet rendered opens on (its navset reads it at render). The summary page's
        mirror effect then takes over again from the navset's own value."""
        with reactive.isolate():
            nonce = state.nav_request_nonce() or 0
            snonce = state.workspace_section_nonce() or 0
        state.curves_section.set(FINAL_SECTION)
        state.nav_request.set("curves")
        state.nav_request_nonce.set(nonce + 1)
        state.workspace_section_request.set(FINAL_SECTION)
        state.workspace_section_nonce.set(snonce + 1)

    def _open_staged_now(*, land: bool = False) -> bool:
        """Open the shown run's assessment in the app (the staged version, else the
        run folder's copy). ``land``: continue to Select final curves. Returns
        whether an open was requested."""
        packet = _packet() or {}
        staged = packet.get("staged") or {}
        path = _session_path()
        if path is None:
            ui.notification_show("This run wrote no assessment file to open.",
                                 type="warning", duration=5)
            return False
        # load_session_payload, not a raw json.loads: it validates the schema and
        # migrates a v1 file forward, which is what every other restore path gets.
        try:
            payload = sio.load_session_payload(path)
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not read the assessment: {exc}",
                                 type="error", duration=8)
            return False
        # Origin seed: the staged version's own provenance when one was staged,
        # else the run folder's decision log when it is a full document. What
        # lets a later in-app publish carry this build's record instead of
        # dropping it. Best effort; a missing file never blocks the open.
        run_folder = _active_dir()
        approvals = None
        if staged.get("path"):
            prov = _read_json(Path(staged["path"]) / lib.PROVENANCE_FILE)
            digest = (_read_json(Path(staged["path"]) / lib.BUNDLE_FILE)
                      or {}).get("contentDigest")
            approvals = (_read_json(Path(staged["path"]) / lib.META_FILE)
                         or {}).get("portfolioApprovals")
            seed_kind = "staged"
        else:
            doc = _provenance()
            prov = doc if isinstance(doc, dict) and doc.get("records") is not None else None
            digest = None
            seed_kind = "run"
        # The same channel the library picker uses; data_overview owns the restore.
        with reactive.isolate():
            nonce = state.session_restore_nonce() or 0
        state.session_restore_request.set(
            {"payload": payload,
             "source_name": (f"{(packet.get('region') or {}).get('name') or 'assessment'}"
                             + (f" v{staged.get('version')} (staged)" if staged
                                else " (built, not staged)")),
             "origin_seed": {
                 "kind": seed_kind,
                 "staged_path": staged.get("path"),
                 "run_dir": str(run_folder) if run_folder else None,
                 "content_digest": digest,
                 "provenance": prov,
                 "portfolio_approvals": approvals,
             }})
        state.session_restore_nonce.set(nonce + 1)
        if land:
            _land_on_final_selection()
        return True

    @reactive.effect
    @reactive.event(input.open_staged)
    @guard("open the assessment")
    def _open_staged():
        _open_staged_now(land=True)

    @reactive.effect
    @reactive.event(input.open_region)
    @guard("open the region's run")
    def _open_region():
        """A row of the campaign index: show that region's run and open its staged
        assessment on Select final curves."""
        code = str(input.open_region() or "").strip()
        if not code:
            return
        folder = rb.run_folder(out_root(), code)
        if not (folder / "review_packet.json").is_file():
            ui.notification_show("That region has no run to open here.", type="warning",
                                 duration=5)
            return
        run_dir.set(folder)
        finished.set(None)
        _open_staged_now(land=True)

    # ── publish ──────────────────────────────────────────────────────────────
    def _decisions_moved() -> Optional[str]:
        """Why the staged run cannot be published as it stands: the region's curve
        decisions changed after it was staged (REF-15), so it would publish without
        the new ones, or with one the owner undid. Promote refuses it too."""
        folder = _active_dir()
        staged = _staged_build(_session_path())
        if folder is None or not staged:
            return None
        if oc.decisions_changed(staged["decisions"], oc.load(folder)):
            return ("Your curve decisions changed after this run was staged. Build it again "
                    "so the version applies them.")
        return None

    def _publish_block():
        """The Publish control, or the sentence that explains why there is none.

        Only a staged run can be promoted: cmd_promote reads the staged version
        directory, so a run a gate refused has nothing to confirm. Publishing goes
        through the same script rather than the app's own publish, because that one
        writes an interactive provenance and would drop the run's record.
        """
        packet = _packet() or {}
        if not (packet.get("staged") or {}).get("path"):
            return ui.div(
                "Not staged, so there is nothing to publish yet. Answer what is left "
                "in Select final curves and build this region again.",
                class_="text-muted small mt-3")
        blocked = lib.publish_gate_reason() or _decisions_moved()
        return ui.div(
            ui.input_action_button(
                ns("publish_run"), ui.TagList(bi("file-earmark-arrow-up"),
                                              " Publish to the library"),
                class_="btn btn-success btn-sm",
                disabled="disabled" if blocked else None),
            (ui.div(blocked, class_="text-muted small mt-1") if blocked else
             ui.div("Confirms this run's decisions under your name and publishes it "
                    "with its own provenance as a Draft. Open it from the library "
                    "to review the curves, then approve it as Preliminary.",
                    class_="text-muted small mt-1")),
            class_="mt-3")

    @reactive.effect
    @reactive.event(input.publish_run)
    @guard("publish this run")
    def _publish_run():
        packet = _packet() or {}
        if not (packet.get("staged") or {}).get("path"):
            return
        blocked = lib.publish_gate_reason() or _decisions_moved()
        if blocked:
            ui.notification_show(blocked, type="warning", duration=10)
            return
        region_name = (packet.get("region") or {}).get("name") or "this region"
        ui.modal_show(ui.modal(
            ui.p(f"Publish {region_name} into the shared assessment library?"),
            ui.p("This confirms every standing decision under your name and writes a "
                 "new Draft version. Review it in the app and approve it as "
                 "Preliminary when it is ready. It cannot be undone from here.",
                 class_="text-muted small"),
            title="Publish to the library",
            footer=ui.TagList(
                ui.modal_button("Cancel"),
                ui.input_action_button(ns("publish_confirm"), "Publish",
                                       class_="btn btn-success")),
            easy_close=True))

    @reactive.effect
    @reactive.event(input.publish_confirm)
    @guard("publish this run")
    def _publish_confirm():
        ui.modal_remove()
        out_dir = _active_dir()
        if out_dir is None:
            return
        _launch(run_stage(rb.promote_command(out_dir, maintainer=_maintainer(state)),
                          Path(out_dir), log_name="promote.log"))

    # ── page ─────────────────────────────────────────────────────────────────
    @render.ui
    def builder_page():
        if active is not None and not active():
            return None
        dataset = _inp("build_dataset") or nrsa_dataset.default_build_dataset_id()
        choices = rb.region_choices(_sites_for(dataset))
        if not choices:
            return not_ready_panel(
                "No NRSA site table",
                "The bundled NRSA data is not present in this checkout, so no "
                "ecoregion can be built here.",
                icon="database")
        if region is not None and not _region_code():
            return not_ready_panel(
                "No ecoregion chosen yet",
                "Choose the Level III ecoregion in Region & data first; the build "
                "runs for that region.",
                action_label="Go to Region", goto_nav="data", goto_step=1,
                icon="database")
        return ui.div(
            ui.h2("Build", class_="sc-page-title"),
            ui.p("Runs the whole workflow for this ecoregion in one pass: the fixed "
                 "pressure screen, the reference pools, every curve, the standing "
                 "decisions. When it finishes the assessment opens on Select final "
                 "curves, where what the build left open is answered. Publishing "
                 "stays a separate step you confirm.",
                 class_="text-muted small"),
            _form(choices, dataset),
            ui.output_ui(ns("run_state")),
            ui.output_ui(ns("packet_view")),
            ui.output_ui(ns("campaign_index")),
            class_="rb-page",
        )

    def _region_control(choices):
        """Stage 1's region as a fixed line, or the page's own select."""
        if region is not None:
            code = _region_code()
            row = next((r for r in choices if r["code"] == code), None)
            name = (row or {}).get("name") or _region_name() or ra.region_name_for(code) or ""
            n = (row or {}).get("n_candidates")
            noun = "candidate" if n == 1 else "candidates"
            return ui.div(
                ui.tags.label("Ecoregion", class_="form-label mb-0"),
                ui.div(ui.tags.strong(f"{name} (L3 {code})"),
                       (ui.tags.span(f"  {n} {noun}, {row['label']}", class_="text-muted small")
                        if row else ui.tags.span("  not in the NRSA site table",
                                                 class_="text-muted small")),
                       class_="small"),
                title="Chosen in Region & data. Counts are candidate stations before "
                      "the reference screen, not the pool the curves are built from.")
        opts = {}
        for r in choices:
            n = r["n_candidates"]
            noun = "candidate" if n == 1 else "candidates"
            opts[r["code"]] = f'{r["code"]}  {r["name"]}  ({n} {noun}, {r["label"]})'
        return ui.div(
            ui.input_select(ns("build_region"), "Ecoregion", opts, width="100%"),
            title="Counts are candidate stations before the reference screen, not "
                  "the pool the curves are built from. Interior Plateau went 25 to "
                  "23; Eastern Corn Belt Plains went 18 to zero least-disturbed "
                  "sites, which triggered its best-available fallback.")

    def _form(choices, dataset):
        # The new-build default lists first, so the select opens on it.
        datasets = sorted(nrsa_dataset.available_datasets(),
                          key=lambda d: d != nrsa_dataset.default_build_dataset_id())
        # Detail rides in tooltips (title=); the form itself stays two lines.
        return ui.div(
            ui.row(
                ui.column(7, _region_control(choices)),
                ui.column(3, ui.div(
                    ui.input_select(
                        ns("build_dataset"), "NRSA data",
                        {d: rb.DATASET_LABELS.get(d, d) for d in datasets},
                        selected=dataset, width="100%"),
                    title=rb.DATASET_NOTE)),
                ui.column(2, ui.div(
                    ui.input_numeric(ns("build_nboot"), "Bootstrap resamples",
                                     value=1000, min=100, max=2000, step=100),
                    title=rb.RESAMPLES_NOTE)),
                ui.column(3, ui.div(
                    ui.input_select(
                        ns("build_reference_frame"), "Reference frame",
                        {"wadeable": "Wadeable, stream order 1 to 5 (default)",
                         "all": "Every stream, including large rivers"},
                        selected=rb.REFERENCE_FRAME_DEFAULT, width="100%"),
                    title="Which stations may enter the reference population. "
                          "Wadeable reads the NHDPlus V2 stream order of each "
                          "station's reach (rule DATA-10); the NRSA sampling "
                          "protocol decides only where an order cannot be "
                          "resolved. Every stream is what the versions published "
                          "before methodology 0.10 drew from.")),
                ui.column(3, ui.div(
                    ui.input_select(
                        ns("build_reference_method"), "Reference method",
                        {"pressure-screen": "Pressure screen (default)",
                         "easi-eci": "EASI condition index (legacy)"},
                        selected="pressure-screen", width="100%"),
                    title="How reference condition is defined. The pressure screen "
                          "reads least-disturbed stations from the committed station "
                          "table, borrows comparable stations from the Level II and "
                          "then the Level I ecoregion where the region has too few, "
                          "withholds a metric no pool supports, and scores landscape "
                          "pressures on fixed criteria. The EASI condition index is "
                          "the method the versions published before methodology 0.12 "
                          "used, and it needs the legacy NRSA data.")),
                # the predictor source, only under the legacy method (its own
                # output, so the select appears and disappears with the method)
                ui.column(2, ui.output_ui(ns("predictor_source_control"))),
            ),
            ui.p(rb.RESAMPLES_HINT, class_="text-muted small mb-2"),
            ui.output_ui(ns("frame_summary")),
            ui.output_ui(ns("policy_summary")),
            ui.input_action_button(
                ns("build_run"), ui.TagList(bi("magic"), " Build this region"),
                class_="btn btn-primary"),
            ui.tags.span(" About 10 minutes with the pressure screen, around 35 with the "
                         "legacy method. You can leave this page. The build keeps running.",
                         class_="text-muted small ms-2"),
            class_="rb-form card card-body mb-3",
        )

    @render.ui
    def predictor_source_control():
        """The predictor-source select, rendered only when the build runs the
        legacy EASI condition screen: under the pressure screen the curve
        predictors are not recomputed, so the control would do nothing."""
        if (_inp("build_reference_method") or "pressure-screen") != rs.REFERENCE_METHOD_EASI:
            return None
        return ui.div(
            ui.input_select(
                ns("build_predictor_source"), "Predictor source",
                {"streamcat": f"{engine_names.STREAMCAT} (default)",
                 "site-engine": f"{engine_names.SITE_ENGINE} (HR reach watershed)"},
                selected="streamcat", width="100%"),
            title="Which engine computes the curve predictors. The "
                  f"{engine_names.SITE_ENGINE} recomputes them at the "
                  f"training sites ({engine_names.SITE_ENGINE_COST}) and "
                  "stamps the bundle predictorSource for the DEEP pairing "
                  "rule.")

    @render.ui
    def frame_summary():
        """What the reference frame will do to the chosen region, before the run.

        The rule decides the reference population before any screening, so the
        count of stations it keeps out belongs on the page, not only in the run
        log (2026-09-07).
        """
        code = _region_code()
        if not code:
            return None
        frame = _inp("build_reference_frame") or rb.REFERENCE_FRAME_DEFAULT
        max_order = (None if frame == "all"
                     else methodology.threshold("reference_panel.max_stream_order"))
        dataset = _inp("build_dataset") or nrsa_dataset.default_build_dataset_id()
        counts = rb.frame_counts(_sites_for(dataset), code,
                                 max_stream_order=max_order)
        text = rb.frame_summary_text(counts, frame, max_order)
        by_order = counts.get("by_order") or {}
        detail = ", ".join(f"order {k}: {v}" for k, v in sorted(by_order.items()))
        return ui.div(
            ui.tags.span(text, class_="small"),
            (ui.tags.span(f" ({detail})", class_="text-muted small")
             if detail else None),
            (ui.tags.span(" Build with every stream to include them, or readmit one "
                          "station with --include-site in the batch command.",
                          class_="text-muted small")
             if counts.get("n_out_of_frame") else None),
            class_="mb-2",
        )

    @render.ui
    def policy_summary():
        """What the Rules page has enabled for this run, read-only here so the
        selection has exactly one writer."""
        labels = {pid: label for pid, label, _ in rb.OPTIONAL_POLICIES}
        enabled = [labels.get(p, p) for p in (state.rule_selections() or [])]
        return ui.div(
            ui.tags.span("Standing decisions enabled for this run: ",
                         class_="text-muted small"),
            ui.tags.span("; ".join(enabled) if enabled else "none",
                         class_="small"),
            ui.tags.a("Change in Rules", href="javascript:void(0)",
                      class_="small ms-2",
                      onclick=_rules_goto_onclick("REF-02")),
            class_="mb-2",
        )

    @render.ui
    def run_state():
        code = finished()
        if running():
            line = rb.phase_from_log(log_text())
            return ui.div(
                ui.tags.strong("Building. "), ui.tags.span(line),
                ui.tags.pre(log_text()[-4000:], class_="rb-log"),
                class_="alert alert-info py-2",
            )
        if code is None:
            return None
        headline, severity, detail = rb.outcome(code, _packet(), log_text())
        return ui.div(
            ui.tags.strong(headline),
            (ui.div(detail, class_="small mt-1") if detail else None),
            ui.tags.pre(log_text()[-4000:], class_="rb-log"),
            class_=f"alert alert-{severity} py-2")

    @render.ui
    def packet_view():
        # Depend on the run's end: _packet() reads a file, and a file appearing is
        # not a reactive event. Without this the packet never rendered, because
        # run_dir() was already set when the build started.
        finished()
        running()
        # ...on the owner's curve decisions, saved on the workspace pages...
        state.owner_curve_decisions()
        # ...and on the region, so a region change shows that region's run.
        if region is not None:
            region()
        else:
            _inp("build_region")
        packet = _packet()
        if not packet:
            return None
        region_block = packet.get("region") or {}
        screening = packet.get("screening") or {}
        cov = packet.get("coverage") or {}
        staged = packet.get("staged") or {}
        facts = [
            ("Reference tier", ui.TagList(
                str(packet.get("reference_tier") or ""),
                (ui.tags.span(rule_chip("REF-02", label="REF-02 fallback"),
                              class_="ms-1")
                 if packet.get("ref02_triggered") else None))),
            ("Screened", f'{screening.get("n_candidates")} candidates, '
                         f'{screening.get("n_retained")} retained '
                         f'({screening.get("pool_disposition") or "?"})'),
            ("Curves", _curves_fact(packet)),
            ("Functions", f'{cov.get("covered")} of {cov.get("total")}'),
            ("Staged",
             f'v{staged.get("version")} at {staged.get("path")}' if staged
             # A refused publish leaves this null; "v None at None" reads as a bug
             # rather than as the gate doing its job.
             else "nothing yet, a gate refused this run"),
        ]
        return ui.div(
            ui.h5(f'{region_block.get("name") or "Region"} (L3 {region_block.get("code")})'),
            ui.tags.table(
                ui.tags.tbody(*[
                    ui.tags.tr(ui.tags.td(k, class_="text-muted pe-3"), ui.tags.td(v))
                    for k, v in facts]),
                class_="table table-sm rb-facts"),
            *( [ui.div(ui.tags.strong("Review flags. "),
                       " ".join(packet.get("review_flags") or []),
                       class_="alert alert-warning py-2")]
               if packet.get("review_flags") else []),
            ui.input_action_button(
                ns("open_staged"), ui.TagList(bi("folder2-open"),
                                              " Open this assessment in StreamCurves"),
                class_="btn btn-outline-primary btn-sm mb-3"),
            _decisions_line(),
            _left_for_you(packet),
            _publish_block(),
            class_="rb-packet card card-body",
        )

    def _curves_fact(packet) -> str:
        """The curves the staged version scores: those this run fitted, and the
        ones it carries or takes from elsewhere (read from the staged session, so
        a run staged before this count existed shows it too)."""
        built = len(packet.get("curves") or [])
        extra = pe.reference_summary_text(_staged_reference_summary(_session_path()))
        return f"{built} built here" + (f", {extra}" if extra else "")

    def _decisions_line():
        """How many standing curve decisions (REF-15) the region holds, read-only:
        they are made and undone in Select final curves."""
        folder = _active_dir()
        items = oc.load(folder) if folder else []
        if not items:
            return None
        return ui.div(
            ui.tags.strong("Curve decisions for this region: "),
            count_text(len(items), "decision"),
            ", applied by every build of this region. Made and undone in Reference "
            "curves, Select final curves.",
            class_="text-muted small mb-2")

    def _left_for_you(packet):
        """What the build left open, as a count and a jump: the items themselves
        are answered in Select final curves. The one-flag re-stage for the
        commonest blocker stays here, because it is a build, not a decision."""
        n_gaps = len(rb.coverage_gaps(packet))
        text = left_for_you_text(packet, n_gaps)
        items = packet.get("open_items") or []
        if not items and not n_gaps:
            return ui.div(text, class_="alert alert-success py-2")
        ref02 = rb.blocking_ref02_item(packet)
        return ui.div(
            ui.div(text, class_="mb-2"),
            ui.tags.button(
                bi("ui-checks"), " Go to Select final curves", type="button",
                class_="btn btn-primary btn-sm",
                onclick=(f"Shiny.setInputValue('{ns('go_final')}',"
                         f"{{t:Date.now()}},{{priority:'event'}})")),
            # The one-flag fix for the commonest blocker: 3 of 4 regions
            # built so far had zero Functioning sites.
            (ui.div(
                ui.tags.code(ref02.get("item_id") or ""),
                ui.tags.span(rule_chip("REF-02"), class_="ms-2"),
                ui.tags.span("BLOCKING", class_="badge bg-danger ms-2"),
                ui.div(str(ref02.get("question") or ""), class_="mb-1"),
                ui.input_action_button(
                    ns("restage_ref02"),
                    ui.TagList(bi("magic"), " Enable REF-02 and build again"),
                    class_="btn btn-outline-primary btn-sm"),
                ui.tags.span(
                    " Reuses this run's cached screening and landscape data. "
                    "The resample diagnostics run again, which is most of the "
                    "remaining time.",
                    class_="text-muted small ms-2"),
                class_="rb-item border rounded p-2 mt-2") if ref02 else None),
            class_="alert alert-warning py-2")

    @reactive.effect
    @reactive.event(input.go_final)
    @guard("go to Select final curves")
    def _go_final():
        # Select final curves shows the open assessment: when this run's is not the
        # one open, open it first (which lands there), else just go there
        with reactive.isolate():
            origin = state.assessment_source() or {}
        d = _active_dir()
        if d is not None and str(origin.get("run_dir") or "") == str(d):
            _land_on_final_selection()
        else:
            _open_staged_now(land=True)

    def _filter_values() -> dict:
        return {name: str(_inp(name) or "") for name, _ in INDEX_FILTERS}

    @render.ui
    def campaign_index():
        """Every region this runs root holds, read-only (``rb.campaign_rows`` plus what
        each packet adds): the filters (region, function, metric, source type, evidence
        status, open items) and the table, on its own output so a filter change never
        repaints the controls. A row opens that region's staged assessment on Select
        final curves; the promote column reads the packet's own eligibility."""
        finished()
        running()
        rows = with_packet_detail(rb.campaign_rows(out_root()))
        if not rows:
            return None
        with reactive.isolate():
            values = _filter_values()
        return ui.div(
            ui.tags.strong("Regions built in this folder"),
            ui.div(str(out_root()), class_="text-muted small mb-1"),
            campaign_filters_ui(index_choices(rows), ns=ns, values=values),
            ui.output_ui(ns("campaign_table")),
            class_="rb-campaign card card-body mt-3")

    @render.ui
    def campaign_table():
        finished()
        running()
        values = _filter_values()
        rows = with_packet_detail(rb.campaign_rows(out_root()))
        kept = filter_index_rows(rows, region=values["ci_region"], function=values["ci_function"],
                                 metric=values["ci_metric"], source_type=values["ci_source"],
                                 evidence=values["ci_evidence"], open_items=values["ci_open"])
        return campaign_table_ui(kept, ns=ns, total=len(rows))
