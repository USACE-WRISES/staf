"""Projects: the start page, New / Open / Save / Save As, recent projects, autosave, the assessment
gallery, and the Project, About, Help and What's new dialogs. After HYPE Desktop's app.py start
page (_show_welcome and its handlers), which this follows rule for rule:

* the start page is ONE modal with three columns (rail: New / Open / Assessment library;
  center: recent projects or the gallery; right: What's new or the selected assessment) and
  two views, home and gallery;
* it is a HARD gate while no project is open (no title, no footer, no Esc), opened off the
  client's ready ping (www/shell.js); Projects in the header raises it mid-session, and then
  it carries a close button;
* recents are re-read on every show, and rows fire positional nonce events (indices only,
  never paths, in inline JS: the backslash-escape trap);
* every cancel funnels back through _ensure_start, so a project-less session is never left
  without a way forward.

A project is a folder with one `<Name>.streamcurves` (streamcurves.project_file). It saves as
you work: a change to any session field marks it dirty, and after a quiet spell with no job
running a detached task encodes and writes it in a worker thread. Save saves now; Save As
writes a copy and moves to it; switching projects and closing the session save first. There
is no dirty flag and no "unsaved changes" prompt.

Long work never runs inside an effect: py-shiny's flush awaits async effects, so awaiting a
file read (or a flush) there would freeze or wedge the session. Effects launch detached tasks
(_run), and the tasks push their UI changes with st.task_flush(), as the wizard's compile does.

File pickers: the desktop shell's native dialogs (www/desktop_bridge.js); without the shell,
a child-process tk dialog (streamcurves.pick_run); failing that, or with
STREAMCURVES_PICKER=modal, a typed-path dialog.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from shiny import reactive, render, ui

from streamcurves import changelog
from streamcurves import desktop_env
from streamcurves import gallery
from streamcurves import library as lib
from streamcurves import pathpick
from streamcurves import pick_run
from streamcurves import prefs
from streamcurves import project_file as pfile
from streamcurves import project_meta as pmeta
from streamcurves import recents
from streamcurves import region_art
from streamcurves import region_build as rb
from streamcurves import session_io as sio
from streamcurves import workspace as ws
from streamcurves.easi_method import io as eio
from streamcurves.easi_method import stages as easi_stages
from streamcurves.easi_method.model import EasiProject
from streamcurves.version import (APP_NAME, APP_SUBTITLE, APP_TAGLINE, APP_VERSION_LABEL,
                                  ISSUES_URL, REPO_URL)
from views import assessment_publish as ap
from views import data_overview as dov
from views import easi_page as ep
from views import state as st
from views.help import app_help_content
from views.state import AppState
from views.theme import STAF_LINKS, versioned_www_asset

logger = logging.getLogger("streamcurves")

# The start page's geometry, injected inline with its modal so it applies to that dialog alone
# (.modal-dialog / .modal-body are shared Bootstrap classes). Three columns need the width; the
# fixed height lets each column scroll on its own.
_START_MODAL_CSS = (
    "#shiny-modal .modal-dialog{max-width:min(1180px,94vw);width:94vw;margin:1rem auto}"
    "#shiny-modal .modal-content{height:min(720px,calc(100vh - 2rem));"
    "max-height:calc(100vh - 2rem);overflow:hidden;display:flex;flex-direction:column;"
    "border-radius:12px;border:0}"
    "#shiny-modal .modal-body{flex:1 1 auto;min-height:0;padding:0;overflow:hidden;display:flex}")

# A tall prose dialog (Help, What's new): capped at the window, the body scrolls.
_TALL_MODAL_CSS = (
    "#shiny-modal .modal-content{max-height:calc(100vh - 2rem);display:flex;"
    "flex-direction:column}"
    "#shiny-modal .modal-body{flex:1 1 auto;min-height:0;overflow-y:auto}")

_EMPTY_ART = region_art.placeholder_svg(200, 110)

#: Seconds of quiet (no state change, no job running) before an autosave writes.
AUTOSAVE_IDLE_S = 3.0

_STATUS_CHIPS = (("all", "All"), ("draft", "Draft"), ("preliminary", "Preliminary"),
                 ("certified", "Final"))


def nonce_js(evt_id: str, **fields) -> str:
    """Inline onclick that posts a nonce'd event input with optional integer fields. Only
    indices ever ride in here, never paths or names (the backslash-escape trap)."""
    extra = "".join(f"{k}: {int(v)}, " for k, v in fields.items())
    return (f"Shiny.setInputValue('{evt_id}', {{{extra}n: Date.now() + Math.random()}}, "
            "{priority: 'event'})")


def nonce_button(evt_id: str, label, cls: str = "btn btn-primary", **attrs):
    """A button that posts a nonce event: every click is an event, including the first after
    its dialog is rebuilt (a rebuilt action button restarts its counter, and a reactive.event
    on it misses the next click)."""
    return ui.tags.button(label, type="button", class_=cls, onclick=nonce_js(evt_id), **attrs)


def when(iso: str) -> str:
    """"just now", "12 min ago", "5 h ago", "3 days ago", else the date."""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = (datetime.now(timezone.utc) - dt).total_seconds()
    except (TypeError, ValueError):
        return ""
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 172800:
        return f"{int(secs // 3600)} h ago"
    if secs < 86400 * 14:
        return f"{int(secs // 86400)} days ago"
    return dt.astimezone().strftime("%b %d, %Y").replace(" 0", " ")


def recent_groups(items: list[dict]) -> list[tuple[str, list[tuple[int, dict]]]]:
    """Today / Last 7 days / Older below the featured card, carrying each item's index in the
    snapshot so the rows keep firing positional events (HEC-RAS 2025's buckets)."""
    now = datetime.now(timezone.utc)
    today = now.astimezone().date()
    buckets: dict[str, list[tuple[int, dict]]] = {"Today": [], "Last 7 days": [], "Older": []}
    for i, it in enumerate(items):
        if i == 0:
            continue                      # the featured card
        key = "Older"
        try:
            dt = datetime.fromisoformat(it["last_opened"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.astimezone().date() == today:
                key = "Today"
            elif (now - dt).total_seconds() < 7 * 86400:
                key = "Last 7 days"
        except (KeyError, TypeError, ValueError):
            pass
        buckets[key].append((i, it))
    return [(k, v) for k, v in buckets.items() if v]


_REGION_CACHE: dict[tuple[str, int], dict | None] = {}


def project_region(path: str) -> dict | None:
    """The region brief a project file records (reads project.json only; cached by mtime)."""
    try:
        mtime = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    key = (os.path.normcase(path), mtime)
    if key not in _REGION_CACHE:
        try:
            with zipfile.ZipFile(path) as zf:
                doc = json.loads(zf.read(pfile.PROJECT_JSON).decode("utf-8"))
            _REGION_CACHE[key] = ((doc.get("region") or (doc.get("origin") or {}).get("region"))
                                  if isinstance(doc, dict) else None)
        except Exception:  # noqa: BLE001 - a picture is never worth an error
            _REGION_CACHE[key] = None
    return _REGION_CACHE[key]


def reveal_path(p: Path, *, select: bool) -> None:
    """Show a file in Explorer (selected) or open a folder."""
    try:
        if os.name == "nt":
            if select:
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                os.startfile(str(p))  # noqa: S606 - a folder the user owns
        else:
            subprocess.Popen(["xdg-open", str(p.parent if select else p)])
    except Exception:  # noqa: BLE001
        logger.warning("could not reveal %s", p, exc_info=True)


def _region_brief(region: dict | None) -> dict | None:
    if not isinstance(region, dict):
        return None
    brief = {k: region.get(k) for k in ("kind", "code", "name") if region.get(k)}
    return brief or None


# --------------------------------------------------------------------------- #
# Static UI pieces app.py places in the header and over the page
# --------------------------------------------------------------------------- #
def header_left_ui():
    """Brand, version (the What's new door) and the open project's name."""
    return ui.div(
        ui.span(ui.tags.img(src=versioned_www_asset("streamcurves-glyph-white.svg"), alt="",
                            class_="sc-brand-mark"),
                APP_NAME, ui.tags.small(APP_SUBTITLE),
                class_="sc-brand", title=f"{APP_NAME}: {APP_TAGLINE}"),
        ui.span(APP_VERSION_LABEL, class_="sc-version-chip", title="What's new in StreamCurves",
                onclick=nonce_js("whatsnew_evt")),
        ui.output_ui("project_badge", inline=True),
        class_="sc-header-left")


def header_nav_ui():
    """Projects (the one door to the start page), Save, Save As..., About, Help."""
    return ui.div(
        ui.input_action_link("nav_projects", "Projects",
                             title="New, open, recent and library projects"),
        ui.output_ui("save_links", inline=True),
        ui.tags.span(class_="sc-nav-sep"),
        ui.input_action_link("nav_about", "About"),
        ui.input_action_link("nav_help", "Help"),
        class_="sc-nav")


def boot_veil_ui():
    """Covers the page from the first byte until the session answers (www/shell.js)."""
    return ui.div(
        ui.div(ui.tags.img(src=versioned_www_asset("streamcurves-icon.svg"), alt="",
                           class_="sc-boot-icon"),
               ui.span(APP_NAME, class_="sc-boot-mark"),
               ui.div(APP_TAGLINE, class_="sc-boot-sub"),
               ui.div(class_="sc-boot-spin"),
               ui.div("Loading StreamCurves", class_="sc-boot-msg"),
               class_="sc-boot-card"),
        id="sc-boot", class_="sc-boot")


# --------------------------------------------------------------------------- #
# The controller
# --------------------------------------------------------------------------- #
def project_server(input, output, session, state: AppState):
    """Root-level (not a module): the header's ids, the start page's nonce events and the
    shell bridge's inputs are all page-level."""

    # ── plumbing ─────────────────────────────────────────────────────────────
    _origin = {"files": {}}                     # the open project's origin/ files, re-saved
    _dirty = {"gen": 0, "saved": 0, "last": 0.0}
    _restoring = {"on": False}
    _save_task: dict = {"t": None}
    _write_lock = threading.Lock()
    _tasks: set = set()

    def _launch(coro):
        t = asyncio.create_task(coro)
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
        return t

    def _run(fn, *args, **kwargs):
        """Run a coroutine function as a detached task; it pushes its UI changes itself, and
        whatever happens, the page gets a flush at the end."""
        async def wrapper():
            try:
                await fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - a task must never die silently
                logger.exception("project action failed")
                ui.notification_show(f"Something went wrong: {e}", type="error", duration=10)
            finally:
                await st.task_flush()
        return _launch(wrapper())

    def _get(value):
        with reactive.isolate():
            return value()

    def _has_project() -> bool:
        return _get(state.project_meta) is not None

    def _gated() -> bool:
        return not _has_project()

    def _has_shell() -> bool:
        try:
            with reactive.isolate():
                v = input.desktop_shell()
            return bool((v or {}).get("present"))
        except Exception:  # noqa: BLE001 - never set in a plain browser
            return False

    def _project_name() -> str:
        with reactive.isolate():
            meta = state.project_meta() or {}
            name = meta.get("project_name") or state.session_name()
        return str(name or "Untitled project")

    def _request_nav(value: str, *, wizard_step: int | None = None):
        with reactive.isolate():
            state.nav_request.set(value)
            state.nav_request_nonce.set((state.nav_request_nonce() or 0) + 1)
            if wizard_step is not None:
                state.wizard_step_request.set(int(wizard_step))
                state.wizard_step_nonce.set((state.wizard_step_nonce() or 0) + 1)

    # ── the shell: title and project folder ──────────────────────────────────
    async def _post_shell(msg: dict):
        if _has_shell():
            try:
                await session.send_custom_message("streamcurves_desktop", msg)
            except Exception:  # noqa: BLE001
                pass

    async def _push_title():
        with reactive.isolate():
            path = state.project_file()
            has = state.project_meta() is not None
        name = _project_name() if has else ""
        try:
            await session.send_custom_message("sc_title", {"title": name})
        except Exception:  # noqa: BLE001
            pass
        await _post_shell({"type": "setTitle", "title": name})
        if path:
            await _post_shell({"type": "setProjectFolder", "path": str(Path(path).parent)})

    # ── header outputs ───────────────────────────────────────────────────────
    @render.ui
    def project_badge():
        meta = state.project_meta()
        path = state.project_file()
        if meta is None:
            return None
        name = meta.get("project_name") or state.session_name() or "Untitled project"
        tip = str(path) if path else "Not saved yet: use Save As to keep this project"
        return ui.TagList(
            ui.tags.span(class_="sc-nav-sep"),
            ui.tags.span(name, class_="sc-project-name" + ("" if path else " is-unsaved"),
                         title=tip, onclick=nonce_js("project_props_evt")))

    @render.ui
    def save_links():
        if state.project_meta() is None:
            return ui.tags.span("Save", class_="sc-nav-dim")
        # nonce links, not action links: this output re-renders, and a rebuilt action link
        # restarts its counter and misses the next click
        return ui.TagList(
            ui.tags.a("Save", href="#", onclick=nonce_js("nav_save") + "; return false;"),
            ui.tags.a("Save As…", href="#",
                      onclick=nonce_js("nav_save_as") + "; return false;"))

    # ── autosave ─────────────────────────────────────────────────────────────
    @reactive.effect
    def _track_changes():
        for name in sio.SESSION_FIELDS:
            state.get(name)
        state.wizard_rev()
        state.easi_project()
        with reactive.isolate():
            if _restoring["on"] or state.project_file() is None:
                return
        _dirty["gen"] += 1
        _dirty["last"] = time.monotonic()
        _ensure_save_loop()

    def _mark_saved():
        _dirty["saved"] = _dirty["gen"]

    def _touch_dirty():
        _dirty["gen"] += 1
        _dirty["last"] = 0.0

    def _is_dirty() -> bool:
        return _dirty["gen"] != _dirty["saved"]

    def _snapshot() -> dict:
        """What a save writes, read on the event loop: path, meta, origin files, and either
        the DEEP session fields or the EASI project."""
        with reactive.isolate():
            if state.assessment_type() == "easi" and state.easi_project() is not None:
                meta = dict(state.project_meta() or {})
                meta["ui"] = {"tab": state.current_tab(), "easi_stage": state.easi_stage()}
                return {"path": state.project_file(), "fields": None,
                        "name": state.session_name(), "meta": meta,
                        "origin": dict(_origin["files"]), "easi": state.easi_project()}
        with reactive.isolate():
            path = state.project_file()
            fields = {n: state.get(n) for n in sio.SESSION_FIELDS}
            name = state.session_name()
            meta = dict(state.project_meta() or {})
            region = state.region_of_applicability()
            loc = {"tab": state.current_tab(), "view": state.data_setup_view(),
                   "wizard_step": state.wizard_current_step(),
                   "section": state.workspace_section()}
        hook = state.hooks.get("wizard_draft")
        try:
            fields["wizard_draft"] = hook() if hook is not None else None
        except Exception:  # noqa: BLE001 - a draft is never worth failing a save
            logger.warning("wizard draft snapshot failed", exc_info=True)
            fields["wizard_draft"] = None
        meta["region"] = _region_brief(region) or meta.get("region")
        meta["ui"] = loc
        return {"path": path, "fields": fields, "name": name, "meta": meta,
                "origin": dict(_origin["files"]), "easi": None}

    def _write(snap: dict) -> None:
        with _write_lock:
            if snap["easi"] is not None:
                eio.write_project(snap["easi"], snap["path"],
                                  name=snap["meta"].get("project_name") or snap["name"]
                                  or Path(snap["path"]).stem,
                                  meta=snap["meta"], origin_files=snap["origin"])
                return
            text = pfile.session_text_from_fields(snap["fields"], session_name=snap["name"])
            pfile.write_project(snap["path"], meta=snap["meta"], session_text=text,
                                origin=snap["origin"])

    async def _save(*, explicit: bool = False) -> bool:
        snap = _snapshot()
        path = snap["path"]
        if not path:
            return False
        gen = _dirty["gen"]
        try:
            await asyncio.to_thread(_write, snap)
        except Exception as e:  # noqa: BLE001
            logger.exception("project save failed")
            ui.notification_show(f"The project could not be saved: {e}", type="error",
                                 duration=10)
            return False
        _dirty["saved"] = max(_dirty["saved"], gen)
        if explicit:
            ui.notification_show(f"Saved {Path(path).name}.", type="message", duration=3)
        return True

    def _save_sync() -> None:
        """A parting save on the calling thread (session end, or before another project
        replaces this one). Quiet on success; logged on failure."""
        if not _is_dirty():
            return
        try:
            snap = _snapshot()
            if snap["path"]:
                gen = _dirty["gen"]
                _write(snap)
                _dirty["saved"] = max(_dirty["saved"], gen)
        except Exception:  # noqa: BLE001
            logger.exception("parting save failed")

    def _ensure_save_loop():
        t = _save_task["t"]
        if t is None or t.done():
            _save_task["t"] = _launch(_save_loop())

    async def _save_loop():
        while True:
            await asyncio.sleep(1.0)
            if not _is_dirty():
                return
            with reactive.isolate():
                busy = ((state.busy_count() or 0) > 0
                        or any((state.tasks_running() or {}).values()))
                path = state.project_file()
            if path is None:
                return
            if busy or time.monotonic() - _dirty["last"] < AUTOSAVE_IDLE_S:
                continue
            await _save()
            await st.task_flush()

    def _adopt_unsaved(name: str):
        """Loaded work with no file yet (a staged run from the Region builder)."""
        state.assessment_type.set("deep")
        state.easi_project.set(None)
        state.project_file.set(None)
        state.project_meta.set({"project_name": name})
        _origin["files"] = {}
        _mark_saved()
        _launch(_push_title())

    state.hooks["before_replace"] = _save_sync
    state.hooks["adopt_unsaved"] = _adopt_unsaved
    state.hooks["project_properties"] = lambda: _show_properties()
    session.on_ended(_save_sync)

    # ── pickers: the shell, a child dialog, or a typed path ──────────────────
    _pending_pick: dict = {"purpose": None, "context": {}}
    _TITLES = {"open_project": "Open StreamCurves Project",
               "save_as": "Save Project As",
               "import_target": "Save the Imported Project As",
               "new_folder": "Choose the Folder for the New Project",
               "gallery_target": "Save the Assessment Copy To",
               "easi_import_target": "Save the EASI Method Project As"}

    def _pick(purpose: str, *, mode: str, file_name: str | None = None,
              initial_dir: str | None = None, context: dict | None = None):
        _pending_pick["purpose"] = purpose
        _pending_pick["context"] = dict(context or {})
        if _has_shell():
            kind = {"open": "pickProjectOpen", "save": "pickProjectSave",
                    "directory": "pickFolder"}[mode]
            _launch(_post_shell({"type": kind, "purpose": purpose, "fileName": file_name or ""}))
            return
        if pathpick.picker_mode() == "auto":
            payload = {"mode": mode, "purpose": purpose, "title": _TITLES.get(purpose, APP_NAME),
                       "initial_file": file_name or "",
                       "initial_dir": initial_dir or str(pathpick.default_projects_dir())}
            _run(_child_pick, payload)
            return
        _show_typed_pick(purpose, mode, file_name=file_name, initial_dir=initial_dir)

    async def _child_pick(payload: dict):
        try:
            reply = await asyncio.to_thread(pick_run.run_child, payload)
        except Exception:  # noqa: BLE001 - no Tk here: the typed dialog
            logger.info("child file dialog unavailable; typed path instead", exc_info=True)
            _show_typed_pick(payload["purpose"], payload["mode"],
                             file_name=payload.get("initial_file"),
                             initial_dir=payload.get("initial_dir"))
            return
        await _on_pick(reply.get("purpose") or payload["purpose"], reply.get("path"),
                       cancelled=bool(reply.get("cancelled")))

    _typed = {"purpose": None, "mode": None}
    _tp_err = reactive.value(None)

    def _show_typed_pick(purpose: str, mode: str, *, file_name=None, initial_dir=None):
        _typed["purpose"], _typed["mode"] = purpose, mode
        _tp_err.set(None)
        if mode == "directory":
            lead = "Type the full path of the folder."
            value = initial_dir or str(pathpick.default_projects_dir())
        elif mode == "save":
            lead = "Type the full path of the project file to create."
            base = Path(initial_dir or pathpick.default_projects_dir())
            value = str(base / (file_name or f"Project{pathpick.SUFFIX}"))
        else:
            lead = ("Type the full path of a StreamCurves project (.streamcurves), a saved "
                    "session (.streamcurves.json) or a workbook (.xlsx).")
            value = ""
        ui.modal_show(ui.modal(
            ui.p(lead, class_="sc-form-note mb-2"),
            ui.input_text("tp_path", None, value=value, width="100%"),
            ui.output_ui("tp_error"),
            title=_TITLES.get(purpose, APP_NAME),
            footer=ui.TagList(nonce_button("tp_cancel", "Cancel", "btn btn-outline-secondary"),
                              nonce_button("tp_ok", "OK")),
            easy_close=False, size="l"))

    @render.ui
    def tp_error():
        msg = _tp_err()
        return ui.div(msg, class_="sc-form-err mt-1") if msg else None

    @reactive.effect
    @reactive.event(input.tp_ok)
    def _tp_ok():
        raw = str(input.tp_path() or "")
        purpose, mode = _typed["purpose"], _typed["mode"]
        if mode == "directory":
            path, err = pathpick.interpret_typed_folder(raw)
        elif mode == "save":
            path, err = pathpick.interpret_typed_save(
                raw, known_stem=_pending_pick["context"].get("stem"))
        else:
            path, err = pathpick.interpret_typed_open(raw)
        if err:
            _tp_err.set(err)
            return
        _tp_err.set(None)
        if purpose != "new_folder":
            ui.modal_remove()
        _run(_on_pick, purpose, str(path), cancelled=False)

    @reactive.effect
    @reactive.event(input.tp_cancel)
    def _tp_cancel():
        _tp_err.set(None)
        purpose = _typed["purpose"]
        if purpose == "new_folder":
            _show_new_project(keep=True)
            return
        ui.modal_remove()
        _run(_on_pick, purpose, None, cancelled=True)

    @reactive.effect
    @reactive.event(input.desktop_pick)
    def _shell_pick():
        d = input.desktop_pick() or {}
        _run(_on_pick, str(d.get("purpose") or ""), d.get("path"),
             cancelled=bool(d.get("cancelled")))

    async def _on_pick(purpose: str | None, path: str | None, *, cancelled: bool):
        ctx = _pending_pick.get("context") or {}
        if purpose == "new_folder":
            # the New project dialog comes back with the chosen folder
            _show_new_project(keep=True, folder=None if (cancelled or not path) else str(path))
            return
        if cancelled or not path:
            if ctx.get("reopen_gallery"):
                _show_start("gallery")
            else:
                _ensure_start()
            return
        p = Path(path)
        if purpose == "open_project":
            kind = pathpick.kind_of(p)
            if kind == "project":
                await _open_path(p)
            elif kind in ("session", "workbook"):
                stem = (p.name[: -len(pathpick.LEGACY_SESSION_SUFFIX)] if kind == "session"
                        else p.stem)
                _pick("import_target", mode="save", file_name=f"{stem}{pathpick.SUFFIX}",
                      context={"source": str(p), "kind": kind, "stem": stem})
            else:
                ui.notification_show(pathpick.MSG_KIND, type="warning", duration=6)
                _ensure_start()
        elif purpose == "import_target":
            await _import_into(Path(ctx["source"]), ctx["kind"], pathpick.ensure_suffix(p))
        elif purpose == "save_as":
            await _save_as(pathpick.ensure_suffix(p))
        elif purpose == "gallery_target":
            _gal_target.set(str(pathpick.ensure_suffix(p)))
            _show_start("gallery")
        elif purpose == "easi_import_target":
            await _import_easi(pathpick.ensure_suffix(p))

    # ── opening, creating, importing ─────────────────────────────────────────
    def _land(meta: dict):
        """Where a project opens: where it was last looked at, else its natural stage."""
        loc = meta.get("ui") or {}
        with reactive.isolate():
            has_data = state.data() is not None
            draft = state.wizard_draft()
        if not has_data:
            step = int((draft or {}).get("step") or loc.get("wizard_step") or 1)
            _request_nav("data", wizard_step=max(1, min(step, 7)))
            return
        tab = loc.get("tab")
        if tab in ("curves", "publish", "validate", "regional", "xsec", "nrsa", "rules"):
            _request_nav(tab)
        else:
            _request_nav("data")

    async def _open_path(path: Path, *, first_open: dict | None = None):
        """Open a project file in place. ``first_open`` (a fresh gallery copy) seeds the
        origin from its library version and saves it into the file."""
        path = Path(path)
        current = _get(state.project_file)
        if current and Path(current) == path and not first_open:
            ui.modal_remove()
            return                  # reopening what is open would only reload it
        ui.notification_show(f"Opening {path.name}...", id="sc-open", duration=None,
                             close_button=False)
        await st.task_flush()
        try:
            proj = await asyncio.to_thread(pfile.read_project, path)
        except (pfile.ProjectFileError, ValueError, OSError) as e:
            ui.notification_remove("sc-open")
            ui.notification_show(f"Could not open {path.name}: {e}", type="error", duration=10)
            _ensure_start()
            return
        if proj.assessment_type == "easi":
            await _open_easi(path, proj, first_open=first_open)
            return
        try:
            payload = await asyncio.to_thread(proj.session_payload)
            fields = await asyncio.to_thread(sio.decode_session_fields, payload)
        except (pfile.ProjectFileError, ValueError, OSError) as e:
            ui.notification_remove("sc-open")
            ui.notification_show(f"Could not open {path.name}: {e}", type="error", duration=10)
            _ensure_start()
            return
        _save_sync()
        _restoring["on"] = True
        meta = dict(proj.meta)
        try:
            ui.modal_remove()
            with reactive.isolate():
                st.reset_app_to_startup(state)
                if not meta.get("project_name"):
                    meta["project_name"] = path.stem
                dov.restore_session(state, payload, source_name=meta["project_name"],
                                    decisions="merge", fields=fields)
                if not state.session_name():
                    state.session_name.set(meta["project_name"])
                state.project_meta.set(meta)
                state.project_file.set(str(path))
            _origin["files"] = dict(proj.origin)
            origin = meta.get("origin") or {}
            bundle = proj.origin_json("assessment.deep.json")
            if bundle is not None:
                ap.register_origin_bundle(origin.get("assessmentId"), origin.get("version"),
                                          bundle)
            if first_open:
                _seed_library_origin(proj, first_open)
            _land(meta)
            recents.touch(path, name=meta["project_name"])
            await st.task_flush()
            await _push_title()
            await st.task_flush()
        finally:
            _restoring["on"] = False
            _mark_saved()
            ui.notification_remove("sc-open")
        if first_open:
            _touch_dirty()
            await _save()
        ui.notification_show(f"Opened {meta['project_name']}.", type="message", duration=4)

    async def _open_easi(path: Path, proj: pfile.ProjectFile, *, first_open: dict | None = None):
        """Open an EASI method project: its own state (views/easi_page.py), none of the DEEP
        session. A fresh library copy already names its origin inside the project."""
        try:
            project = await asyncio.to_thread(EasiProject.from_parts, proj.parts)
        except ValueError as e:
            ui.notification_remove("sc-open")
            ui.notification_show(f"Could not open {path.name}: this EASI project is damaged ({e}).",
                                 type="error", duration=10)
            _ensure_start()
            return
        _save_sync()
        _restoring["on"] = True
        meta = dict(proj.meta)
        try:
            ui.modal_remove()
            with reactive.isolate():
                st.reset_app_to_startup(state)
                if not meta.get("project_name"):
                    meta["project_name"] = path.stem
                state.session_name.set(meta["project_name"])
                state.project_meta.set(meta)
                state.project_file.set(str(path))
                state.assessment_type.set("easi")
                state.easi_project.set(project)
                stage = str((meta.get("ui") or {}).get("easi_stage") or "method")
                state.easi_stage.set(stage if stage in easi_stages.STAGE_KEYS else "method")
            _origin["files"] = dict(proj.origin)
            _request_nav("easi")
            recents.touch(path, name=meta["project_name"])
            await st.task_flush()
            await _push_title()
            await st.task_flush()
        finally:
            _restoring["on"] = False
            _mark_saved()
            ui.notification_remove("sc-open")
        if first_open:
            _touch_dirty()
            await _save()
        ui.notification_show(f"Opened {meta['project_name']}.", type="message", duration=4)

    async def _import_easi(target: Path):
        """EASI's current method from this checkout, unchanged, as a new project."""
        ui.notification_show("Importing EASI's method (its preview cases take a moment)...",
                             id="sc-open", duration=None, close_button=False)
        await st.task_flush()
        try:
            project = await asyncio.to_thread(eio.import_from_checkout, ws.repo_root(),
                                              imported_by=ep.person() or "maintainer")
            await asyncio.to_thread(eio.write_project, project, target, name=target.stem,
                                    prepared_by=prefs.get(prefs.PREPARED_BY) or None)
        except Exception as e:  # noqa: BLE001 - say what happened, then offer the start page
            ui.notification_remove("sc-open")
            logger.exception("EASI import failed")
            ui.notification_show(f"Could not import EASI's method: {e}", type="error",
                                 duration=10)
            _ensure_start()
            return
        ui.notification_remove("sc-open")
        prefs.set(prefs.LAST_PROJECTS_DIR, str(target.parent.parent))
        await _open_path(target)

    def _seed_library_origin(proj: pfile.ProjectFile, first_open: dict):
        """A fresh gallery copy: record its library origin the way opening a library version
        always has (dov.seed_origin), from the pack's own origin files."""
        aid, ver = str(first_open["id"]), int(first_open["version"])
        meta_doc = proj.origin_json("meta.json") or {}
        with reactive.isolate():
            dov.seed_origin(state, kind="library", library_id=lib.slugify(aid), version=ver,
                            content_digest=first_open.get("digest"),
                            provenance=proj.origin_json("provenance.json"),
                            portfolio_approvals=meta_doc.get("portfolioApprovals"))
            records = []
            if first_open.get("source") == "checkout":
                try:
                    records = lib._validation_records_for(aid, ver)
                except Exception:  # noqa: BLE001
                    records = []
            state.validation_records.set(records)

    async def _create_project(target: Path, meta: dict):
        _save_sync()
        _restoring["on"] = True
        try:
            ui.modal_remove()
            with reactive.isolate():
                st.reset_app_to_startup(state)
                state.session_name.set(meta["project_name"])
                state.project_meta.set(meta)
                state.project_file.set(str(target))
            _origin["files"] = {}
            _request_nav("data", wizard_step=1)
            await st.task_flush()
        finally:
            _restoring["on"] = False
        _touch_dirty()
        if await _save():
            recents.touch(target, name=meta["project_name"])
            prefs.set(prefs.LAST_PROJECTS_DIR, str(target.parent.parent))
        await _push_title()
        ui.notification_show(f"Created {meta['project_name']}.", type="message", duration=4)

    async def _import_into(src: Path, kind: str, target: Path):
        """A legacy session file or a workbook becomes a new project at `target`."""
        stem = target.stem
        meta = {"project_name": stem, "project_id": pmeta.new_identity(),
                "project_created": pmeta.now_iso(),
                "prepared_by": prefs.get(prefs.PREPARED_BY) or None}
        if kind == "session":
            try:
                text = await asyncio.to_thread(src.read_text, encoding="utf-8")
                await asyncio.to_thread(sio.load_session_payload, text)   # validate first
                await asyncio.to_thread(pfile.write_project, target, meta=meta,
                                        session_text=text)
            except Exception as e:  # noqa: BLE001
                ui.notification_show(f"Could not import {src.name}: {e}", type="error",
                                     duration=10)
                _ensure_start()
                return
            await _open_path(target)
            return
        # a workbook: a new project, then the workbook pipeline into it
        try:
            from streamcurves.workbook import read_input_workbook
            bundle = await asyncio.to_thread(read_input_workbook, str(src))
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not read {src.name}: {e}", type="error", duration=10)
            _ensure_start()
            return
        await _create_project(target, meta)
        try:
            with reactive.isolate():
                dov.apply_workbook_bundle(state, bundle, src.name)
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Could not load the workbook: {e}", type="error",
                                 duration=10)
            return
        _request_nav("data")

    async def _save_as(target: Path):
        with reactive.isolate():
            current = state.project_file()
            meta = dict(state.project_meta() or {})
        if current and Path(current).resolve() == target.resolve():
            _touch_dirty()
            await _save(explicit=True)
            return
        new_meta = dict(meta)
        new_meta["project_id"] = pmeta.new_identity()
        new_meta["project_created"] = pmeta.now_iso()
        if not meta.get("project_name") or (current and Path(current).stem
                                            == meta.get("project_name")):
            new_meta["project_name"] = target.stem
        _save_sync()
        with reactive.isolate():
            state.project_meta.set(new_meta)
            state.project_file.set(str(target))
        _touch_dirty()
        if await _save():
            recents.touch(target, name=new_meta["project_name"])
            await _push_title()
            ui.notification_show(f"Saved a copy as {target.name}; you are now working in it.",
                                 type="message", duration=6)

    # ── header actions ───────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.nav_projects)
    def _nav_projects():
        _show_start("home")

    @reactive.effect
    @reactive.event(input.start_page_open)
    def _start_page_open():
        _show_start("home")

    @reactive.effect
    @reactive.event(input.nav_save)
    def _nav_save():
        if not _get(state.project_file):
            _pick("save_as", mode="save",
                  file_name=f"{pmeta.filename_stem(_project_name(), 'Project')}{pathpick.SUFFIX}")
            return
        _touch_dirty()
        _run(_save, explicit=True)

    @reactive.effect
    @reactive.event(input.nav_save_as)
    def _nav_save_as():
        stem = pmeta.filename_stem(_project_name(), "Project")
        _pick("save_as", mode="save", file_name=f"{stem}{pathpick.SUFFIX}",
              context={"stem": stem})

    @reactive.effect
    @reactive.event(input.sc_ready)
    def _start_gate():
        prefs.apply_environment()
        _run(_refresh_gallery, force=False)
        if _gated():
            _show_start("home")

    # ── the start page ───────────────────────────────────────────────────────
    _welcome = {"recents": [], "view": "home"}

    def _ensure_start():
        if _gated():
            _show_start("home")

    def _start_tile(evt_id: str, title: str, sub: str, icon: str, *, primary: bool = False,
                    active: bool = False):
        cls = "sc-start-tile" + (" is-primary" if primary else "") + (" is-active" if active else "")
        return ui.tags.button(
            ui.span(class_=f"sc-start-tile-ic ic-{icon}"),
            ui.span(ui.span(title, class_="sc-start-tile-t"),
                    ui.span(sub, class_="sc-start-tile-s"), class_="sc-start-tile-txt"),
            type="button", class_=cls, title=title, onclick=nonce_js(evt_id))

    def _start_link(evt_id: str, label: str, title: str = ""):
        return ui.tags.button(label, type="button", class_="sc-start-link",
                              title=title or label, onclick=nonce_js(evt_id))

    def _recent_row(i: int, it: dict):
        return ui.tags.button(
            ui.span(it["name"], class_="sc-welcome-name"),
            ui.span(str(Path(it["path"]).parent), class_="sc-welcome-dir"),
            ui.span(when(it["last_opened"]), class_="sc-welcome-when"),
            # a span, not a button: the row is a <button> and HTML forbids nesting them
            ui.span("×", class_="sc-welcome-rm", title="Remove from recent projects",
                    onclick="event.stopPropagation(); " + nonce_js("welcome_recent_rm", i=i)),
            type="button", class_="sc-welcome-row", title=it["path"],
            onclick=nonce_js("welcome_recent", i=i))

    def _home_columns(items: list[dict]):
        if items:
            feat = items[0]
            region = project_region(feat["path"])
            featured = ui.div(
                ui.div(ui.HTML(region_art.outline_svg(region, width=200, height=128)),
                       class_="sc-start-thumb"),
                ui.div(
                    ui.div(feat["name"], class_="sc-start-feat-name"),
                    ui.div(f"Last opened {when(feat['last_opened'])}",
                           class_="sc-start-feat-when"),
                    ui.div(str(Path(feat["path"]).parent), class_="sc-start-feat-dir",
                           title=feat["path"]),
                    ui.div(
                        ui.tags.button("Open", type="button", class_="btn btn-primary btn-sm",
                                       onclick="event.stopPropagation(); "
                                               + nonce_js("welcome_recent", i=0)),
                        ui.tags.button("Show in folder", type="button",
                                       class_="btn btn-outline-secondary btn-sm",
                                       onclick="event.stopPropagation(); "
                                               + nonce_js("welcome_reveal", i=0)),
                        ui.tags.button("Remove", type="button",
                                       class_="btn btn-link btn-sm sc-start-feat-rm",
                                       title="Remove from recent projects",
                                       onclick="event.stopPropagation(); "
                                               + nonce_js("welcome_recent_rm", i=0)),
                        class_="sc-start-feat-actions"),
                    class_="sc-start-feat-body"),
                class_="sc-start-feat", title=feat["path"],
                onclick=nonce_js("welcome_recent", i=0))
            groups = [
                ui.div(ui.div(label, class_="sc-sec sc-start-group"),
                       ui.div(*[_recent_row(i, it) for i, it in rows], class_="sc-start-list"))
                for label, rows in recent_groups(items)]
            body = [featured, *groups]
        else:
            body = [ui.div(ui.div(ui.HTML(_EMPTY_ART), class_="sc-start-thumb is-empty"),
                           ui.p("No recent projects yet. Start a new project, or download an "
                                "assessment from the library to work on your own copy."),
                           class_="sc-start-empty")]
        center = ui.div(ui.div("Recent projects", class_="sc-start-h"), *body,
                        class_="sc-start-main")
        rels = changelog.load()
        if rels:
            rel_nodes = [
                ui.div(ui.div(ui.span(r.date_display, class_="sc-start-rel-date"),
                              ui.span(r.label, class_="sc-start-rel-ver"),
                              class_="sc-start-rel-head"),
                       ui.tags.ul(*[ui.tags.li(changelog.plain(b)) for b in r.bullets])
                       if r.bullets else ui.div("Maintenance release.",
                                                class_="sc-start-rel-none"),
                       class_="sc-start-rel")
                for r in rels]
        else:
            rel_nodes = [ui.div(f"{APP_NAME} {APP_VERSION_LABEL}", class_="sc-start-rel-none")]
        side = ui.div(ui.div(ui.span("What's new", class_="sc-start-h"),
                             ui.span(APP_VERSION_LABEL, class_="sc-start-verchip"),
                             class_="sc-start-side-head"),
                      ui.div(*rel_nodes, class_="sc-start-rels"),
                      class_="sc-start-side")
        return center, side

    def _runs_count() -> int:
        try:
            root = rb.default_runs_root()
            return sum(1 for p in root.iterdir() if p.is_dir() and p.name.startswith("l3-"))
        except Exception:  # noqa: BLE001
            return 0

    def _show_start(view: str = "home"):
        gated = _gated()
        items = recents.load()
        _welcome["recents"] = items
        _welcome["view"] = view
        chips = [ui.span(APP_VERSION_LABEL, class_="sc-start-ver")]
        if ws.mode() == "maintainer":
            chips.append(ui.span("Maintainer", class_="sc-start-mode",
                                 title="Running from a STAF checkout with publishing on"))
        n_lib = len(_get(_gal_entries) or [])
        links = [_start_link("start_nrsa", "NRSA explorer",
                             "Browse the NRSA reference archive; no project needed"),
                 _start_link("start_rules", "Rules",
                             "The methodology's rules and standing decisions")]
        if ws.is_checkout():
            links.append(_start_link("start_runs", f"Region builder runs ({_runs_count()})",
                                     "Staged regional builds in this checkout"))
            if eio.easi_source(ws.repo_root()) is not None:
                links.append(_start_link("start_easi_import", "Import EASI's method",
                                         "A new project holding EASI's current screening "
                                         "method, unchanged"))
        rail = ui.div(
            ui.div(ui.tags.img(src=versioned_www_asset("streamcurves-icon.svg"), alt="",
                               class_="sc-start-icon"),
                   ui.span(APP_NAME, class_="sc-start-mark"),
                   ui.div(APP_TAGLINE, class_="sc-start-sub"),
                   ui.div(*chips, class_="sc-start-chips"),
                   class_="sc-start-brand"),
            ui.div(
                _start_tile("welcome_new", "New project", "Choose a region and build curves",
                            "new", primary=True),
                _start_tile("welcome_open", "Open project",
                            "Choose a project's .streamcurves file", "open"),
                _start_tile("start_library", "Assessment library",
                            (f"{n_lib} assessments: draft, preliminary and final" if n_lib
                             else "Draft, preliminary and final assessments"),
                            "library", active=view == "gallery"),
                class_="sc-start-tiles"),
            ui.div(*links, class_="sc-start-links"),
            ui.div(nonce_button("start_help", "Help", "sc-start-link"),
                   ui.a("Report an issue", href=ISSUES_URL, target="_blank", rel="noopener",
                        class_="sc-start-link"),
                   class_="sc-start-foot"),
            class_="sc-start-rail")
        if view == "gallery":
            center, side = _gallery_columns()
        else:
            center, side = _home_columns(items)
        close = ([] if gated else
                 [ui.tags.button("×", type="button", class_="sc-start-close", title="Close",
                                 onclick=nonce_js("welcome_cancel"))])
        ui.modal_show(ui.modal(
            ui.tags.style(_START_MODAL_CSS),
            ui.div(rail, center, side, *close,
                   class_="sc-start" + (" is-gallery" if view == "gallery" else "")),
            title=None, footer=None, easy_close=not gated))

    @reactive.effect
    @reactive.event(input.welcome_cancel)
    def _welcome_cancel():
        ui.modal_remove()

    @reactive.effect
    @reactive.event(input.welcome_new)
    def _welcome_new():
        _show_new_project()

    @reactive.effect
    @reactive.event(input.welcome_open)
    def _welcome_open():
        _pick("open_project", mode="open")

    def _recent_at(i) -> dict | None:
        try:
            return _welcome["recents"][int(i)]
        except (IndexError, TypeError, ValueError):
            return None

    @reactive.effect
    @reactive.event(input.welcome_recent)
    def _welcome_recent():
        it = _recent_at((input.welcome_recent() or {}).get("i"))
        if not it:
            return
        p = Path(it["path"])
        if not p.is_file():
            ui.notification_show("That project file is no longer there.", type="warning",
                                 duration=6)
            recents.forget(p)
            _show_start("home")
            return
        _run(_open_path, p)

    @reactive.effect
    @reactive.event(input.welcome_reveal)
    def _welcome_reveal():
        it = _recent_at((input.welcome_reveal() or {}).get("i"))
        if it:
            reveal_path(Path(it["path"]), select=True)

    @reactive.effect
    @reactive.event(input.welcome_recent_rm)
    def _welcome_recent_rm():
        it = _recent_at((input.welcome_recent_rm() or {}).get("i"))
        if it:
            recents.forget(it["path"])
        _show_start(_welcome.get("view") or "home")

    def _open_tool(nav: str):
        ui.modal_remove()
        _request_nav(nav)

    @reactive.effect
    @reactive.event(input.start_nrsa)
    def _start_nrsa():
        _open_tool("nrsa")

    @reactive.effect
    @reactive.event(input.start_rules)
    def _start_rules():
        _open_tool("rules")

    @reactive.effect
    @reactive.event(input.start_runs)
    def _start_runs():
        _open_tool("build")

    @reactive.effect
    @reactive.event(input.start_easi_import)
    def _start_easi_import():
        stem = "EASI screening method"
        _pick("easi_import_target", mode="save", file_name=f"{stem}{pathpick.SUFFIX}",
              context={"stem": stem})

    @reactive.effect
    @reactive.event(input.start_help)
    def _start_help():
        _show_help()

    # ── the gallery ──────────────────────────────────────────────────────────
    _gal_entries = reactive.value([])
    _gal_source = reactive.value("")
    _gal_status = reactive.value("all")
    _gal_sel = reactive.value(None)          # (id, version)
    _gal_target = reactive.value(None)       # Save-to path for the selection
    _gal_error = reactive.value(None)
    _gal_tick = reactive.value(0)
    _dl = {"active": False, "got": 0, "total": 0, "name": "", "cancel": None}
    _grid_rows: dict = {"rows": []}

    def _load_entries():
        with reactive.isolate():
            if ws.gallery_source() == "checkout":
                try:
                    _gal_entries.set(gallery.entries_from_library())
                    _gal_source.set("checkout")
                except Exception as e:  # noqa: BLE001
                    _gal_error.set(f"The library could not be read: {e}")
            else:
                entries, where = gallery.load_catalog()
                _gal_entries.set(entries)
                _gal_source.set(where)

    async def _refresh_gallery(*, force: bool):
        if ws.gallery_source() == "checkout" or not _get(_gal_entries):
            entries_src = await asyncio.to_thread(
                lambda: (gallery.entries_from_library(), "checkout")
                if ws.gallery_source() == "checkout" else gallery.load_catalog())
            with reactive.isolate():
                _gal_entries.set(entries_src[0])
                _gal_source.set(entries_src[1])
            if ws.gallery_source() == "checkout":
                return
        try:
            entries = await asyncio.to_thread(gallery.refresh_catalog, force=force)
            with reactive.isolate():
                _gal_entries.set(entries)
                _gal_source.set("release")
                _gal_error.set(None)
        except gallery.GalleryError as e:
            if force:
                with reactive.isolate():
                    _gal_error.set(str(e))

    @reactive.effect
    @reactive.event(input.start_library)
    def _start_library():
        if not _gal_entries():
            _load_entries()
        if _gal_sel() is None:
            first = next(iter(_gal_entries() or []), None)
            if first is not None:
                _gal_sel.set((first.id, first.latest_version))
                _gal_target.set(None)
        _show_start("gallery")

    @reactive.effect
    @reactive.event(input.start_back)
    def _start_back():
        _show_start("home")

    @reactive.effect
    @reactive.event(input.gal_refresh)
    def _gal_refresh():
        _run(_refresh_gallery, force=True)

    @reactive.effect
    @reactive.event(input.gal_status)
    def _gal_status_pick():
        idx = int((input.gal_status() or {}).get("i") or 0)
        _gal_status.set(_STATUS_CHIPS[max(0, min(idx, len(_STATUS_CHIPS) - 1))][0])

    def _filtered() -> list[gallery.Entry]:
        entries = _gal_entries() or []
        status = _gal_status()
        try:
            q = str(input.gal_filter() or "").strip().lower()
        except Exception:  # noqa: BLE001 - the box is not on screen yet
            q = ""
        out = []
        for e in entries:
            if status != "all" and not any(v.status == status for v in e.versions):
                continue
            if q and q not in f"{e.name} {e.region_line} {e.id}".lower():
                continue
            out.append(e)
        return out

    def _gallery_columns():
        center = ui.div(
            ui.div(nonce_button("start_back", "‹ Back", "btn btn-link sc-start-back"),
                   ui.span("Assessment library", class_="sc-start-h"),
                   nonce_button("gal_refresh", "Refresh", "btn btn-link btn-sm ms-auto")
                   if ws.gallery_source() == "release" else None,
                   class_="sc-start-main-head"),
            ui.p("Every published version: draft, preliminary and final. Opening one saves "
                 "your own copy as a project. DEEP runs the preliminary and final versions.",
                 class_="sc-start-lead"),
            ui.div(
                ui.input_text("gal_filter", None, placeholder="Filter by name or region"),
                ui.output_ui("gal_chips", inline=True),
                ui.output_ui("gal_count", inline=True),
                class_="sc-gallery-toolbar"),
            ui.output_ui("gal_grid"),
            class_="sc-start-main")
        side = ui.div(ui.output_ui("gal_detail"), class_="sc-start-side")
        return center, side

    def _status_tag(v: gallery.Version):
        return ui.span(v.status_label, class_=f"sc-tag st-{v.status}")

    def _copy_of(entry: gallery.Entry, v: gallery.Version) -> str | None:
        targets = prefs.get(prefs.GALLERY_TARGETS) or {}
        p = targets.get(f"{entry.id}@{v.version}") if isinstance(targets, dict) else None
        return p if p and Path(p).is_file() else None

    @output(suspend_when_hidden=False)
    @render.ui
    def gal_chips():
        cur = _gal_status()
        return ui.div(*[ui.tags.button(label, type="button",
                                       class_="sc-chip" + (" is-active" if cur == key else ""),
                                       onclick=nonce_js("gal_status", i=i))
                        for i, (key, label) in enumerate(_STATUS_CHIPS)],
                      class_="sc-chipset")

    @output(suspend_when_hidden=False)
    @render.ui
    def gal_count():
        n = len(_filtered())
        total = len(_gal_entries() or [])
        src = {"checkout": "your checkout", "release": "the published library",
               "snapshot": "the copy shipped with the app"}.get(_gal_source(), "")
        return ui.span(f"{n} of {total}" + (f" · from {src}" if src else ""),
                       class_="sc-gallery-count")

    @output(suspend_when_hidden=False)
    @render.ui
    def gal_grid():
        _gal_tick()
        rows = _filtered()
        _grid_rows["rows"] = rows
        if not rows:
            return ui.div(_gal_error() or "No assessments match.", class_="sc-start-empty")
        sel = _gal_sel()
        groups: dict[str, list] = {}
        for i, e in enumerate(rows):
            v = e.version()
            if v is None:
                continue
            tags = [ui.span(f"v{v.version}", class_="sc-tag is-version"), _status_tag(v)]
            if v.validation == "validated":
                tags.append(ui.span("Verified", class_="sc-tag is-verified"))
            if _copy_of(e, v):
                tags.append(ui.span("Your copy", class_="sc-tag is-have"))
            groups.setdefault(e.group, []).append(ui.tags.button(
                ui.div(ui.HTML(region_art.outline_svg(e.region, width=320, height=200)),
                       class_="sc-gallery-thumb"),
                ui.div(ui.div(e.name, class_="sc-gallery-title"),
                       ui.div(e.region_line, class_="sc-gallery-desc"),
                       ui.div(*tags, class_="sc-gallery-tags"),
                       class_="sc-gallery-body"),
                type="button",
                class_="sc-gallery-tile" + (" is-sel" if sel and sel[0] == e.id else ""),
                onclick=nonce_js("gal_pick", i=i)))
        return ui.TagList(*[
            ui.div(ui.div(g, class_="sc-gallery-group-h"),
                   ui.div(*tiles, class_="sc-gallery-grid"), class_="sc-gallery-group")
            for g, tiles in groups.items()])

    @reactive.effect
    @reactive.event(input.gal_pick)
    def _gal_pick():
        try:
            e = _grid_rows["rows"][int((input.gal_pick() or {}).get("i"))]
        except (IndexError, TypeError, ValueError):
            return
        _gal_sel.set((e.id, e.latest_version))
        _gal_target.set(None)

    def _selected() -> tuple[gallery.Entry, gallery.Version] | None:
        sel = _gal_sel()
        if not sel:
            return None
        e = next((x for x in _gal_entries() or [] if x.id == sel[0]), None)
        if e is None:
            return None
        v = e.version(sel[1]) or e.version()
        return (e, v) if v is not None else None

    def _default_target(e: gallery.Entry, v: gallery.Version) -> Path:
        root = prefs.get(prefs.LAST_PROJECTS_DIR) or str(pathpick.default_projects_dir())
        return pathpick.suffixed_target(root, pmeta.filename_stem(f"{e.name} v{v.version}",
                                                                   "Assessment"))

    @output(suspend_when_hidden=False)
    @render.ui
    def gal_detail():
        _gal_tick()
        picked = _selected()
        if picked is None:
            return ui.div("Choose an assessment to see its versions.", class_="sc-start-empty")
        e, v = picked
        target = _gal_target() or str(_default_target(e, v))
        have = _copy_of(e, v)
        deep_base = (STAF_LINKS.get("deep") or "").rstrip("/")
        facts = [("Region", e.region_line), ("Version", f"v{v.version} of {e.latest_version}"),
                 ("Status", v.status_label)]
        if e.type == "easi":
            if v.method_version:
                facts.append(("Method version", v.method_version))
        else:
            facts.append(("Validation", v.validation_label))
        if v.published_display:
            facts.append(("Published", v.published_display
                          + (f" by {v.published_by}" if v.published_by else "")))
        if v.metrics is not None and e.type != "easi":
            facts.append(("Metrics", str(v.metrics)))
        if v.functions_covered is not None:
            facts.append(("Functions", f"{v.functions_covered} of 20"))
        versions = [ui.tags.button(
            ui.span(f"v{x.version}", class_="sc-gv-v"), _status_tag(x),
            ui.span(x.published_display, class_="sc-gv-date"),
            type="button",
            class_="sc-gallery-version" + (" is-sel" if x.version == v.version else ""),
            onclick=nonce_js("gal_version", v=x.version)) for x in e.versions]
        actions = []
        if _dl["active"]:
            actions.append(ui.output_ui("gal_progress"))
        else:
            if have:
                actions.append(nonce_button("gal_open_copy", "Open my copy"))
                actions.append(nonce_button("gal_open", "Get a fresh copy",
                                            "btn btn-outline-secondary"))
            else:
                actions.append(nonce_button("gal_open", "Open a copy"
                                            if ws.gallery_source() == "checkout"
                                            else "Download and open"))
            if v.in_deep and deep_base:
                actions.append(ui.a("Open in DEEP",
                                    href=f"{deep_base}/?assessment={e.id}@{v.version}",
                                    target="_blank", rel="noopener", class_="btn btn-link"))
        target_row = (
            ui.div(ui.span("Your copy", class_="sc-gallery-k"),
                   ui.span(have, class_="sc-gallery-path", title=have),
                   class_="sc-gallery-target")
            if have else
            ui.div(ui.span("Save to", class_="sc-gallery-k"),
                   ui.span(target, class_="sc-gallery-path", title=target),
                   nonce_button("gal_change_target", "Change…", "btn btn-link btn-sm p-0"),
                   class_="sc-gallery-target"))
        return ui.div(
            ui.div(ui.HTML(region_art.outline_svg(e.region, width=320, height=200)),
                   class_="sc-gallery-hero"),
            ui.div(e.name, class_="sc-start-h"),
            ui.tags.dl(*[ui.TagList(ui.tags.dt(k), ui.tags.dd(val)) for k, val in facts],
                       class_="sc-gallery-facts"),
            ui.div(v.revision_notes, class_="sc-gallery-note") if v.revision_notes else None,
            ui.div("Versions", class_="sc-sec"),
            ui.div(*versions, class_="sc-gallery-versions"),
            target_row,
            ui.div(_gal_error(), class_="sc-gallery-err") if _gal_error() else None,
            ui.div(*actions, class_="sc-gallery-actions"),
            ui.div("An EASI screening method version. EASI keeps the method it ships until "
                   "a library version is adopted." if e.type == "easi" else
                   "DEEP runs this version." if v.in_deep else
                   "A draft is for review; DEEP runs preliminary and final versions.",
                   class_="sc-form-note mt-2"),
            class_="sc-gallery-detail")

    @reactive.effect
    @reactive.event(input.gal_version)
    def _gal_version():
        sel = _gal_sel()
        if not sel:
            return
        _gal_sel.set((sel[0], int((input.gal_version() or {}).get("v") or sel[1])))
        _gal_target.set(None)

    @reactive.effect
    @reactive.event(input.gal_change_target)
    def _gal_change_target():
        picked = _selected()
        if picked is None:
            return
        e, v = picked
        default = _default_target(e, v)
        _pick("gallery_target", mode="save", file_name=default.name,
              initial_dir=str(default.parent.parent),
              context={"reopen_gallery": True, "stem": default.stem})

    @output(suspend_when_hidden=False)
    @render.ui
    def gal_progress():
        if _dl["active"]:
            reactive.invalidate_later(0.5)
        got, total = _dl["got"], _dl["total"] or 1
        pct = max(0, min(100, int(100 * got / total)))
        return ui.div(
            ui.div(f"Downloading {_dl['name']}: {got / 1e6:.1f} of {total / 1e6:.1f} MB"),
            ui.div(ui.div(style=f"width:{pct}%"), class_="sc-prog"),
            nonce_button("gal_cancel", "Cancel", "btn btn-outline-secondary btn-sm"),
            class_="sc-gallery-progress")

    @reactive.effect
    @reactive.event(input.gal_cancel)
    def _gal_cancel():
        ev = _dl.get("cancel")
        if ev is not None:
            ev.set()

    @reactive.effect
    @reactive.event(input.gal_open_copy)
    def _gal_open_copy():
        picked = _selected()
        if picked is None:
            return
        have = _copy_of(*picked)
        if have:
            _run(_open_path, Path(have))

    @reactive.effect
    @reactive.event(input.gal_open)
    def _gal_open():
        picked = _selected()
        if picked is None or _dl["active"]:
            return
        e, v = picked
        target = Path(_gal_target() or _default_target(e, v))
        _run(_gallery_open, e, v, target)

    async def _gallery_open(e: gallery.Entry, v: gallery.Version, target: Path):
        source = ws.gallery_source()
        with reactive.isolate():
            _gal_error.set(None)
        try:
            if source == "checkout":
                data = await asyncio.to_thread(gallery.pack_bytes, e, v, source="checkout")
            else:
                asset = v.assets.get("pack")
                if asset is None:
                    raise gallery.GalleryError("This version has no download in the library "
                                               "yet. Refresh the library and try again.")
                cancel = threading.Event()
                _dl.update(active=True, got=0, total=asset.size, name=f"{e.name} v{v.version}",
                           cancel=cancel)
                with reactive.isolate():
                    _gal_tick.set(_gal_tick() + 1)
                await st.task_flush()

                def progress(got, total):
                    _dl["got"], _dl["total"] = got, total

                try:
                    path = await asyncio.to_thread(gallery.fetch_pack, asset,
                                                   progress=progress, cancel=cancel)
                finally:
                    _dl["active"] = False
                    with reactive.isolate():
                        _gal_tick.set(_gal_tick() + 1)
                data = await asyncio.to_thread(path.read_bytes)
            pack = await asyncio.to_thread(pfile.read_project, data)
            main = await asyncio.to_thread(
                pfile.import_as_project, pack, target, name=f"{e.name} v{v.version}",
                prepared_by=prefs.get(prefs.PREPARED_BY) or None)
        except gallery.GalleryCancelled:
            ui.notification_show("Download cancelled.", type="message", duration=3)
            return
        except (gallery.GalleryError, pfile.ProjectFileError, OSError, ValueError) as err:
            with reactive.isolate():
                _gal_error.set(str(err))
                _gal_tick.set(_gal_tick() + 1)
            return
        targets = prefs.get(prefs.GALLERY_TARGETS) or {}
        targets = dict(targets) if isinstance(targets, dict) else {}
        targets[f"{e.id}@{v.version}"] = str(main)
        prefs.set(prefs.GALLERY_TARGETS, targets)
        prefs.set(prefs.LAST_PROJECTS_DIR, str(main.parent.parent))
        await _open_path(main, first_open={"id": e.id, "version": v.version,
                                           "digest": v.content_digest, "source": source})

    # ── New project ──────────────────────────────────────────────────────────
    _np_err = reactive.value(None)
    _np_values: dict = {}

    def _show_new_project(*, keep: bool = False, folder: str | None = None):
        """The New project dialog. ``keep`` re-shows it with what was typed (after a folder
        pick replaced it); ``folder`` is the folder just chosen."""
        vals = dict(_np_values) if keep else {}
        if not keep:
            _np_err.set(None)
        default_folder = prefs.get(prefs.LAST_PROJECTS_DIR) or str(pathpick.default_projects_dir())
        ui.modal_show(ui.modal(
            ui.input_text("np_name", "Name", value=vals.get("name", ""),
                          placeholder="Eastern Corn Belt Plains curves", width="100%"),
            ui.div(ui.input_text("np_folder", "Folder",
                                 value=folder or vals.get("folder") or default_folder,
                                 width="100%"),
                   nonce_button("np_browse", "Browse…", "btn btn-outline-secondary"),
                   class_="sc-folder-row mb-2"),
            ui.output_ui("np_target"),
            ui.input_text("np_prepared", "Prepared by",
                          value=vals.get("prepared", prefs.get(prefs.PREPARED_BY) or ""),
                          width="100%"),
            ui.input_text_area("np_desc", "Description (optional)", value=vals.get("desc", ""),
                               rows=2, width="100%"),
            ui.output_ui("np_error"),
            title="New project",
            footer=ui.TagList(nonce_button("np_cancel", "Cancel", "btn btn-outline-secondary"),
                              nonce_button("np_create", "Create")),
            easy_close=False))

    def _np_read() -> dict:
        def val(name):
            try:
                return str(input[name]() or "")
            except Exception:  # noqa: BLE001
                return ""
        return {"name": val("np_name"), "folder": val("np_folder"),
                "prepared": val("np_prepared"), "desc": val("np_desc")}

    @render.ui
    def np_target():
        try:
            target, err = pathpick.new_project_target(input.np_name(), input.np_folder())
        except Exception:  # noqa: BLE001 - the dialog is not on screen
            return None
        if err:
            return ui.div("Enter a name and a folder.", class_="sc-form-note mb-2")
        return ui.div(f"Creates {target}", class_="sc-form-target mb-2")

    @render.ui
    def np_error():
        msg = _np_err()
        return ui.div(msg, class_="sc-form-err mt-1") if msg else None

    @reactive.effect
    @reactive.event(input.np_browse)
    def _np_browse():
        _np_values.clear()
        _np_values.update(_np_read())
        _pick("new_folder", mode="directory", initial_dir=_np_values.get("folder") or None)

    @reactive.effect
    @reactive.event(input.np_cancel)
    def _np_cancel():
        _np_values.clear()
        ui.modal_remove()
        _ensure_start()

    @reactive.effect
    @reactive.event(input.np_create)
    def _np_create():
        vals = _np_read()
        target, err = pathpick.new_project_target(vals["name"], vals["folder"])
        if err:
            _np_err.set(err)
            return
        prepared = vals["prepared"].strip()
        if prepared:
            prefs.set(prefs.PREPARED_BY, prepared)
        meta = {"project_name": " ".join(vals["name"].split()),
                "project_id": pmeta.new_identity(), "project_created": pmeta.now_iso(),
                "project_description": vals["desc"].strip() or None,
                "prepared_by": prepared or None}
        _np_values.clear()
        _run(_create_project, target, meta)

    # ── Project properties ───────────────────────────────────────────────────
    def _show_properties():
        with reactive.isolate():
            meta = dict(state.project_meta() or {})
            path = state.project_file()
        if not meta:
            _show_start("home")
            return
        origin = pmeta.origin_label(meta.get("origin")) or "Created here"
        with reactive.isolate():
            easi = state.easi_project() if state.assessment_type() == "easi" else None
        if easi is not None and not meta.get("origin"):
            origin = ("Imported from EASI" if easi.origin().get("kind") == "import"
                      else easi_stages.version_line(easi))
        rows = [
            ("Location", ui.TagList(
                ui.span(str(Path(path).parent) if path else "Not saved yet"),
                nonce_button("pp_folder", "Show in folder", "btn btn-link") if path else None)),
            ("File", Path(path).name if path else "None yet"),
            ("Created", pmeta.created_display(meta.get("project_created"))),
            ("Started from", origin),
        ]
        ui.modal_show(ui.modal(
            ui.input_text("pp_name", "Name", value=meta.get("project_name") or "",
                          width="100%"),
            ui.input_text("pp_prepared", "Prepared by", value=meta.get("prepared_by") or "",
                          width="100%"),
            ui.input_text_area("pp_desc", "Description",
                               value=meta.get("project_description") or "", rows=2,
                               width="100%"),
            ui.div(*[ui.div(ui.span(k, class_="sc-proj-k"), ui.div(v, class_="sc-proj-v"),
                            class_="sc-proj-row") for k, v in rows], class_="mt-2"),
            title="Project",
            footer=ui.TagList(nonce_button("pp_close", "Cancel", "btn btn-outline-secondary"),
                              nonce_button("pp_save", "Save")),
            easy_close=True))

    @reactive.effect
    @reactive.event(input.project_props_evt)
    def _project_props():
        _show_properties()

    @reactive.effect
    @reactive.event(input.pp_folder)
    def _pp_folder():
        path = _get(state.project_file)
        if path:
            reveal_path(Path(path), select=True)

    @reactive.effect
    @reactive.event(input.pp_close)
    def _pp_close():
        ui.modal_remove()

    @reactive.effect
    @reactive.event(input.pp_save)
    def _pp_save():
        meta = dict(state.project_meta() or {})
        name = " ".join(str(input.pp_name() or "").split())
        if name:
            meta["project_name"] = name
            state.session_name.set(name)
        meta["prepared_by"] = str(input.pp_prepared() or "").strip() or None
        meta["project_description"] = str(input.pp_desc() or "").strip() or None
        state.project_meta.set(meta)
        ui.modal_remove()
        path = state.project_file()
        if path:
            recents.touch(path, name=meta.get("project_name"))
            _touch_dirty()
            _run(_save)
        _launch(_push_title())

    # ── About, Help, What's new ──────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.nav_about)
    def _nav_about():
        build = desktop_env.desktop_build_line()
        mode = {"maintainer": "Maintainer: publishing to this checkout's library",
                "checkout": "Running from a STAF checkout",
                "installed": "Installed copy"}[ws.mode()]
        ui.modal_show(ui.modal(
            ui.div(ui.tags.img(src=versioned_www_asset("streamcurves-icon.svg"), alt=""),
                   ui.div(ui.div(APP_NAME, class_="sc-about-name"),
                          ui.div(APP_TAGLINE, class_="sc-about-sub"),
                          ui.div(ui.div(f"Version {APP_VERSION_LABEL}"),
                                 ui.div(build) if build else None,
                                 ui.div(mode),
                                 class_="sc-about-lines"),
                          ui.p("StreamCurves builds reference and regional curves for stream "
                               "metrics and publishes them as detailed assessments that DEEP "
                               "runs. It is part of the Stream Tiered Assessment Framework "
                               "(STAF).", class_="mt-2 mb-0"),
                          ui.div(ui.a("STAF", href=STAF_LINKS.get("home"), target="_blank",
                                      rel="noopener"),
                                 ui.a("DEEP", href=STAF_LINKS.get("deep"), target="_blank",
                                      rel="noopener"),
                                 ui.a("Source and releases", href=REPO_URL, target="_blank",
                                      rel="noopener"),
                                 ui.a("Report an issue", href=ISSUES_URL, target="_blank",
                                      rel="noopener"),
                                 class_="sc-about-links")),
                   class_="sc-about"),
            title="About StreamCurves",
            footer=ui.TagList(nonce_button("about_whatsnew", "What's new",
                                           "btn btn-outline-secondary"),
                              ui.modal_button("Close")),
            easy_close=True))

    @reactive.effect
    @reactive.event(input.about_whatsnew)
    def _about_whatsnew():
        _show_whatsnew()

    @reactive.effect
    @reactive.event(input.whatsnew_evt)
    def _whatsnew():
        _show_whatsnew()

    def _show_whatsnew():
        md = changelog.markdown() or f"{APP_NAME} {APP_VERSION_LABEL}"
        ui.modal_show(ui.modal(
            ui.tags.style(_TALL_MODAL_CSS),
            ui.div(ui.markdown(md), class_="sc-whatsnew"),
            title="What's new in StreamCurves",
            footer=nonce_button("whatsnew_close", "Close"),
            easy_close=not _gated()))

    @reactive.effect
    @reactive.event(input.whatsnew_close)
    def _whatsnew_close():
        ui.modal_remove()
        _ensure_start()

    def _show_help():
        ui.modal_show(ui.modal(
            ui.tags.style(_TALL_MODAL_CSS),
            app_help_content(),
            title="StreamCurves Help",
            footer=nonce_button("help_close", "Close"),
            easy_close=not _gated(), size="l"))

    @reactive.effect
    @reactive.event(input.nav_help)
    def _nav_help():
        _show_help()

    @reactive.effect
    @reactive.event(input.help_close)
    def _help_close():
        ui.modal_remove()
        _ensure_start()


__all__ = ["header_left_ui", "header_nav_ui", "boot_veil_ui", "project_server",
           "recent_groups", "project_region", "nonce_js", "nonce_button", "when",
           "AUTOSAVE_IDLE_S"]
