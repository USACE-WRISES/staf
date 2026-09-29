"""The Round 4 candidate families (WP-R4k): the package builder, the refit plumbing of the
candidate curve sets, and what the built packages do in an evaluation worker.

Every family builds a verified package from the vendored base files and names the evaluator
capabilities it needs; E2 and E4 refuse without their curve set; the two candidate sets are
fitted by stratum only and assembled in the method file's shape; and in a worker the E1
package withholds an intermittent reach and rates a perennial one, E7's mean differs from
the worst-of on a constructed case, E8's interval brackets the point ECI, and the base scores
exactly as it did.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from streamcurves._vendor.easi import method_package as mp
from streamcurves.easi_method import campaigns, evaluate, refit, round4

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent.parent
VENDORED_DATA = APP / "streamcurves" / "_vendor" / "easi" / "data"
LIBRARY_V1 = REPO / "apps" / "library" / "assessments" / "easi-screening" / "v1"
#: the Round 4 studies ran on Alternative 2 (easi-screening v1); the vendored data is EASI v2
#: (E5b) since 2026-09-29, so the base is v1's method files where the library is present
A2_DATA = (LIBRARY_V1 / "method" if (LIBRARY_V1 / "method" / "scoring-identity.json").is_file()
           else VENDORED_DATA)
SCRIPT = APP / "scripts" / "build_easi_candidate_package.py"
LOW_FLOW = "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity"
CATCHMENT = "catchment-hydrology-impervious-surface-cover"
COMPOSITE_METRICS = {
    CATCHMENT, "sediment-continuity-sediment-supply-potential-watershed-banks",
    "light-and-thermal-regime-stream-temperature",
    "channel-evolution-channel-evolution-stage-and-trends",
    "hyporheic-connectivity-hyporheic-exchange-indicators"}


def _curve(x39: float, x69: float) -> dict:
    return {"points": [[0.0, 0.0], [x39, 0.39], [x69, 0.69], [1.0, 1.0]], "n": 40, "nMembers": 40,
            "q25": x39, "q50": (x39 + x69) / 2, "q75": x69, "x39": x39, "x69": x69,
            "status": "complete", "panelTier": "complete", "screen": "strict"}


def _set(quantity: str) -> dict:
    return {"higherIsBetter": True, "quantity": quantity, "stratifier": "nars9",
            "curves": {"national": _curve(0.2, 0.5), "TPL": _curve(0.25, 0.55)}}


# E2 as re-specified (Addendum 1, 2026-09-27): its set records the registry's fcode_class split
SETS = {"flow-min-ratio": {**_set("q_min_ratio"), "split": {"fcode_class": "perennial"}},
        "width-variability": _set("bankfull_width_cv")}
REFIT_SCRIPT = APP / "scripts" / "refit_easi_candidate_sets.py"


@pytest.fixture(scope="module")
def base():
    return round4.base_files(A2_DATA)


# --------------------------------------------------------------------------- #
# the addendum and the builder
# --------------------------------------------------------------------------- #
def test_the_addendum_is_the_frozen_file_and_names_eight_families(tmp_path):
    assert round4.addendum_sha256() == round4.ADDENDUM_SHA256
    doc = round4.addendum()
    assert sorted(doc["candidates"]) == list(round4.FAMILIES)
    assert round4.family_spec("E1")["family"] == "low_flow_intermittence"
    assert round4.family_spec("E8")["family"] == "completeness"
    for family in round4.FAMILIES:
        spec = round4.family_spec(family)
        assert spec["mechanism"] and spec["change"] and spec["id"] == family
    with pytest.raises(round4.CandidateError, match="unknown family"):
        round4.family_spec("E9")
    edited = tmp_path / round4.ADDENDUM_FILE
    edited.write_bytes(round4.ADDENDUM_PATH.read_bytes() + b"\n# edited\n")
    with pytest.raises(round4.CandidateError, match="not the frozen addendum"):
        round4.family_spec("E1", edited)


def test_the_texts_people_read_are_plain_ascii_without_em_dashes():
    em_dash = chr(8212)
    src = Path(round4.__file__).read_text(encoding="utf-8")
    assert em_dash not in src and src.isascii()
    for name in ("build_easi_candidate_package.py", "refit_easi_candidate_sets.py"):
        text = (APP / "scripts" / name).read_text(encoding="utf-8")
        assert em_dash not in text and text.isascii()


def test_every_family_builds_a_verified_package(base):
    base_digest = mp.package_digest({n: mp._sha(b) for n, b in base.items()})
    for family in round4.FAMILIES:
        built = round4.build_candidate(base, family, curve_sets=SETS)
        assert mp.validate_files(built["files"]) == []
        assert built["identity"]["packageDigest"] != base_digest
        assert built["identity"]["evaluatorDigest"] == mp.evaluator_digest()
        assert {"easi-metrics.json", "cwa-mapping.json", "functions.json", "ecoregion-crosswalk.json",
                "nars-ecoregions-9.geojson.gz"} <= set(built["unchangedFiles"])
        assert built["scoringIdentity"]["alternative_id"] == f"round4-{family}"
        assert built["scoringIdentity"]["derived_from"]["alternative_id"] == "alternative-2"
        assert "rehearsal" in built["scoringIdentity"]["alternative_name"]
        assert built["edits"]
        pkg = round4.package(built["files"], family, built["spec"])
        back = mp.read_package(mp.to_zip(pkg))
        assert back.digest == built["identity"]["packageDigest"]
        assert "rehearsal" in back.envelope["label"] and family in back.envelope["label"]
        req = back.envelope["evaluator"]["requires"]
        extras = [b for b in req["behaviors"] if b not in mp.BEHAVIORS]
        if family in ("E1", "E2", "E5"):
            # E2 carries E1's rule since Addendum 1 (intermittent reaches withheld)
            assert extras == ["applicability-rules"]
        elif family == "E8":
            assert extras == ["rollup-completeness-interval"]
        else:
            assert extras == []
        assert ("mean_index" in req["operators"]) == (family == "E7")
        assert built["respecified"] == ("2026-09-27" if family == "E2" else None)
        if family in round4.FAMILY_SETS:
            sets = json.loads(built["files"]["reference-curves.json"])["sets"]
            assert round4.FAMILY_SETS[family] in sets
            assert built["curveSets"][round4.FAMILY_SETS[family]]["curves"] == ["TPL", "national"]
            if family == "E2":
                assert sets["flow-min-ratio"]["split"] == {"fcode_class": "perennial"}
                assert built["curveSets"]["flow-min-ratio"]["split"] == {"fcode_class": "perennial"}


def test_family_spec_reports_the_e2_respecification():
    spec = round4.family_spec("E2")
    assert spec["respecified"]["date"] == "2026-09-27" and spec["respecified"]["addendum"] == 1
    assert spec["respecified"]["split"] == "fcode_class"
    assert "perennial" in spec["respecified"]["summary"]
    assert spec["respecified"]["comparisonScope"] == {"function": "low-flow-baseflow-dynamics",
                                                       "rule": "candidate-rated"}
    assert "respecified" not in round4.family_spec("E1")
    # the prose addendum carries Addendum 1 and the frozen yaml is untouched
    prose = (round4.ADDENDUM_PATH.parent / "EVALUATION_PROTOCOL_V1_EASI_ADDENDUM.md").read_text(encoding="utf-8")
    assert "Addendum 1 (2026-09-27, before any Round 4 result was read)" in prose
    assert "(none)" not in prose.split("## Addenda")[1]
    assert round4.addendum_sha256() == round4.ADDENDUM_SHA256


def test_the_edits_say_what_each_family_changes(base):
    cat = lambda built: json.loads(built["files"]["screening-methods.json"])  # noqa: E731

    def method(doc, key):
        for m in doc["methods"]:
            if m["methodKey"] == key:
                return m
            for v in m.get("variants") or []:
                if v["methodKey"] == key:
                    return v
        raise KeyError(key)

    e1 = method(cat(round4.build_candidate(base, "E1")), "erom-flow-variability")
    assert e1["applicability"] == {"input": "fcodeContext", "exclude": ["46003", "46007"],
                                   "statement": round4.E1_STATEMENT}
    e2_built = round4.build_candidate(base, "E2", curve_sets=SETS)
    e2 = method(cat(e2_built), "erom-flow-min-ratio")
    assert [i["key"] for i in e2["inputs"]] == ["flowMinRatio", "meanAnnualFlow", "fcodeContext"]
    assert e2["curve"]["set"] == "flow-min-ratio" and e2["metricId"] == LOW_FLOW
    # Addendum 1: E1's rule and statement on the E2 method, the re-specification named
    assert e2["applicability"] == {"input": "fcodeContext", "exclude": ["46003", "46007"],
                                   "statement": round4.E1_STATEMENT}
    assert any("Addendum 1, 2026-09-27" in t and "perennial members" in t for t in e2["limitations"])
    assert any(e.get("field") == "applicability" and e.get("respecified") == "2026-09-27" for e in e2_built["edits"])
    e3 = method(cat(round4.build_candidate(base, "E3")), "streamcat-integrity-products")
    assert e3["operator"] == "unscored" and e3["statement"] == round4.E3_STATEMENT
    e4 = method(cat(round4.build_candidate(base, "E4", curve_sets=SETS)), "habitat-width-variability")
    assert [i["key"] for i in e4["inputs"]] == ["widthCv", "sinuosity"]
    e5 = cat(round4.build_candidate(base, "E5"))
    for key in round4.CROSS_SECTION_METHODS:
        assert method(e5, key)["applicability"]["withhold_when"] == ["low_quality", "out_of_range"]
    e6 = cat(round4.build_candidate(base, "E6"))
    assert [i["key"] for i in method(e6, "sediment-supply-potential")["inputs"]] == ["kFactor", "roadDensity"]
    substrate = method(e6, "watershed-agriculture-share")
    assert substrate["operator"] == "unscored" and "bands" not in substrate
    assert method(e6, "catchment-land-cover-pressure")["operator"] == "worst_index"
    e7 = cat(round4.build_candidate(base, "E7"))
    for key in round4.COMPOSITES:
        assert method(e7, key)["operator"] == "mean_index"
        assert not any("governs" in t for t in method(e7, key)["limitations"])
    assert method(e7, "regional-nutrient-condition")["operator"] == "worst_index"
    assert cat(round4.build_candidate(base, "E8"))["rollupReporting"] is True


def test_e2_and_e4_refuse_without_their_curve_set(base):
    with pytest.raises(round4.CandidateError, match="flow-min-ratio") as exc:
        round4.build_candidate(base, "E2")
    assert "refit_easi_candidate_sets.py" in str(exc.value)
    with pytest.raises(round4.CandidateError, match="width-variability"):
        round4.build_candidate(base, "E4", curve_sets={"flow-min-ratio": SETS["flow-min-ratio"]})
    no_national = copy.deepcopy(SETS)
    del no_national["width-variability"]["curves"]["national"]
    with pytest.raises(round4.CandidateError, match="national curve"):
        round4.build_candidate(base, "E4", curve_sets=no_national)
    # E2 as written (a set assembled without the perennial split) cannot be built
    as_written = copy.deepcopy(SETS)
    del as_written["flow-min-ratio"]["split"]
    with pytest.raises(round4.CandidateError, match="Addendum 1") as exc:
        round4.build_candidate(base, "E2", curve_sets=as_written)
    assert "--registry-split flow-min-ratio=perennial" in str(exc.value)


def test_the_cli_writes_the_folder_the_zip_and_candidate_json(tmp_path):
    source = LIBRARY_V1 if (LIBRARY_V1 / mp.ENVELOPE).is_file() else VENDORED_DATA
    out = tmp_path / "E1"
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E1", "--base", str(source),
                           "--out", str(out)], cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    record = json.loads((out / "candidate.json").read_text(encoding="utf-8"))
    assert record["family"] == "E1" and record["familyName"] == "low_flow_intermittence"
    assert record["addendum"]["sha256"] == round4.ADDENDUM_SHA256
    assert record["label"] == "rehearsal" and record["edits"]
    pkg = mp.read_package(out / "E1.easi-method.zip")
    assert pkg.digest == record["candidate"]["packageDigest"] != record["base"]["packageDigest"]
    assert record["candidate"]["methodVersion"] != record["base"]["methodVersion"]
    if source == LIBRARY_V1:
        assert record["base"]["methodVersion"] == "b2e3033116e3"
        assert record["base"]["methodVersionRecordedBy"] == "the base's envelope"
    for name in mp.METHOD_FILES:
        assert (out / "method" / name).read_bytes() == pkg.files[name]
    # never under the app's data or the library
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E8", "--base", str(source),
                           "--out", str(REPO / "apps" / "easi" / "data" / "never")],
                          cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode != 0 and "never written there" in proc.stderr
    assert not (REPO / "apps" / "easi" / "data" / "never").exists()
    # E2 without the set: refused with the refit command, nothing written
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E2", "--base", str(source),
                           "--out", str(tmp_path / "E2")], cwd=str(APP), capture_output=True, text=True,
                          timeout=600)
    assert proc.returncode != 0 and "flow-min-ratio" in proc.stderr
    assert not (tmp_path / "E2").exists()


# --------------------------------------------------------------------------- #
# the refit plumbing of the candidate sets
# --------------------------------------------------------------------------- #
def _members_package(tmp_path: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from streamcurves import evidence_store as es
    rng = np.random.default_rng(11)
    rows, comid = [], 1
    for level, stratum in (("nars9", "nars9:CPL"), ("nars9", "nars9:NAP"), ("national", "national:national")):
        for _ in range(45):
            rows.append({"comid": comid, "huc12": f"h{comid}", "level": level, "stratum": stratum,
                         "slope_class": "ge_2", "fcode_class": "stream", "screen": "strict",
                         "panel_tier": "exploratory"})
            comid += 1
    d = tmp_path / "members" / "data"
    d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "panel_members.parquet")
    n = comid - 1
    pq.write_table(pa.table({"comid": list(range(1, n + 1)),
                             "q_min_ratio": rng.uniform(0.05, 0.6, n).tolist(),
                             "bankfull_width_cv": rng.uniform(0.05, 0.8, n).tolist(),
                             "composite_pressure": rng.uniform(0, 1, n).tolist()}), d / "member_values.parquet")
    panels = [{"level": lv, "stratum": st, "panel_tier": "exploratory", "screen": "strict"}
              for lv, st in {(r["level"], r["stratum"]) for r in rows}]
    pq.write_table(pa.Table.from_pylist(panels), d / "reference_panels.parquet")
    files = {f"data/{f.name}": {"bytes": f.stat().st_size, "sha256": es.sha_file(f)}
             for f in sorted(d.iterdir())}
    (d.parent / "evidence.json").write_text(json.dumps({
        "schema": es.SCHEMA, "schemaVersion": 1, "packageId": "easi-dev-members", "version": "test",
        "dataDigest": es.data_digest(files), "files": files}), encoding="utf-8")
    return d.parent


def test_the_candidate_sets_are_fitted_by_stratum_only_and_assembled_for_the_method_file(tmp_path, base):
    folder = _members_package(tmp_path)
    members, values, panels = refit.load_members(folder)
    quantities = sorted(set(refit.CANDIDATE_SETS.values()))
    # the registry grouping is unchanged: the flow ratio splits by fcode class, width
    # variability is a geometry quantity fitted at the national level only
    registry = refit.fit_registry(members, values, panels, quantities=quantities)
    assert {(r["quantity"], r["level"], r["split"]) for r in registry} == {
        ("q_min_ratio", "nars9", "stream"), ("q_min_ratio", "national", "stream"),
        ("bankfull_width_cv", "national", "ge_2")}
    rows = refit.fit_registry(members, values, panels, quantities=quantities, by_stratum_only=quantities)
    keys = sorted((r["quantity"], r["level"], r["stratum"], r["split"]) for r in rows)
    assert keys == sorted([(q, lv, st, "") for q in quantities
                           for lv, st in (("nars9", "nars9:CPL"), ("nars9", "nars9:NAP"),
                                          ("national", "national:national"))])
    assert all(r["n"] == 45 and r["status"] == "complete" and r["usable"] for r in rows)
    sets, diagnostics = refit.candidate_curves(rows)
    assert sorted(sets) == ["flow-min-ratio", "width-variability"]
    for set_id, definition in sets.items():
        assert definition["stratifier"] == "nars9" and definition["higherIsBetter"] is True
        assert sorted(definition["curves"]) == ["CPL", "NAP", "national"]
        for curve in definition["curves"].values():
            assert curve["x39"] < curve["x69"] and len(curve["points"]) >= 2
            assert set(curve) >= {"points", "n", "nMembers", "q25", "q50", "q75", "x39", "x69",
                                  "status", "panelTier", "screen"}
        assert diagnostics[set_id]["notUsable"] == {}
    # a set without a usable national curve is left out and said
    partial, diag = refit.candidate_curves([r for r in rows if r["level"] != "national"])
    assert partial == {} and all(d["omitted"] == "no usable national curve" for d in diag.values())
    # the by-stratum output builds E4; E2 (Addendum 1) needs the set from the registry split
    built = round4.build_candidate(base, "E4", curve_sets=sets)
    assert mp.validate_files(built["files"]) == []
    mp.read_package(mp.to_zip(round4.package(built["files"], "E4", built["spec"])))
    with pytest.raises(round4.CandidateError, match="Addendum 1"):
        round4.build_candidate(base, "E2", curve_sets=sets)
    # the split-aware assembly: the registry rows of q_min_ratio's own fcode_class split
    # (every synthetic member is "stream"), the split recorded in the definition
    split_sets, split_diag = refit.candidate_curves(registry, sets={"flow-min-ratio": "q_min_ratio"},
                                                    splits={"flow-min-ratio": "stream"})
    assert sorted(split_sets) == ["flow-min-ratio"]
    assert split_sets["flow-min-ratio"]["split"] == {"fcode_class": "stream"}
    assert sorted(split_sets["flow-min-ratio"]["curves"]) == ["CPL", "NAP", "national"]
    assert split_diag["flow-min-ratio"]["split"] == {"fcode_class": "stream"}
    # the rows of another split value select nothing, and a quantity without a split refuses
    none_sets, none_diag = refit.candidate_curves(registry, sets={"flow-min-ratio": "q_min_ratio"},
                                                  splits={"flow-min-ratio": "perennial"})
    assert none_sets == {} and none_diag["flow-min-ratio"]["omitted"] == "no usable national curve"
    with pytest.raises(ValueError, match="no registry split"):
        refit.candidate_curves(rows, sets={"width-variability": "bankfull_width_cv"},
                               splits={"width-variability": "ge_2"})
    e2 = round4.build_candidate(base, "E2", curve_sets=split_sets)
    assert mp.validate_files(e2["files"]) == [] and e2["respecified"] == "2026-09-27"
    assert json.loads(e2["files"]["reference-curves.json"])["sets"]["flow-min-ratio"]["split"] == {"fcode_class": "stream"}
    mp.read_package(mp.to_zip(round4.package(e2["files"], "E2", e2["spec"])))
    # the campaign fits the same rows, one job per quantity, with the grouping in the spec
    from streamcurves import evidence_store as es
    digest = es.read_manifest(folder)["dataDigest"]
    plain_jobs = campaigns.refit_jobs(folder, members_digest=digest, quantities=quantities,
                                      by_stratum_only=quantities)
    registry_jobs = campaigns.refit_jobs(folder, members_digest=digest, quantities=quantities)
    assert all(j.spec["byStratumOnly"] is True for j in plain_jobs)
    assert all("byStratumOnly" not in j.spec for j in registry_jobs)
    assert {j.id for j in plain_jobs}.isdisjoint({j.id for j in registry_jobs})
    got, summary = campaigns.refit_campaign(folder, tmp_path / "c", members_digest=digest, workers=2,
                                            quantities=quantities, by_stratum_only=quantities)
    assert summary["counts"]["completed"] == 2
    norm = lambda rs: json.loads(json.dumps(sorted(rs, key=lambda r: (r["quantity"], r["level"],  # noqa: E731
                                                                       r["stratum"])), default=float))
    assert norm(got) == norm(rows)


def test_the_refit_cli_fits_a_set_on_its_registry_split(tmp_path):
    """``--registry-split flow-min-ratio=<value>`` fits q_min_ratio with its fcode_class split
    (a plain registry refit of the quantity), assembles the set from that split's rows, records
    the split and the per-stratum quantiles, and refuses a split for a quantity without one."""
    folder = _members_package(tmp_path)
    out = tmp_path / "curve-sets.json"
    proc = subprocess.run([sys.executable, "-B", str(REFIT_SCRIPT), "--members", str(folder),
                           "--campaign", str(tmp_path / "campaign"), "--out", str(out),
                           "--sets", "flow-min-ratio", "--registry-split", "flow-min-ratio=stream",
                           "--workers", "1"], cwd=str(APP), capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stderr[-2000:] + proc.stdout[-2000:]
    doc = json.loads(out.read_text(encoding="utf-8"))
    definition = doc["sets"]["flow-min-ratio"]
    assert definition["split"] == {"fcode_class": "stream"}
    assert sorted(definition["curves"]) == ["CPL", "NAP", "national"]
    assert doc["provenance"]["registrySplits"] == {"flow-min-ratio": "stream"}
    assert "registry split for flow-min-ratio (stream)" in doc["provenance"]["groupedBy"]
    quantiles = doc["provenance"]["stratumQuantiles"]
    assert {(q["stratum"], q["split"]) for q in quantiles} == {("nars9:CPL", "stream"), ("nars9:NAP", "stream"),
                                                               ("national:national", "stream")}
    assert all(q["q25"] is not None and q["status"] == "complete" for q in quantiles)
    assert "q25" in proc.stdout and "split {'fcode_class': 'stream'}" in proc.stdout
    proc = subprocess.run([sys.executable, "-B", str(REFIT_SCRIPT), "--members", str(folder),
                           "--campaign", str(tmp_path / "campaign2"), "--out", str(tmp_path / "x.json"),
                           "--sets", "width-variability", "--registry-split", "width-variability=ge_2"],
                          cwd=str(APP), capture_output=True, text=True, timeout=900)
    assert proc.returncode != 0 and "has no registry split" in proc.stderr


# --------------------------------------------------------------------------- #
# what the packages do in a worker
# --------------------------------------------------------------------------- #
def _case_record() -> dict:
    cases = LIBRARY_V1 / "cases.json"
    if not cases.is_file():
        pytest.skip("the library's EASI preview cases are not in this copy")
    doc = json.loads(cases.read_text(encoding="utf-8"))
    return next(c["record"] for c in doc["cases"] if c["id"] == "base")


def test_the_packages_score_as_their_families_say(base):
    record = _case_record()
    perennial = {**copy.deepcopy(record), "fcode": 46006}
    intermittent = {**copy.deepcopy(record), "fcode": 46003}
    mixed = copy.deepcopy(record)
    mixed["streamcat"] = {**mixed["streamcat"], "pctimp2019ws": 2.0, "pctcrop2019ws": 60.0, "pcthay2019ws": 5.0}
    cases = {"cases": [{"id": "perennial", "record": perennial}, {"id": "intermittent", "record": intermittent},
                       {"id": "mixed", "record": mixed}]}
    packages = {"base": mp.package_from_dir(A2_DATA, version=1, label="base")}
    for family in ("E1", "E7", "E8"):
        built = round4.build_candidate(base, family)
        packages[family] = round4.package(built["files"], family, built["spec"])
    results = {label: evaluate.run_cases(pkg, cases)["results"] for label, pkg in packages.items()}
    metric = lambda res, cid, mid: res[cid]["metrics"][mid]  # noqa: E731
    # the base rates the intermittent reach like the perennial one and reports no completeness fields
    assert metric(results["base"], "intermittent", LOW_FLOW)["rating"] is not None
    assert metric(results["base"], "intermittent", LOW_FLOW) == metric(results["base"], "perennial", LOW_FLOW)
    assert "functionsRated" not in results["base"]["perennial"]
    # E1 withholds the intermittent reach as a documented gap and changes nothing else
    withheld = metric(results["E1"], "intermittent", LOW_FLOW)
    assert withheld["rating"] is None and withheld["completeness"] == "withheld"
    # the worker carries the documented gap per metric: the rule, what it matched, the statement
    assert withheld["gap"]["rule"]["input"] == "fcodeContext"
    assert withheld["gap"]["matched"] == {"input": "fcodeContext", "value": 46003}
    assert "FCODE 46003" in withheld["gap"]["statement"]
    assert "gap" not in metric(results["E1"], "perennial", LOW_FLOW)
    assert "gap" not in metric(results["base"], "intermittent", LOW_FLOW)
    assert metric(results["E1"], "perennial", LOW_FLOW) == metric(results["base"], "perennial", LOW_FLOW)
    for mid, item in results["base"]["intermittent"]["metrics"].items():
        if mid != LOW_FLOW:
            assert metric(results["E1"], "intermittent", mid) == item
    assert results["E1"]["intermittent"]["rated"] == results["base"]["intermittent"]["rated"] - 1
    # E7: the mean of Good impervious and Poor agriculture is Fair where the worst-of is Poor
    assert metric(results["base"], "mixed", CATCHMENT)["rating"] == "Poor"
    assert metric(results["E7"], "mixed", CATCHMENT)["rating"] == "Fair"
    for mid, item in results["base"]["mixed"]["metrics"].items():
        if mid not in COMPOSITE_METRICS:
            assert metric(results["E7"], "mixed", mid) == item
    # E8 reports completeness and an interval that brackets the unchanged point ECI
    for cid in ("perennial", "mixed"):
        res = results["E8"][cid]
        assert res["eci"] == results["base"][cid]["eci"] and res["metrics"] == results["base"][cid]["metrics"]
        assert res["functionsRated"] == res["rated"]
        lo, hi = res["ecosystemConditionIndexInterval"]
        assert lo <= res["eci"] <= hi


# --------------------------------------------------------------------------- #
# the finalist composition (WP-R4s-3)
# --------------------------------------------------------------------------- #
#: the package digests of the single-family packages the Round 4 studies scored (the
#: candidate.json of E1, E3 and E5 under D:/Data/staf-campaign-2026-09/easi/candidates,
#: built from the same base files): a composition must leave the single builds byte for byte
SINGLE_FAMILY_DIGESTS = {
    "E1": "sha256:41b3ae6c4163b8900f9f8300baf2de2b2a04eb15f5676aec64659676a8f48fbf",
    "E3": "sha256:4558f32fe1b53baa2ee43f109b4a1c748cc367e4bb71daec383a0bc5643c0ac7",
    "E5": "sha256:6530e461132d890f86c75c5faa86e4ba96c930ad6cd1d706c37739baf170a89f",
}


def _method_of(doc: dict, key: str) -> dict:
    for m in doc["methods"]:
        if m["methodKey"] == key:
            return m
        for v in m.get("variants") or []:
            if v["methodKey"] == key:
                return v
    raise KeyError(key)


def test_the_composition_applies_the_families_in_addendum_order(base):
    built = round4.build_composition(base, ["E5", "E1", "E3"])
    spec = built["spec"]
    assert spec["id"] == "FINALIST" == round4.COMPOSITION and spec["family"] == "finalist_composition"
    assert spec["composition"] == ["E1", "E3", "E5"] == built["composition"]
    assert spec["decision"] == "composition" and spec["primary_outcome"] == "P1"
    assert spec["function"] == ["low-flow-baseflow-dynamics", "population-support", "high-flow-dynamics",
                                "floodplain-connectivity", "channel-evolution", "channel-floodplain-dynamics"]
    assert sorted(spec["components"]) == ["E1", "E3", "E5"]
    assert spec["components"]["E3"]["family"] == "biological_fallback" and spec["components"]["E3"]["id"] == "E3"
    assert "E1:" in spec["change"] and "E5:" in spec["coverage_effect"]
    assert "respecified" not in spec and built["respecified"] is None
    # each component's edit is exactly what its single build applies, in the addendum's order
    assert [e["family"] for e in built["edits"]] == ["E1", "E3", "E5"]
    for entry in built["edits"]:
        assert entry["edits"] == round4.build_candidate(base, entry["family"])["edits"]
        assert entry["familyName"] == round4.family_spec(entry["family"])["family"]
    assert mp.validate_files(built["files"]) == []
    cat = json.loads(built["files"]["screening-methods.json"])
    assert _method_of(cat, "erom-flow-variability")["applicability"]["exclude"] == ["46003", "46007"]
    assert _method_of(cat, "streamcat-integrity-products")["operator"] == "unscored"
    for key in round4.CROSS_SECTION_METHODS:
        assert _method_of(cat, key)["applicability"]["withhold_when"] == ["low_quality", "out_of_range"]
    assert _method_of(cat, "sediment-supply-potential")["operator"] == "worst_index"   # E6 and E7 absent
    assert "rollupReporting" not in cat                                                # E8 absent
    ident = built["scoringIdentity"]
    assert ident["alternative_id"] == "round4-FINALIST" and ident["derived_from"]["alternative_id"] == "alternative-2"
    assert ident["alternative_name"] == "Round 4 finalist composition E1+E3+E5, rehearsal"
    assert built["identity"]["evaluatorDigest"] == mp.evaluator_digest()
    assert built["identity"]["packageDigest"] not in set(SINGLE_FAMILY_DIGESTS.values())
    # no accepted family refits a curve set: the curves file is the base's
    assert {"reference-curves.json", "easi-metrics.json", "cwa-mapping.json", "functions.json",
            "ecoregion-crosswalk.json", "nars-ecoregions-9.geojson.gz"} == set(built["unchangedFiles"])
    assert built["curveSets"] == {}
    pkg = round4.package(built["files"], "FINALIST", spec)
    back = mp.read_package(mp.to_zip(pkg))
    assert back.digest == built["identity"]["packageDigest"]
    assert back.envelope["label"] == "Round 4 finalist composition E1+E3+E5 (rehearsal)"
    extras = [b for b in back.envelope["evaluator"]["requires"]["behaviors"] if b not in mp.BEHAVIORS]
    assert extras == ["applicability-rules"] and "mean_index" not in back.envelope["evaluator"]["requires"]["operators"]
    # the same composition however its families are given
    assert round4.build_composition(base, ("E1", "E3", "E5"))["files"] == built["files"]


def test_single_family_builds_are_untouched_by_the_composition(base):
    for family, digest in SINGLE_FAMILY_DIGESTS.items():
        built = round4.build_candidate(base, family)
        assert built["identity"]["packageDigest"] == digest, family
        assert "composition" not in built and "composition" not in built["spec"]
        pkg = round4.package(built["files"], family, built["spec"])
        assert pkg.envelope["label"].startswith(f"Round 4 candidate {family}:")


def test_the_composition_refuses_what_it_cannot_compose(base):
    with pytest.raises(round4.CandidateError, match="at least two"):
        round4.build_composition(base, ["E1"])
    with pytest.raises(round4.CandidateError, match="once"):
        round4.build_composition(base, ["E1", "E1", "E3"])
    with pytest.raises(round4.CandidateError, match="addendum's families"):
        round4.build_composition(base, ["E1", "E9"])
    with pytest.raises(round4.CandidateError, match="addendum's families"):
        round4.composition_spec(["FINALIST", "E1"])
    with pytest.raises(round4.CandidateError, match="none was given"):
        round4.composition_spec([])
    assert round4.composition_members(["E8", "E3"]) == ["E3", "E8"]
    # a refit family needs its set inside a composition as it does alone
    with pytest.raises(round4.CandidateError, match="flow-min-ratio"):
        round4.build_composition(base, ["E1", "E2"])
    # a re-specified component rides into the composition's record, dated
    spec = round4.composition_spec(["E2", "E1"])
    assert spec["composition"] == ["E1", "E2"] and spec["respecified"] == {"E2": round4.RESPECIFIED["E2"]}
    built = round4.build_composition(base, ["E2", "E1"], curve_sets=SETS)
    assert built["respecified"] == {"E2": "2026-09-27"} and built["composition"] == ["E1", "E2"]
    assert built["curveSets"]["flow-min-ratio"]["split"] == {"fcode_class": "perennial"}
    assert mp.validate_files(built["files"]) == []


def test_the_cli_builds_a_composition_as_candidate_finalist(tmp_path):
    source = LIBRARY_V1 if (LIBRARY_V1 / mp.ENVELOPE).is_file() else VENDORED_DATA
    out = tmp_path / "FINALIST"
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E5", "E1", "E3", "--base", str(source),
                           "--out", str(out)], cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    record = json.loads((out / "candidate.json").read_text(encoding="utf-8"))
    assert record["family"] == "FINALIST" and record["familyName"] == "finalist_composition"
    assert record["composition"] == ["E1", "E3", "E5"] and record["decision"] == "composition"
    assert record["primaryOutcome"] == "P1" and sorted(record["components"]) == ["E1", "E3", "E5"]
    assert record["components"]["E5"]["decision"] == "simplification"
    assert record["components"]["E5"]["function"] == ["high-flow-dynamics", "floodplain-connectivity",
                                                      "channel-evolution", "channel-floodplain-dynamics"]
    assert record["respecified"] is None and record["respecification"] is None
    assert [e["family"] for e in record["edits"]] == ["E1", "E3", "E5"] and record["label"] == "rehearsal"
    assert record["candidate"]["zip"] == "FINALIST.easi-method.zip"
    assert record["candidate"]["label"] == "Round 4 finalist composition E1+E3+E5 (rehearsal)"
    assert record["candidate"]["scoringIdentity"]["alternative_id"] == "round4-FINALIST"
    assert record["addendum"]["sha256"] == round4.ADDENDUM_SHA256
    pkg = mp.read_package(out / "FINALIST.easi-method.zip")
    assert pkg.digest == record["candidate"]["packageDigest"] != record["base"]["packageDigest"]
    if source == LIBRARY_V1:
        assert record["base"]["methodVersion"] == "b2e3033116e3"
    for name in mp.METHOD_FILES:
        assert (out / "method" / name).read_bytes() == pkg.files[name]
    printed = json.loads(proc.stdout)
    assert printed["composition"] == ["E1", "E3", "E5"] and printed["family"] == "FINALIST"
    # a family given twice is refused before anything is written
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E1", "E1", "--base", str(source),
                           "--out", str(tmp_path / "twice")], cwd=str(APP), capture_output=True, text=True,
                          timeout=600)
    assert proc.returncode != 0 and "once" in proc.stderr and not (tmp_path / "twice").exists()


# --------------------------------------------------------------------------- #
# E5b, the owner's adoption refinement of E5 (WP-R6c, Addendum 2, 2026-09-28)
# --------------------------------------------------------------------------- #
def _entrenchment_refit(base) -> dict:
    """A reliable-member refit of the shipped entrenchment set: the shipped curves with the
    lt_0.5 and national quartiles moved, the shipped quantity, stratifier and direction."""
    shipped = json.loads(base["reference-curves.json"])["sets"]["entrenchment"]
    definition = copy.deepcopy(shipped)
    for key in ("lt_0.5", "national"):
        curve = definition["curves"][key]
        curve["q25"] = round(curve["q25"] + 0.17, 6)
        curve["q75"] = round(curve["q75"] + 0.38, 6)
        curve["x39"] = round(curve["x39"] + 0.09, 6)
        curve["x69"] = round(curve["x69"] + 0.17, 6)
        curve["points"] = [[round(x + (0.1 if 0 < x else 0.0), 6), y] for x, y in curve["points"]]
        curve["n"] = curve["n"] - 1000
    return definition


def test_e5b_is_e5s_rule_on_the_k2b_flags_with_its_own_record(base):
    spec = round4.family_spec("E5b")
    parent = round4.family_spec("E5")
    assert spec["id"] == "E5b" and spec["family"] == "dem_geometry_reliable_sections"
    assert spec["function"] == parent["function"] and spec["decision"] == "simplification" == parent["decision"]
    assert spec["primary_outcome"] == parent["primary_outcome"] == "P1"
    assert spec["refinement"] == {"parent": "E5", "date": "2026-09-28", "addendum": 2, "decisionRecord": "D15",
                                  "curveSet": "entrenchment", "curves": round4.REFINEMENTS["E5b"]["curves"],
                                  "summary": round4.REFINEMENTS["E5b"]["summary"], "afterResults": True}
    assert "K2b" in spec["change"] and "supporting" not in spec and "respecified" not in spec
    assert round4.REFINEMENT_IDS == ("E5b",) and "E5b" not in round4.FAMILIES
    # the prose addendum carries Addendum 2 and says it came after the results; the yaml is frozen
    prose = (round4.ADDENDUM_PATH.parent / "EVALUATION_PROTOCOL_V1_EASI_ADDENDUM.md").read_text(encoding="utf-8")
    assert "Addendum 2 (2026-09-28): E5b, the owner's adoption refinement of E5" in prose
    assert "after the families were read" in prose and prose.isascii() and chr(8212) not in prose
    assert round4.addendum_sha256() == round4.ADDENDUM_SHA256
    # without a refit the shipped curves stand: only the catalog changes
    built = round4.build_candidate(base, "E5b")
    cat = json.loads(built["files"]["screening-methods.json"])
    for key in round4.CROSS_SECTION_METHODS:
        m = _method_of(cat, key)
        assert m["applicability"] == {"evidence": "crossSectionQuality", "withhold_when": ["low_quality", "out_of_range"],
                                      "statement": round4.E5B_STATEMENT}
        assert round4.E5B_LIMITATION in m["limitations"] and round4.E5B_REFIT_LIMITATION not in m["limitations"]
    assert "reference-curves.json" in built["unchangedFiles"] and built["curveSets"] == {}
    assert built["files"]["reference-curves.json"] == base["reference-curves.json"]
    assert built["refinement"]["parent"] == "E5" and built["respecified"] is None
    # the adoption candidate (WP-R6c-2): the kept edit names the shipped sets and the refit as
    # evaluated and not adopted, and the record carries that note beside the refinement
    kept = [e for e in built["edits"] if e.get("file") == "reference-curves.json" and e.get("kept")]
    assert len(kept) == 1 and kept[0]["kept"] == round4.REFINEMENTS["E5b"]["curves"] and kept[0]["replaced"] == []
    assert kept[0]["kept"] == "curves: the shipped sets; the reliable-member refit evaluated and not adopted, see WP-R6c"
    assert built["refinement"]["curves"] == kept[0]["kept"] and "not adopted" in spec["change"]
    assert built["scoringIdentity"]["alternative_id"] == "round4-E5b"
    # the statement differs from E5's, so the two packages are never confused
    e5 = round4.build_candidate(base, "E5")
    assert built["identity"]["packageDigest"] != e5["identity"]["packageDigest"]
    assert built["identity"]["methodVersion"] != e5["identity"]["methodVersion"]
    pkg = round4.package(built["files"], "E5b", built["spec"])
    back = mp.read_package(mp.to_zip(pkg))
    assert back.envelope["label"] == "Round 4 candidate E5b: dem_geometry_reliable_sections (rehearsal)"
    extras = [b for b in back.envelope["evaluator"]["requires"]["behaviors"] if b not in mp.BEHAVIORS]
    assert extras == ["applicability-rules"]


def test_e5b_replaces_the_entrenchment_set_in_place_when_a_refit_is_given(base):
    definition = _entrenchment_refit(base)
    built = round4.build_candidate(base, "E5b", curve_sets={"entrenchment": definition})
    curves = json.loads(built["files"]["reference-curves.json"])
    assert curves["sets"]["entrenchment"] == definition
    assert sorted(curves["sets"]) == ["corridor-natural", "corridor-woody", "entrenchment", "flow-variability"]
    assert built["curveSets"] == {"entrenchment": {"curves": ["0.5_to_2", "ge_2", "lt_0.5", "national"],
                                                    "quantity": "er_median", "replaced": ["lt_0.5", "national"],
                                                    "dropped": []}}
    assert "reference-curves.json" not in built["unchangedFiles"]
    cat = json.loads(built["files"]["screening-methods.json"])
    for key in ("entrenchment-ratio", "channel-adjustment-susceptibility"):
        assert round4.E5B_REFIT_LIMITATION in _method_of(cat, key)["limitations"]
    assert round4.E5B_REFIT_LIMITATION not in _method_of(cat, "bank-height-ratio")["limitations"]
    assert mp.validate_files(built["files"]) == []
    assert json.loads(built["files"]["scoring-identity.json"])["curve_count"] == 34
    mp.read_package(mp.to_zip(round4.package(built["files"], "E5b", built["spec"])))
    # a set that changes the shipped quantity, direction or strata, or equals it, is refused
    wrong = copy.deepcopy(definition)
    wrong["quantity"] = "bhr_median"
    with pytest.raises(round4.CandidateError, match="changes quantity"):
        round4.build_candidate(base, "E5b", curve_sets={"entrenchment": wrong})
    odd = copy.deepcopy(definition)
    odd["curves"]["TPL"] = odd["curves"]["national"]
    with pytest.raises(round4.CandidateError, match="slope-class curves only"):
        round4.build_candidate(base, "E5b", curve_sets={"entrenchment": odd})
    shipped = json.loads(base["reference-curves.json"])["sets"]["entrenchment"]
    with pytest.raises(round4.CandidateError, match="no refit to apply"):
        round4.build_candidate(base, "E5b", curve_sets={"entrenchment": copy.deepcopy(shipped)})
    # a refinement is built alone, never composed
    with pytest.raises(round4.CandidateError, match="built alone"):
        round4.build_composition(base, ["E1", "E5b"])
    with pytest.raises(round4.CandidateError, match="unknown family"):
        round4.family_spec("E5c")


def test_the_cli_builds_the_k2b_only_e5b_on_the_shipped_sets(tmp_path, base):
    """WP-R6c-2: the adoption candidate is E5b without --curve-sets; the curves file is the
    base's byte for byte and candidate.json says the refit was evaluated and not adopted."""
    source = LIBRARY_V1 if (LIBRARY_V1 / mp.ENVELOPE).is_file() else VENDORED_DATA
    out = tmp_path / "E5b-k2b"
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E5b", "--base", str(source), "--out", str(out)],
                          cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    record = json.loads((out / "candidate.json").read_text(encoding="utf-8"))
    assert record["family"] == "E5b" and record["curveSets"] == {"note": round4.REFINEMENTS["E5b"]["curves"]}
    assert "reference-curves.json" in record["unchangedFiles"]
    assert record["refinement"]["curves"] == round4.REFINEMENTS["E5b"]["curves"] and record["refinement"]["afterResults"] is True
    pkg = mp.read_package(out / "E5b.easi-method.zip")
    assert pkg.files["reference-curves.json"] == round4.base_files(source)["reference-curves.json"]
    assert pkg.digest == record["candidate"]["packageDigest"]
    cat = json.loads(pkg.files["screening-methods.json"])
    for key in round4.CROSS_SECTION_METHODS:
        m = _method_of(cat, key)
        assert m["applicability"]["statement"] == round4.E5B_STATEMENT
        assert round4.E5B_REFIT_LIMITATION not in m["limitations"]
    # the same build with a refit set is a different package: the two are never confused
    with_refit = round4.build_candidate(base, "E5b", curve_sets={"entrenchment": _entrenchment_refit(base)})
    assert with_refit["identity"]["packageDigest"] != pkg.digest
    assert with_refit["identity"]["methodVersion"] != record["candidate"]["methodVersion"]


