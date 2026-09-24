"""EASI method packages: the built-in method round-trips byte for byte, damaged or
foreign packages are refused whole, and activation never mixes two methods.

The release method is ``b2e3033116e3`` (README release record). A package of the
built-in files must reproduce it, score the calculator cases exactly like the
built-in method, and regenerate the committed calculator workbook byte for byte.
"""
from __future__ import annotations

import io
import json
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from easi import config, method_package as mp
from easi.national import method_version

ROOT = Path(__file__).resolve().parents[1]
RELEASE_METHOD = "b2e3033116e3"
PY = sys.executable


def _builtin_pkg() -> mp.MethodPackage:
    return mp.package_from_dir(mp.builtin_data_dir(), version=1, label="Alternative 2: NARS-9 references")


def _edited_pkg(edge: float = 1.5) -> mp.MethodPackage:
    """The built-in method with the reach-inflow road-density Good edge moved (a test
    edit, never a method change)."""
    files = dict(_builtin_pkg().files)
    cat = json.loads(files["screening-methods.json"].decode("utf-8"))
    for m in cat["methods"]:
        if m["methodKey"] == "road-density-inflow-pressure":
            for b in m["bands"]:
                if b["rating"] == "Good":
                    b["max"] = edge
                elif b["rating"] == "Fair":
                    b["min"] = edge
    files["screening-methods.json"] = (json.dumps(cat, indent=2, sort_keys=True)
                                       .replace("\n", "\r\n") + "\r\n").encode("utf-8")
    return _repackage(files, version=2)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv(mp.ENV_CACHE, str(tmp_path / "cache"))
    return tmp_path / "cache"


@pytest.fixture
def restore():
    yield
    mp.activate(None)
    assert method_version() == RELEASE_METHOD


def test_the_digest_file_lists_match_the_method_version():
    from easi import national
    assert mp.DIGEST_SOURCES == national._METHOD_SOURCES
    assert mp.DIGEST_DATA == national._METHOD_DATA
    assert set(mp.METHOD_FILES) == {"screening-methods.json", *national._METHOD_DATA}
    assert mp.method_version_for("regional", _builtin_pkg().files) == method_version()


def test_builtin_package_reproduces_the_release_identity():
    assert method_version() == RELEASE_METHOD
    pkg = _builtin_pkg()
    ident = pkg.envelope["identity"]
    assert ident["methodVersion"] == RELEASE_METHOD
    assert ident["evaluatorDigest"] == mp.evaluator_digest()
    assert ident["scoringIdentity"]["alternative_id"] == "alternative-2"
    blob = mp.to_zip(pkg)
    assert mp.to_zip(_builtin_pkg()) == blob  # deterministic
    back = mp.read_package(blob)
    assert back.files == pkg.files and back.digest == pkg.digest
    for name, data in pkg.files.items():
        assert data == (mp.builtin_data_dir() / name).read_bytes()


def _rezip(blob: bytes, edit) -> bytes:
    entries = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            entries[info.filename] = zf.read(info)
    edit(entries)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _envelope_edit(key_path, value):
    def edit(entries):
        env = json.loads(entries[mp.ENVELOPE])
        target = env
        for k in key_path[:-1]:
            target = target[k]
        target[key_path[-1]] = value
        entries[mp.ENVELOPE] = json.dumps(env).encode()
    return edit


@pytest.mark.parametrize("edit, message", [
    (lambda e: e.__setitem__("method/functions.json", e["method/functions.json"] + b" "),
     "does not match"),
    (lambda e: e.__setitem__("method/evil.py", b"print(1)"), "unexpected entry"),
    (lambda e: e.__setitem__("../escape.json", b"{}"), "unexpected entry"),
    (lambda e: e.pop("method/reference-curves.json"), "missing"),
    (lambda e: e.pop(mp.ENVELOPE), "no method.json"),
    (_envelope_edit(["schemaVersion"], mp.SCHEMA_VERSION + 1), "newer EASI"),
    (_envelope_edit(["evaluator", "engineApi"], mp.ENGINE_API + 1), "newer EASI evaluator"),
    (_envelope_edit(["evaluator", "app"], "deep"), "not for EASI"),
    (_envelope_edit(["schema"], "staf-deep-bundle"), "not an EASI method package"),
    (_envelope_edit(["criteriaSet"], "legacy"), "not supported"),
    (_envelope_edit(["identity", "packageDigest"], "sha256:" + "0" * 64), "packageDigest"),
])
def test_damaged_or_foreign_packages_are_refused_whole(edit, message):
    blob = _rezip(mp.to_zip(_builtin_pkg()), edit)
    with pytest.raises(mp.MethodPackageError, match=message):
        mp.read_package(blob)


