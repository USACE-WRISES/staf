"""StreamCurves Desktop: projects, recents, the changelog, the modes, the gallery and the start
page's rules (after HYPE Desktop's test_recents / test_changelog / test_start_modal /
test_welcome).

Pure modules are exercised directly; the start page's wiring lives in a server closure, so its
rules are source lints, as HYPE's are.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import threading
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from streamcurves import changelog
from streamcurves import desktop_env
from streamcurves import gallery
from streamcurves import library as lib
from streamcurves import pathpick
from streamcurves import prefs
from streamcurves import project_file as pf
from streamcurves import project_meta as pm
from streamcurves import recents
from streamcurves import region_art
from streamcurves import region_build as rb
from streamcurves import session_io as sio
from streamcurves import workspace as ws
from streamcurves.version import APP_VERSION

APP_DIR = Path(__file__).resolve().parents[1]
REPO = APP_DIR.parents[1]


@pytest.fixture(autouse=True)
def _data_root(tmp_path, monkeypatch):
    """Every test gets its own per-user data root (recents, prefs, gallery cache)."""
    monkeypatch.setenv("STREAMCURVES_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("STREAMCURVES_LIBRARY_BASE_URL", raising=False)
    monkeypatch.delenv("STREAMCURVES_GALLERY_SOURCE", raising=False)
    return tmp_path / "data"


def _session_text(**fields) -> str:
    return sio.dumps_session(sio.dump_session_fields(fields, session_name=fields.get("session_name")))


# --------------------------------------------------------------------------- #
# The project file
# --------------------------------------------------------------------------- #
def test_a_project_round_trips_its_identity_session_and_origin(tmp_path):
    main = tmp_path / "Site A" / "Site A.streamcurves"
    meta = {"project_name": "Site A", "project_id": pm.new_identity(),
            "project_created": pm.now_iso(), "prepared_by": "Reviewer",
            "origin": {"kind": "library", "assessmentId": "x", "version": 3},
            "region": {"kind": "ecoregion", "code": "55", "name": "ECBP", "polygon": [1]},
            "ui": {"tab": "curves"}}
    pf.write_project(main, meta=meta, session_text=_session_text(session_name="Site A"),
                     origin={"meta.json": b'{"a": 1}'})
    assert (main.parent / "exports").is_dir(), "a project folder carries exports/"
    back = pf.read_project(main)
    assert back.desktop_project is True and back.format_version == pf.FORMAT_VERSION
    assert back.meta["project_name"] == "Site A" and back.meta["prepared_by"] == "Reviewer"
    assert back.meta["origin"]["version"] == 3
    assert back.meta["region"] == {"kind": "ecoregion", "code": "55", "name": "ECBP"}, \
        "project.json keeps a region brief, never the polygon"
    assert back.meta["ui"] == {"tab": "curves"}
    assert back.origin_json("meta.json") == {"a": 1}
    assert back.session_payload()["session_name"] == "Site A"


def test_a_pack_is_byte_deterministic_and_imports_as_a_new_project(tmp_path):
    kw = dict(meta={"project_name": "P"}, session_text=_session_text(session_name="P"),
              origin={"provenance.json": b"{}"}, desktop_project=False, deterministic=True)
    a, b = pf.build_bytes(**kw), pf.build_bytes(**kw)
    assert a == b, "the release builder uploads only names it never published"
    pack = pf.read_project(a)
    assert pack.desktop_project is False and pack.path is None
    main = pf.import_as_project(pack, tmp_path / "Copy" / "Copy.streamcurves", name="Copy",
                                prepared_by="Me")
    copy = pf.read_project(main)
    assert copy.desktop_project is True
    assert copy.meta["project_name"] == "Copy" and copy.meta["prepared_by"] == "Me"
    assert copy.meta["project_id"] and copy.meta["project_created"]
    assert copy.origin == pack.origin, "the origin files travel with every copy"


@pytest.mark.parametrize("payload, msg", [
    (b"not a zip", "not a StreamCurves project"),
])
def test_a_file_that_is_not_a_project_says_so(payload, msg, tmp_path):
    p = tmp_path / "bad.streamcurves"
    p.write_bytes(payload)
    with pytest.raises(pf.ProjectFileError, match=msg):
        pf.read_project(p)


def test_a_project_from_a_newer_app_asks_for_an_update(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(pf.PROJECT_JSON, json.dumps({"format": pf.FORMAT,
                                                 "format_version": pf.FORMAT_VERSION_MAX + 1}))
        zf.writestr(pf.SESSION_NAME, _session_text())
    with pytest.raises(pf.ProjectFileError, match="newer StreamCurves"):
        pf.read_project(buf.getvalue())


def test_an_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "a.bin"
    pf.atomic_write(target, b"one")
    pf.atomic_write(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]


def test_the_wizard_draft_is_a_session_field_that_round_trips_a_compiled_table():
    assert "wizard_draft" in sio.SESSION_FIELDS
    draft = {"step": 6, "picker": ["a", "b"],
             "compiled": pd.DataFrame({"site_id": ["s1", "s2"], "m": [1.5, None]}),
             "col_source": {"m": "StreamCat"}}
    text = _session_text(wizard_draft=draft)
    back = sio.decode_session_fields(sio.load_session_payload(text))["wizard_draft"]
    assert back["step"] == 6 and back["picker"] == ["a", "b"]
    assert back["compiled"]["site_id"].tolist() == ["s1", "s2"]


# --------------------------------------------------------------------------- #
# Recents and prefs (the per-user data root)
# --------------------------------------------------------------------------- #
def test_recents_touch_dedupes_by_path_and_skips_missing_files(tmp_path):
    a = tmp_path / "a.streamcurves"
    b = tmp_path / "b.streamcurves"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    recents.touch(a, name="Alpha")
    recents.touch(b)
    recents.touch(a, name="Alpha renamed")
    items = recents.load()
    assert [Path(i["path"]).name for i in items] == ["a.streamcurves", "b.streamcurves"]
    assert items[0]["name"] == "Alpha renamed"
    b.unlink()
    assert [Path(i["path"]).name for i in recents.load()] == ["a.streamcurves"]
    recents.forget(a)
    assert recents.load() == []


def test_the_data_root_follows_the_environment(_data_root):
    assert desktop_env.data_root() == _data_root
    prefs.set(prefs.PREPARED_BY, "Someone")
    assert prefs.get(prefs.PREPARED_BY) == "Someone"
    assert (_data_root / "prefs.json").is_file()


def test_a_stored_mmw_key_reaches_the_environment_unless_one_is_set(monkeypatch):
    monkeypatch.delenv("MMW_API_KEY", raising=False)
    prefs.set(prefs.MMW_API_KEY, "k1")
    prefs.apply_environment()
    import os
    assert os.environ["MMW_API_KEY"] == "k1"
    monkeypatch.setenv("MMW_API_KEY", "explicit")
    prefs.apply_environment()
    assert os.environ["MMW_API_KEY"] == "explicit"


# --------------------------------------------------------------------------- #
# The changelog and the version lockstep
# --------------------------------------------------------------------------- #
def test_the_newest_changelog_section_is_this_version():
    rels = changelog.load()
    assert rels, "apps/stream-curves/CHANGELOG.md has no ## vX.Y.Z (date) section"
    assert rels[0].version == APP_VERSION
    assert [r.version_tuple for r in rels] == sorted((r.version_tuple for r in rels),
                                                    reverse=True)


def test_changelog_bullets_are_one_short_line_without_em_dashes():
    for r in changelog.load():
        assert r.bullets, f"v{r.version} has no bullets"
        for b in r.bullets:
            assert len(b) <= 140, f"v{r.version}: {b[:60]}..."
            assert "—" not in b


def test_the_shell_version_matches_the_app():
    csproj = REPO / "desktop" / "src" / "StreamCurves.Desktop" / "StreamCurves.Desktop.csproj"
    if not csproj.is_file():
        pytest.skip("no desktop shell in this tree")
    m = re.search(r"<Version>([^<]+)</Version>", csproj.read_text(encoding="utf-8"))
    assert m and m.group(1).strip() == APP_VERSION


def test_the_whats_new_markdown_drops_the_title():
    md = changelog.markdown()
    assert md.startswith("## v"), md[:40]


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def test_kinds_and_suffixes():
    assert pathpick.kind_of("x/Site.streamcurves") == "project"
    assert pathpick.kind_of("x/Site.streamcurves.json") == "session"
    assert pathpick.kind_of("x/Site.XLSX") == "workbook"
    assert pathpick.kind_of("x/Site.txt") is None
    assert pathpick.ensure_suffix(Path("Site v1.2")).name == "Site v1.2.streamcurves"


def test_a_new_project_gets_its_own_folder_and_never_lands_loose(tmp_path):
    target, err = pathpick.new_project_target("  My   Site ", str(tmp_path))
    assert err is None and target == tmp_path / "My Site" / "My Site.streamcurves"
    (tmp_path / "My Site").mkdir()
    (tmp_path / "My Site" / "other.txt").write_text("x")
    target2, _ = pathpick.new_project_target("My Site", str(tmp_path))
    assert target2 == tmp_path / "My Site (2)" / "My Site (2).streamcurves"
    assert pathpick.new_project_target("", str(tmp_path))[1] == pathpick.MSG_NEW_NAME
    assert pathpick.new_project_target("a", "relative")[1] == pathpick.MSG_NEW_FOLDER_ABS


def test_typed_paths(tmp_path):
    p = tmp_path / "a.streamcurves"
    p.write_bytes(b"x")
    assert pathpick.interpret_typed_open(f'"{p}"') == (p, None)
    assert pathpick.interpret_typed_open(str(tmp_path))[1] == pathpick.MSG_OPEN_DIR
    assert pathpick.interpret_typed_open(str(tmp_path / "a.txt"))[1] == pathpick.MSG_KIND
    empty = tmp_path / "Empty"
    empty.mkdir()
    assert pathpick.interpret_typed_save(str(empty)) == (empty / "Empty.streamcurves", None)
    assert pathpick.interpret_typed_save(str(tmp_path / "b"))[0].name == "b.streamcurves"


# --------------------------------------------------------------------------- #
# Modes: an installed copy never writes the library or a region record
# --------------------------------------------------------------------------- #
def test_a_checkout_reads_its_own_library_and_publishes_only_with_the_switch(monkeypatch):
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    assert ws.is_checkout() and ws.mode() == "checkout"
    assert ws.gallery_source() == "checkout"
    monkeypatch.setenv("STAF_LIBRARY_PUBLISH", "1")
    assert ws.can_publish() and ws.mode() == "maintainer"
    monkeypatch.setenv("STREAMCURVES_GALLERY_SOURCE", "release")
    assert ws.gallery_source() == "release"


def test_an_installed_copy_is_read_only_whatever_its_environment(monkeypatch, tmp_path):
    manifest = tmp_path / "desktop-manifest.json"
    manifest.write_text("{}")
    monkeypatch.setattr(desktop_env, "desktop_manifest_path", lambda: manifest)
    monkeypatch.setenv("STAF_LIBRARY_PUBLISH", "1")
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path))
    assert desktop_env.is_installed_copy()
    assert lib.writable() is False
    assert ws.repo_root() is None and ws.mode() == "installed"
    assert ws.gallery_source() == "release"
    assert rb.region_run_dir({"kind": "ecoregion", "code": "55"}) is None, \
        "REF-15 choices stay in an installed copy's own session"


def test_a_worktree_counts_as_a_checkout(monkeypatch, tmp_path):
    repo = tmp_path / "wt"
    (repo / "apps").mkdir(parents=True)
    (repo / ".git").write_text("gitdir: elsewhere")
    monkeypatch.setenv("STAF_REPO_ROOT", str(repo))
    assert ws.repo_root() == repo


# --------------------------------------------------------------------------- #
# The gallery
# --------------------------------------------------------------------------- #
def _catalog(pack: bytes | None = None, *, schema: int = 1, name="demo-v2-p1.streamcurves"):
    asset = ({"name": name, "size": len(pack), "sha256": hashlib.sha256(pack).hexdigest()}
             if pack is not None else None)
    return {"schema": schema, "assessments": [
        {"id": "demo", "name": "Demo assessment", "region": {"kind": "ecoregion", "code": "55"},
         "latestVersion": 2, "defaultVersion": 2, "extra": "ignored",
         "versions": [
             {"version": 1, "status": "preliminary", "assets": {}},
             {"version": 2, "status": "draft", "statusLabel": "Draft", "metrics": 5,
              "assets": {"pack": asset, "thumbnail": None}},
         ]},
        {"id": "Bad Id!", "versions": [{"version": 1, "status": "draft"}]},
    ]}


def test_a_catalog_parses_newest_first_and_skips_bad_rows():
    entries = gallery.parse_catalog(json.dumps(_catalog(b"abc")))
    assert [e.id for e in entries] == ["demo"]
    e = entries[0]
    assert [v.version for v in e.versions] == [2, 1]
    assert e.version().status == "draft" and not e.version().in_deep
    assert e.version(1).in_deep
    assert e.version().assets["pack"].size == 3
    with pytest.raises(ValueError, match="newer"):
        gallery.parse_catalog(json.dumps(_catalog(schema=2)))


def test_the_library_builds_the_same_catalog_the_release_publishes():
    entries = gallery.entries_from_library()
    assert entries, "the library snapshot lists nothing"
    doc = gallery.catalog_doc(entries, source_commit="abc")
    back = gallery.parse_catalog(json.dumps(doc))
    assert [e.id for e in back] == [e.id for e in entries]
    ecbp = next(e for e in back if e.id == "eastern-corn-belt-plains")
    assert ecbp.latest_version >= 8 and ecbp.version().functions_covered == 20


def test_a_pack_from_the_library_carries_the_version_and_its_origin():
    e = next(x for x in gallery.entries_from_library() if x.id == "eastern-corn-belt-plains")
    v = e.version()
    data = gallery.pack_bytes(e, v)
    assert data == gallery.pack_bytes(e, v)
    pack = pf.read_project(data)
    assert pack.meta["origin"]["assessmentId"] == e.id
    assert pack.meta["origin"]["version"] == v.version
    assert set(pack.origin) <= set(pf.ORIGIN_FILES)
    assert "assessment.deep.json" in pack.origin
    assert pf.pack_asset_name(e.id, v.version).endswith("-p1.streamcurves")


class _Resp:
    def __init__(self, status, data=b""):
        self.status_code, self.content, self.headers = status, data, {}

    def iter_content(self, n):
        for i in range(0, len(self.content), max(1, n)):
            yield self.content[i:i + n]

    def close(self):
        pass


def test_a_download_is_verified_resumed_and_cancellable(monkeypatch):
    data = b"x" * 1000
    asset = gallery.Asset(name="demo-v2-p1.streamcurves", size=len(data),
                          sha256=hashlib.sha256(data).hexdigest())
    calls = []

    def fake_get(url, *, headers=None, stream=False, timeout=30.0):
        calls.append(dict(headers or {}))
        rng = (headers or {}).get("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return _Resp(206, data[start:])
        return _Resp(200, data)

    monkeypatch.setattr(gallery, "http_get", fake_get)
    part = gallery.packs_dir() / (asset.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(data[:400])
    got = gallery.fetch_pack(asset)
    assert got.read_bytes() == data and not part.exists()
    assert calls[-1] == {"Range": "bytes=400-"}, "a partial download resumes"
    assert gallery.fetch_pack(asset) == got, "a verified copy is reused"

    bad = gallery.Asset(name="other.streamcurves", size=len(data), sha256="0" * 64)
    with pytest.raises(gallery.GalleryError, match="did not match"):
        gallery.fetch_pack(bad)
    assert not (gallery.packs_dir() / "other.streamcurves.part").exists()

    monkeypatch.setattr(gallery, "http_get", lambda *a, **k: _Resp(404))
    with pytest.raises(gallery.GalleryGone):
        gallery.fetch_pack(gallery.Asset(name="gone.streamcurves", size=3, sha256="1" * 64))

    ev = threading.Event()
    ev.set()
    monkeypatch.setattr(gallery, "http_get", fake_get)
    with pytest.raises(gallery.GalleryCancelled):
        gallery.fetch_pack(gallery.Asset(name="c.streamcurves", size=len(data),
                                         sha256=asset.sha256), cancel=ev)


def test_a_folder_library_serves_the_catalog_and_packs(monkeypatch, tmp_path):
    pack = pf.build_bytes(meta={"project_name": "Demo v2"}, session_text=_session_text(),
                          desktop_project=False, deterministic=True)
    (tmp_path / "demo-v2-p1.streamcurves").write_bytes(pack)
    (tmp_path / gallery.CATALOG_NAME).write_text(json.dumps(_catalog(pack)))
    monkeypatch.setenv(gallery.BASE_URL_ENV, str(tmp_path))
    entries = gallery.refresh_catalog(force=True)
    asset = entries[0].version().assets["pack"]
    assert gallery.fetch_pack(asset).read_bytes() == pack
    assert gallery.load_catalog()[1] == "release", "the cached download wins over the snapshot"


def test_a_missing_catalog_reads_as_the_library_being_updated(monkeypatch):
    monkeypatch.setattr(gallery, "http_get", lambda *a, **k: _Resp(404))
    with pytest.raises(gallery.GalleryError, match="being updated"):
        gallery.refresh_catalog(force=True)


# --------------------------------------------------------------------------- #
# Pictures
# --------------------------------------------------------------------------- #
def test_region_outlines_and_the_placeholder():
    svg = region_art.outline_svg({"kind": "ecoregion", "code": "55"})
    assert svg.startswith("<svg") and "fill-rule" in svg
    assert region_art.outline_svg({"kind": "polygon"}) == region_art.placeholder_svg()


# --------------------------------------------------------------------------- #
# The start page and the controller (source lints: the wiring lives in a closure)
# --------------------------------------------------------------------------- #
PROJECT = (APP_DIR / "views" / "project.py").read_text(encoding="utf-8")


def _body(start: str, end: str, src: str = PROJECT) -> str:
    i = src.index(start)
    return src[i:src.index(end, i)]


def test_no_effect_awaits_work_or_a_flush():
    """py-shiny's flush awaits async effects: awaiting a file read (or a flush) inside one
    freezes or wedges the session. Effects launch detached tasks instead (_run)."""
    tree = ast.parse(PROJECT)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            decs = [ast.unparse(d) for d in node.decorator_list]
            assert not any("reactive.effect" in d for d in decs), \
                f"{node.name} is an async effect"


def test_the_start_page_is_a_gate_only_while_nothing_is_open():
    show = _body("def _show_start(", "@reactive.effect")
    assert "gated = _gated()" in show
    assert "title=None" in show and "footer=None" in show
    assert "easy_close=not gated" in show
    assert "close = ([] if gated" in show
    assert "items = recents.load()" in show, "recents are re-read on every show"
    assert '_welcome["recents"] = items' in show


def test_recent_rows_fire_positional_events_only():
    row = _body("def _recent_row(", "def _home_columns(")
    assert 'nonce_js("welcome_recent", i=i)' in row
    assert 'nonce_js("welcome_recent_rm", i=i)' in row
    assert "event.stopPropagation()" in row
    assert 'it["path"]' not in row.split("onclick=")[-1], "a path rode into inline JS"


def test_every_dialog_button_is_a_nonce_button():
    """Rebuilt action buttons restart their counters and miss clicks."""
    for name in ("_show_new_project", "_show_properties", "_show_typed_pick"):
        body = _body(f"def {name}(", "\n    def ", PROJECT) if f"def {name}(" in PROJECT else ""
        assert "input_action_button" not in body, name


def test_opening_saves_the_open_project_first_and_merges_decisions():
    body = _body("async def _open_path(", "def _seed_library_origin(")
    assert body.index("_save_sync()") < body.index("st.reset_app_to_startup(state)")
    assert 'decisions="merge"' in body
    assert "_restoring[\"on\"] = True" in body and "_mark_saved()" in body


def test_autosave_waits_for_quiet_and_for_every_job():
    loop = _body("async def _save_loop(", "def _adopt_unsaved(")
    assert "busy_count" in loop and "tasks_running" in loop
    assert "AUTOSAVE_IDLE_S" in loop
    assert "asyncio.to_thread(_write" in _body("async def _save(", "def _save_sync(")
    assert "session.on_ended(_save_sync)" in PROJECT


def test_gallery_links_to_deep_use_the_form_deep_parses():
    assert "?assessment={e.id}@{v.version}" in PROJECT
    assert "@v{v.version}" not in PROJECT
