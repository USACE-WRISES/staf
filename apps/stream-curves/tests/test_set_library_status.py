"""scripts/set_library_status.py: one audited status record per selected version, the catalog
rebuilt once, nothing else a version holds touched (owner, 2026-10-08: every Preliminary version
of the Level III ecoregions becomes a Draft, except Northeastern Highlands and Eastern Corn Belt
Plains). STAF_LIBRARY_ROOT points at a tmp dir, so nothing here touches apps/library."""
from __future__ import annotations

import errno

import pytest

from streamcurves import library as lib
from test_library_types import _load
from test_library_v2 import _bundle, _session_payload
from test_library_visibility import APP

SCRIPT = APP / "scripts" / "set_library_status.py"


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    return root


@pytest.fixture
def sls():
    return _load("set_library_status_under_test", SCRIPT)


def _publish(aid, kind="ecoregion", status="preliminary", y2=0.3):
    meta = {"assessmentName": aid, "region": {"kind": kind, "code": "1", "name": aid}}
    return lib.publish_version(aid, meta, _session_payload(), _bundle(y2), status=status)


@pytest.fixture
def library(libroot):
    _publish("alpha")
    _publish("alpha", status="draft", y2=0.31)
    _publish("alpha", y2=0.32)
    _publish("keep-me")
    _publish("bravo")
    _publish("state-x", kind="state")
    _publish("ohio-sqt-adapted", kind="state")
    return libroot


def _state(aid):
    return [lib.version_status(aid, v) for v in range(1, lib.latest_version(aid) + 1)]


def test_the_selection_is_every_from_status_version_of_the_kind_minus_the_exclusions(library, sls):
    changes = sls.select(to="draft", frm="preliminary", kind="ecoregion", exclude=("keep-me",))
    assert changes == [("alpha", 1, "preliminary"), ("alpha", 3, "preliminary"),
                       ("bravo", 1, "preliminary")]
    assert sls.select(to="draft", frm="preliminary", only=("state-x",)) == [
        ("state-x", 1, "preliminary")]
    assert not [c for c in sls.select(to="draft") if c[0].endswith("-sqt-adapted")], \
        "DEEP does not list the SQT transcriptions: never touched"


def test_a_misspelled_exclusion_is_refused_before_anything_is_written(library, sls):
    with pytest.raises(SystemExit, match="keep-mee"):
        sls.select(to="draft", frm="preliminary", exclude=("keep-mee",))
    with pytest.raises(SystemExit, match="ohio-sqt-adapted"):
        sls.select(to="draft", only=("ohio-sqt-adapted",))


def test_a_dry_run_lists_and_writes_nothing(library, sls, capsys):
    before = dict((p, p.read_bytes()) for p in library.rglob("*.json"))
    assert sls.main(["--to", "draft", "--from", "preliminary", "--kind", "ecoregion",
                     "--exclude", "keep-me", "--actor", "GM", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "alpha v1: Preliminary -> Draft" in out and "keep-me" not in out
    assert "3 version(s) in 2 assessment(s)" in out and "dry run: nothing written" in out
    assert dict((p, p.read_bytes()) for p in library.rglob("*.json")) == before


def test_the_change_appends_one_record_each_and_rebuilds_the_catalog_once(library, sls,
                                                                          monkeypatch, capsys):
    rebuilt = []
    real = lib._regenerate_catalog
    monkeypatch.setattr(lib, "_regenerate_catalog", lambda: (rebuilt.append(1), real())[1])
    manifests = dict((p, p.read_bytes()) for p in library.rglob("manifest.json"))
    bundles = dict((p, p.read_bytes()) for p in library.rglob("assessment.deep.json"))
    assert sls.main(["--to", "draft", "--from", "preliminary", "--kind", "ecoregion",
                     "--exclude", "keep-me", "--actor", "GM", "--note", "Owner decision."]) == 0
    assert len(rebuilt) == 1
    assert _state("alpha") == ["draft", "draft", "draft"]
    assert _state("bravo") == ["draft"]
    assert _state("keep-me") == ["preliminary"] and _state("state-x") == ["preliminary"]
    rec = lib.read_status("alpha")["history"][-1]
    assert (rec["version"], rec["status"], rec["actor"], rec["note"]) == (3, "draft", "GM",
                                                                          "Owner decision.")
    assert len(lib.read_status("bravo")["history"]) == 2, "one record appended, none rewritten"
    entry = next(e for e in lib.list_assessments() if e["assessmentId"] == "alpha")
    assert (entry["deepDefaultVersion"], entry["deepDefaultStatus"]) == (3, "draft")
    # nothing a version holds moved
    assert dict((p, p.read_bytes()) for p in library.rglob("manifest.json")) == manifests
    assert dict((p, p.read_bytes()) for p in library.rglob("assessment.deep.json")) == bundles
    out = capsys.readouterr().out
    assert "recorded 3 status change(s)" in out
    assert "DEEP registry not rebaked: " in out and "(not the canonical" in out, \
        "DEEP's registry is rebuilt from apps/library only"


def test_final_is_never_set_in_bulk(library, sls, capsys):
    with pytest.raises(SystemExit):
        sls.main(["--to", "certified", "--actor", "GM"])
    assert "invalid choice" in capsys.readouterr().err


def test_an_empty_actor_is_refused(library, sls):
    with pytest.raises(SystemExit, match="actor"):
        sls.main(["--to", "draft", "--actor", " "])


def test_the_canonical_library_needs_the_publish_flag(sls, monkeypatch):
    monkeypatch.delenv("STAF_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    monkeypatch.setattr(sls, "apply", lambda *a, **k: pytest.fail("wrote without the flag"))
    with pytest.raises(SystemExit, match="STAF_LIBRARY_PUBLISH=1"):
        sls.main(["--to", "draft", "--from", "preliminary", "--kind", "ecoregion",
                  "--actor", "GM", "--no-rebake"])


def test_a_write_held_for_a_moment_is_tried_again(library, sls, monkeypatch):
    monkeypatch.setattr(sls, "RETRY_WAIT_S", 0)
    calls = []
    real = lib._append_status

    def flaky(*args):
        calls.append(args[:2])
        if len(calls) == 1:
            raise OSError(errno.EINVAL, "Invalid argument")
        return real(*args)
    monkeypatch.setattr(lib, "_append_status", flaky)
    sls.apply([("bravo", 1, "preliminary")], to="draft", actor="GM", note=None)
    assert calls == [("bravo", 1), ("bravo", 1)] and _state("bravo") == ["draft"]

    def broken(*args):
        raise OSError(errno.EACCES, "denied")
    monkeypatch.setattr(lib, "_append_status", broken)
    with pytest.raises(PermissionError):
        sls.apply([("bravo", 1, "draft")], to="preliminary", actor="GM", note=None)


def test_the_library_readme_names_the_script_and_the_visibility_record():
    text = (APP.parent / "library" / "README.md").read_text(encoding="utf-8")
    assert "set_library_status.py" in text and "visibility.json" in text
