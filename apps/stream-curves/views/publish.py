"""Publish page — save the open project as a Draft file, or promote it into the
shared STAF assessment library as a Preliminary or Final (certified) version.

Replaces the old Library tab: browsing and opening moved to the header Open
dialog (views/data_overview.py), and lifecycle/governance collapsed into
publish-time choices. A version is validated and certified when it is published
that way; changing a published version's status means opening it and publishing
again at the new level (republish-only governance).

See apps/library/README.md for the on-disk format. Reading the catalog works
anywhere the folder is reachable; publishing is a local/desktop action (writable
folder) and degrades to "save a Draft and send it to the publisher" on the web.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

from shiny import module, reactive, render, req, ui

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
from views.uihelpers import not_ready_panel

logger = logging.getLogger("streamcurves")

_NEW = "__new__"

_LEVEL_CHOICES = {
    "draft": "Draft: save a file to your computer",
    "preliminary": "Preliminary: publish to the assessment library",
    "final": "Final: publish as validated and certified",
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
    """One plain sentence when publishing cannot work here, else None.

    library.publish_gate_reason explains how to switch publishing on, in terms of
    an env var and a repository checkout. That belongs in the runbook, not over a
    form, so its branches are mapped to short copy and the Publish button carries
    the state by being disabled. The not-writable branch has no case here:
    _publish_pane already replaces the whole form for that.
    """
    if not lib.publish_gate_reason(_maintainer_name()):
        return None
    if not _maintainer_name():
        return "No publisher name is available for the audit trail."
    return "Publishing is off in this session."


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
                *[ui.tags.li(i["label"]) for i in outstanding],
                class_="small mb-0",
            ),
            class_="publish-checklist border rounded p-2 mb-3",
        )

    # ── save-level pane visibility (CSS, so form inputs keep their values) ────
    @render.ui
    def level_style():
        lvl = "draft"
        try:
            lvl = input.save_level() or "draft"
        except Exception:  # noqa: BLE001 — radio not bound yet
            pass
        css = ".pub-pane {display: none;} "
        if lvl == "draft":
            css += ".pub-pane-draft {display: block;}"
        else:
            css += ".pub-pane-publish {display: block;}"
        css += (
            " .pub-final-only {display: block;}"
            if lvl == "final"
            else " .pub-final-only {display: none;}"
        )
        return ui.tags.style(css)

    def _draft_pane():
        return ui.div(
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
                class_="mb-3",
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
            ),
            class_="pub-pane pub-pane-draft",
            style="max-width: 420px;",
        )

    def _publish_pane(session_name: str, region: dict | None):
        if not lib.writable():
            return ui.div(
                ui.div(
                    bi("info-circle"),
                    " Publishing writes to the version-controlled library, which is "
                    "a local or desktop action. On the hosted app, save a Draft "
                    "(above) and send the file to whoever maintains the library.",
                    class_="alert alert-info mb-0",
                ),
                class_="pub-pane pub-pane-publish",
            )

        existing = {
            a["assessmentId"]: a.get("assessmentName") or a["assessmentId"]
            for a in _assessments()
        }
        target_choices = dict(existing)
        target_choices[_NEW] = "New assessment..."

        body = [
            ui.input_select(
                "pub_assessment",
                "Assessment",
                choices=target_choices,
                selected=_NEW,
            ),
            # Only rendered when the target is a new assessment; the field is inert
            # when updating one, and its old label carried that as a parenthetical.
            ui.output_ui("new_id_field"),
            ui.input_text("pub_name", "Assessment name", value=session_name),
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
            # Validation: optional for Preliminary, required for Final. Detail
            # fields + the certify control are server-rendered (output_ui) off
            # pub_validated, which is more robust than a namespaced client-side
            # conditionalPanel inside a module.
            ui.tags.hr(class_="my-2"),
            ui.input_checkbox("pub_validated", "Validated", value=False),
            ui.div(
                "An independent check was completed for this version.",
                class_="text-muted small mb-2",
            ),
            ui.output_ui("validation_detail"),
            ui.div(ui.output_ui("final_certify"), class_="pub-final-only"),
        ]
        if _is_desktop():
            body.append(
                ui.div(
                    ui.hr(class_="mt-2 mb-2"),
                    ui.tags.label("Test before publishing", class_="form-label mb-1"),
                    ui.div(
                        "Open the current draft in DEEP to try scoring before committing a "
                        "version.",
                        class_="text-muted small mb-2",
                    ),
                    ui.input_action_button(
                        "draft_to_deep",
                        ui.TagList(bi("arrow-right-circle"), " Prepare draft for DEEP"),
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
        body.append(
            ui.input_action_button(
                "publish_btn",
                ui.TagList(bi("file-earmark-arrow-up"), " Publish new version"),
                class_="btn btn-success",
                disabled="disabled" if blocked else None,
            )
        )
        if blocked:
            body.append(ui.div(blocked, class_="text-muted small mt-1"))
        return ui.div(*body, class_="pub-pane pub-pane-publish")

    def _validated_checked() -> bool:
        try:
            return bool(input.pub_validated())
        except Exception:  # noqa: BLE001 — checkbox not mounted yet
            return False

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

    @output(suspend_when_hidden=False)
    @render.ui
    def validation_detail():
        # Independent-check fields, shown once Validated is checked.
        if not _validated_checked():
            return None
        return ui.div(
            ui.div(
                ui.div(
                    ui.input_text("pub_val_method", "Check method",
                                  placeholder="independent recompute / field re-measure"),
                    class_="col-sm-6",
                ),
                ui.div(
                    ui.input_text("pub_val_checker", "Checker",
                                  placeholder="name or organization"),
                    class_="col-sm-6",
                ),
                class_="row g-2",
            ),
            ui.input_select("pub_val_outcome", "Outcome",
                            choices={"match": "Matches", "minor": "Minor differences",
                                     "major": "Major differences"}),
            ui.input_text_area("pub_val_note", "Validation note", rows=2,
                               placeholder="Aggregate only, no site data"),
            class_="border rounded p-2 mb-2 bg-light",
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def final_certify():
        # Final only (the parent div is CSS-gated on save_level==final): the
        # certify checkbox once Validated is checked, else a nudge to validate.
        if _validated_checked():
            return ui.TagList(
                ui.input_checkbox("pub_certified", "Certify this version", value=False),
                ui.div(
                    "EcoPCX independent review is complete.",
                    class_="text-muted small mb-2",
                ),
            )
        return ui.div(
            fa("triangle-exclamation"),
            " Final requires validation. Check Validated above first.",
            class_="alert alert-warning py-1 px-2 small mb-2",
        )

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
                "Draft file, or publish it to the shared STAF assessment library.",
                action_label="Go to Region & data",
                goto_nav="data",
                goto_step=1,
                icon="file-arrow-up",
            )
        with reactive.isolate():
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
                    ui.input_radio_buttons(
                        "save_level", "Save level", choices=_LEVEL_CHOICES,
                        selected="draft",
                    ),
                    ui.output_ui("level_style"),
                    _draft_pane(),
                    _publish_pane(session_name, region),
                ),
                class_="mb-3",
            ),
        )

    # ── Draft downloads (moved from the Data & Setup Save modal) ──────────────
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

    # ── publish (Preliminary / Final) ─────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.publish_btn)
    def _publish():
        if not lib.writable():
            ui.notification_show(
                "The library is read-only here. Save a Draft and send it to the "
                "publisher.",
                type="warning",
                duration=8,
            )
            return

        level = input.save_level() or "preliminary"
        if level == "draft":
            ui.notification_show(
                "Draft saves a file instead: use the download buttons above.",
                type="warning", duration=6,
            )
            return
        validated = bool(input.pub_validated())
        certify = level == "final"
        if certify and not validated:
            ui.notification_show(
                "Final publishes a certified version, and certification requires "
                "validation. Check Validated first (or publish as Preliminary).",
                type="warning", duration=8,
            )
            return
        if certify and not bool(input.pub_certified()):
            ui.notification_show(
                "Confirm certification: check the certify box (EcoPCX review "
                "complete) or publish as Preliminary.",
                type="warning", duration=8,
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
            # Every published version carries a provenance document. The
            # interactive one records what this path genuinely applied (curve
            # review, family, portfolio counts) and lists the rest as not
            # evaluated, so an interactive publish is auditable without faking
            # an agent-grade chain.
            with reactive.isolate():
                curve_review = dict(state.curve_review() or {})
                region = state.region_of_applicability()
                session_name = state.session_name()
            provenance_doc = pv.build_interactive_provenance(
                bundle, curve_review, region=region,
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

        # Validation and certification writers (append-only, audited). A failure
        # here leaves the version published as preliminary and says so.
        governed = "preliminary"
        gov_problem = None
        if validated:
            try:
                checker = (input.pub_val_checker() or "").strip() or maintainer
                lib.add_validation_record(aid, version, {
                    "method": input.pub_val_method() or "",
                    "checker": checker,
                    "outcome": input.pub_val_outcome() or "match",
                }, actor=maintainer, note=input.pub_val_note() or None)
                n_records = len(lib._validation_records_for(lib.slugify(aid), version))
                lib.set_version_validation(aid, version, "validated",
                                           {"n_records": n_records}, maintainer)
                governed = "validated"
            except Exception as e:  # noqa: BLE001
                logger.exception("publish: validation writers failed")
                gov_problem = f"validation record failed ({e})"
        if certify and governed == "validated":
            try:
                lib.set_version_status(
                    aid, version, "certified", maintainer,
                    note="Published as Final; validated and certified at publish.",
                )
                governed = "certified"
            except Exception as e:  # noqa: BLE001
                logger.exception("publish: certification failed")
                gov_problem = f"certification failed ({e})"

        refresh.set(refresh() + 1)

        # Fold the new latest into DEEP's baked registry so the cloud DEEP ships it.
        baked_ok, baked_msg = lib.rebake_deep()
        status_txt = {"preliminary": "as a preliminary version",
                      "validated": "as a validated preliminary version",
                      "certified": "as a certified (Final) version"}[governed]
        suffix = f" Note: {gov_problem}." if gov_problem else ""
        if baked_ok:
            ui.notification_show(
                f"Published {name} v{version} {status_txt}, and updated DEEP's "
                f"registry.{suffix} Commit apps/library and apps/deep/data, then "
                "redeploy DEEP.",
                type="message",
                duration=10,
            )
        else:
            ui.notification_show(
                f"Published {name} v{version} {status_txt}.{suffix} DEEP registry "
                f"not auto-updated ({baked_msg}). Run "
                "apps/deep/scripts/bake_library_into_deep.py, then commit "
                "apps/library and apps/deep/data.",
                type="warning",
                duration=12,
            )

    # ── desktop-only: stage the current draft and hand it to DEEP (?handoff=) ──
    @reactive.effect
    @reactive.event(input.draft_to_deep)
    def _prepare_draft_handoff():
        try:
            bundle = ap.build_bundle_from_state(state)
        except ValueError as e:  # no finalized curves yet
            ui.notification_show(str(e), type="warning", duration=8)
            return
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not build the draft: {e}", type="error", duration=8)
            return
        handoff_dir = Path(tempfile.gettempdir()) / "staf-handoff"
        try:
            handoff_dir.mkdir(parents=True, exist_ok=True)
            path = handoff_dir / "draft.deep.json"
            write_deep_assessment_bundle(bundle, path)
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not stage the draft: {e}", type="error", duration=8)
            return
        deep_base = (STAF_LINKS.get("deep") or "").rstrip("/")
        draft_handoff_url.set(f"{deep_base}/?handoff={quote(str(path))}")
        ui.notification_show(
            "Draft staged. Click 'Open draft in DEEP' to load it.", type="message", duration=6
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def draft_deep_link():
        url = draft_handoff_url()
        if not url:
            return None
        return ui.tags.a(
            ui.TagList(bi("arrow-right-circle"), " Open draft in DEEP"),
            href=url,
            target="_blank",
            rel="noopener",
            class_="btn btn-primary btn-sm d-inline-block mt-2",
        )
