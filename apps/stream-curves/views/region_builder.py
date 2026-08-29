"""Region builder — run a whole ecoregion build from the app.

The batch runner (``scripts/run_region_batch.py stage``) already does the work: six
stages unattended, the standing-decision policy applied to the review queue, a staged
publish and a review packet. It was CLI-only, so this page is the surface for it:
pick a region, watch the build, read the packet, answer what the policy left open,
and open the staged assessment in StreamCurves to review it whole.

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
import os
from pathlib import Path

import pandas as pd
from shiny import module, reactive, render, ui

from streamcurves import library as lib
from streamcurves import nrsa_dataset, region_build as rb
from streamcurves import rules_view
from streamcurves import session_io as sio
from streamcurves import regional_agent as ra
from views import state as st
from views.state import AppState
from views.theme import bi
from views.uihelpers import (
    _rules_goto_onclick,
    guard,
    linkify_rule_ids,
    not_ready_panel,
    rule_chip,
)

#: Working folder for runs. notes/ is gitignored, which is where the pilots' runs
#: live, so a build leaves nothing in the tracked tree until it is promoted.
DEFAULT_OUT_ROOT = rb.repo_root() / "notes" / "DEEP_Working" / "analysis" / "runs"

_TASK_KEY = "region_build"


def _maintainer() -> str:
    """Who to record, derived rather than asked for. Same chain views/publish.py uses."""
    return (os.environ.get("STAF_LIBRARY_MAINTAINER")
            or os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()


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


@module.ui
def region_builder_ui():
    return ui.output_ui("builder_page")


@module.server
def region_builder_server(input, output, session, state: AppState, active=None):
    ns = session.ns

    out_root = reactive.value(DEFAULT_OUT_ROOT)
    run_dir = reactive.value(None)          # Path of the run being shown
    log_text = reactive.value("")
    running = reactive.value(False)
    finished = reactive.value(None)          # exit code of the last run
    _tasks: set = set()

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

    @reactive.effect
    @reactive.event(input.build_run)
    @guard("start the build")
    def _build():
        code = (input.build_region() or "").strip()
        if not code:
            ui.notification_show("Choose an ecoregion first.", type="warning", duration=4)
            return
        with reactive.isolate():
            if running():
                ui.notification_show("A build is already running.", type="message",
                                     duration=4)
                return
            dataset = input.build_dataset() or nrsa_dataset.default_build_dataset_id()
            rows = {r["code"]: r for r in rb.region_choices(_sites_for(dataset))}
        row = rows.get(code) or {}
        name = row.get("name") or ra.region_name_for(code) or f"Ecoregion {code}"
        out_dir = rb.run_folder(out_root(), code)
        decisions = out_dir / "owner_decisions.json"
        gaps = out_dir / "coverage_exceptions.json"
        argv = rb.stage_command(
            code, name, out_dir,
            maintainer=_maintainer() or "unknown",
            n_boot=int(input.build_nboot() or 1000),
            # The Rules page owns the opt-in selection; validate so a stale id
            # can never reach --enable-policy (the script would refuse the run).
            enable_policies=rules_view.validate_selections(
                state.rule_selections())[0],
            # Always explicit, so every recorded argv says which data it read.
            dataset_id=dataset,
            predictor_source=(input.build_predictor_source() or "streamcat"),
            reviewer_decisions=decisions if decisions.exists() else None,
            coverage_exceptions=gaps if gaps.exists() else None)
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
        decisions = out_dir / "owner_decisions.json"
        gaps = out_dir / "coverage_exceptions.json"
        argv = rb.stage_command(
            kw["l3_code"], kw["name"], out_dir,
            maintainer=_maintainer() or "unknown",
            n_boot=kw["n_boot"],
            enable_policies=kw["enable_policies"],
            dataset_id=kw["dataset_id"],
            reviewer_decisions=decisions if decisions.exists() else None,
            coverage_exceptions=gaps if gaps.exists() else None)
        _launch(run_stage(argv, out_dir))

    # ── answering an open item ───────────────────────────────────────────────
    def _active_dir():
        """The run this page is showing.

        The one that just ran, else the selected region's folder if it already holds
        a packet. A build is a folder on disk, so returning to a region later reads
        it back rather than asking for another half hour.
        """
        d = run_dir()
        if d is not None:
            return Path(d)
        try:
            code = (input.build_region() or "").strip()
        except Exception:  # noqa: BLE001 - before the select exists
            return None
        if not code:
            return None
        cand = rb.run_folder(out_root(), code)
        return cand if (cand / "review_packet.json").is_file() else None

    def _packet():
        d = _active_dir()
        return _read_json(d / "review_packet.json") if d else None

    def _provenance():
        d = _active_dir()
        return _read_json(d / "decision_provenance_log.json") if d else None

    @reactive.effect
    @reactive.event(input.save_decisions)
    @guard("save the decisions")
    def _save_decisions():
        packet, doc = _packet(), _provenance()
        if not packet or doc is None:
            ui.notification_show("No run to answer.", type="warning", duration=4)
            return
        records = doc if isinstance(doc, dict) else {"records": doc}
        decisions, problems = [], []
        for i, item in enumerate(packet.get("open_items") or []):
            action = input[f"act_{i}"]() if f"act_{i}" in input else None
            note = input[f"why_{i}"]() if f"why_{i}" in input else ""
            if not action:
                continue
            d = rb.build_decision(records, item, action, note,
                                  reviewer=_maintainer() or "owner")
            found = rb.decision_problems(records, d)
            if found:
                problems.append(f"{item.get('item_id')}: " + "; ".join(found))
            else:
                decisions.append(d)
        if problems:
            ui.notification_show("Not saved. " + " | ".join(problems),
                                 type="error", duration=14)
            return
        # Coverage gaps ride in the same form: they are answered as a build input,
        # so the next build stages cleanly instead of being patched afterwards.
        gaps, gap_problems = [], []
        for j, gap in enumerate(rb.coverage_gaps(packet)):
            reason = input[f"gap_r_{j}"]() if f"gap_r_{j}" in input else None
            why = input[f"gap_w_{j}"]() if f"gap_w_{j}" in input else ""
            if not reason:
                continue
            exc = rb.build_coverage_exception(gap["function_id"], reason, why,
                                              recorded_by=_maintainer() or "owner")
            found = rb.coverage_problems([exc])
            if found:
                gap_problems.append(f'{gap["item_id"]}: ' + "; ".join(found))
            else:
                gaps.append(exc)
        if gap_problems:
            ui.notification_show("Not saved. " + " | ".join(gap_problems),
                                 type="error", duration=14)
            return
        if not decisions and not gaps:
            ui.notification_show("Answer at least one item first.", type="warning",
                                 duration=4)
            return
        out = _active_dir()
        saved = []
        if decisions:
            (out / "owner_decisions.json").write_text(
                json.dumps(decisions, indent=1) + "\n", encoding="utf-8")
            saved.append(f"{len(decisions)} decision(s)")
        if gaps:
            (out / "coverage_exceptions.json").write_text(
                json.dumps(gaps, indent=1) + "\n", encoding="utf-8")
            saved.append(f"{len(gaps)} coverage exception(s)")
        ui.notification_show(
            "Saved " + " and ".join(saved) + ". Build this region again to fold them in.",
            type="message", duration=8)

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

    @reactive.effect
    @reactive.event(input.open_staged)
    @guard("open the assessment")
    def _open_staged():
        packet = _packet() or {}
        staged = packet.get("staged") or {}
        path = _session_path()
        if path is None:
            ui.notification_show("This run wrote no assessment file to open.",
                                 type="warning", duration=5)
            return
        # load_session_payload, not a raw json.loads: it validates the schema and
        # migrates a v1 file forward, which is what every other restore path gets.
        try:
            payload = sio.load_session_payload(path)
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not read the assessment: {exc}",
                                 type="error", duration=8)
            return
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

    # ── publish ──────────────────────────────────────────────────────────────
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
                "above and build this region again.",
                class_="text-muted small mt-3")
        blocked = lib.publish_gate_reason(_maintainer())
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
        blocked = lib.publish_gate_reason(_maintainer())
        if blocked:
            ui.notification_show(blocked, type="warning", duration=10)
            return
        region = (packet.get("region") or {}).get("name") or "this region"
        ui.modal_show(ui.modal(
            ui.p(f"Publish {region} into the shared assessment library?"),
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
        _launch(run_stage(rb.promote_command(out_dir, maintainer=_maintainer()),
                          Path(out_dir), log_name="promote.log"))

    # ── page ─────────────────────────────────────────────────────────────────
    @render.ui
    def builder_page():
        if active is not None and not active():
            return None
        dataset = input.build_dataset() if "build_dataset" in input else None
        dataset = dataset or nrsa_dataset.default_build_dataset_id()
        choices = rb.region_choices(_sites_for(dataset))
        if not choices:
            return not_ready_panel(
                "No NRSA site table",
                "The bundled NRSA data is not present in this checkout, so no "
                "ecoregion can be built here.",
                icon="database")
        return ui.div(
            ui.h4("Region builder", class_="mb-1"),
            ui.p("Run the whole workflow for one Level III ecoregion, then review "
                 "what it decided. Publishing stays a separate step you confirm.",
                 class_="text-muted small"),
            _form(choices, dataset),
            ui.output_ui(ns("run_state")),
            ui.output_ui(ns("packet_view")),
            class_="rb-page",
        )

    def _form(choices, dataset):
        opts = {}
        for r in choices:
            n = r["n_candidates"]
            noun = "candidate" if n == 1 else "candidates"
            opts[r["code"]] = f'{r["code"]}  {r["name"]}  ({n} {noun}, {r["label"]})'
        # The new-build default lists first, so the select opens on it.
        datasets = sorted(nrsa_dataset.available_datasets(),
                          key=lambda d: d != nrsa_dataset.default_build_dataset_id())
        # Detail rides in tooltips (title=); the form itself stays two lines.
        return ui.div(
            ui.row(
                ui.column(7, ui.div(
                    ui.input_select(ns("build_region"), "Ecoregion", opts,
                                    width="100%"),
                    title="Counts are candidate stations before the reference "
                          "screen, not the pool the curves are built from. "
                          "Interior Plateau went 25 to 23; Eastern Corn Belt "
                          "Plains went 18 to zero least-disturbed sites, which "
                          "triggered its best-available fallback.")),
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
                ui.column(2, ui.div(
                    ui.input_select(
                        ns("build_predictor_source"), "Predictor source",
                        {"streamcat": "StreamCat (default)",
                         "site-engine": "Site engine (exact watershed)"},
                        selected="streamcat", width="100%"),
                    title="Which source computes the curve predictors. The "
                          "site engine recomputes them at the training sites "
                          "(about a minute per uncached site) and stamps the "
                          "bundle predictorSource for the DEEP pairing rule.")),
            ),
            ui.p(rb.RESAMPLES_HINT, class_="text-muted small mb-2"),
            ui.output_ui(ns("policy_summary")),
            ui.input_action_button(
                ns("build_run"), ui.TagList(bi("magic"), " Build this region"),
                class_="btn btn-primary"),
            ui.tags.span(" Around 35 minutes. You can leave this page; the build keeps "
                         "running.", class_="text-muted small ms-2"),
            class_="rb-form card card-body mb-3",
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
        # ...and on the selection, so switching region shows that region's run.
        try:
            input.build_region()
        except Exception:  # noqa: BLE001
            pass
        packet = _packet()
        if not packet:
            return None
        region = packet.get("region") or {}
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
            ("Curves", str(len(packet.get("curves") or []))),
            ("Functions", f'{cov.get("covered")} of {cov.get("total")}'),
            ("Staged",
             f'v{staged.get("version")} at {staged.get("path")}' if staged
             # A refused publish leaves this null; "v None at None" reads as a bug
             # rather than as the gate doing its job.
             else "nothing yet, a gate refused this run"),
        ]
        return ui.div(
            ui.h5(f'{region.get("name") or "Region"} (L3 {region.get("code")})'),
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
            _open_items(packet),
            _publish_block(),
            class_="rb-packet card card-body",
        )

    def _gap_cards(packet):
        """One card per undocumented STAF function.

        This is what refused the Driftless Area run. It is answered here rather than
        in the app because stage takes the exceptions as an input, which is what lets
        the next build stage cleanly and publish with its own provenance.
        """
        cards = []
        reasons = rb.coverage_reasons()
        for j, gap in enumerate(rb.coverage_gaps(packet)):
            cands = gap.get("candidates") or []
            cards.append(ui.div(
                ui.div(
                    ui.tags.code(gap["item_id"]),
                    ui.tags.span("BLOCKS PUBLISH", class_="badge bg-danger ms-2"),
                    class_="mb-1"),
                ui.p(gap["question"], class_="mb-1"),
                (ui.p("Metrics that could inform it: " + ", ".join(cands),
                      class_="text-muted small mb-1") if cands else None),
                ui.input_select(ns(f"gap_r_{j}"), "Reason it is out of scope",
                                {"": "(unanswered)",
                                 **{r: r.replace("-", " ") for r in reasons}}),
                ui.input_text_area(
                    ns(f"gap_w_{j}"), "Justification (required, at least 20 characters)",
                    rows=3, width="100%",
                    placeholder="Why this assessment carries no metric for it."),
                class_="rb-item border rounded p-2 mb-2"))
        return cards

    def _open_items(packet):
        items = packet.get("open_items") or []
        gaps = _gap_cards(packet)
        if not items and not gaps:
            return ui.div("Every queue item received a standing decision and every STAF "
                          "function is covered. Nothing is left for you to answer.",
                          class_="alert alert-success py-2")
        cards = list(gaps)
        ref02 = rb.blocking_ref02_item(packet)
        for i, item in enumerate(items):
            ev = item.get("evidence") or {}
            is_ref02 = (ref02 is not None
                        and item.get("item_id") == ref02.get("item_id"))
            cards.append(ui.div(
                ui.div(
                    ui.tags.code(item.get("item_id") or ""),
                    (ui.tags.span(rule_chip(item["rule_id"]), class_="ms-2")
                     if item.get("rule_id") else None),
                    (ui.tags.span("BLOCKING", class_="badge bg-danger ms-2")
                     if item.get("blocking") else None),
                    class_="mb-1"),
                ui.p(linkify_rule_ids(item.get("question") or ""), class_="mb-1"),
                # The one-flag fix for the commonest blocker: 3 of 4 regions
                # built so far had zero Functioning sites.
                (ui.div(
                    ui.input_action_button(
                        ns("restage_ref02"),
                        ui.TagList(bi("magic"), " Enable REF-02 and build again"),
                        class_="btn btn-primary btn-sm"),
                    ui.tags.span(
                        " Reuses this run's cached screening and landscape data. "
                        "The resample diagnostics run again, which is most of the "
                        "remaining time.",
                        class_="text-muted small ms-2"),
                    class_="mb-2") if is_ref02 else None),
                ui.tags.details(
                    ui.tags.summary("Evidence", class_="text-muted small"),
                    ui.tags.pre(json.dumps(ev, indent=1, default=str),
                                class_="rb-log")),
                ui.input_select(ns(f"act_{i}"), "Decision",
                                {"": "(unanswered)",
                                 **{a: a.replace("_", " ") for a in rb.REVIEWER_ACTIONS}}),
                ui.input_text_area(ns(f"why_{i}"), "Rationale (required)", rows=3,
                                   width="100%"),
                class_="rb-item border rounded p-2 mb-2"))
        return ui.div(
            ui.h6(f"Items left for you ({len(items) + len(gaps)})"),
            *cards,
            ui.input_action_button(ns("save_decisions"),
                                   ui.TagList(bi("ui-checks"), " Save decisions"),
                                   class_="btn btn-primary btn-sm"),
        )
