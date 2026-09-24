"""Publish page: publish the open project into the STAF assessment library, or, in any copy
that does not publish, save a copy for the maintainer who does.

Who publishes: the maintainer, from a STAF checkout with STAF_LIBRARY_PUBLISH=1
(streamcurves.workspace "maintainer" mode). There the page is the publish form: the
assessment defaults to the one the project started from, the version is published as a
Draft (kept out of DEEP) or Preliminary (a version DEEP runs once DEEP carries it), and
the Validate stage moves it on (Approve as Preliminary, then Certify as Final). Anywhere
else, including every installed copy, the page says the maintainer publishes and offers
the project file to send them.

Exports for anyone: the workbook, the DEEP calculator preview and the DEEP bundle (to try
in DEEP by upload). Saving the project itself is Save / Save As in the header.

See apps/library/README.md for the on-disk format.
"""

from __future__ import annotations

import copy
import io
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from shiny import module, reactive, render, req, ui

from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import owner_curves as oc
from streamcurves import provenance as pv
from streamcurves import region_build as rb
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from streamcurves import workspace as ws
from streamcurves.workbook import write_input_workbook
from views import assessment_publish as ap
from views import final_selection as fs
from views.data_overview import _default_session_name, _sanitize_file_stem
from views.state import AppState
from views.theme import bi, fa
from views.uihelpers import _goto_onclick, guard, not_ready_panel, rule_chip

logger = logging.getLogger("streamcurves")

_NEW = "__new__"


def _status_choices() -> dict:
    """The status a new version is published with. Draft is the default: a maintainer
    publishing a reviewer's revision reviews it before DEEP runs it."""
    return {
        "draft": ui.TagList(
            ui.tags.span("Draft", class_="pub-seg-title"),
            ui.tags.span("For review; DEEP does not run it", class_="pub-seg-caption"),
        ),
        "preliminary": ui.TagList(
            ui.tags.span("Preliminary", class_="pub-seg-title"),
            ui.tags.span("DEEP runs it once DEEP carries it", class_="pub-seg-caption"),
        ),
    }


def _maintainer_name(state=None) -> str:
    """Who to record as the publisher, derived rather than asked for: the initials every
    StreamCurves page records (``views.state.recorded_by``: STAF_LIBRARY_MAINTAINER, else the
    open project's Prepared by, else ``n/a``; never the login)."""
    from views import state as _st
    return _st.recorded_by(state)


def _publish_block_reason() -> str | None:
    """The actionable gate reason from library.publish_gate_reason, or None.

    The library's own copy names the fix (STAF_LIBRARY_PUBLISH=1 in a verified
    repository checkout), which is what a blocked publisher actually needs to read.
    A missing name never blocks: the publish records ``n/a``. The not-writable
    branch never reaches this note: _publish_pane replaces the whole form for
    that case.
    """
    return lib.publish_gate_reason()


def _portfolio_approval_text(pending: list[dict]) -> str:
    """The sentence over the SELECT-01 checkbox, naming the functions and their counts."""
    named = ", ".join(f"{p['functionName']} ({p['nMetrics']} metrics)" for p in pending)
    verb = "carries" if len(pending) == 1 else "carry"
    return (f"{named} {verb} more than the default maximum of two metrics. Rule SELECT-01 "
            "publishes such a function only with a recorded human approval, and this "
            "publish writes yours into the version's metadata.")


def _waiting_note(state: AppState):
    """The sources the build refused that the owner accepted and no build has
    computed yet (REF-15): they apply nothing to this version, and the version's
    record marks them as waiting (``owner_curves.summary``)."""
    from streamcurves import metric_names
    with reactive.isolate():
        waiting = oc.pending(state.owner_curve_decisions() or [])
    if not waiting:
        return None
    n = len(waiting)
    names = "; ".join(f"{metric_names.display_name_for(mk, None) or mk}: "
                      f"{(d.get('source') or {}).get('title')}"
                      for mk, d in sorted(waiting.items()))
    return ui.div(
        f"{n} curve decision{'' if n == 1 else 's'} wait{'s' if n == 1 else ''} for a build "
        f"and do{'es' if n == 1 else ''} not apply to this version: {names}. Build the region "
        "again in the Region builder to compute "
        f"{'it' if n == 1 else 'them'}.",
        class_="text-muted small mb-2 pub-waiting")


