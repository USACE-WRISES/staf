"""Publish page — save the open project to a file, or publish it into the
shared STAF assessment library as a Preliminary version (an interactive publish
IS the human review; automation publishes Drafts through the Region builder).

Replaces the old Library tab: browsing and opening moved to the header Open
dialog (views/data_overview.py). Lifecycle after publishing lives on the
Validate stage: a published version is validated there against field data and
certified in place, so this page carries no validation form of its own.

See apps/library/README.md for the on-disk format. Reading the catalog works
anywhere the folder is reachable; publishing is a local/desktop action (writable
folder) and degrades to "save a project file and send it to the publisher" on
the web.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from shiny import module, reactive, render, req, ui

from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import provenance as pv
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from streamcurves.deep_export import write_deep_assessment_bundle
from streamcurves.workbook import write_input_workbook
from views import assessment_publish as ap
from views.data_overview import _default_session_name, _sanitize_file_stem
from views.state import AppState
from views.theme import STAF_LINKS, bi, fa
from views.uihelpers import _goto_onclick, guard, not_ready_panel, rule_chip

logger = logging.getLogger("streamcurves")

_NEW = "__new__"

# Save-level values: "file" downloads the project to the user's computer,
# "library" publishes a Preliminary version. ("Draft" is NOT a save level: it
# is the library lifecycle status automation output carries; renamed 2026-08-27
# so the two never collide on this page.)
def _level_choices() -> dict:
    """Two-line labels for the save-level segmented control. Still a plain
    input_radio_buttons under the hood (id, value protocol, level_style
    toggler all unchanged); .pub-seg CSS renders the options as cards."""
    return {
        "file": ui.TagList(
            ui.tags.span("Save to file", class_="pub-seg-title"),
            ui.tags.span("Download the project to your computer",
                         class_="pub-seg-caption"),
        ),
        "library": ui.TagList(
            ui.tags.span("Publish to library", class_="pub-seg-title"),
            ui.tags.span("Add a version to the shared assessment library",
                         class_="pub-seg-caption"),
        ),
    }


def _is_desktop() -> bool:
    """The desktop shell injects STAF_LINKS_OVERRIDES (cross-app links become
    staf-desktop:// URIs). Absent on the web deploys."""
    return bool(os.environ.get("STAF_LINKS_OVERRIDES"))


def _maintainer_name() -> str:
    """Who to record as the publisher, derived rather than asked for.

    Same chain views/discipline_map.py uses for a coverage exception's author. The
    page used to carry a "Maintainer name (for the canonical publish audit trail)"
    field pre-filled from the first of these, which asked the publisher to retype
    something the environment already knows.
    """
    return (os.environ.get("STAF_LIBRARY_MAINTAINER")
            or os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()


def _publish_block_reason() -> str | None:
    """The actionable gate reason from library.publish_gate_reason, or None.

    The library's own copy names the fix (STAF_LIBRARY_PUBLISH=1 in a verified
    repository checkout; a maintainer name for the audit trail), which is what
    a blocked publisher actually needs to read. The page used to compress it to
    "Publishing is off in this session.", which read as an unexplained fault,
    and its branch order could mask the flag message behind the maintainer one.
    The not-writable branch never reaches this note: _publish_pane replaces the
    whole form for that case.
    """
    reason = lib.publish_gate_reason(_maintainer_name())
    if reason is None:
        return None
    if not _maintainer_name() and lib.can_publish_canonical("anyone"):
        # Flag and writability are fine; only the audit name is missing. The
        # library's wording ("Enter a maintainer name...") assumes a form
        # field this page deliberately does not have.
        return ("No publisher name is available for the audit trail. Set "
                "STAF_LIBRARY_MAINTAINER (or run where USERNAME is set), then reload.")
    return reason


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
    draft_handoff_url = reactive.value(None)  # set after a desktop draft is staged

    def _assessments() -> list[dict]:
        refresh()
        try:
            return lib.list_assessments()
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

    # ── save-level pane visibility (CSS, so form inputs keep their values) ────
    @render.ui
    def level_style():
        lvl = "file"
        try:
            lvl = input.save_level() or "file"
        except Exception:  # noqa: BLE001 — radio not bound yet
            pass
        css = ".pub-pane {display: none;} "
        if lvl == "file":
            css += ".pub-pane-file {display: block;}"
        else:
            css += ".pub-pane-publish {display: block;}"
        return ui.tags.style(css)

    def _file_pane():
        return ui.div(
            ui.div(
                ui.div(
                    ui.download_button(
                        "download_session",
                        ui.TagList(fa("floppy-disk"), " Project (.json)"),
                        class_="btn btn-primary w-100",
                    ),
                    ui.tags.small(
                        "Full session. Reopen and continue where you left off.",
                        class_="text-muted d-block mt-1",
                    ),
                    class_="col-md-6",
                ),
                ui.div(
                    ui.download_button(
                        "download_workbook",
                        ui.TagList(fa("file-excel"), " Workbook (.xlsx)"),
                        class_="btn btn-outline-primary w-100",
                    ),
                    ui.tags.small(
                        "Data and setup sheets for Excel. Reopening rebuilds the analysis.",
                        class_="text-muted d-block mt-1",
                    ),
                    class_="col-md-6",
                ),
                class_="row g-3",
            ),
            class_="pub-pane pub-pane-file pub-form",
        )

    def _publish_pane(session_name: str, region: dict | None):
        if not lib.writable():
            return ui.div(
                ui.div(
                    bi("info-circle"),
                    " Publishing writes to the version-controlled library, which is "
                    "a local or desktop action. On the hosted app, choose Save to "
                    "file and send the project to whoever maintains the library.",
                    class_="alert alert-info mb-0",
                ),
                class_="pub-pane pub-pane-publish pub-form",
            )

        existing = {
            a["assessmentId"]: a.get("assessmentName") or a["assessmentId"]
            for a in _assessments()
        }
        target_choices = dict(existing)
        target_choices[_NEW] = "New assessment..."

        body = [
            ui.div(
                ui.div(
                    ui.input_select(
                        "pub_assessment",
                        "Assessment",
                        choices=target_choices,
                        selected=_NEW,
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
        if _is_desktop():
            body.append(
                ui.div(
                    ui.hr(class_="mt-2 mb-2"),
                    ui.tags.label("Test before publishing", class_="form-label mb-1"),
                    ui.div(
                        "Open the current work in DEEP to try scoring before committing a "
                        "version.",
                        class_="text-muted small mb-2",
                    ),
                    ui.input_action_button(
                        "draft_to_deep",
                        ui.TagList(bi("arrow-right-circle"), " Preview in DEEP"),
                        class_="btn btn-outline-primary btn-sm",
                    ),
                    ui.output_ui("draft_deep_link"),
                    ui.hr(class_="mt-2 mb-2"),
                    class_="mt-2",
                )
            )
        # The button carries the blocked state. Previously it stayed enabled and
        # green while an alert explained the env var, so the only way to find out
        # was to fill the form and read a warning toast. The gate reads env vars
        # only, so it cannot change mid-session and this can stay static.
        blocked = _publish_block_reason()
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
    def publish_body():
        refresh()
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
                    ui.TagList(bi("file-earmark-arrow-up"), " Save or publish: ",
                               ui.tags.strong(session_name))
                ),
                ui.card_body(
                    ui.output_ui("publish_checklist"),
                    ui.div(
                        ui.input_radio_buttons(
                            "save_level", "How do you want to save this work?",
                            choices=_level_choices(),
                            selected="file",
                        ),
                        class_="pub-seg",
                    ),
                    ui.output_ui("level_style"),
                    _file_pane(),
                    _publish_pane(session_name, region),
                ),
                class_="mb-3 publish-card",
            ),
        )

    # ── File downloads (moved from the Data & Setup Save modal) ──────────────
    # suspend_when_hidden=False: the Publish panel may never have been shown
    # when the user clicks download; default suspension would leave the links
    # permanently disabled (the bb98c92 wedge).
    @output(suspend_when_hidden=False)
    @render.download(
        filename=lambda: _sanitize_file_stem(
            state.isolate_get("session_name"), state.isolate_get("upload_filename")
        )
        + sio.SESSION_SUFFIX
    )
    def download_session():
        with reactive.isolate():
            req(state.app_data_loaded())
            session_name = _default_session_name(
                state.session_name(), state.upload_filename()
            )
            state.session_name.set(session_name)
            fields = {name: state.get(name) for name in sio.SESSION_FIELDS}
        payload = sio.dump_session_fields(fields, session_name=session_name)
        yield sio.dumps_session(payload).encode("utf-8")

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

        level = input.save_level() or "library"
        if level == "file":
            ui.notification_show(
                "'Save to file' downloads the project instead: use the download "
                "buttons above.",
                type="warning", duration=6,
            )
            return
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
        maintainer = _maintainer_name()
        gate_reason = lib.publish_gate_reason(maintainer)
        if gate_reason:
            ui.notification_show(gate_reason, type="warning", duration=10)
            return

        region = ap.region_from_state(state)
        name = input.pub_name() or aid
        meta = {
            "assessmentName": name,
            "region": region,
            "sourceCitation": input.pub_citation() or ap.DEFAULT_SOURCE_CITATION,
            "author": input.pub_author() or maintainer,
            "revisionNotes": input.pub_notes() or "",
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
            ui.notification_show(
                "Complete the publish checklist first: "
                + (f"{len(unresolved)} flagged curve(s) still need review."
                   if unresolved else "region, retained sites, enrichment, and in-scope "
                   "curves must all be complete."),
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
                              "label": f"Published {name} v{expected_version}."}
        state.run_stage_status.set(stamped)
        state.run_meta.set(rs.touch_run_meta(prev_meta))

        try:
            bundle = ap.build_bundle_from_state(
                state,
                meta={"assessmentName": name, "sourceCitation": meta["sourceCitation"]},
            )
            full_payload = ap.session_payload_from_state(state)
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
                meta["portfolioApprovals"] = origin["portfolio_approvals"]
            now_iso = datetime.now(timezone.utc).isoformat()
            if source_doc:
                changes = ap.origin_changes(
                    state, origin, content_digest=lib.content_digest(bundle))
                provenance_doc = pv.build_carried_provenance(
                    source_doc, origin=origin or {}, publisher=_maintainer_name(),
                    session_name=session_name, changes=changes, timestamp=now_iso)
                if dec.is_pending(provenance_doc):
                    # ValueError (a rationale contradicting its record) aborts
                    # below before anything is written.
                    dec.confirm_pending_decisions(
                        provenance_doc, reviewer=maintainer, date=now_iso)
            else:
                provenance_doc = pv.build_interactive_provenance(
                    bundle, curve_review, region=region_now,
                    publisher=_maintainer_name(), session_name=session_name)
            version = lib.publish_version(aid, meta, full_payload, bundle,
                                          provenance=provenance_doc)
        except Exception as e:  # noqa: BLE001
            state.run_stage_status.set(prev_stage_status)
            state.run_meta.set(prev_meta)
            logger.exception("library publish failed")
            ui.notification_show(f"Publish failed: {e}", type="error", duration=10)
            return
        if version != expected_version:
            with reactive.isolate():
                ss = dict(state.run_stage_status() or {})
            ss["publish"] = {"status": "done", "label": f"Published {name} v{version}."}
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

        # Fold the new latest into DEEP's baked registry so the cloud DEEP ships it.
        # Validation and certification live on the Validate stage now.
        baked_ok, baked_msg = lib.rebake_deep()
        if baked_ok:
            ui.notification_show(
                f"Published {name} v{version} as a Preliminary version, and updated "
                "DEEP's registry. Commit apps/library and apps/deep/data, then "
                "redeploy DEEP. Validate it with field data in the Validate stage "
                "when ready.",
                type="message",
                duration=10,
            )
        else:
            ui.notification_show(
                f"Published {name} v{version} as a Preliminary version. DEEP registry "
                f"not auto-updated ({baked_msg}). Run "
                "apps/deep/scripts/bake_library_into_deep.py, then commit "
                "apps/library and apps/deep/data.",
                type="warning",
                duration=12,
            )

    # ── desktop-only: stage a preview bundle and hand it to DEEP (?handoff=) ──
    @reactive.effect
    @reactive.event(input.draft_to_deep)
    def _prepare_draft_handoff():
        try:
            bundle = ap.build_bundle_from_state(state)
        except ValueError as e:  # no finalized curves yet
            ui.notification_show(str(e), type="warning", duration=8)
            return
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not build the preview: {e}", type="error", duration=8)
            return
        handoff_dir = Path(tempfile.gettempdir()) / "staf-handoff"
        try:
            handoff_dir.mkdir(parents=True, exist_ok=True)
            path = handoff_dir / "preview.deep.json"
            write_deep_assessment_bundle(bundle, path)
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not stage the preview: {e}", type="error", duration=8)
            return
        deep_base = (STAF_LINKS.get("deep") or "").rstrip("/")
        draft_handoff_url.set(f"{deep_base}/?handoff={quote(str(path))}")
        ui.notification_show(
            "Preview staged. Click 'Open preview in DEEP' to load it.",
            type="message", duration=6
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def draft_deep_link():
        url = draft_handoff_url()
        if not url:
            return None
        return ui.tags.a(
            ui.TagList(bi("arrow-right-circle"), " Open preview in DEEP"),
            href=url,
            target="_blank",
            rel="noopener",
            class_="btn btn-primary btn-sm d-inline-block mt-2",
        )
