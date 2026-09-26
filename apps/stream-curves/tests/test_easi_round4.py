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
import json
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


SETS = {"flow-min-ratio": _set("q_min_ratio"), "width-variability": _set("bankfull_width_cv")}


@pytest.fixture(scope="module")
def base():
    return round4.base_files(VENDORED_DATA)


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
        if family in ("E1", "E5"):
            assert extras == ["applicability-rules"]
        elif family == "E8":
            assert extras == ["rollup-completeness-interval"]
        else:
            assert extras == []
        assert ("mean_index" in req["operators"]) == (family == "E7")
        if family in round4.FAMILY_SETS:
            assert round4.FAMILY_SETS[family] in json.loads(built["files"]["reference-curves.json"])["sets"]
            assert built["curveSets"][round4.FAMILY_SETS[family]]["curves"] == ["TPL", "national"]


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
    e2 = method(cat(round4.build_candidate(base, "E2", curve_sets=SETS)), "erom-flow-min-ratio")
    assert [i["key"] for i in e2["inputs"]] == ["flowMinRatio", "meanAnnualFlow", "fcodeContext"]
    assert e2["curve"]["set"] == "flow-min-ratio" and e2["metricId"] == LOW_FLOW
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
    # the refit output builds the E2 and E4 packages
    for family in ("E2", "E4"):
        built = round4.build_candidate(base, family, curve_sets=sets)
        assert mp.validate_files(built["files"]) == []
        mp.read_package(mp.to_zip(round4.package(built["files"], family, built["spec"])))
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
    packages = {"base": mp.package_from_dir(VENDORED_DATA, version=1, label="base")}
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