def _origin_steer(state: AppState, origin: dict | None, has_doc: bool, built_by):
    """One line saying what this publish records, or the promote steer when the
    staged content is untouched (promote keeps the build's record verbatim)."""
    if (origin or {}).get("kind") == "staged" and (origin or {}).get("content_digest"):
        try:
            unchanged = (lib.content_digest(ap.build_bundle_from_state(state))
                         == origin["content_digest"])
        except Exception:  # noqa: BLE001 - no finalized curves yet
            unchanged = False
        if unchanged:
            return ui.div(
                "Content unchanged from the staged build. Publish it from the "
                "Region builder to confirm and publish with the build's own record.",
                ui.tags.button("Open Region builder",
                               class_="btn btn-outline-primary btn-sm ms-2",
                               onclick=_goto_onclick("build", None), type="button"),
                class_="alert alert-info py-2 small")
    if has_doc:
        return ui.div(
            "Publishing carries the originating run's provenance and records your "
            "edits.", class_="text-muted small mb-2")
    if built_by == "regional-agent":
        return ui.div(
            ui.tags.strong("This assessment came from a region build. "),
            "Publishing here records an interactive provenance without the build's "
            "own record. To keep it, publish from the Region builder.",
            class_="alert alert-warning py-2 small")
    return None


@module.ui
def publish_ui():
    return ui.div(ui.output_ui("publish_body"), class_="mt-3")


