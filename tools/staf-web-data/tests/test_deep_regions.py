"""deep_regions.py lists what DEEP lists, from the library release alone."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from webdata import deep_regions  # noqa: E402

REPO = HERE.parents[1]
DEEP = REPO / "apps" / "deep"
LIBRARY = Path(os.environ.get("STAF_WEB_LIBRARY") or r"D:\Data\staf-web\library-release")
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}


def _write(folder: Path, name: str, doc: dict) -> dict:
    data = json.dumps(doc).encode()
    (folder / name).write_bytes(data)
    return {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_default_version_and_hidden(tmp_path):
    def bundle(aid, version, region="R"):
        return {"assessmentId": aid, "library": {"version": version, "region": {"name": region, "code": "45",
                                                                                 "polygon": SQUARE}}}
    entries = []
    for aid, versions in (("a", [(1, "certified"), (2, "draft"), (3, "preliminary")]),
                          ("b", [(1, "draft"), (2, "draft")]),
                          ("c", [(1, "retired"), (2, "certified")]),
                          ("d", [(1, "preliminary")]),
                          ("x-sqt-adapted", [(1, "certified")])):
        listed = []
        for number, status in versions:
            asset = _write(tmp_path, f"{aid}-v{number}.deep.json", bundle(aid, number))
            raw = {"version": number, "status": status, "assets": {"bundle": asset}}
            if aid == "d":
                raw["visibleInDeep"] = False
            listed.append(raw)
        entries.append({"id": aid, "versions": listed})
    (tmp_path / "library.json").write_text(json.dumps({"schema": 1, "assessments": entries}))
    got = {f["assessmentId"]: f for f in deep_regions.payload(tmp_path)["features"]}
    # a: the newest Final (v1) over a newer Preliminary and Draft; b: the newest Draft; c: Final v2;
    # d is hidden by Show in DEEP and x by DEEP's hidden suffix
    assert {k: (f["version"], f["lifecycle"], f["status"]) for k, f in got.items()} == {
        "a": (1, "certified", "Final"), "b": (2, "draft", "Draft"), "c": (2, "certified", "Final")}
    assert got["a"]["name"] == "R" and got["a"]["code"] == "45" and got["a"]["geometry"] == SQUARE
    # what Change shows: the default's name and region name, every eligible version's reference tier
    assert got["a"]["assessmentName"] == "a" and got["a"]["regionName"] == "R"
    assert got["a"]["referenceTierByRef"] == {"a@v1": None, "a@v2": None, "a@v3": None}


@pytest.mark.skipif(not (DEEP / "deep").is_dir() or not (LIBRARY / "library.json").is_file(),
                    reason="needs apps/deep and the downloaded library release")
def test_same_as_deep(tmp_path, monkeypatch):
    ours = {f["assessmentId"]: f for f in deep_regions.payload(LIBRARY)["features"]}
    monkeypatch.setenv("DEEP_REMOTE_LIBRARY", "1")
    monkeypatch.setenv("DEEP_LIBRARY_URL", str(LIBRARY.resolve()) + os.sep)
    monkeypatch.setenv("DEEP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "no-live-library"))   # as deployed
    monkeypatch.syspath_prepend(str(DEEP))
    from deep import assessments, remote_library, session
    assert remote_library.refresh(wait=True, timeout=300)
    theirs = {}
    for f in assessments.library_region_features().get("features") or []:      # app.py _send_coverage
        p = f.get("properties") or {}
        life = session.lifecycle_status({"lifecycle": p.get("lifecycle")})
        theirs[p.get("assessmentId")] = {
            "assessmentId": p.get("assessmentId"),
            "name": p.get("regionName") or p.get("assessmentName") or p.get("assessmentId"),
            "code": p.get("regionCode") or "", "version": p.get("version"),
            "status": session.status_label(life), "lifecycle": life, "certified": life == "certified",
            "geometry": f.get("geometry")}
    assert len(ours) == len(theirs) > 0
    change = ("assessmentName", "regionName", "referenceTierByRef")
    assert dict((k, dict((x, y) for x, y in f.items() if x not in change)) for k, f in ours.items()) == theirs
    # Change's fields as covering_refs reads them from DEEP's registry records
    from deep import config
    records = config._registry_records()
    for aid, f in ours.items():
        recs = [r for r in records if r.get("assessmentId") == aid]
        assert f["referenceTierByRef"] == dict((r["assessmentRef"], r.get("referenceTier")) for r in recs), aid
        default = next(r for r in recs if r["version"] == max(r2["version"] for r2 in recs if r2.get("lifecycle") == f["lifecycle"]))
        region = default.get("region") or (default.get("library") or {}).get("region") or {}
        assert (f["assessmentName"], f["regionName"]) == (default.get("assessmentName", aid), region.get("name", "")), aid
