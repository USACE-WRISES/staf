"""Show in DEEP (owner, 2026-10-08): per published version, on by default, changeable later on the
Validate stage. DEEP runs Draft, Preliminary and Final versions, labeled, and skips a hidden one;
its default is the newest shown Final version, else Preliminary, else Draft.

The record is library ``visibility.json``, append-only like status.json; a library without it
shows every version. The catalog says what DEEP shows, both release feeds carry
``visibleInDeep: false`` on a hidden version only, and DEEP's reader agrees with the writer.
STAF_LIBRARY_ROOT points at a tmp dir, so nothing here touches apps/library.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from streamcurves import gallery
from streamcurves import library as lib
from streamcurves import owner_sources as osrc
from test_library_types import _load
from test_library_v2 import _bundle, _session_payload

APP = Path(__file__).resolve().parents[1]
DEEP = APP.parent / "deep"
META = {"assessmentName": "ECBP", "region": {"kind": "ecoregion", "code": "55",
                                             "name": "Eastern Corn Belt Plains"}}


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    return root


def _publish(status="preliminary", *, visible=True, y2=0.3) -> int:
    return lib.publish_version("ecbp", dict(META), _session_payload(), _bundle(y2),
                               status=status, visible=visible)


def _deep_remote():
    """DEEP's release reader, imported from the sibling app as the other DEEP tests here do."""
    if str(DEEP) not in sys.path:
        sys.path.insert(0, str(DEEP))
    return pytest.importorskip("deep.remote_library", reason=f"DEEP not importable from {DEEP}")


def test_the_writer_and_deeps_reader_run_the_same_statuses():
    assert lib.DEEP_STATUSES == ("draft", "preliminary", "certified")
    assert gallery.DEEP_STATUSES == lib.DEEP_STATUSES
    assert osrc.ELIGIBLE_STATUSES == lib.DEEP_STATUSES
    src = (DEEP / "deep" / "library.py").read_text(encoding="utf-8")
    assert '_ELIGIBLE = ("draft", "preliminary", "certified")' in src


def test_a_library_without_the_record_shows_every_version(libroot):
    v1 = _publish()
    assert not lib.visibility_path("ecbp").exists()
    assert lib.version_visible_in_deep("ecbp", v1)
    assert lib.hidden_versions("ecbp") == set()


def test_hiding_and_showing_append_audited_records_and_a_no_op_appends_nothing(libroot):
    v1 = _publish()
    assert lib.set_version_visibility("ecbp", v1, False, "GM", note="not yet") is False
    assert not lib.version_visible_in_deep("ecbp", v1)
    assert lib.set_version_visibility("ecbp", v1, False, "GM") is False   # already hidden
    history = lib.read_visibility("ecbp")["history"]
    assert len(history) == 1
    assert history[0]["version"] == v1 and history[0]["visibleInDeep"] is False
    assert history[0]["actor"] == "GM" and history[0]["note"] == "not yet" and history[0]["timestamp"]
    lib.set_version_visibility("ecbp", v1, True, "GM")
    assert lib.version_visible_in_deep("ecbp", v1)
    assert len(lib.read_visibility("ecbp")["history"]) == 2, "the last record wins"
    # nothing else about the version moves
    assert lib.version_status("ecbp", v1) == "preliminary"
    assert len(lib.read_status("ecbp")["history"]) == 1


