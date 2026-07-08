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
        class_="mt-3",
    )


@module.server
def library_server(input, output, session, state: AppState):
    refresh = reactive.value(0)
    draft_handoff_url = reactive.value(None)  # set after a desktop draft is staged

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

        region = ap.region_from_state(state)
        name = input.pub_name() or aid
        meta = {
            "assessmentName": name,
            "region": region,
            "sourceCitation": input.pub_citation() or ap.DEFAULT_SOURCE_CITATION,
            "author": input.pub_author() or "",
            "revisionNotes": input.pub_notes() or "",
        }
        if region and region.get("kind") == "state":
            meta["stateCode"] = region.get("code") or ""
            meta["stateName"] = region.get("name") or ""

        try:
            bundle = ap.build_bundle_from_state(
                state,
                meta={"assessmentName": name, "sourceCitation": meta["sourceCitation"]},
            )
            payload = ap.session_payload_from_state(state)
            version = lib.publish_version(aid, meta, payload, bundle)
        except Exception as e:  # noqa: BLE001
            logger.exception("library publish failed")
            ui.notification_show(f"Publish failed: {e}", type="error", duration=10)
            return

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
