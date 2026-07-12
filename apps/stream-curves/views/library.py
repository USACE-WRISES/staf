"""Library tab — browse the shared STAF assessment library, open a completed
assessment to keep working on it, and publish/update a version.

See apps/library/README.md for the on-disk format. Reading the catalog works
anywhere the folder is reachable; publishing is a local/desktop action (writable
folder) and degrades to "save the session and send it to the publisher" on the web.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from shiny import module, reactive, render, ui

from streamcurves import library as lib
from streamcurves import packaging, redaction
from streamcurves import run_state as rs
from streamcurves.deep_export import write_deep_assessment_bundle
from views import assessment_publish as ap
from views.state import AppState
from views.theme import STAF_LINKS, bi
from views.uihelpers import explanation_card

logger = logging.getLogger("streamcurves")

_NEW = "__new__"


def _is_desktop() -> bool:
    """The desktop shell injects STAF_LINKS_OVERRIDES (cross-app links become
    staf-desktop:// URIs). Absent on the web deploys."""
    return bool(os.environ.get("STAF_LINKS_OVERRIDES"))


def _region_label(region: dict | None) -> str:
    if not region or region.get("kind") == "none":
        return "No region set"
    kind = region.get("kind")
    name = region.get("name") or region.get("code") or ""
    if kind == "ecoregion":
        return f"{name} (L3 {region.get('code')})"
    if kind == "state":
        return f"{name} (state)"
    if kind == "polygon":
        return "Custom drawn area"
    return str(name)


@module.ui
def library_ui():
    return ui.div(
        explanation_card(
            "STAF Assessment Library",
            ui.tags.p(
                "The shared, versioned home for completed detailed STAF assessments. "
                "DEEP runs the latest version of each; older versions stay here for "
                "reference.",
                class_="mb-2",
            ),
            ui.tags.p(ui.tags.strong("From a session to a completed assessment:"), class_="mb-1"),
            ui.tags.ol(
                ui.tags.li(
                    ui.tags.strong("Workbook"),
                    " (.xlsx): your inputs and setup only. Save it from Data & Setup.",
                ),
                ui.tags.li(
                    ui.tags.strong("Session"),
                    " (.streamcurves.json): your full working state. Save it from Data & "
                    "Setup and share it with the publisher.",
                ),
                ui.tags.li(
                    ui.tags.strong("Published assessment"),
                    ": a session promoted into this library with a region and a version. "
                    "DEEP scores against it.",
                ),
                class_="mb-0",
            ),
        ),
        ui.output_ui("lib_available"),
        ui.card(
            ui.card_header(
                ui.TagList(bi("folder2-open"), " Open an assessment to keep working")
            ),
            ui.card_body(
                ui.p(
                    "Loading a version restores its full session so you can modify it and "
                    "publish an update. This replaces whatever is currently open.",
                    class_="text-muted small",
                ),
                ui.input_select("open_assessment", "Assessment", choices={}),
                ui.input_select("open_version", "Version", choices={}),
                ui.input_action_button(
                    "open_btn",
                    ui.TagList(bi("folder2-open"), " Open this version"),
                    class_="btn btn-primary",
                ),
            ),
            class_="mb-3",
        ),
        ui.output_ui("lib_publish"),
        ui.output_ui("lib_governance"),
        class_="mt-3",
    )


@module.server
def library_server(input, output, session, state: AppState):
    refresh = reactive.value(0)
    draft_handoff_url = reactive.value(None)  # set after a desktop draft is staged
    restricted_pkg = reactive.value(None)     # {bytes, filename, sha256} after a publish

    def _assessments() -> list[dict]:
        refresh()
        try:
            return lib.list_assessments()
        except Exception:  # noqa: BLE001
            logger.exception("library: reading catalog failed")
            return []

    # ── available assessments (read-only list) ────────────────────────────────
    # suspend_when_hidden=False: this lives in a nav panel that may be hidden at
    # first render; without it the output never resumes when the tab is shown
    # (matches the pattern in views/regional_curve.py).
    @output(suspend_when_hidden=False)
    @render.ui
    def lib_available():
        items = _assessments()
        if not items:
            return ui.div(
                bi("info-circle"),
                " No assessments in the library yet. Publish one below.",
                class_="alert alert-secondary",
            )
        rows = []
        for a in items:
            latest = int(a.get("latestVersion") or 0)
            if latest > 0:
                badge = ui.tags.span(
                    f"v{latest}", class_="badge bg-primary"
                )
                updated = a.get("latestUpdatedAt") or ""
                sub = f"Latest v{latest}" + (f" - updated {updated}" if updated else "")
            else:
                badge = ui.tags.span("no versions", class_="badge bg-secondary")
                sub = "No versions published yet"
            actions = None
            if latest > 0:
                deep_base = (STAF_LINKS.get("deep") or "").rstrip("/")
                deep_url = f"{deep_base}/?assessment={a.get('assessmentId')}"
                actions = ui.div(
                    ui.tags.a(
                        ui.TagList(bi("arrow-right-circle"), " Open latest in DEEP"),
                        href=deep_url,
                        target="_blank",
                        rel="noopener",
                        class_="btn btn-sm btn-outline-primary",
                    ),
                    class_="mt-1",
                )
            rows.append(
                ui.div(
                    ui.div(
                        ui.tags.strong(a.get("assessmentName") or a.get("assessmentId")),
                        " ",
                        badge,
                        ui.div(_region_label(a.get("region")), class_="text-muted small"),
                        ui.div(sub, class_="text-muted small"),
                    ),
                    actions,
                    class_="list-group-item",
                )
            )
        return ui.card(
            ui.card_header(ui.TagList(bi("clipboard2-data"), " Available assessments")),
            ui.div(*rows, class_="list-group list-group-flush"),
            class_="mb-3",
        )

    # ── populate the open selects from the catalog ────────────────────────────
    @reactive.effect
    def _fill_open_assessment():
        items = [a for a in _assessments() if int(a.get("latestVersion") or 0) > 0]
        choices = {
            a["assessmentId"]: f'{a.get("assessmentName") or a["assessmentId"]} (v{a["latestVersion"]})'
            for a in items
        }
        ui.update_select(
            "open_assessment", choices=choices or {"": "No published assessments yet"}
        )

    @reactive.effect
    def _fill_open_version():
        refresh()
        aid = input.open_assessment()
        if not aid:
            ui.update_select("open_version", choices={})
            return
        manifest = lib.read_manifest(aid) or {}
        latest = int(manifest.get("latestVersion") or 0)
        versions = sorted(
            (int(v.get("version") or 0) for v in (manifest.get("versions") or [])),
            reverse=True,
        )
        choices = {
            str(v): (f"v{v}" + (" (latest)" if v == latest else "")) for v in versions
        }
        ui.update_select(
            "open_version", choices=choices, selected=str(latest) if latest else None
        )

    @reactive.effect
    @reactive.event(input.open_btn)
    def _open():
        aid = input.open_assessment()
        ver = input.open_version()
        if not aid or not ver:
            ui.notification_show(
                "Pick an assessment and version to open.", type="warning", duration=5
            )
            return
        try:
            payload = lib.load_version_session(aid, int(ver))
        except Exception as e:  # noqa: BLE001
            ui.notification_show(
                f"Could not open {aid} v{ver}: {e}", type="error", duration=8
            )
            return
        manifest = lib.read_manifest(aid) or {}
        name = manifest.get("assessmentName") or aid
        state.session_restore_request.set(
            {"payload": payload, "source_name": f"{name} v{ver}"}
        )
        with reactive.isolate():
            state.session_restore_nonce.set((state.session_restore_nonce() or 0) + 1)

    # ── publish form (writable) or share-with-publisher notice (read-only) ────
    @output(suspend_when_hidden=False)
    @render.ui
    def lib_publish():
        refresh()
        if not lib.writable():
            return ui.card(
                ui.card_header(ui.TagList(bi("file-earmark-arrow-up"), " Publish a completed assessment")),
                ui.card_body(
                    ui.div(
                        bi("info-circle"),
                        " Publishing writes to the version-controlled library, which is a "
                        "local or desktop action. On the hosted app, save your session "
                        "(Data & Setup, then Save) and send the file to whoever maintains "
                        "the library.",
                        class_="alert alert-info mb-0",
                    ),
                ),
                class_="mb-3",
            )

        with reactive.isolate():
            loaded = bool(state.app_data_loaded())
            session_name = state.session_name() or ""
            region = state.region_of_applicability()
        existing = {
            a["assessmentId"]: a.get("assessmentName") or a["assessmentId"]
            for a in _assessments()
        }
        target_choices = dict(existing)
        target_choices[_NEW] = "New assessment..."

        body = [
            ui.p(
                "Promote the currently open session into a new library version. Requires a "
                "confirmed Discipline to Function mapping and at least one finalized curve.",
                class_="text-muted small",
            ),
            ui.input_select(
                "pub_assessment",
                "Update an existing assessment, or create a new one",
                choices=target_choices,
                selected=_NEW,
            ),
            ui.input_text(
                "pub_new_id",
                "New assessment id (only when creating new; letters, numbers, hyphens)",
                value="",
                placeholder="eastern-corn-belt-plains",
            ),
            ui.input_text("pub_name", "Assessment name", value=session_name),
            ui.div(
                ui.tags.label("Region of applicability", class_="form-label mb-0"),
                ui.div(_region_label(region), class_="text-muted small"),
                ui.div(
                    "Set in the Data & Setup import wizard; it travels with the assessment.",
                    class_="text-muted",
                    style="font-size:0.72rem;",
                ),
                class_="mb-2",
            ),
            ui.input_text(
                "pub_citation", "Source citation", value=ap.DEFAULT_SOURCE_CITATION
            ),
            ui.input_text("pub_author", "Author (optional)", value=""),
            ui.input_text_area(
                "pub_notes", "Revision notes (what changed in this version)", value="", rows=2
            ),
            ui.input_text(
                "pub_maintainer",
                "Maintainer name (for the canonical publish audit trail)",
                value=os.environ.get("STAF_LIBRARY_MAINTAINER", ""),
            ),
        ]
        # writable() is already True in this branch, so a non-None reason here means
        # canonical publishing is off (the STAF_LIBRARY_PUBLISH flag is not set). Publish
        # still runs the local flow, but it will not mutate the shared library.
        canonical_off = lib.publish_gate_reason("_probe_")
        if canonical_off:
            body.append(
                ui.div(
                    bi("lock"),
                    " ",
                    canonical_off,
                    class_="alert alert-secondary mt-2 mb-0 small",
                )
            )
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
                        disabled=None if loaded else "disabled",
                    ),
                    ui.output_ui("draft_deep_link"),
                    ui.hr(class_="mt-2 mb-2"),
                    class_="mt-2",
                )
            )
        body.append(
            ui.input_action_button(
                "publish_btn",
                ui.TagList(bi("file-earmark-arrow-up"), " Publish new version"),
                class_="btn btn-success",
                disabled=None if loaded else "disabled",
            )
        )
        body.append(ui.output_ui("restricted_download"))
        if not loaded:
            body.append(
                ui.div(
                    bi("info-circle"),
                    " Load or build a session first.",
                    class_="alert alert-warning mt-2 mb-0",
                )
            )
        return ui.card(
            ui.card_header(ui.TagList(bi("file-earmark-arrow-up"), " Publish a completed assessment")),
            ui.card_body(*body),
            class_="mb-3",
        )

    @reactive.effect
    @reactive.event(input.publish_btn)
    def _publish():
        if not lib.writable():
            ui.notification_show(
                "The library is read-only here. Save the session and send it to the "
                "publisher.",
                type="warning",
                duration=8,
            )
            return

        target = input.pub_assessment()
        if target == _NEW:
            aid = lib.slugify(input.pub_new_id() or input.pub_name() or "")
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
                "Confirm the Discipline to Function to Metric mapping on the Reference "
                "Curves page before publishing.",
                type="warning",
                duration=8,
            )
            return

        # Canonical-publish gate: STAF_LIBRARY_PUBLISH=1 + writable + maintainer name.
        maintainer = (input.pub_maintainer() or "").strip()
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

        # Guided readiness gate: only enforced once a guided run exists (a populated
        # curve_review). The Advanced path (confirmed mapping + finalized curves, no
        # guided run) publishes on the mapping/curve checks already made above.
        snap = ap.run_snapshot(state)
        if snap.get("curve_review") and not rs.is_ready_to_publish(snap):
            unresolved = rs.flagged_metrics(snap.get("curve_review") or {})
            ui.notification_show(
                "Finish the guided checklist before publishing: "
                + (f"{len(unresolved)} flagged curve(s) still need review."
                   if unresolved else "region, retained sites, enrichment, and in-scope "
                   "curves must all be complete."),
                type="warning", duration=10)
            return

        try:
            bundle = ap.build_bundle_from_state(
                state,
                meta={"assessmentName": name, "sourceCitation": meta["sourceCitation"]},
            )
            full_payload = ap.session_payload_from_state(state)
            # Restricted (full-detail) package for access-controlled distribution.
            with reactive.isolate():
                validation = list(state.validation_records() or [])
            zip_bytes, sha256, summary = packaging.build_restricted_package(
                full_payload, bundle, validation_records=validation)
            # Redact the public session and prove the redaction held before writing it.
            redacted, _report = redaction.redact_session_payload(full_payload)
            violations = redaction.redaction_violations(redacted)
            if violations:
                ui.notification_show(
                    "Publish blocked: the public session still contains identity data ("
                    + "; ".join(violations[:3]) + "). Nothing was written.",
                    type="error", duration=12)
                return
            version = lib.publish_version(
                aid, meta, redacted, bundle,
                restricted_package={"sha256": sha256, "summary": summary})
        except Exception as e:  # noqa: BLE001
            logger.exception("library publish failed")
            ui.notification_show(f"Publish failed: {e}", type="error", duration=10)
            return

        # Stash the restricted ZIP for download and mark the run published.
        restricted_pkg.set({
            "bytes": zip_bytes,
            "filename": f"{aid}-v{version}-restricted.zip",
            "sha256": sha256,
        })
        with reactive.isolate():
            stage_status = dict(state.run_stage_status() or {})
        stage_status["publish"] = {"status": "done",
                                   "label": f"Published {name} v{version}."}
        state.run_stage_status.set(stage_status)

        refresh.set(refresh() + 1)

        # Fold the new latest into DEEP's baked registry so the cloud DEEP ships it.
        baked_ok, baked_msg = lib.rebake_deep()
        if baked_ok:
            ui.notification_show(
                f"Published {name} as v{version}, and updated DEEP's registry. Commit "
                "apps/library and apps/deep/data, then redeploy DEEP.",
                type="message",
                duration=10,
            )
        else:
            ui.notification_show(
                f"Published {name} as v{version}. DEEP registry not auto-updated "
                f"({baked_msg}). Run apps/deep/scripts/bake_library_into_deep.py, then "
                "commit apps/library and apps/deep/data.",
                type="warning",
                duration=12,
            )

    @output(suspend_when_hidden=False)
    @render.ui
    def restricted_download():
        pkg = restricted_pkg()
        if not pkg:
            return None
        return ui.div(
            ui.hr(class_="my-2"),
            ui.div(
                bi("lock"),
                f" Restricted full-detail package ready (sha256 {pkg['sha256'][:12]}...). "
                "Distribute it only under access control; it holds coordinates and identities.",
                class_="text-muted small mb-1"),
            ui.download_button(
                "dl_restricted",
                ui.TagList(bi("file-earmark-arrow-up"), " Download restricted package"),
                class_="btn btn-outline-secondary btn-sm"),
        )

    @render.download(
        filename=lambda: (restricted_pkg() or {}).get("filename") or "restricted.zip")
    def dl_restricted():
        pkg = restricted_pkg()
        if pkg:
            yield pkg["bytes"]

    # ── Lifecycle & governance (validation records + certification) ───────────
    @output(suspend_when_hidden=False)
    @render.ui
    def lib_governance():
        refresh()
        if not lib.writable():
            return None
        items = [a for a in _assessments() if int(a.get("latestVersion") or 0) > 0]
        if not items:
            return None
        choices = {a["assessmentId"]: a.get("assessmentName") or a["assessmentId"]
                   for a in items}
        return ui.card(
            ui.card_header(ui.TagList(bi("ui-checks"), " Lifecycle & governance")),
            ui.card_body(
                ui.p("Attach independent-check evidence, mark a version validated, then "
                     "certify it for DEEP. Certification requires a completed EcoPCX review.",
                     class_="text-muted small"),
                ui.div(
                    ui.div(ui.input_select("gov_assessment", "Assessment", choices=choices),
                           class_="col-sm-8"),
                    ui.div(ui.input_select("gov_version", "Version", choices={}),
                           class_="col-sm-4"),
                    class_="row g-2"),
                ui.output_ui("gov_status"),
                ui.div(
                    ui.input_action_button("gov_add_record", "Add validation record",
                                           class_="btn btn-sm btn-outline-primary"),
                    ui.input_action_button("gov_mark_validated", "Mark validated",
                                           class_="btn btn-sm btn-outline-success ms-1"),
                    ui.input_action_button("gov_retire", "Retire",
                                           class_="btn btn-sm btn-outline-secondary ms-1"),
                    class_="mt-2"),
                ui.hr(class_="my-2"),
                ui.input_checkbox("gov_ecopcx",
                                  "EcoPCX independent review is complete", value=False),
                ui.input_text("gov_actor", "Maintainer name (audit trail)",
                              value=os.environ.get("STAF_LIBRARY_MAINTAINER", "")),
                ui.input_action_button("gov_certify", "Certify version",
                                       class_="btn btn-sm btn-success mt-1"),
            ),
            class_="mb-3",
        )

    @reactive.effect
    def _fill_gov_version():
        refresh()
        try:
            aid = input.gov_assessment()
        except Exception:  # noqa: BLE001 — the select may not be mounted yet
            return
        if not aid:
            return
        manifest = lib.read_manifest(aid) or {}
        versions = sorted((int(v.get("version") or 0)
                           for v in (manifest.get("versions") or [])), reverse=True)
        ui.update_select("gov_version",
                         choices={str(v): f"v{v}" for v in versions},
                         selected=str(versions[0]) if versions else None)

    def _gov_target() -> tuple[str, int] | None:
        try:
            aid = input.gov_assessment()
            ver = int(input.gov_version())
        except Exception:  # noqa: BLE001
            return None
        if not aid or not ver:
            return None
        return aid, ver

    @output(suspend_when_hidden=False)
    @render.ui
    def gov_status():
        refresh()
        tgt = _gov_target()
        if not tgt:
            return None
        aid, ver = tgt
        status = lib.version_status(aid, ver)
        vstate = lib.version_validation_state(aid, ver)
        n_records = len(lib.read_validation(aid).get("records") or [])
        status_cls = {"certified": "bg-success", "preliminary": "bg-primary",
                      "retired": "bg-secondary"}.get(status, "bg-info text-dark")
        val_cls = "bg-success" if vstate == "validated" else "bg-secondary"
        return ui.div(
            ui.tags.span(f"status: {status}", class_=f"badge {status_cls}"),
            ui.tags.span(f"validation: {vstate}", class_=f"badge {val_cls} ms-1"),
            ui.tags.span(f"{n_records} record(s)", class_="badge bg-light text-dark ms-1"),
            class_="mt-2")

    @reactive.effect
    @reactive.event(input.gov_add_record)
    def _gov_add_record():
        tgt = _gov_target()
        if not tgt:
            return
        ui.modal_show(ui.modal(
            ui.input_text("gov_rec_method", "Check method",
                          placeholder="independent recompute / field re-measure"),
            ui.input_text("gov_rec_checker", "Checker",
                          placeholder="name or organization"),
            ui.input_select("gov_rec_outcome", "Outcome",
                            choices={"match": "Matches", "minor": "Minor differences",
                                     "major": "Major differences"}),
            ui.input_text_area("gov_rec_note", "Note (aggregate only, no site data)",
                               width="100%"),
            title="Add validation record", easy_close=True,
            footer=ui.TagList(
                ui.modal_button("Cancel"),
                ui.input_action_button("gov_rec_save", "Save record",
                                       class_="btn btn-primary"))))

    @reactive.effect
    @reactive.event(input.gov_rec_save)
    def _gov_rec_save():
        tgt = _gov_target()
        if not tgt:
            return
        aid, ver = tgt
        actor = (input.gov_actor() or "").strip() or (input.gov_rec_checker() or "").strip()
        if not actor:
            ui.notification_show("Enter a checker or maintainer name.", type="warning",
                                 duration=5)
            return
        try:
            lib.add_validation_record(aid, ver, {
                "method": input.gov_rec_method() or "",
                "checker": input.gov_rec_checker() or "",
                "outcome": input.gov_rec_outcome() or "",
            }, actor=actor, note=input.gov_rec_note() or None)
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not add record: {e}", type="error", duration=8)
            return
        ui.modal_remove()
        refresh.set(refresh() + 1)
        ui.notification_show("Validation record added.", type="message", duration=4)

    @reactive.effect
    @reactive.event(input.gov_mark_validated)
    def _gov_mark_validated():
        tgt = _gov_target()
        if not tgt:
            return
        aid, ver = tgt
        actor = (input.gov_actor() or "").strip()
        if not actor:
            ui.notification_show("Enter a maintainer name first.", type="warning", duration=5)
            return
        n_records = len(lib._validation_records_for(aid, ver))
        try:
            lib.set_version_validation(aid, ver, "validated",
                                       {"n_records": n_records}, actor)
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Cannot mark validated: {e}", type="warning", duration=8)
            return
        refresh.set(refresh() + 1)
        ui.notification_show(f"{aid} v{ver} marked validated.", type="message", duration=5)

    @reactive.effect
    @reactive.event(input.gov_certify)
    def _gov_certify():
        tgt = _gov_target()
        if not tgt:
            return
        aid, ver = tgt
        actor = (input.gov_actor() or "").strip()
        if not actor:
            ui.notification_show("Enter a maintainer name first.", type="warning", duration=5)
            return
        if lib.version_validation_state(aid, ver) != "validated":
            ui.notification_show("Mark the version validated before certifying.",
                                 type="warning", duration=6)
            return
        if not input.gov_ecopcx():
            ui.notification_show("Confirm the EcoPCX review is complete before certifying.",
                                 type="warning", duration=6)
            return
        gate = lib.publish_gate_reason(actor)
        if gate:
            ui.notification_show(gate, type="warning", duration=10)
            return
        try:
            lib.set_version_status(aid, ver, "certified", actor,
                                   note="EcoPCX complete; validated.")
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Certify failed: {e}", type="error", duration=8)
            return
        baked_ok, baked_msg = lib.rebake_deep()
        refresh.set(refresh() + 1)
        ui.notification_show(
            f"Certified {aid} v{ver}."
            + (" DEEP registry updated." if baked_ok else f" Rebake DEEP manually ({baked_msg})."),
            type="message", duration=10)

    @reactive.effect
    @reactive.event(input.gov_retire)
    def _gov_retire():
        tgt = _gov_target()
        if not tgt:
            return
        aid, ver = tgt
        actor = (input.gov_actor() or "").strip()
        if not actor:
            ui.notification_show("Enter a maintainer name first.", type="warning", duration=5)
            return
        try:
            lib.set_version_status(aid, ver, "retired", actor, note="Retired by maintainer.")
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Retire failed: {e}", type="error", duration=8)
            return
        refresh.set(refresh() + 1)
        ui.notification_show(f"Retired {aid} v{ver}.", type="message", duration=5)

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