def test_a_visibility_change_is_refused_without_an_actor_a_version_or_a_deep_assessment(libroot):
    v1 = _publish()
    with pytest.raises(ValueError, match="actor"):
        lib.set_version_visibility("ecbp", v1, False, "  ")
    with pytest.raises(ValueError, match="no version v9"):
        lib.set_version_visibility("ecbp", 9, False, "GM")
    easi = libroot / "assessments" / "easi-screening"
    (easi / "v1").mkdir(parents=True)
    (easi / "manifest.json").write_text(json.dumps({
        "assessmentId": "easi-screening", "assessmentType": "easi", "latestVersion": 1,
        "versions": [{"version": 1}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="EASI"):
        lib.set_version_visibility("easi-screening", 1, False, "GM")


def test_publish_with_show_in_deep_off_records_the_version_hidden(libroot):
    v1 = _publish(visible=False)
    assert not lib.version_visible_in_deep("ecbp", v1)
    rec = lib.read_visibility("ecbp")["history"][-1]
    assert rec["visibleInDeep"] is False and rec["note"] == "Published with Show in DEEP off."
    v2 = _publish(y2=0.31)
    assert lib.version_visible_in_deep("ecbp", v2), "on by default"


def test_the_catalog_says_what_deep_shows_and_keeps_the_streamcurves_default(libroot):
    v1 = _publish("preliminary")
    v2 = _publish("draft", y2=0.31)
    entry = lib.list_assessments()[0]
    assert (entry["defaultVersion"], entry["deepDefaultVersion"]) == (v1, v1)
    assert entry["deepDefaultStatus"] == "preliminary" and entry["hiddenFromDeep"] == []
    lib.set_version_visibility("ecbp", v1, False, "GM")
    entry = lib.list_assessments()[0]
    assert entry["defaultVersion"] == v1, "StreamCurves still opens the reviewed version"
    assert (entry["deepDefaultVersion"], entry["deepDefaultStatus"]) == (v2, "draft")
    assert entry["hiddenFromDeep"] == [v1]
    lib.set_version_visibility("ecbp", v2, False, "GM")
    entry = lib.list_assessments()[0]
    assert (entry["deepDefaultVersion"], entry["deepDefaultStatus"]) == (0, None)
    assert lib.deep_default_version("ecbp") == 0


def test_deeps_default_prefers_final_then_preliminary_then_draft(libroot):
    v1 = _publish("preliminary")
    v2 = _publish("draft", y2=0.31)
    v3 = _publish("draft", y2=0.32)
    assert lib.deep_default_version("ecbp") == v1
    lib._append_status("ecbp", v1, "draft", "GM", None)
    assert lib.deep_default_version("ecbp") == v3, "all drafts: the newest"
    lib._append_status("ecbp", v2, "certified", "GM", None)
    assert lib.deep_default_version("ecbp") == v2


def test_in_deep_and_both_feeds_carry_the_flag_on_a_hidden_version_only(libroot):
    v1 = _publish("preliminary")
    v2 = _publish("draft", y2=0.31)
    lib.set_version_visibility("ecbp", v1, False, "GM")
    entry = gallery.entries_from_library()[0]
    assert not entry.version(v1).in_deep and not entry.version(v1).visible_in_deep
    assert entry.version(v2).in_deep, "a shown Draft runs in DEEP"
    for schema in (gallery.CATALOG_SCHEMA, gallery.CATALOG_SCHEMA_V2):
        doc = gallery.catalog_doc([entry], schema=schema)
        rows = dict((r["version"], r) for r in doc["assessments"][0]["versions"])
        assert rows[v1]["visibleInDeep"] is False
        assert "visibleInDeep" not in rows[v2], "a shown version lists exactly as before"
        back = gallery.parse_catalog(json.dumps(doc))[0]
        assert not back.version(v1).visible_in_deep and back.version(v2).visible_in_deep


def test_deeps_release_reader_withholds_the_hidden_version_and_1_0_0_still_parses_the_feed():
    remote = _deep_remote()
    v = {"version": 1, "status": "draft", "assets": {"bundle": {
        "name": "x-v1.deep.json", "size": 2, "sha256": "a" * 64}}}
    doc = {"schema": 1, "assessments": [{"id": "x", "versions": [
        v, dict(v, version=2, visibleInDeep=False)]}]}
    cat = remote.parse_catalog(json.dumps(doc))
    assert [(c.ref, c.eligible) for c in cat.versions] == [("x@v1", True), ("x@v2", False)]
    # StreamCurves 1.0.0 (installed copies) reads the same feed and ignores the key
    legacy = _load("streamcurves.legacy_gallery_1_0_0", APP / "tests" / "legacy" / "gallery_1_0_0.py")
    assert [x.version for x in legacy.parse_catalog(json.dumps(doc))[0].versions] == [2, 1]


def test_a_hidden_version_is_never_an_other_assessments_curve_source(libroot):
    _publish("preliminary")
    assert osrc.library_options("perImperv", region_code="99"), "offered while shown"
    lib.set_version_visibility("ecbp", 1, False, "GM")
    assert not [o for o in osrc.library_options("perImperv", region_code="99")
                if o["kind"] == osrc.OTHER]
