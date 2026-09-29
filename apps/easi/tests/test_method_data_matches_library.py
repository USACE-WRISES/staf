"""The method files EASI ships are the library's EASI version, byte for byte.

The drift gate of the authority flip (``apps/stream-curves/AUTHORING.md``, Authority;
ADOPTION step 3): ``apps/easi/data`` holds exactly the method files of the library's default
EASI version (``apps/library/assessments/easi-screening``), ``method_version()`` is the one
that version records, and the three scripts that used to write method files refuse to while
that holds (``easi.method_authority``). Red until the two agree; skipped where no library
sits beside EASI (a deployed copy, the vendored copy).

``EASI_EXPORTED_PACKAGE=<pkg.zip>`` points the parity test at a package the exporter wrote
(``export_easi_method.py``); unset, the package is built from the library version itself.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
from pathlib import Path

import pytest

from easi import method_authority as ma
from easi import method_package as mp
from easi.national import method_version

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT.parent / "library" / "assessments" / ma.ASSESSMENT_ID
SCRIPTS = ROOT / "scripts"
SCRIPT_NAMES = ("build_easi_metrics", "fetch_nars_ecoregions", "promote_alternative_2")

pytestmark = pytest.mark.skipif(not (ENTRY / "manifest.json").is_file(),
                                reason="no assessment library beside this EASI")


def _manifest() -> dict:
    return json.loads((ENTRY / "manifest.json").read_text(encoding="utf-8"))


def _manifest_row(version: int) -> dict:
    return next(v for v in _manifest()["versions"] if int(v["version"]) == version)


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def test_the_gate_reads_the_manifests_default_version():
    assert ma.library_entry() == ENTRY
    manifest = _manifest()
    want = manifest.get("defaultVersion") or manifest.get("latestVersion")
    assert ma.default_version() == int(want)
    assert ma.version_dir().is_dir()


def test_every_method_file_equals_the_library_version():
    files = ma.library_files()
    assert set(files) == set(mp.METHOD_FILES)
    differing = ma.differences()
    assert differing == [], (
        f"apps/easi/data differs from the library's EASI v{ma.default_version()} in {differing}: "
        "write it with apps/stream-curves/scripts/export_easi_method.py --write-easi-data, "
        "or publish the changed method as a new library version first")
    for name, blob in files.items():
        assert (mp.builtin_data_dir() / name).read_bytes() == blob


def test_the_method_version_is_the_one_the_library_records():
    """The manifest row names the version's published identity; the running evaluator's
    method version is the one the envelope's validatedUnder records for this evaluator
    (the published pair first, a later validated evaluator adds a row); under an evaluator
    the library has not validated the lookup falls back to the published one and this
    gate reads red, as designed."""
    version = ma.default_version()
    published = ma.published_method_version()
    assert published == _manifest_row(version)["methodVersion"]
    recorded = ma.recorded_method_version()
    assert method_version() == recorded
    assert mp.method_version_for("regional", ma.library_files()) == recorded
    rows = ma.validated_under()
    assert rows[0]["methodVersion"] == published
    assert any(r.get("evaluatorDigest") == mp.evaluator_digest() and r.get("methodVersion") == recorded
               for r in rows)
    # an evaluator the library never validated reads the published version, never a new one
    assert ma.recorded_method_version(evaluator_digest="sha256:" + "0" * 64) == published


def test_the_authority_is_active_in_this_checkout():
    assert ma.authority_active() is True


def test_the_authority_is_inactive_without_a_library_entry(monkeypatch):
    monkeypatch.setattr(ma, "library_entry", lambda: None)
    assert ma.authority_active() is False


def test_the_authority_is_inactive_when_a_method_file_differs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    for name in mp.METHOD_FILES:
        (data / name).write_bytes((mp.builtin_data_dir() / name).read_bytes())
    assert ma.differences(data_dir=data) == []
    (data / "functions.json").write_bytes((data / "functions.json").read_bytes() + b"\n")
    assert ma.differences(data_dir=data) == ["functions.json"]
    monkeypatch.setattr(mp, "builtin_data_dir", lambda: data)
    assert ma.authority_active() is False
    (data / "cwa-mapping.json").unlink()
    assert sorted(ma.differences(data_dir=data)) == ["cwa-mapping.json", "functions.json"]


def test_refuse_direct_write_names_the_exporter():
    with pytest.raises(SystemExit) as exc:
        ma.refuse_direct_write("some_script.py")
    message = str(exc.value)
    assert "some_script.py" in message
    assert "export_easi_method.py" in message and "--write-easi-data" in message
    assert ma.DIRECT_WRITE_FLAG in message


# --------------------------------------------------------------------------- #
# the three direct writers
# --------------------------------------------------------------------------- #
def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"easi_script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, doc):
        self._doc = doc

    def raise_for_status(self):
        return None

    def json(self):
        return self._doc


_FEATURES = {"type": "FeatureCollection", "features": [
    {"type": "Feature",
     "properties": {"WSA_9": "CPL", "WSA_9_NM": "Coastal Plains", "ECOREGIONS": "x"},
     "geometry": {"type": "Polygon",
                  "coordinates": [[[-80.0, 35.0], [-79.0, 35.0], [-79.0, 36.0], [-80.0, 36.0],
                                   [-80.0, 35.0]]]}}]}


def _prepare(name: str, tmp_path, monkeypatch):
    """The script module with its output under tmp_path and its side effects stubbed:
    (module, argv, the output it would write, the calls its stubs saw)."""
    module = _script(name)
    calls: list = []
    if name == "build_easi_metrics":
        out = tmp_path / "easi-metrics.json"
        monkeypatch.setattr(module, "OUT_JSON", out)
        monkeypatch.setattr(module, "ROOT", tmp_path)     # its report names the path relative to ROOT
        return module, [], out, calls
    if name == "fetch_nars_ecoregions":
        out = tmp_path / "nars-ecoregions-9.geojson.gz"
        monkeypatch.setattr(module, "OUT", out)

        def get(url, params=None, timeout=None):
            calls.append(url)
            return _Response(_FEATURES)

        monkeypatch.setattr(module.requests, "get", get)
        return module, [], out, calls
    dest = tmp_path / "dest"

    def promote(study, destination):
        calls.append((study, destination))
        dest.mkdir(exist_ok=True)
        (dest / "receipt.json").write_text("{}", encoding="utf-8")
        return {"stub": True}

    monkeypatch.setattr(module, "promote", promote)
    # the refusal guards the app data folder only; the stub writes into tmp whatever the
    # destination says, so the default destination is safe to name here
    argv = ["--study", str(tmp_path / "2026-09-15-controlled-alternatives"),
            "--destination", str(module.APP / "data")]
    return module, argv, dest / "receipt.json", calls


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_a_direct_writer_refuses_while_the_authority_is_active(name, tmp_path, monkeypatch):
    module, argv, out, calls = _prepare(name, tmp_path, monkeypatch)
    monkeypatch.setattr(ma, "authority_active", lambda: True)
    with pytest.raises(SystemExit) as exc:
        module.main(argv)
    assert "export_easi_method.py" in str(exc.value) and f"{name}.py" in str(exc.value)
    assert not out.exists() and calls == []


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_a_direct_writer_honors_the_transition_flag(name, tmp_path, monkeypatch):
    module, argv, out, calls = _prepare(name, tmp_path, monkeypatch)
    monkeypatch.setattr(ma, "authority_active", lambda: True)
    assert module.main([*argv, ma.DIRECT_WRITE_FLAG]) == 0
    assert out.is_file()
    if name == "build_easi_metrics":
        assert json.loads(out.read_text(encoding="utf-8"))["count"] == 20
    elif name == "fetch_nars_ecoregions":
        doc = json.loads(gzip.decompress(out.read_bytes()).decode("utf-8"))
        assert doc["features"][0]["properties"]["WSA_9"] == "CPL"
        assert calls == [module.URL]
    else:
        assert len(calls) == 1


def test_a_promotion_into_another_folder_is_never_refused(tmp_path, monkeypatch):
    module, argv, out, calls = _prepare("promote_alternative_2", tmp_path, monkeypatch)
    monkeypatch.setattr(ma, "authority_active", lambda: True)
    elsewhere = [*argv[:-1], str(tmp_path / "rehearsal")]
    assert module.main(elsewhere) == 0
    assert len(calls) == 1 and calls[0][1] == tmp_path / "rehearsal"


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_a_direct_writer_runs_as_before_while_the_authority_is_inactive(name, tmp_path, monkeypatch):
    module, argv, out, calls = _prepare(name, tmp_path, monkeypatch)
    monkeypatch.setattr(ma, "authority_active", lambda: False)
    assert module.main(argv) == 0
    assert out.is_file()


# --------------------------------------------------------------------------- #
# scoring parity: the library version, or the package the exporter wrote from it
# --------------------------------------------------------------------------- #
@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv(mp.ENV_CACHE, str(tmp_path / "cache"))
    return tmp_path / "cache"


def _library_package() -> mp.MethodPackage:
    """The default library version as the package the release serves (envelope, method
    files, calculator), read back through the package reader so it is verified."""
    vdir = ma.version_dir()
    envelope = json.loads((vdir / mp.ENVELOPE).read_text(encoding="utf-8"))
    calculator = None
    cdir = vdir / mp.CALCULATOR_DIR
    found = sorted(cdir.glob("*.xlsx")) if cdir.is_dir() else []
    if found:
        calculator = (found[0].name, found[0].read_bytes())
    blob = mp.to_zip(mp.MethodPackage(envelope=envelope, files=ma.library_files(),
                                      calculator=calculator))
    return mp.read_package(blob)


def test_the_exported_package_scores_every_case_like_the_builtin_method(tmp_path, cache):
    from test_method_package import RELEASE_METHOD, _run
    library = _library_package()
    exported = os.environ.get("EASI_EXPORTED_PACKAGE")
    if exported:
        pkg = mp.read_package(exported)
        assert pkg.files == library.files and pkg.envelope == library.envelope
        assert pkg.calculator == library.calculator
        path = Path(exported)
    else:
        path = tmp_path / "library-method.zip"
        path.write_bytes(mp.to_zip(library))
    out = _run({mp.ENV_PACKAGE: str(path), mp.ENV_CACHE: str(cache)})
    assert out["ident"]["source"] == "package"
    assert out["ident"]["methodVersion"] == RELEASE_METHOD == ma.recorded_method_version()
    assert out["ident"]["packageDigest"] == library.digest
    assert library.digest == _manifest_row(ma.default_version())["contentDigest"]
    fixture = json.loads((ROOT / "tests" / "data" / "calculator_cases.json").read_text(encoding="utf-8"))
    expected = {c["id"]: c["expected"] for c in fixture["cases"]}
    assert len(expected) == 792
    assert out["results"] == expected