def test_the_cli_builds_e5b_with_the_refit_set(tmp_path, base):
    source = LIBRARY_V1 if (LIBRARY_V1 / mp.ENVELOPE).is_file() else VENDORED_DATA
    sets_path = tmp_path / "curve-sets.json"
    sets_path.write_text(json.dumps({"schema": "staf-easi-candidate-curves", "schemaVersion": 1,
                                     "sets": {"entrenchment": _entrenchment_refit(base)},
                                     "provenance": {"reliability": "K2b", "material": True}}), encoding="utf-8")
    out = tmp_path / "E5b"
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E5b", "--base", str(source),
                           "--out", str(out), "--curve-sets", str(sets_path)], cwd=str(APP), capture_output=True,
                          text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    record = json.loads((out / "candidate.json").read_text(encoding="utf-8"))
    assert record["family"] == "E5b" and record["familyName"] == "dem_geometry_reliable_sections"
    assert record["refinement"]["parent"] == "E5" and record["refinement"]["afterResults"] is True
    assert record["curveSets"]["entrenchment"]["replaced"] == ["lt_0.5", "national"]
    assert record["curveSets"]["provenance"] == {"reliability": "K2b", "material": True}
    assert record["decision"] == "simplification" and record["respecified"] is None
    pkg = mp.read_package(out / "E5b.easi-method.zip")
    assert pkg.digest == record["candidate"]["packageDigest"]
    printed = json.loads(proc.stdout)
    assert printed["refinement"]["addendum"] == 2 and printed["family"] == "E5b"


