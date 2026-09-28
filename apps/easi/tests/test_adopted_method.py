"""The adopted method (owner decision D20, 2026-09-28): EASI scores with the pinned
library version, from a verified cache, the library release or the bundled copy
of that same version, and never with anything else."""
from __future__ import annotations

import hashlib
import json

import pytest

from easi import adopted_method as am
from easi import method_package as mp

BASE = "https://example.invalid/library/"


def _package(version: int = 9) -> tuple[bytes, dict]:
    data = mp.builtin_data_dir()
    files = {n: (data / n).read_bytes() for n in mp.METHOD_FILES}
    env = mp.build_envelope(files, method_id="easi-screening", version=version, label="test")
    pkg = mp.MethodPackage(envelope=env, files=files)
    blob = mp.to_zip(pkg)
    sha = hashlib.sha256(blob).hexdigest()
    pin = {"schema": am.PIN_SCHEMA, "assessmentId": "easi-screening", "version": version,
           "packageDigest": pkg.digest, "methodVersion": pkg.identity["methodVersion"],
           "package": {"name": f"easi-screening-v{version}-{sha[:8]}.easi-method.zip",
                       "sha256": sha, "bytes": len(blob)}}
    return blob, pin


def _feed(pin: dict, *, extra_versions=(), sha=None) -> bytes:
    versions = [{"version": pin["version"], "methodVersion": pin["methodVersion"],
                 "assets": {"method": {"name": pin["package"]["name"],
                                       "sha256": sha or pin["package"]["sha256"]}}}]
    versions += [{"version": v, "methodVersion": "x", "assets": {"method": {
        "name": f"easi-screening-v{v}.easi-method.zip", "sha256": "0" * 64}}}
        for v in extra_versions]
    doc = {"schema": 2, "assessments": [
        {"type": "easi", "id": "easi-screening", "versions": versions}]}
    return json.dumps(doc).encode("utf-8")


@pytest.fixture
def http():
    served: dict = {}
    calls: list = []

    def get(url, timeout):
        calls.append(url)
        if url not in served:
            raise ConnectionError("offline")
        return 200, served[url]

    old = am.set_http_get(get)
    yield served, calls
    am.set_http_get(old)


def test_the_release_is_verified_cached_and_a_newer_version_ignored(http, tmp_path):
    served, calls = http
    blob, pin = _package()
    served[BASE + am.FEED_NAME] = _feed(pin, extra_versions=(10,))
    served[BASE + pin["package"]["name"]] = blob
    got = am.resolve(pin, base_url=BASE, cache=tmp_path)
    assert got["source"] == "release" and got["path"].endswith(pin["package"]["name"])
    assert (tmp_path / pin["package"]["name"]).read_bytes() == blob
    assert any("newer" in n and "keeps v9" in n for n in got["notes"])
    # the next start reads the verified cache and needs no network
    served.clear()
    calls.clear()
    again = am.resolve(pin, base_url=BASE, cache=tmp_path)
    assert again["source"] == "cache" and calls == []


def test_offline_the_bundled_copy_of_the_same_version_is_used(http, tmp_path):
    _blob, pin = _package()
    got = am.resolve(pin, base_url=BASE, cache=tmp_path)
    assert got["source"] == "bundled" and got["path"] is None
    assert any("offline" in n for n in got["notes"])


def test_a_package_that_is_not_the_pinned_file_is_refused(http, tmp_path):
    served, _calls = http
    blob, pin = _package()
    served[BASE + am.FEED_NAME] = _feed(pin)
    served[BASE + pin["package"]["name"]] = blob + b"tampered"
    got = am.resolve(pin, base_url=BASE, cache=tmp_path)
    assert got["source"] == "bundled"
    assert any("sha256 is not the pin's" in n for n in got["notes"])
    # a feed that lists another file for the pinned version is refused before any download
    served[BASE + am.FEED_NAME] = _feed(pin, sha="f" * 64)
    got = am.resolve(pin, base_url=BASE, cache=tmp_path)
    assert got["source"] == "bundled"
    assert any("not the pinned file" in n for n in got["notes"])


def test_nothing_else_is_ever_substituted(http, tmp_path):
    """A pin the bundled files are not, with no verifiable download, stops startup."""
    _blob, pin = _package()
    other = dict(pin, packageDigest="sha256:" + "1" * 64)
    with pytest.raises(am.AdoptedMethodError, match="Nothing else is substituted"):
        am.resolve(other, base_url=BASE, cache=tmp_path)


def test_apply_to_env_honors_the_override_the_switch_and_an_absent_pin(monkeypatch, tmp_path):
    monkeypatch.setenv(mp.ENV_PACKAGE, str(tmp_path / "some.zip"))
    assert am.apply_to_env()["source"] == "override"
    monkeypatch.delenv(mp.ENV_PACKAGE)
    monkeypatch.setenv(am.ENV_SWITCH, "0")
    assert am.apply_to_env()["source"] == "off"
    monkeypatch.delenv(am.ENV_SWITCH)
    monkeypatch.setattr(am, "pin_path", lambda: tmp_path / "absent.json")
    assert am.apply_to_env()["source"] == "unpinned"
    assert "no adopted version pinned" in am.status_line()


def test_apply_to_env_points_the_package_variable_at_the_verified_download(http, monkeypatch, tmp_path):
    served, _calls = http
    blob, pin = _package()
    served[BASE + am.FEED_NAME] = _feed(pin)
    served[BASE + pin["package"]["name"]] = blob
    pin_file = tmp_path / am.PIN_NAME
    pin_file.write_text(json.dumps(pin), encoding="utf-8")
    monkeypatch.setattr(am, "pin_path", lambda: pin_file)
    monkeypatch.setenv(am.ENV_URL, BASE)
    monkeypatch.setenv(mp.ENV_CACHE, str(tmp_path / "cache"))
    monkeypatch.delenv(mp.ENV_PACKAGE, raising=False)
    monkeypatch.delenv(am.ENV_SWITCH, raising=False)
    got = am.apply_to_env()
    assert got["source"] == "release" and got["version"] == 9
    import os
    assert os.environ[mp.ENV_PACKAGE] == got["path"]
    assert "easi-screening v9" in am.status_line() and "verified" in am.status_line()
    monkeypatch.delenv(mp.ENV_PACKAGE)