def test_truncated_and_non_zip_packages_are_refused():
    blob = mp.to_zip(_builtin_pkg())
    with pytest.raises(mp.MethodPackageError):
        mp.read_package(blob[: len(blob) // 2])
    with pytest.raises(mp.MethodPackageError, match="bad zip"):
        mp.read_package(b"not a zip")


def test_a_recorded_method_version_that_disagrees_with_its_files_is_refused():
    pkg = _builtin_pkg()
    pkg.envelope["identity"]["methodVersion"] = "000000000000"
    with pytest.raises(mp.MethodPackageError, match="methodVersion"):
        mp.check_identity(pkg)


def test_materialize_is_content_addressed_and_repairs_damage(cache):
    pkg = _builtin_pkg()
    first, _ = mp.materialize(pkg)
    again, _ = mp.materialize(mp.to_zip(pkg))
    assert first == again and first.is_dir()
    (first / "functions.json").write_bytes(b"{}")
    repaired, _ = mp.materialize(pkg)
    assert repaired == first
    assert (repaired / "functions.json").read_bytes() == pkg.files["functions.json"]
    for name in mp.EVALUATOR_ASSETS:
        src = mp.builtin_data_dir() / name
        if src.is_file():
            assert (repaired / name).read_bytes() == src.read_bytes()


def _score_script(n_cases: int) -> str:
    return ("import json, sys; sys.path.insert(0, 'tests'); "
            "from easi import method_package as mp; ident = mp.verify_active(); "
            "import calculator_cases as cc; fx = cc.load_fixture(); "
            f"out = dict(ident=ident, results=dict((c['id'], cc.expected_from(cc.score_case(c))) "
            f"for c in fx['cases'][:{n_cases}])); "
            "print(json.dumps(out, sort_keys=True, default=str))")


def _run(env_extra: dict, n_cases: int = 792) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("EASI_DATA_DIR", "EASI_METHOD_PACKAGE", "EASI_CRITERIA_SET")}
    env.update(env_extra)
    proc = subprocess.run([PY, "-B", "-c", _score_script(n_cases)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_package_process_scores_every_case_like_the_builtin_method(tmp_path, cache):
    zpath = tmp_path / "method.zip"
    zpath.write_bytes(mp.to_zip(_builtin_pkg()))
    out = _run({mp.ENV_PACKAGE: str(zpath), mp.ENV_CACHE: str(cache)})
    assert out["ident"]["source"] == "package"
    assert out["ident"]["methodVersion"] == RELEASE_METHOD
    fixture = json.loads((ROOT / "tests" / "data" / "calculator_cases.json").read_text(encoding="utf-8"))
    expected = {c["id"]: c["expected"] for c in fixture["cases"]}
    assert out["results"] == expected


def test_two_packages_in_parallel_processes_never_mix(tmp_path, cache):
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    a.write_bytes(mp.to_zip(_builtin_pkg()))
    edited = _edited_pkg()
    b.write_bytes(mp.to_zip(edited))
    envs = [{mp.ENV_PACKAGE: str(a), mp.ENV_CACHE: str(cache)},
            {mp.ENV_PACKAGE: str(b), mp.ENV_CACHE: str(cache)}]
    base = {k: v for k, v in os.environ.items()
            if k not in ("EASI_DATA_DIR", "EASI_METHOD_PACKAGE", "EASI_CRITERIA_SET")}
    procs = [subprocess.Popen([PY, "-B", "-c", _score_script(792)], cwd=ROOT, env={**base, **e},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for e in envs]
    outs = []
    for p in procs:
        so, se = p.communicate(timeout=900)
        assert p.returncode == 0, se[-2000:]
        outs.append(json.loads(so.strip().splitlines()[-1]))
    ra, rb = outs
    assert ra["ident"]["packageDigest"] == _builtin_pkg().digest
    assert rb["ident"]["packageDigest"] == edited.digest
    assert ra["ident"]["methodVersion"] == RELEASE_METHOD != rb["ident"]["methodVersion"]
    fixture = json.loads((ROOT / "tests" / "data" / "calculator_cases.json").read_text(encoding="utf-8"))
    assert ra["results"] == {c["id"]: c["expected"] for c in fixture["cases"]}
    reach_inflow = "m03"
    moved = [cid for cid in ra["results"]
             if ra["results"][cid]["metrics"] != rb["results"][cid]["metrics"]]
    assert moved, "the edited road-density edge must move some case"
    for cid in moved:
        for key, item in ra["results"][cid]["metrics"].items():
            if key != reach_inflow:
                assert rb["results"][cid]["metrics"][key] == item


def test_activate_switches_in_process_and_back(cache, restore):
    base = mp.activate(_builtin_pkg())
    assert base["methodVersion"] == RELEASE_METHOD and base["source"] == "package"
    edited = _edited_pkg()
    ident = mp.activate(edited)
    assert ident["packageDigest"] == edited.digest
    assert ident["methodVersion"] == method_version() != RELEASE_METHOD
    from easi import screening_methods as sm
    m = next(x for x in sm.catalog()["methods"] if x["methodKey"] == "road-density-inflow-pressure")
    assert any(b.get("max") == 1.5 for b in m["bands"])
    ident = mp.activate(None)
    assert ident["source"] == "built-in" and method_version() == RELEASE_METHOD
    assert Path(config.DATA_DIR) == mp.builtin_data_dir()
    m = next(x for x in sm.catalog()["methods"] if x["methodKey"] == "road-density-inflow-pressure")
    assert not any(b.get("max") == 1.5 for b in m["bands"])


def test_environment_conflicts_are_refused(tmp_path, cache):
    zpath = tmp_path / "method.zip"
    zpath.write_bytes(mp.to_zip(_builtin_pkg()))
    base = {k: v for k, v in os.environ.items()
            if k not in ("EASI_DATA_DIR", "EASI_METHOD_PACKAGE", "EASI_CRITERIA_SET")}
    for extra, message in (({"EASI_DATA_DIR": str(tmp_path)}, "both set"),
                           ({"EASI_CRITERIA_SET": "legacy"}, "does not match")):
        env = {**base, mp.ENV_PACKAGE: str(zpath), mp.ENV_CACHE: str(cache), **extra}
        proc = subprocess.run([PY, "-B", "-c", "import easi.config"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=300)
        assert proc.returncode != 0 and message in proc.stderr


def test_the_calculator_regenerates_byte_for_byte_from_the_builtin_package(tmp_path, cache):
    zpath = tmp_path / "method.zip"
    zpath.write_bytes(mp.to_zip(_builtin_pkg()))
    env = {k: v for k, v in os.environ.items()
           if k not in ("EASI_DATA_DIR", "EASI_METHOD_PACKAGE", "EASI_CRITERIA_SET")}
    env.update({mp.ENV_PACKAGE: str(zpath), mp.ENV_CACHE: str(cache)})
    proc = subprocess.run([PY, "-B", "scripts/build_calculator.py", "--check"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unchanged" in proc.stdout


def _repackage(files: dict, **kw) -> mp.MethodPackage:
    """A package of ``files`` whose scoring-identity names their hashes."""
    import hashlib
    ident = json.loads(files["scoring-identity.json"].decode("utf-8"))
    for key, name in (("catalog_sha256", "screening-methods.json"),
                      ("curves_sha256", "reference-curves.json"),
                      ("nars_geography_sha256", "nars-ecoregions-9.geojson.gz")):
        ident[key] = hashlib.sha256(files[name]).hexdigest()
    files = dict(files)
    files["scoring-identity.json"] = json.dumps(ident, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    env = mp.build_envelope(files, method_id="easi-screening", version=kw.get("version", 3),
                            label="test package")
    return mp.MethodPackage(envelope=env, files=files)


def test_operator_and_stratifier_lists_match_the_evaluator():
    from easi import screening_methods as sm
    assert set(mp.OPERATORS) == set(sm.VALID_OPERATORS)
    req = _builtin_pkg().envelope["evaluator"]["requires"]
    assert set(req["operators"]) <= set(mp.OPERATORS)
    assert set(req["stratifiers"]) <= set(mp.STRATIFIERS)
    # every stratifier a package may name is one the evaluator resolves: a strata key of a
    # reach, or the national-only set
    from easi import geo
    keys = set(geo.strata_for("50", slope=0.01)) | {"nars9", "national"}
    assert set(mp.STRATIFIERS) <= keys
    assert req["behaviors"] == list(mp.BEHAVIORS)


def test_a_package_needing_capabilities_this_evaluator_lacks_is_refused():
    blob = _rezip(mp.to_zip(_builtin_pkg()),
                  _envelope_edit(["evaluator", "requires", "operators"], ["threshold", "geometric_mean"]))
    with pytest.raises(mp.MethodPackageError, match="capabilities"):
        mp.read_package(blob)
    blob = _rezip(mp.to_zip(_builtin_pkg()),
                  _envelope_edit(["evaluator", "requires", "behaviors"], ["curve-index-bands-0.40-0.70"]))
    with pytest.raises(mp.MethodPackageError, match="behaviors"):
        mp.read_package(blob)


def test_cross_file_inconsistencies_are_refused():
    base = dict(_builtin_pkg().files)
    metrics = json.loads(base["easi-metrics.json"].decode("utf-8"))
    metrics["metrics"][0]["indexMidpoints"]["Good"] = 0.9
    bad = dict(base, **{"easi-metrics.json": json.dumps(metrics).encode("utf-8")})
    assert any("indexMidpoints" in p for p in mp.validate_files(bad))
    curves = json.loads(base["reference-curves.json"].decode("utf-8"))
    curves["sets"]["corridor-woody"]["curves"]["ZZZ"] = curves["sets"]["corridor-woody"]["curves"]["CPL"]
    bad = dict(base, **{"reference-curves.json": json.dumps(curves).encode("utf-8")})
    assert any("not nars9 codes" in p for p in mp.validate_files(bad))
    assert any("scoring-identity.json curves_sha256" in p for p in mp.validate_files(bad))
    with pytest.raises(mp.MethodPackageError, match="not consistent"):
        mp.build_envelope(bad, method_id="x", version=1, label="x")


def test_switching_methods_resets_the_live_region_lookup(cache, restore):
    """A package whose NARS-9 geography labels two regions the other way round: the live
    point-in-polygon lookup must follow the active package, both ways."""
    import gzip
    from easi import geo
    point = (35.4, -77.4)          # North Carolina coastal plain
    assert geo.strata_at(*point).get("nars9") == "CPL"
    files = dict(_builtin_pkg().files)
    doc = json.loads(gzip.decompress(files["nars-ecoregions-9.geojson.gz"]).decode("utf-8"))
    swap = {"CPL": "SAP", "SAP": "CPL"}
    for f in doc["features"]:
        code = f["properties"].get("WSA_9")
        if code in swap:
            f["properties"]["WSA_9"] = swap[code]
    files["nars-ecoregions-9.geojson.gz"] = gzip.compress(json.dumps(doc).encode("utf-8"), mtime=0)
    swapped = _repackage(files)
    mp.activate(swapped)
    assert geo.strata_at(*point).get("nars9") == "SAP"
    mp.activate(_builtin_pkg())
    assert geo.strata_at(*point).get("nars9") == "CPL"
    mp.activate(swapped)
    assert geo.strata_at(*point).get("nars9") == "SAP"
    mp.activate(None)
    assert geo.strata_at(*point).get("nars9") == "CPL"


def test_the_acquisition_digest_leaves_out_the_method_and_presentation_code():
    names = {p.name for p in mp.acquisition_sources()}
    assert "threedep.py" in names and "geomorph.py" in names
    assert "physio_divisions.geojson" in names
    assert not names & {"scoring.py", "assessment.py", "report.py", "method_package.py"}
    ident = mp.active_identity()
    assert ident["acquisitionDigest"].startswith("sha256:")
    assert ident["methodVersion"] == RELEASE_METHOD


# --------------------------------------------------------------------------- #
# review A (2026-09-23): stratifiers, the calculator in the cache, status, direction
# --------------------------------------------------------------------------- #
def _with_curves(files: dict, edit) -> dict:
    files = dict(files)
    curves = json.loads(files["reference-curves.json"].decode("utf-8"))
    edit(curves)
    files["reference-curves.json"] = json.dumps(curves, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    ident = json.loads(files["scoring-identity.json"].decode("utf-8"))
    ident["curves_sha256"] = hashlib.sha256(files["reference-curves.json"]).hexdigest()
    ident["curve_count"] = sum(len(s["curves"]) for s in curves["sets"].values())
    files["scoring-identity.json"] = json.dumps(ident, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    return files


def test_level_ii_and_national_curve_sets_are_accepted_as_the_evaluator_resolves_them():
    files = dict(_builtin_pkg().files)
    l2 = list(json.loads(files["ecoregion-crosswalk.json"].decode("utf-8"))["l2"])[:2]

    def to_l2(curves):
        s = curves["sets"]["corridor-woody"]
        s["stratifier"] = "l2"
        nat = s["curves"]["national"]
        s["curves"] = {"national": nat, **{code: dict(nat) for code in l2}}

    def to_national(curves):
        s = curves["sets"]["corridor-natural"]
        s["stratifier"], s["curves"] = "national", {"national": s["curves"]["national"]}

    assert not mp.validate_files(_with_curves(files, to_l2))
    assert not mp.validate_files(_with_curves(files, to_national))

    def bad_l2(curves):
        curves["sets"]["corridor-woody"]["stratifier"] = "l2"     # NARS-9 codes under an l2 set

    assert any("are not l2 codes" in p for p in mp.validate_files(_with_curves(files, bad_l2)))

    def bad_national(curves):
        curves["sets"]["corridor-natural"]["stratifier"] = "national"   # regional curves kept

    assert any("holds only the national curve" in p for p in mp.validate_files(_with_curves(files, bad_national)))


def test_a_curve_against_its_sets_direction_is_refused():
    files = dict(_builtin_pkg().files)

    def reversed_curve(curves):
        c = curves["sets"]["corridor-woody"]["curves"]["national"]     # higher is better
        c["points"] = [[0.0, 1.0], [50.0, 0.5], [100.0, 0.0]]

    assert any("runs against its set's direction" in p
               for p in mp.validate_files(_with_curves(files, reversed_curve)))


def test_the_package_names_no_lifecycle_status():
    env = _builtin_pkg().envelope
    assert "status" not in env
    with zipfile.ZipFile(io.BytesIO(mp.to_zip(_builtin_pkg()))) as zf:
        assert "status" not in json.loads(zf.read(mp.ENVELOPE))


def test_two_packages_with_other_calculators_never_share_a_folder(cache, restore):
    files = dict(_builtin_pkg().files)
    env = mp.build_envelope(files, method_id="easi-screening", version=1, label="x")
    bare = mp.MethodPackage(envelope=env, files=files)
    wb = (mp.builtin_data_dir().parent / "www" / "EASI_Calculator_1.0.xlsx")
    if not wb.is_file():
        wb = next(mp.builtin_data_dir().parent.rglob("EASI_Calculator_*.xlsx"), None)
    if wb is None:
        pytest.skip("no committed calculator in this checkout")
    calc = (wb.name, wb.read_bytes())
    env2 = mp.build_envelope(files, method_id="easi-screening", version=1, label="x", calculator=calc)
    with_calc = mp.MethodPackage(envelope=env2, files=files, calculator=calc)
    d1, _ = mp.materialize(bare)
    d2, _ = mp.materialize(with_calc)
    assert d1 != d2
    assert (d2.parent / mp.CALCULATOR_DIR / wb.name).read_bytes() == calc[1]
    assert not (d1.parent / mp.CALCULATOR_DIR).exists()


def test_rolling_back_restores_the_criteria_set_the_process_started_with(cache, restore, monkeypatch):
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    mp.activate(_builtin_pkg())
    mp.activate(None)
    assert os.environ.get("EASI_CRITERIA_SET") == "regional"