def _fake_calculator(path: Path, method_version: str) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Metadata"
    ws.append(["EASI calculator metadata"])
    ws.append(["Item", "Value"])
    ws.append(["Scoring method digest", method_version])
    ws.append(["Generator sha256", "0" * 64])
    wb.save(path)
    return path


def test_the_exporter_carries_a_calculator_generated_for_the_candidates_files(tmp_path):
    source = LIBRARY_V1 if (LIBRARY_V1 / mp.ENVELOPE).is_file() else VENDORED_DATA
    cand = tmp_path / "E5b"
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E5b", "--base", str(source), "--out", str(cand)],
                          cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    record = json.loads((cand / "candidate.json").read_text(encoding="utf-8"))
    version = record["candidate"]["methodVersion"]
    good = _fake_calculator(tmp_path / "EASI_Calculator_test.xlsx", version)
    assert round4.calculator_stamps(good.read_bytes()) == {"method": version, "generator": "0" * 64}
    name, blob = round4.candidate_calculator(good, method_version=version)
    assert name == "EASI_Calculator_test.xlsx" and blob == good.read_bytes()
    project, _ = round4.candidate_project(cand, calculator=good)
    assert project.calculator == (name, blob) and project.meta["calculatorFor"] == project.package_digest
    assert project.meta["calculator"]["generatedFor"] == version
    vdir = tmp_path / "version"
    proc = subprocess.run([sys.executable, "-B", str(EXPORT_SCRIPT), str(cand), "--version-dir", str(vdir),
                           "--calculator", str(good), "--calculator-note", "template 1.1 at adoption"],
                          cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    envelope = json.loads((vdir / "method.json").read_text(encoding="utf-8"))
    assert envelope["calculator"] == {"name": name, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
    assert (vdir / "calculator" / name).read_bytes() == blob
    assert out["versionDir"]["calculator"]["path"] == f"calculator/{name}" and f"calculator/{name}" in out["versionDir"]["files"]
    # the note rides in the version candidate's record only, never in the workbook or the envelope
    assert out["versionDir"]["calculator"]["note"] == "template 1.1 at adoption" and "note" not in envelope["calculator"]
    proc = subprocess.run([sys.executable, "-B", str(EXPORT_SCRIPT), str(cand), "--out", str(tmp_path / "x.zip"),
                           "--calculator-note", "orphan"], cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode != 0 and "--calculator-note needs" in proc.stderr
    zip_name = out["versionDir"]["zip"]
    pkg = mp.read_package(vdir / zip_name)
    assert pkg.calculator == (name, blob) and pkg.digest == record["candidate"]["packageDigest"]
    written = json.loads((vdir / "candidate.json").read_text(encoding="utf-8"))
    assert written["versionCandidate"]["calculator"]["generatedFor"] == version
    assert written["versionCandidate"]["lineage"]["candidate"]["refinement"]["parent"] == "E5"
    # a workbook stamped with another method never rides along; nothing is written
    bad = _fake_calculator(tmp_path / "EASI_Calculator_other.xlsx", "000000000000")
    with pytest.raises(round4.CandidateError, match="records scoring method"):
        round4.candidate_calculator(bad, method_version=version)
    proc = subprocess.run([sys.executable, "-B", str(EXPORT_SCRIPT), str(cand), "--version-dir", str(tmp_path / "never"),
                           "--calculator", str(bad)], cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode != 0 and "records scoring method" in proc.stderr and not (tmp_path / "never").exists()
    with pytest.raises(round4.CandidateError, match="EASI_Calculator_"):
        round4.candidate_calculator(cand / "candidate.json", method_version=version)
    with pytest.raises(round4.CandidateError, match="not found"):
        round4.candidate_calculator(tmp_path / "EASI_Calculator_missing.xlsx", method_version=version)


EXPORT_SCRIPT = APP / "scripts" / "export_easi_method.py"


def test_the_exporter_writes_a_library_version_candidate_from_a_candidate_folder(tmp_path):
    """The adoption candidate leaves StreamCurves through the exporter, the sole writer: a
    candidate package folder is a project it packages; --version-dir writes the version
    candidate folder outside every library, registering nothing."""
    source = LIBRARY_V1 if (LIBRARY_V1 / mp.ENVELOPE).is_file() else VENDORED_DATA
    cand = tmp_path / "FINALIST"
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--family", "E1", "E3", "E5", "--base", str(source),
                           "--out", str(cand)], cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    record = json.loads((cand / "candidate.json").read_text(encoding="utf-8"))
    vdir = tmp_path / "finalist-version"
    proc = subprocess.run([sys.executable, "-B", str(EXPORT_SCRIPT), str(cand), "--version-dir", str(vdir)],
                          cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    expected_version = 2 if source == LIBRARY_V1 else 1
    assert out["versionDir"]["version"] == expected_version and out["versionDir"]["registered"] is False
    assert out["packageDigest"] == record["candidate"]["packageDigest"] == out["versionDir"]["packageDigest"]
    zip_name = f"easi-screening-v{expected_version}.easi-method.zip"
    envelope = json.loads((vdir / "method.json").read_text(encoding="utf-8"))
    assert envelope["label"] == "Round 4 finalist composition E1+E3+E5 (rehearsal)"
    assert envelope["version"] == expected_version and envelope["methodId"] == "easi-screening"
    assert envelope["identity"]["packageDigest"] == record["candidate"]["packageDigest"]
    assert envelope["identity"]["methodVersion"] == record["candidate"]["methodVersion"]
    assert envelope["identity"]["evaluatorDigest"] == mp.evaluator_digest()
    assert envelope["evaluator"]["requires"]["behaviors"][-1] == "applicability-rules" and "calculator" not in envelope
    pkg = mp.read_package(vdir / zip_name)
    assert pkg.digest == record["candidate"]["packageDigest"] and pkg.envelope == envelope
    sha_text = (vdir / (zip_name + ".sha256")).read_text(encoding="utf-8")
    assert sha_text.split()[0] == hashlib.sha256((vdir / zip_name).read_bytes()).hexdigest() == out["versionDir"]["zipSha256"]
    assert zip_name in sha_text
    for name in mp.METHOD_FILES:
        assert (vdir / "method" / name).read_bytes() == pkg.files[name] == (cand / "method" / name).read_bytes()
    written = json.loads((vdir / "candidate.json").read_text(encoding="utf-8"))
    assert written["composition"] == ["E1", "E3", "E5"] and written["family"] == "FINALIST"
    vc = written["versionCandidate"]
    assert vc["registered"] is False and vc["proposedVersion"] == expected_version and vc["zip"] == zip_name
    assert vc["identity"] == envelope["identity"] and vc["lineage"]["candidate"]["composition"] == ["E1", "E3", "E5"]
    assert vc["lineage"]["origin"]["packageDigest"] == record["base"]["packageDigest"]
    assert vc["lineage"]["origin"]["methodVersion"] == record["base"]["methodVersion"]
    assert "owner" in vc["note"] and "registered in no manifest" in vc["note"]
    assert sorted(p.name for p in vdir.iterdir()) == sorted(["method", "method.json", zip_name, zip_name + ".sha256",
                                                             "candidate.json"])
    # never under the library, never a library layout, never EASI's data folder
    for bad in (REPO / "apps" / "library" / "assessments" / "easi-screening" / "v9",
                tmp_path / "lib" / "assessments" / "easi-screening" / "v2",
                REPO / "apps" / "easi" / "data" / "v2"):
        proc = subprocess.run([sys.executable, "-B", str(EXPORT_SCRIPT), str(cand), "--version-dir", str(bad)],
                              cwd=str(APP), capture_output=True, text=True, timeout=600)
        assert proc.returncode != 0 and ("never written there" in proc.stderr or "registered nowhere" in proc.stderr), bad
        assert not bad.exists()
    # a candidate whose method files no longer match its record is refused
    tampered = tmp_path / "tampered"
    shutil.copytree(cand, tampered)
    path = tampered / "method" / "cwa-mapping.json"
    path.write_bytes(path.read_bytes() + b"\n")
    proc = subprocess.run([sys.executable, "-B", str(EXPORT_SCRIPT), str(tampered), "--version-dir", str(tmp_path / "never")],
                          cwd=str(APP), capture_output=True, text=True, timeout=600)
    assert proc.returncode != 0 and "names package" in proc.stderr and not (tmp_path / "never").exists()
    # the project the exporter packages is the candidate byte for byte, a revision of the base
    project, rec = round4.candidate_project(cand)
    assert project.files == pkg.files and project.meta["label"] == envelope["label"] and project.is_revision()
    assert project.meta["lineage"]["origin"]["methodVersion"] == rec["base"]["methodVersion"]
    assert project.package_digest == rec["candidate"]["packageDigest"] and project.meta["version"] == expected_version
    assert round4.candidate_project(cand, version=7)[0].meta["version"] == 7
    with pytest.raises(round4.CandidateError, match="no candidate.json"):
        round4.candidate_project(tmp_path / "nowhere")