@module.server
def publish_server(input, output, session, state: AppState):
    refresh = reactive.value(0)

    def _assessments() -> list[dict]:
        refresh()
        try:
            # the DEEP publish page publishes DEEP assessments only (an EASI method has its own)
            return [a for a in lib.list_assessments() if lib.entry_type(a) == "deep"]
        except Exception:  # noqa: BLE001
            logger.exception("publish: reading catalog failed")
            return []

    # ── readiness checklist (shown for staged guided runs only) ───────────────
    @render.ui
    def publish_checklist():
        # Readiness list for the gate enforced in _publish; shown for guided
        # runs only (same rule as the gate). run_snapshot() isolates its own
        # reads, so declare the dependencies here.
        state.region_of_applicability()
        state.run_meta()
        state.easi_screening_sites()
        state.run_stage_status()
        state.data()
        state.curve_review()
        # The mapping item's inputs (run_snapshot isolates its own reads).
        state.discipline_function_mapping()
        state.discipline_function_mapping_confirmed()
        state.function_coverage_exceptions()
        state.owner_curve_decisions()
        state.metric_config()
        # Stratifier diagnostics: they drive the enrichment_build attention state.
        state.strat_config()
        state.all_layer1_results()
        state.phase2_ranking()
        state.summary_available_overrides()
        # The Validate stage's status inputs (run_snapshot isolates its reads).
        state.validation_records()
        state.assessment_source()
        snap = ap.run_snapshot(state)
        if not snap.get("curve_review"):
            return None
        items = rs.readiness_checklist(snap)
        # Only the failing items say anything. Printing all seven meant six green
        # ticks of noise above the form on every render.
        outstanding = [i for i in items if not i["ok"]]
        if not outstanding:
            return ui.div(
                ui.tags.span("✓ ", class_="fw-bold"),
                "Ready to publish.",
                class_="publish-checklist border rounded p-2 mb-3 small text-success",
            )
        n = len(outstanding)
        return ui.div(
            ui.tags.h6(
                f"{n} item{'' if n == 1 else 's'} left before publishing", class_="mb-1"
            ),
            ui.tags.ul(
                *[ui.tags.li(
                    i["label"],
                    (ui.tags.span(rule_chip(i["rule"]), class_="ms-1")
                     if i.get("rule") else None),
                ) for i in outstanding],
                class_="small mb-0",
            ),
            class_="publish-checklist border rounded p-2 mb-3",
        )

    def _exports_card():
        """What anyone can take away: the workbook, the calculator preview and the DEEP
        bundle. The project itself is Save / Save As in the header."""
        def item(output_id, label, icon, note):
            return ui.div(
                ui.download_button(output_id, ui.TagList(fa(icon), f" {label}"),
                                   class_="btn btn-outline-primary w-100"),
                ui.tags.small(note, class_="text-muted d-block mt-1"),
                class_="col-md-4")
        return ui.card(
            ui.card_header(ui.TagList(bi("file-earmark-arrow-up"), " Exports")),
            ui.card_body(ui.div(
                item("download_workbook", "Workbook (.xlsx)", "file-excel",
                     "Data and setup sheets for Excel. Reopening rebuilds the analysis."),
                item("download_calculator", "Calculator preview (.xlsx)", "calculator",
                     "The DEEP Excel calculator for these curves, as they stand."),
                item("download_deep_bundle", "DEEP bundle (.deep.json)", "file-arrow-down",
                     "Load it in DEEP (Detailed assessment, upload) to try the curves."),
                class_="row g-3")),
            class_="mb-3 publish-card")

    def _share_pane():
        """Every copy that does not publish: the maintainer does, from the project file."""
        with reactive.isolate():
            path = state.project_file()
        where = (ui.div(ui.tags.strong("Your project file: "), ui.tags.code(str(path)),
                        class_="small mb-2")
                 if path else
                 ui.div("This project is not saved yet: use Save As in the header first.",
                        class_="small mb-2"))
        hint = None
        if ws.is_checkout() and not ws.can_publish():
            hint = ui.div("To publish from this checkout, start StreamCurves with "
                          "STAF_LIBRARY_PUBLISH=1 and STAF_LIBRARY_MAINTAINER set.",
                          class_="text-muted small mt-2")
        return ui.div(
            ui.p("Assessments are published to the STAF assessment library by its "
                 "maintainer. To contribute this revision, send them your project file; "
                 "they open it, review it and publish the next version.", class_="mb-2"),
            where,
            ui.download_button("download_project_copy",
                               ui.TagList(fa("floppy-disk"), " Save a copy for the maintainer"),
                               class_="btn btn-primary"),
            hint,
            class_="pub-form")

    def _publish_pane(session_name: str, region: dict | None):
        if not ws.can_publish():
            return _share_pane()
        if not lib.writable():
            return ui.div(
                bi("info-circle"),
                " The library is read-only here, so this copy cannot publish.",
                class_="alert alert-info mb-0")

        existing = {
            a["assessmentId"]: a.get("assessmentName") or a["assessmentId"]
            for a in _assessments()
        }
        target_choices = dict(existing)
        target_choices[_NEW] = "New assessment..."
        # A project that started from a library version publishes that assessment's next
        # version by default (the reviewer round trip); a new build defaults to new.
        with reactive.isolate():
            origin_now = state.assessment_source() or {}
        origin_id = (lib.slugify(origin_now["library_id"])
                     if origin_now.get("kind") in ("library", "staged")
                     and origin_now.get("library_id") else None)
        selected = origin_id if origin_id in existing else _NEW
        if selected != _NEW:
            session_name = existing[selected]

        body = [
            ui.div(
                ui.div(
                    ui.input_select(
                        "pub_assessment",
                        "Assessment",
                        choices=target_choices,
                        selected=selected,
                    ),
                    class_="col-md-6",
                ),
                ui.div(
                    ui.input_text("pub_name", "Assessment name", value=session_name),
                    class_="col-md-6",
                ),
                class_="row g-2",
            ),
            # Only rendered when the target is a new assessment; the field is inert
            # when updating one, and its old label carried that as a parenthetical.
            ui.output_ui("new_id_field"),
            ui.output_ui("origin_note"),
            ui.div(
                ui.input_radio_buttons("pub_status", "Publish as",
                                       choices=_status_choices(), selected="draft",
                                       inline=True),
                class_="pub-seg"),
            ui.div(
                ui.tags.label("Region of applicability", class_="form-label mb-0"),
                ui.div(ap.region_label(region), class_="text-muted small"),
                class_="mb-2",
            ),
            ui.input_text_area(
                "pub_notes", "Revision notes", value="", rows=2,
                placeholder="What changed in this version",
            ),
            # Both have fallbacks on submit (citation to DEFAULT_SOURCE_CITATION,
            # author to the publisher name), so neither needs to be on screen.
            ui.accordion(
                ui.accordion_panel(
                    "Optional details",
                    ui.input_text(
                        "pub_citation", "Source citation",
                        value=ap.DEFAULT_SOURCE_CITATION,
                    ),
                    ui.input_text("pub_author", "Author", value=""),
                    value="optional",
                ),
                id="pub_optional", open=False, class_="mb-2",
            ),
        ]
        # The button carries the blocked state. Previously it stayed enabled and
        # green while an alert explained the env var, so the only way to find out
        # was to fill the form and read a warning toast. The gate reads env vars
        # only, so it cannot change mid-session and this can stay static.
        blocked = _publish_block_reason() or ap.transcription_refusal(state)
        # An assessment that came from an agent build carries its origin and the
        # build's provenance in state; say what this publish will record before
        # it runs, and steer an untouched staged build to promote instead.
        with reactive.isolate():
            built_by = (state.run_meta() or {}).get("built_by")
            origin = state.assessment_source()
            has_doc = state.source_provenance() is not None
        if not blocked:
            steer = _origin_steer(state, origin, has_doc, built_by)
            if steer is not None:
                body.append(steer)
            # SELECT-01: the gate in library.publish_version refuses a function carrying
            # more metrics than the portfolio maximum without a named approval. Ask for it
            # here, where the publisher can see which functions and say so, instead of
            # letting the click come back with the gate's error.
            standing = ap.pending_standing_decisions(state)
            if standing["approvals"] or standing["exceptions"]:
                body.append(ui.div(
                    ui.tags.label("Standing decisions to confirm", class_="form-label mb-0"),
                    ui.div(ap.pending_decisions_text(standing),
                           class_="text-muted small mb-1"),
                    ui.input_checkbox(
                        "pub_confirm_pending", "I confirm them under my name", value=False),
                    class_="pub-confirm-pending mb-2",
                ))
            # REF-15: a source the build refused that no build has computed yet
            # applies nothing to this version; say so before it is published (its
            # own output, so a decision taken elsewhere updates it without
            # re-rendering the form)
            body.append(ui.output_ui("publish_waiting"))
            pending = ap.portfolio_approval_needed(state)
            if pending:
                body.append(ui.div(
                    ui.tags.label("Portfolio approval", class_="form-label mb-0"),
                    ui.div(
                        _portfolio_approval_text(pending),
                        class_="text-muted small mb-1",
                    ),
                    ui.input_checkbox(
                        "pub_select01",
                        f"I approve {'this set' if len(pending) == 1 else 'these sets'} "
                        "as complementary, recorded under my name",
                        value=False,
                    ),
                    class_="pub-select01 mb-2",
                ))
        body.append(
            ui.input_action_button(
                "publish_btn",
                ui.TagList(bi("file-earmark-arrow-up"), " Publish new version"),
                class_="btn btn-success",
                disabled="disabled" if blocked else None,
            )
        )
        if blocked:
            body.append(ui.div(blocked, class_="text-muted small mt-1 pub-blocked-note"))
        return ui.div(*body, class_="pub-pane pub-pane-publish pub-form")

    def _new_id_value() -> str:
        """The typed id, or "" when the field is not on screen (updating, not new)."""
        try:
            return (input.pub_new_id() or "").strip()
        except Exception:  # noqa: BLE001 — input absent unless the target is new
            return ""

    @render.ui
    def new_id_field():
        try:
            target = input.pub_assessment()
        except Exception:  # noqa: BLE001 — select not bound yet
            target = _NEW
        if target != _NEW:
            return None
        return ui.input_text(
            "pub_new_id", "New assessment id", value="",
            placeholder="letters, numbers and hyphens",
        )

    @reactive.effect
    @reactive.event(input.pub_assessment, ignore_init=True)
    @guard("autofill the assessment name")
    def _autofill_pub_name():
        # Selecting an existing assessment fills its recorded name into the
        # name field. Without this, a publish under an existing id silently
        # RENAMED the assessment to the current session name (meta writes
        # input.pub_name at submit). Fires only on selection change, so hand
        # edits to the name are never clobbered mid-edit.
        target = input.pub_assessment()
        if target == _NEW:
            with reactive.isolate():
                fallback = _default_session_name(
                    state.session_name(), state.upload_filename()
                )
            ui.update_text("pub_name", value=fallback)
            return
        names = {
            a["assessmentId"]: a.get("assessmentName") or a["assessmentId"]
            for a in _assessments()
        }
        if target in names:
            ui.update_text("pub_name", value=names[target])

    # suspend_when_hidden=False: this output lives in a nav panel that may be
    # hidden at first render; without it the output never resumes when the tab
    # is shown (matches the pattern in views/regional_curve.py).
    @output(suspend_when_hidden=False)
    @render.ui
    def publish_waiting():
        state.owner_curve_decisions()           # re-render when a decision changes
        return _waiting_note(state)

    @render.ui
    def publish_body():
        refresh()
        state.reference_build()     # a transcription says so, whatever else is loaded
        refusal = ap.transcription_refusal(state)
        if refusal:
            return not_ready_panel("Not revised in the app", refusal, icon="file-arrow-up")
        loaded = bool(state.app_data_loaded())
        if not loaded:
            return not_ready_panel(
                "Nothing to publish yet",
                "Build a project first. Once one is open you can save it here as a "
                "project file, or publish it to the shared STAF assessment library.",
                action_label="Go to Region & data",
                goto_nav="data",
                goto_step=1,
                icon="file-arrow-up",
            )
        # Deliberate dependencies, NOT isolated: opening a different assessment
        # leaves app_data_loaded True, and with these isolated the form kept the
        # previous session's name and target, so a publish landed under the old
        # assessment's id (found live on the end-to-end verification).
        session_name = _default_session_name(
            state.session_name(), state.upload_filename()
        )
        region = state.region_of_applicability()
        return ui.TagList(
            ui.card(
                ui.card_header(
                    ui.TagList(bi("file-earmark-arrow-up"), " Publish: ",
                               ui.tags.strong(session_name))
                ),
                ui.card_body(
                    ui.output_ui("publish_checklist") if ws.can_publish() else None,
                    _publish_pane(session_name, region),
                ),
                class_="mb-3 publish-card",
            ),
            _exports_card(),
        )

    @render.ui
    def origin_note():
        """Say which version a revision starts from, and warn when the library has moved on
        since (a reviewer's copy of v7 published over a v9 library)."""
        try:
            target = input.pub_assessment()
        except Exception:  # noqa: BLE001 - not bound yet
            return None
        origin = state.assessment_source() or {}
        if target in (None, _NEW) or origin.get("kind") != "library":
            return None
        if lib.slugify(origin.get("library_id") or "") != target:
            return None
        latest = int((lib.read_manifest(target) or {}).get("latestVersion") or 0)
        started = int(origin.get("version") or 0)
        if started and latest > started:
            return ui.div(
                f"This project started from v{started}; the library is now at v{latest}. "
                f"Publishing makes v{latest + 1} from this project's content, so check what "
                f"v{started + 1} to v{latest} changed first.",
                class_="alert alert-warning py-2 small")
        return ui.div(f"This project started from v{started}; publishing makes "
                      f"v{latest + 1}.", class_="text-muted small mb-2")

    # ── File downloads (moved from the Data & Setup Save modal) ──────────────
    # suspend_when_hidden=False: the Publish panel may never have been shown
    # when the user clicks download; default suspension would leave the links
    # permanently disabled (the bb98c92 wedge).
    @output(suspend_when_hidden=False)
    @render.download(
        filename=lambda: _sanitize_file_stem(
            state.isolate_get("session_name"), state.isolate_get("upload_filename")
        )
        + ".deep.json"
    )
    def download_deep_bundle():
        """The DEEP bundle of the curves as they stand, to try in DEEP by upload. No
        version and no library record: DEEP treats it as an uploaded assessment."""
        with reactive.isolate():
            req(state.app_data_loaded())
        try:
            bundle = ap.build_bundle_from_state(state)
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=8)
            return
        yield json.dumps(bundle, indent=1, ensure_ascii=False).encode("utf-8")

    @output(suspend_when_hidden=False)
    @render.download(
        filename=lambda: Path(state.isolate_get("project_file") or "project.streamcurves").name
    )
    def download_project_copy():
        """The project file, saved first, for a reviewer to send to the maintainer."""
        with reactive.isolate():
            path = state.project_file()
        req(path)
        saver = state.hooks.get("before_replace")
        if saver is not None:
            saver()                   # the parting save: the copy carries the latest work
        yield Path(path).read_bytes()

    @output(suspend_when_hidden=False)
    @render.download(
        filename=lambda: _sanitize_file_stem(
            state.isolate_get("session_name"), state.isolate_get("upload_filename")
        )
        + f"_workbook_{date.today():%Y%m%d}.xlsx"
    )
    def download_workbook():
        with reactive.isolate():
            req(state.app_data_loaded())
            tables = state.input_metadata()
        req(tables is not None)
        buf = io.BytesIO()
        write_input_workbook(tables, buf)
        yield buf.getvalue()

    @output(suspend_when_hidden=False)
    @render.download(
        filename=lambda: _sanitize_file_stem(
            state.isolate_get("session_name"), state.isolate_get("upload_filename")
        )
        + "_DEEP_calculator_preview.xlsx"
    )
    def download_calculator():
        """The calculator a publish would build, from the curves as they stand.
        A preview: it carries no version and no content digest, so DEEP never
        offers it as a published version's workbook."""
        from streamcurves import deep_calculator
        with reactive.isolate():
            req(state.app_data_loaded())
        try:
            bundle = ap.build_bundle_from_state(state)
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=8)
            return
        yield deep_calculator.build_calculator(bundle)

    # ── publish (preliminary; validation lives on the Validate stage) ─────────
    @reactive.effect
    @reactive.event(input.publish_btn)
    def _publish():
        if not lib.writable():
            ui.notification_show(
                "The library is read-only here. Save a project file and send it "
                "to the publisher.",
                type="warning",
                duration=8,
            )
            return

        if not ws.can_publish():
            ui.notification_show("The maintainer publishes: save a copy for them instead.",
                                 type="warning", duration=8)
            return
        refusal = ap.transcription_refusal(state)
        if refusal:
            ui.notification_show(refusal, type="warning", duration=8)
            return
        status = input.pub_status() or "draft"
        if status not in lib.PUBLISH_STATUSES:
            status = "draft"
        status_word = lib.status_label(status)
        target = input.pub_assessment()
        if target == _NEW:
            aid = lib.slugify(_new_id_value() or input.pub_name() or "")
        else:
            aid = target
        if not aid:
            ui.notification_show(
                "Enter a new assessment id or name.", type="warning", duration=5
            )
            return

        with reactive.isolate():
            loaded = bool(state.app_data_loaded())
            confirmed = bool(state.discipline_function_mapping_confirmed())
        if not loaded:
            ui.notification_show("Load or build a session first.", type="warning", duration=5)
            return
        if not confirmed:
            ui.notification_show(
                "Confirm the function mapping first: Refine & map in the workflow "
                "strip, Function mapping, then Save mapping.",
                type="warning",
                duration=8,
            )
            return

        # Canonical-publish gate: STAF_LIBRARY_PUBLISH=1 + writable + publisher name.
        # The button is already disabled when this fails, so reaching here needs a
        # deliberate DOM edit; keep the technical reason for that case.
        maintainer = _maintainer_name(state)
        gate_reason = lib.publish_gate_reason(maintainer)
        if gate_reason:
            ui.notification_show(gate_reason, type="warning", duration=10)
            return

        region = ap.region_from_state(state)
        name = input.pub_name() or aid
        # who prepared the revision (a reviewer's name from their project), in the notes
        with reactive.isolate():
            prepared_by = str((state.project_meta() or {}).get("prepared_by") or "").strip()
        notes = input.pub_notes() or ""
        if prepared_by and prepared_by.lower() not in notes.lower():
            notes = (notes.rstrip() + " " if notes.strip() else "") + f"Prepared by {prepared_by}."
        meta = {
            "assessmentName": name,
            "region": region,
            "sourceCitation": input.pub_citation() or ap.DEFAULT_SOURCE_CITATION,
            "author": input.pub_author() or maintainer,
            "revisionNotes": notes,
        }
        if region and region.get("kind") == "state":
            meta["stateCode"] = region.get("code") or ""
            meta["stateName"] = region.get("name") or ""

        # Staged-run readiness gate: only enforced once a staged run exists (a
        # populated curve_review). The Advanced path (confirmed mapping + finalized
        # curves, no staged run) publishes on the mapping/curve checks made above.
        snap = ap.run_snapshot(state)
        if snap.get("curve_review") and not rs.is_ready_to_publish(snap):
            unresolved = rs.flagged_metrics(snap.get("curve_review") or {})
            # Naming the outstanding items beats the old fixed list of four: the
            # checklist above the button already says which ones they are, and a
            # refusal that repeats them is what sends the publisher back to it.
            outstanding = [i["label"] for i in rs.readiness_checklist(snap) if not i["ok"]]
            ui.notification_show(
                "Complete the publish checklist first: "
                + (f"{len(unresolved)} flagged curve(s) still need review."
                   if unresolved else ", ".join(outstanding) + "."),
                type="warning", duration=10)
            return

        # Stamp the publish stage BEFORE capturing the session payload so the
        # stored session reopens with stage 5 done (capturing first is why
        # earlier published sessions showed publish still pending). Reverted
        # if the write fails.
        expected_version = int((lib.read_manifest(lib.slugify(aid)) or {}).get(
            "latestVersion") or 0) + 1
        with reactive.isolate():
            prev_stage_status = dict(state.run_stage_status() or {})
            prev_meta = state.run_meta()
        stamped = dict(prev_stage_status)
        stamped["publish"] = {"status": "done",
                              "label": f"Published {name} v{expected_version} "
                                       f"({status_word})."}
        state.run_stage_status.set(stamped)
        state.run_meta.set(rs.touch_run_meta(prev_meta))

        # Standing decisions the build left pending (SELECT-01 approvals, COV-01
        # documented gaps) are the publisher's to confirm, by name, before they ride
        # into the version, exactly as promote confirms them.
        now_iso = datetime.now(timezone.utc).isoformat()
        standing = ap.pending_standing_decisions(state)
        with reactive.isolate():
            exceptions = copy.deepcopy(list(state.function_coverage_exceptions() or []))
            curve_decisions = list(state.owner_curve_decisions() or [])
        if standing["approvals"] or standing["exceptions"]:
            try:
                ticked = bool(input.pub_confirm_pending())
            except Exception:  # noqa: BLE001 - the box is absent when nothing is pending
                ticked = False
            if not ticked:
                state.run_stage_status.set(prev_stage_status)
                state.run_meta.set(prev_meta)
                ui.notification_show(
                    "Confirm the standing decisions first: tick the confirmation box on "
                    "this form. " + ap.pending_decisions_text(standing),
                    type="warning", duration=12)
                return
            dec.confirm_exceptions(exceptions, maintainer=maintainer, date=now_iso)

        try:
            bundle = ap.build_bundle_from_state(
                state,
                meta={"assessmentName": name, "sourceCitation": meta["sourceCitation"],
                      "functionCoverageExceptions": oc.with_exceptions(
                          exceptions, curve_decisions)},
            )
            full_payload = ap.session_payload_from_state(state)
            # the session keeps its own gaps; one recorded with a curve decision
            # that no longer stands does not ride along (REF-15)
            full_payload["fields"]["function_coverage_exceptions"] = sio.encode_value(
                oc.live_exceptions(exceptions, curve_decisions),
                path="$.function_coverage_exceptions")
            # Every published version carries a provenance document. When the
            # assessment came from an agent build, the build's own document is
            # carried through with an appended interactive-revision entry, and
            # the human publishing after the full in-app review is the owner
            # confirming any standing decisions still marked pending. Otherwise
            # the interactive document records what this path genuinely applied
            # and lists the rest as not evaluated, so an interactive publish is
            # auditable without faking an agent-grade chain.
            with reactive.isolate():
                curve_review = dict(state.curve_review() or {})
                region_now = state.region_of_applicability()
                session_name = state.session_name()
                source_doc = state.source_provenance()
                origin = state.assessment_source()
            # SELECT-01 approvals recorded on the origin ride into the publish
            # meta: this form builds fresh meta, and without them an opened
            # agent build with a >2-metric function would be refused by the
            # very gate its own build already satisfied.
            if (origin or {}).get("portfolio_approvals"):
                meta["portfolioApprovals"] = copy.deepcopy(origin["portfolio_approvals"])
                dec.confirm_approvals(meta["portfolioApprovals"], maintainer=maintainer,
                                      date=now_iso)
            # The rest are this publisher's to give: the checkbox on the form is the
            # recorded human approval SELECT-01 asks for, and the real bundle (not the
            # page's quick count) names the functions it covers.
            approved = {str(a.get("functionId"))
                        for a in (meta.get("portfolioApprovals") or [])
                        if a.get("functionId") and a.get("approvedBy")}
            unapproved = [(fid, n) for fid, n in lib.functions_over_metric_limit(bundle)
                          if fid not in approved]
            if unapproved:
                try:
                    ticked = bool(input.pub_select01())
                except Exception:  # noqa: BLE001 — the box is absent when nothing needs one
                    ticked = False
                if not ticked:
                    state.run_stage_status.set(prev_stage_status)
                    state.run_meta.set(prev_meta)
                    ui.notification_show(
                        "Approve the portfolio first: "
                        + ", ".join(f"{fid} ({n} metrics)" for fid, n in unapproved)
                        + " carry more than two metrics, so SELECT-01 needs the approval "
                        "checkbox on this form ticked before publishing.",
                        type="warning", duration=12)
                    return
                meta["portfolioApprovals"] = (meta.get("portfolioApprovals") or []) + [
                    {"functionId": fid, "approvedBy": maintainer,
                     "note": (f"Approved at interactive publish: {n} metrics kept as a "
                              "complementary set after review in StreamCurves.")}
                    for fid, n in unapproved]
            if source_doc:
                changes = ap.origin_changes(
                    state, origin, content_digest=lib.content_digest(bundle))
                provenance_doc = pv.build_carried_provenance(
                    source_doc, origin=origin or {}, publisher=_maintainer_name(state),
                    session_name=session_name, changes=changes, timestamp=now_iso)
                if dec.is_pending(provenance_doc):
                    # ValueError (a rationale contradicting its record) aborts
                    # below before anything is written.
                    dec.confirm_pending_decisions(
                        provenance_doc, reviewer=maintainer, date=now_iso)
            else:
                provenance_doc = pv.build_interactive_provenance(
                    bundle, curve_review, region=region_now,
                    publisher=_maintainer_name(state), session_name=session_name)
            # promote's last check: nothing still marked pending rides into the
            # version the owner confirms
            if dec.is_pending(json.dumps({"bundle": bundle.get("functionCoverage"),
                                          "approvals": meta.get("portfolioApprovals")},
                                         default=str)):
                raise ValueError("a standing decision is still marked pending owner "
                                 "confirmation.")
            # the candidate register: what was considered for each function and why,
            # beside the bundle and never in it
            register_doc = fs.register_export(state)
            if register_doc and isinstance(provenance_doc, dict):
                provenance_doc = {**provenance_doc, "candidateRegister": register_doc}
            elif register_doc is None and state.reference_build() is not None:
                ui.notification_show("The candidate register could not be read, so this version's "
                                     "record goes without it. The log has the details.",
                                     type="warning", duration=10)
            version = lib.publish_version(aid, meta, full_payload, bundle,
                                          provenance=provenance_doc, status=status)
        except Exception as e:  # noqa: BLE001
            state.run_stage_status.set(prev_stage_status)
            state.run_meta.set(prev_meta)
            logger.exception("library publish failed")
            ui.notification_show(f"Publish failed: {e}", type="error", duration=10)
            return
        if standing["exceptions"]:
            state.function_coverage_exceptions.set(exceptions)
        if version != expected_version:
            with reactive.isolate():
                ss = dict(state.run_stage_status() or {})
            ss["publish"] = {"status": "done",
                             "label": f"Published {name} v{version} ({status_word})."}
            state.run_stage_status.set(ss)

        # The published version becomes the new origin: Validate targets it
        # immediately, and a later revision chains on this publish's record.
        # The validation-record mirror follows the new version too (it starts
        # unvalidated; without this the strip kept the old version's count).
        # Disclosure only; a failure here never undoes the publish.
        try:
            state.source_provenance.set(
                lib.load_version_provenance(aid, version) or provenance_doc)
            state.assessment_source.set(ap.build_origin(
                state, kind="library", library_id=lib.slugify(aid), version=version,
                content_digest=lib.version_content_digest(aid, version),
                portfolio_approvals=meta.get("portfolioApprovals"),
                loaded_at=datetime.now(timezone.utc).isoformat()))
            state.validation_records.set(
                lib._validation_records_for(lib.slugify(aid), version))
        except Exception:  # noqa: BLE001
            logger.exception("publish: origin re-establish failed")

        refresh.set(refresh() + 1)
        _record_decisions_for_region()

        # Fold the new latest into DEEP's baked registry so the cloud DEEP ships it.
        # Validation and certification live on the Validate stage now.
        baked_ok, baked_msg = lib.rebake_deep()
        deep_line = ("DEEP runs it once DEEP is redeployed." if status == "preliminary"
                     else "Drafts stay out of DEEP until approved on the Validate stage.")
        if baked_ok:
            ui.notification_show(
                f"Published {name} v{version} as a {status_word} version. {deep_line} "
                "Commit apps/library and apps/deep/data and push; the library release "
                "refreshes for everyone after the push.",
                type="message",
                duration=12,
            )
        else:
            ui.notification_show(
                f"Published {name} v{version} as a {status_word} version. DEEP registry "
                f"not auto-updated ({baked_msg}). Run "
                "apps/deep/scripts/bake_library_into_deep.py, then commit "
                "apps/library and apps/deep/data.",
                type="warning",
                duration=12,
            )

    def _record_decisions_for_region():
        """A reviewer's project can carry curve decisions (REF-15) the region's record
        does not hold; publishing their revision records them for the region, so the next
        build applies them too. Disclosure-level: a failure never undoes the publish."""
        with reactive.isolate():
            region = state.region_of_applicability()
            decisions = list(state.owner_curve_decisions() or [])
        run_dir = rb.region_run_dir(region)
        if run_dir is None or not decisions:
            return
        try:
            held = {d.get("id") for d in rb.standing_decisions(run_dir, (region or {}).get("code"))}
            added = 0
            for d in decisions:
                if d.get("id") in held or str(d.get("id") or "").startswith(oc.FLAG_PREFIX):
                    continue
                oc.save(run_dir, d)
                added += 1
            if added:
                ui.notification_show(
                    f"{added} curve decision{'' if added == 1 else 's'} from this project "
                    f"{'is' if added == 1 else 'are'} now recorded for the region.",
                    type="message", duration=8)
        except Exception:  # noqa: BLE001
            logger.exception("publish: recording curve decisions for the region failed")
