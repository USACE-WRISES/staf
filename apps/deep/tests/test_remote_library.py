"""The remote library: DEEP reads assessments from the STAF library release.

Every test here runs offline. ``remote_library.set_http_get`` routes each request
to an in-memory release (:class:`FakeRelease`), the disk cache is a tmp folder,
and STAF_LIBRARY_ROOT points at a folder that does not exist, so the real
``apps/library`` never joins the merge unless a test writes its own. The suite's
conftest keeps the remote library off for every other test.
"""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.parse

import pytest

from deep import assessments, calculator, config, library, remote_library

BASE = "https://release.test/library/"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(aid: str, version: int) -> str:
    return "sha256:" + _sha(f"{aid}@{version}".encode("utf-8"))


class FakeRelease:
    """An in-memory release: asset name -> bytes, and a log of every request."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.threads: list[str] = []
        self.gate: threading.Event | None = None  # when set, library.json waits on it
        self._lock = threading.Lock()

    def __call__(self, url, timeout):
        assert url.startswith(BASE), url
        assert timeout == remote_library.TIMEOUT
        name = urllib.parse.unquote(url[len(BASE):])
        with self._lock:
            self.calls.append(name)
            self.threads.append(threading.current_thread().name)
        if name == "library.json" and self.gate is not None:
            assert self.gate.wait(10), "the test never opened the gate"
        data = self.files.get(name)
        return (404, b"Not Found") if data is None else (200, data)

    def count(self, name: str) -> int:
        with self._lock:
            return self.calls.count(name)


def _bundle(aid: str, version: int, *, name: str | None = None) -> dict:
    region = {"kind": "ecoregion_l3", "code": "99", "name": "Test region"}
    return {
        "schemaVersion": 1,
        "tier": "detailed",
        "assessmentId": aid,
        "assessmentName": name or f"{aid} v{version}",
        "sourceCitation": "Remote library test",
        "region": region,
        "library": {"libraryId": aid, "version": version, "region": region},
        "contentDigest": _digest(aid, version),
        "metricsByFunction": [{
            "functionId": "catchment-hydrology",
            "functionName": "Catchment hydrology",
            "discipline": "Hydrology",
            "metrics": [{"metricId": "m1", "metricName": "M1",
                         "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}],
        }],
    }


def _version(release: FakeRelease, aid: str, version: int, status: str = "preliminary", *,
             bundle: dict | None = None, calculator: bytes | None = None,
             catalog_digest: str | None = None) -> dict:
    """One catalog version entry, in the schema-1 shape; its assets go into the release."""
    bundle = bundle if bundle is not None else _bundle(aid, version)
    data = json.dumps(bundle).encode("utf-8")
    bundle_name = f"{aid}-v{version}.deep.json"
    release.files[bundle_name] = data
    assets = {
        "bundle": {"name": bundle_name, "size": len(data), "sha256": _sha(data)},
        "calculator": None,
        # listed like the real release's, never in it: DEEP must not ask for them
        "pack": {"name": f"{aid}-v{version}-pack.zip", "size": 1, "sha256": "0" * 64},
        "thumbnail": {"name": f"{aid}-v{version}.png", "size": 1, "sha256": "1" * 64},
    }
    if calculator is not None:
        calc_name = f"{aid}-v{version}-calculator.xlsx"
        release.files[calc_name] = calculator
        assets["calculator"] = {"name": calc_name, "size": len(calculator),
                                "sha256": _sha(calculator)}
    return {
        "version": version,
        "status": status,
        "statusLabel": status.title(),
        "validation": "unvalidated",
        "publishedAt": "2026-09-22T20:10:00Z",
        "publishedBy": "tester",
        "contentDigest": (catalog_digest if catalog_digest is not None
                          else bundle.get("contentDigest")),
        "metrics": 1,
        "functionsCovered": 1,
        "assets": assets,
    }


def _publish(release: FakeRelease, *entries, schema: int = 1) -> dict:
    """Write library.json; each entry is ``(id, [version entries])``."""

    def latest(versions) -> int:
        return max((v["version"] for v in versions if isinstance(v.get("version"), int)),
                   default=0)

    doc = {
        "schema": schema,
        "generatedAt": "2026-09-23T12:00:00Z",
        "source": {"repo": "USACE-WRISES/staf", "commit": "abc123"},
        "assessments": [
            {"id": aid, "name": f"{aid} reference assessment",
             "region": {"kind": "ecoregion_l3", "code": "99", "name": "Test region"},
             "latestVersion": latest(versions),
             "defaultVersion": latest(versions),
             "versions": versions}
            for aid, versions in entries],
    }
    _put_catalog(release, doc)
    return doc


def _put_catalog(release: FakeRelease, doc: dict) -> None:
    release.files["library.json"] = json.dumps(doc).encode("utf-8")


def _write_local(root, aid: str, versions: list) -> None:
    """A local apps/library holding ``aid`` with ``[(version, bundle, status)]``."""
    adir = root / "assessments" / aid
    for version, bundle, _status in versions:
        vdir = adir / f"v{version}"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "assessment.deep.json").write_text(json.dumps(bundle), encoding="utf-8")
    (adir / "manifest.json").write_text(json.dumps({
        "schemaVersion": 2, "assessmentId": aid,
        "latestVersion": max(v for v, _b, _s in versions),
        "versions": [{"version": v} for v, _b, _s in versions]}), encoding="utf-8")
    (adir / "status.json").write_text(json.dumps({
        "schemaVersion": 2, "assessmentId": aid,
        "history": [{"version": v, "status": s} for v, _b, s in versions]}), encoding="utf-8")
    (root / "catalog.json").write_text(json.dumps({
        "schemaVersion": 1,
        "assessments": [{"assessmentId": aid,
                         "latestVersion": max(v for v, _b, _s in versions)}]}),
        encoding="utf-8")


@pytest.fixture
def release(tmp_path, monkeypatch):
    fake = FakeRelease()
    monkeypatch.setenv("DEEP_REMOTE_LIBRARY", "1")
    monkeypatch.setenv("DEEP_LIBRARY_URL", BASE)
    monkeypatch.setenv("DEEP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("DEEP_LIBRARY_TTL_S", raising=False)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "no-local-library"))
    remote_library.reset()
    remote_library.set_http_get(fake)
    calculator.clear_cache()
    yield fake
    remote_library.reset()
    remote_library.set_http_get(None)
    calculator.clear_cache()


def _refresh() -> None:
    assert remote_library.refresh(wait=True, timeout=10)


def _join() -> None:
    """Wait for the refresh a snapshot() call may have started."""
    lib = remote_library._state
    thread = lib.thread if lib is not None else None
    if thread is not None:
        thread.join(10)
        assert not thread.is_alive()


def _refs(snap) -> list[str]:
    return [r["assessmentRef"] for r in snap.records]


def _baked_visible() -> list[dict]:
    return [config._normalize_record(r) for r in config.assessments_doc()["assessments"]
            if not config._is_hidden(r)]


def _registry_refs() -> list[str]:
    return [r["assessmentRef"] for r in config._registry_records()]


# --------------------------------------------------------------------------- #
# the switch
# --------------------------------------------------------------------------- #
def test_the_suite_runs_with_the_remote_library_off():
    assert not remote_library.enabled()
    assert remote_library.snapshot() is remote_library.EMPTY


@pytest.mark.parametrize("value", ["0", "false", "off", "OFF"])
def test_off_means_no_network_no_cache_and_no_thread(release, tmp_path, monkeypatch, value):
    monkeypatch.setenv("DEEP_REMOTE_LIBRARY", value)
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1, calculator=b"PK-one")]))
    assert remote_library.snapshot() is remote_library.EMPTY
    assert remote_library.refresh(wait=True, timeout=1) is False
    assert remote_library.catch_up(1) is False
    assert remote_library.calculator_path("zz-alpha", 1) is None
    assert config.load_ref("zz-alpha@v1") is None
    assert release.calls == []
    assert remote_library._state is None
    assert not (tmp_path / "cache").exists()


# --------------------------------------------------------------------------- #
# library.json
# --------------------------------------------------------------------------- #
def test_unknown_fields_are_ignored(release):
    doc = _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1),
                                          _version(release, "zz-alpha", 2, "certified")]))
    doc["futureTopLevel"] = {"anything": [1, 2]}
    entry = doc["assessments"][0]
    entry["futureEntryField"] = "x"
    entry["versions"][0]["futureVersionField"] = True
    entry["versions"][0]["assets"]["bundle"]["futureAssetField"] = 7
    entry["versions"][0]["assets"]["somethingNew"] = {"name": "x", "size": 1, "sha256": "0" * 64}
    _put_catalog(release, doc)

    catalog = remote_library.parse_catalog(release.files["library.json"])
    assert catalog.schema == 1
    assert catalog.generated_at == "2026-09-23T12:00:00Z"
    assert [(v.ref, v.status, v.eligible) for v in catalog.versions] == [
        ("zz-alpha@v1", "preliminary", True), ("zz-alpha@v2", "certified", True)]
    first = catalog.versions[0]
    assert first.bundle.name == "zz-alpha-v1.deep.json"
    assert first.calculator is None
    assert first.content_digest == _digest("zz-alpha", 1).split(":", 1)[1]

    _refresh()
    assert _refs(remote_library.snapshot()) == ["zz-alpha@v1", "zz-alpha@v2"]


@pytest.mark.parametrize("raw", [
    b"not json", b"[]", b'{"assessments": []}', b'{"schema": 0, "assessments": []}',
    b'{"schema": "one", "assessments": []}', b'{"schema": true, "assessments": []}',
    b'{"schema": 1}', b'{"schema": 1, "assessments": {}}',
])
def test_an_unusable_catalog_is_refused(raw):
    with pytest.raises(remote_library.CatalogError):
        remote_library.parse_catalog(raw)


def test_a_newer_schema_is_never_used(release):
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1)]))
    _refresh()
    before = remote_library.snapshot()
    assert _refs(before) == ["zz-alpha@v1"]

    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1),
                                    _version(release, "zz-alpha", 2)]), schema=2)
    with pytest.raises(remote_library.NewerSchemaError):
        remote_library.parse_catalog(release.files["library.json"])
    _refresh()
    assert remote_library.snapshot() is before
    assert "schema 2" in remote_library.status()["lastError"]
    assert release.count("zz-alpha-v2.deep.json") == 0


def test_malformed_records_are_skipped_and_the_rest_kept(release):
    good = _version(release, "zz-alpha", 1)
    no_status = dict(_version(release, "zz-alpha", 2), status="")
    bad_sha = _version(release, "zz-alpha", 3)
    bad_sha["assets"]["bundle"]["sha256"] = "xyz"
    climbing = _version(release, "zz-alpha", 4)
    climbing["assets"]["bundle"]["name"] = "../../outside.deep.json"
    no_number = dict(_version(release, "zz-alpha", 5), version="five")
    second_v1 = _version(release, "zz-alpha", 1, "retired")
    retired = {"version": 6, "status": "retired"}  # needs no assets
    doc = _publish(release, ("zz-alpha", [good, no_status, bad_sha, climbing, no_number,
                                          second_v1, retired]))
    doc["assessments"] += [
        {"id": "../evil", "versions": [_version(release, "zz-beta", 1)]},
        {"id": "zz-alpha", "versions": [_version(release, "zz-alpha", 9)]},  # a second entry
        {"id": "zz-gamma", "versions": "none"},
        "not an entry",
    ]
    _put_catalog(release, doc)

    catalog = remote_library.parse_catalog(release.files["library.json"])
    assert [(v.ref, v.status) for v in catalog.versions] == [
        ("zz-alpha@v1", "preliminary"), ("zz-alpha@v6", "retired")]

    _refresh()
    snap = remote_library.snapshot()
    assert _refs(snap) == ["zz-alpha@v1"]
    assert snap.ineligible_refs == {"zz-alpha@v6"}
    assert release.calls == ["library.json", "zz-alpha-v1.deep.json"]


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #
def test_only_preliminary_and_certified_versions_are_served(release):
    statuses = ["preliminary", "certified", "draft", "under_review", "revised", "retired",
                "Preliminary"]
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", n, status)
                                    for n, status in enumerate(statuses, start=1)]))
    _refresh()
    snap = remote_library.snapshot()
    assert _refs(snap) == ["zz-alpha@v1", "zz-alpha@v2", "zz-alpha@v7"]
    assert [r["lifecycle"] for r in snap.records] == ["preliminary", "certified", "preliminary"]
    assert snap.ineligible_refs == {"zz-alpha@v3", "zz-alpha@v4", "zz-alpha@v5", "zz-alpha@v6"}
    assert snap.generation == 1
    assert snap.records[1] == {**_bundle("zz-alpha", 2), "version": 2,
                               "lifecycle": "certified", "assessmentRef": "zz-alpha@v2"}
    # nothing DEEP will not serve is downloaded: no ineligible bundle, no pack, no thumbnail
    assert sorted(release.calls) == sorted(["library.json", "zz-alpha-v1.deep.json",
                                            "zz-alpha-v2.deep.json", "zz-alpha-v7.deep.json"])


def test_records_are_stamped_like_the_local_library(release, tmp_path, monkeypatch):
    bundle = _bundle("zz-alpha", 3)
    root = tmp_path / "library"
    _write_local(root, "zz-alpha", [(3, bundle, "certified")])
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    local = library.all_eligible_bundles()
    assert len(local) == 1

    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 3, "certified", bundle=bundle)]))
    _refresh()
    assert list(remote_library.snapshot().records) == local


def test_a_bundle_that_is_not_the_catalogs_is_skipped(release):
    wrong_id = _version(release, "zz-alpha", 1, bundle=_bundle("zz-beta", 1))
    not_json = _version(release, "zz-alpha", 2)
    release.files["zz-alpha-v2.deep.json"] = b"{not json"
    not_json["assets"]["bundle"].update(size=9, sha256=_sha(b"{not json"))
    _publish(release, ("zz-alpha", [wrong_id, not_json, _version(release, "zz-alpha", 3)]))
    _refresh()
    snap = remote_library.snapshot()
    assert _refs(snap) == ["zz-alpha@v3"]
    assert snap.ineligible_refs == frozenset()


@pytest.mark.parametrize("tamper", ["sha256", "size"])
def test_an_asset_that_differs_from_the_catalog_is_rejected(release, tmp_path, tamper):
    v1 = _version(release, "zz-alpha", 1)
    _publish(release, ("zz-alpha", [v1]))
    _refresh()
    before = remote_library.snapshot()

    v2 = _version(release, "zz-alpha", 2)
    good = release.files["zz-alpha-v2.deep.json"]
    release.files["zz-alpha-v2.deep.json"] = (
        good.replace(b"zz-alpha v2", b"zz-alpha vX") if tamper == "sha256" else good + b" ")
    _publish(release, ("zz-alpha", [v1, v2]))
    _refresh()
    assert remote_library.snapshot() is before  # all or nothing: v1 stays, v2 never shows
    assert tamper in remote_library.status()["lastError"]
    assert not [p.name for p in (tmp_path / "cache" / "assets").iterdir()
                if "zz-alpha-v2" in p.name]

    release.files["zz-alpha-v2.deep.json"] = good  # the publisher fixes it; the retry takes it
    _refresh()
    assert _refs(remote_library.snapshot()) == ["zz-alpha@v1", "zz-alpha@v2"]


def test_a_bad_calculator_holds_the_refresh_back(release):
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1, calculator=b"PK-one")]))
    release.files["zz-alpha-v1-calculator.xlsx"] = b"PK-two"  # same size, other bytes
    _refresh()
    assert remote_library.snapshot() is remote_library.EMPTY
    assert "zz-alpha-v1-calculator.xlsx" in remote_library.status()["lastError"]


def test_a_restart_serves_the_disk_cache_before_any_network(release):
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1, calculator=b"PK-one"),
                                    _version(release, "zz-alpha", 2, "retired")]))
    _refresh()
    first = remote_library.snapshot()
    remote_library.reset()  # a restart: memory gone, disk cache kept

    gate = threading.Event()
    asked = []

    def offline(url, timeout):
        asked.append(url)
        gate.wait(10)
        raise ConnectionError("offline")

    remote_library.set_http_get(offline)
    snap = remote_library.snapshot()  # its refresh is held at the gate
    assert _refs(snap) == ["zz-alpha@v1"]
    assert snap.ineligible_refs == first.ineligible_refs == {"zz-alpha@v2"}
    assert snap.generation == 1
    assert remote_library.calculator_path("zz-alpha", 1).read_bytes() == b"PK-one"

    gate.set()
    _join()
    assert remote_library.snapshot() is snap  # the failed refresh kept it
    assert "offline" in remote_library.status()["lastError"]
    assert asked == [BASE + "library.json"]


def test_a_404_on_library_json_keeps_the_last_snapshot(release):
    v1 = _version(release, "zz-alpha", 1)
    _publish(release, ("zz-alpha", [v1]))
    _refresh()
    before = remote_library.snapshot()

    del release.files["library.json"]  # a publish is replacing it
    _refresh()
    assert remote_library.snapshot() is before
    assert "404" in remote_library.status()["lastError"]

    _publish(release, ("zz-alpha", [v1, _version(release, "zz-alpha", 2)]))
    _refresh()
    assert _refs(remote_library.snapshot()) == ["zz-alpha@v1", "zz-alpha@v2"]
    assert remote_library.snapshot().generation == 2
    assert remote_library.status()["lastError"] is None


def test_one_refresh_at_a_time_and_never_on_the_callers_thread(release):
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1)]))
    release.gate = threading.Event()
    assert remote_library.snapshot() is remote_library.EMPTY  # returns at once

    triggers = [threading.Thread(target=remote_library.refresh) for _ in range(2)]
    for thread in triggers:
        thread.start()
    for thread in triggers:
        thread.join(5)
    assert remote_library.snapshot().records == ()

    release.gate.set()
    _join()
    assert release.count("library.json") == 1
    assert _refs(remote_library.snapshot()) == ["zz-alpha@v1"]
    assert release.threads and all(name.startswith("deep-remote-library")
                                   for name in release.threads)


def test_refresh_follows_the_ttl_and_retries_a_failure_sooner(release, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(remote_library, "_clock", lambda: now[0])
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1)]))

    def tick(seconds: float) -> int:
        now[0] += seconds
        remote_library.snapshot()
        _join()
        return release.count("library.json")

    assert tick(0) == 1
    assert tick(599) == 1  # younger than the 600 s default
    assert tick(1) == 2
    saved = release.files.pop("library.json")
    assert tick(600) == 3  # fails with a 404
    assert tick(59) == 3
    assert tick(1) == 4  # a failure is retried after a minute, not the full TTL
    release.files["library.json"] = saved
    monkeypatch.setenv("DEEP_LIBRARY_TTL_S", "5")
    assert tick(60) == 5
    assert tick(4) == 5
    assert tick(1) == 6
    assert remote_library.snapshot().generation == 1  # the catalog never changed


# --------------------------------------------------------------------------- #
# the registry merge (deep/config.py)
# --------------------------------------------------------------------------- #
def test_an_empty_or_unreachable_remote_library_changes_nothing(release, monkeypatch):
    monkeypatch.setenv("DEEP_REMOTE_LIBRARY", "0")
    today = config._registry_records()
    monkeypatch.setenv("DEEP_REMOTE_LIBRARY", "1")

    _refresh()  # nothing published: a 404, and nothing cached
    assert remote_library.snapshot() is remote_library.EMPTY
    assert config._registry_records() == today

    _publish(release)  # reachable, listing nothing
    _refresh()
    snap = remote_library.snapshot()
    assert snap.generation == 1 and snap.records == () and not snap.ineligible_refs
    assert config._registry_records() == today


def test_remote_versions_beat_baked_ones_and_new_refs_are_appended(release):
    baked = _baked_visible()
    target = baked[0]
    aid, ver = target["assessmentId"], target["version"]
    _publish(release,
             (aid, [_version(release, aid, ver, bundle=_bundle(aid, ver, name="Remote copy"))]),
             ("zz-new", [_version(release, "zz-new", 1), _version(release, "zz-new", 2)]))
    _refresh()
    assert _registry_refs() == [r["assessmentRef"] for r in baked] + ["zz-new@v1", "zz-new@v2"]
    assert config.load_ref(target["assessmentRef"])["assessmentName"] == "Remote copy"
    assert config.default_ref_for("zz-new") == "zz-new@v2"


def test_the_local_library_beats_the_remote_one(release, tmp_path, monkeypatch):
    root = tmp_path / "library"
    _write_local(root, "zz-alpha", [(1, _bundle("zz-alpha", 1, name="Local copy"), "preliminary")])
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    _publish(release, ("zz-alpha", [
        _version(release, "zz-alpha", 1, bundle=_bundle("zz-alpha", 1, name="Remote copy")),
        _version(release, "zz-alpha", 2)]))
    _refresh()
    by_ref = config.assessments_by_ref()
    assert by_ref["zz-alpha@v1"]["assessmentName"] == "Local copy"
    assert by_ref["zz-alpha@v2"]["assessmentName"] == "zz-alpha v2"
    assert _registry_refs()[-2:] == ["zz-alpha@v1", "zz-alpha@v2"]


def test_a_version_the_catalog_retired_leaves_the_baked_registry(release, tmp_path, monkeypatch):
    baked = _baked_visible()
    target = baked[-1]
    aid, ver, ref = target["assessmentId"], target["version"], target["assessmentRef"]
    _publish(release, (aid, [_version(release, aid, ver, "retired")]))
    _refresh()
    assert _registry_refs() == [r["assessmentRef"] for r in baked if r["assessmentRef"] != ref]
    assert config.load_ref(ref) is None
    assert config.default_ref_for(aid) != ref

    # the live local library is never overruled: its copy of the ref comes back
    root = tmp_path / "library"
    _write_local(root, aid, [(ver, dict(target), "preliminary")])
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    assert ref in _registry_refs()


def test_hidden_assessments_stay_hidden(release):
    aid = "zz" + config._HIDDEN_ID_SUFFIXES[0]
    _publish(release, (aid, [_version(release, aid, 7)]))
    _refresh()
    assert _refs(remote_library.snapshot()) == [f"{aid}@v7"]  # in the snapshot...
    assert not [ref for ref in _registry_refs() if ref.startswith(aid + "@")]  # ...never in DEEP
    assert config.load_ref(f"{aid}@v7") is None


def test_a_link_to_a_remote_only_version_resolves(release):
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 8),
                                    _version(release, "zz-alpha", 9)]))
    _refresh()
    rec = config.load_ref("zz-alpha@v9")
    assert rec is not None and rec["version"] == 9 and rec["lifecycle"] == "preliminary"
    assert config.default_ref_for("zz-alpha") == "zz-alpha@v9"
    assert assessments.load_ref("zz-alpha@v9").assessment_name == "zz-alpha v9"


def test_a_link_opened_during_the_first_refresh_waits_for_it(release):
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1)]))
    release.gate = threading.Event()
    opener = threading.Timer(0.2, release.gate.set)
    opener.start()
    try:
        rec = config.load_ref("zz-alpha@v1")  # its snapshot() starts the first refresh
    finally:
        opener.join(5)
    assert rec is not None and rec["assessmentRef"] == "zz-alpha@v1"
    assert release.count("library.json") == 1


def test_an_unknown_ref_refreshes_at_most_every_30_seconds(release, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(remote_library, "_clock", lambda: now[0])
    v1 = _version(release, "zz-alpha", 1)
    _publish(release, ("zz-alpha", [v1]))
    _refresh()
    assert config.load_ref("zz-alpha@v1") is not None

    # v2 is published while the snapshot is well inside its TTL
    _publish(release, ("zz-alpha", [v1, _version(release, "zz-alpha", 2)]))
    now[0] += 60
    assert config.load_ref("zz-alpha@v2") is not None
    assert release.count("library.json") == 2

    now[0] += 29
    assert config.load_ref("zz-alpha@v3") is None
    assert config.default_ref_for("zz-unknown") is None
    assert release.count("library.json") == 2
    now[0] += 1
    assert config.load_ref("zz-alpha@v3") is None
    assert release.count("library.json") == 3


# --------------------------------------------------------------------------- #
# calculators
# --------------------------------------------------------------------------- #
def test_calculator_path_gives_the_cached_release_workbook(release):
    calc = b"PK\x03\x04 remote workbook"
    two = _bundle("zz-alpha", 2)
    _publish(release, ("zz-alpha", [
        _version(release, "zz-alpha", 1),
        _version(release, "zz-alpha", 2, bundle=two, calculator=calc),
        _version(release, "zz-alpha", 3, "draft", calculator=b"PK draft")]))
    _refresh()
    path = remote_library.calculator_path("zz-alpha", 2)
    assert path is not None and path.read_bytes() == calc
    assert remote_library.calculator_path("zz-alpha", "2", two["contentDigest"]) == path
    assert remote_library.calculator_path("zz-alpha", 2, "sha256:" + "0" * 64) is None
    assert remote_library.calculator_path("zz-alpha", 1) is None  # published without one
    assert remote_library.calculator_path("zz-alpha", 3) is None  # a draft
    assert release.count("zz-alpha-v3-calculator.xlsx") == 0  # never even fetched
    assert remote_library.calculator_path("zz-alpha", "two") is None


def test_template_for_serves_a_remote_only_version_its_workbook(release, tmp_path, monkeypatch):
    calc = b"PK\x03\x04 remote workbook"
    two = _bundle("zz-alpha", 2)
    bare_hex = two["contentDigest"].split(":", 1)[1]  # the catalog may omit the prefix
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 2, bundle=two,
                                             calculator=calc, catalog_digest=bare_hex)]))
    _refresh()
    la = assessments.load_ref("zz-alpha@v2")
    nothing_baked = tmp_path / "no-calculators"
    assert calculator.template_for(la, nothing_baked) == calc

    # built for other curves: never handed out
    other = assessments.LoadedAssessment.from_dict({**la.raw, "contentDigest": "sha256:" + "1" * 64})
    assert calculator.template_for(other, nothing_baked) is None

    # a baked workbook recorded for the same content still comes first
    baked = tmp_path / "baked"
    baked.mkdir()
    (baked / "zz-alpha@v2.xlsx").write_bytes(b"PK baked")
    (baked / "index.json").write_text(json.dumps({"schemaVersion": 1, "calculators": {
        "zz-alpha@v2": {"file": "zz-alpha@v2.xlsx", "contentDigest": two["contentDigest"]}}}),
        encoding="utf-8")
    calculator.clear_cache()
    assert calculator.template_for(la, baked) == b"PK baked"

    # with the remote library off, the remote-only version has no workbook
    monkeypatch.setenv("DEEP_REMOTE_LIBRARY", "0")
    assert calculator.template_for(la, nothing_baked) is None


def test_a_local_folder_can_stand_in_for_the_release(release, tmp_path, monkeypatch):
    _publish(release, ("zz-alpha", [_version(release, "zz-alpha", 1, calculator=b"PK-one")]))
    folder = tmp_path / "release-folder"
    folder.mkdir()
    for name, data in release.files.items():
        (folder / name).write_bytes(data)
    monkeypatch.setenv("DEEP_LIBRARY_URL", str(folder))
    _refresh()
    assert _refs(remote_library.snapshot()) == ["zz-alpha@v1"]
    assert remote_library.calculator_path("zz-alpha", 1).read_bytes() == b"PK-one"
    assert release.calls == []  # nothing went through the network function
