"""Fold-local refits exclude whole evaluation watersheds and preserve originals."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis import artifact, curves
from builder.analysis.alternatives import field_evaluation, spatial
from builder.analysis.alternatives.io import read_json, sha, write_json, write_parquet


def hucs_by_fold():
    out = {}
    for number in range(100, 1000):
        huc = str(number).zfill(8)
        out.setdefault(field_evaluation.huc8_fold(huc), huc)
        if len(out) == 5:
            return out
    raise AssertionError("Could not find five deterministic test watersheds")


def test_connected_ledger_uses_missing_target_identities_and_excludes_all_hucs():
    hucs = hucs_by_fold()
    rows = [{"station_key": "shared", "comid": 1, "huc8": hucs[0], "status": "eligible"},
            {"station_key": "shared", "comid": 2, "huc8": hucs[1], "status": "missing_target"},
            {"station_key": "other", "comid": 2, "huc8": hucs[2], "status": "eligible"},
            {"station_key": "unresolved", "comid": 3, "huc8": "", "status": "missing_huc8"}]
    ledgers, summary = spatial.fold_ledger(rows)
    selected = next(row for row in ledgers if row["evaluation_comids"])
    assert selected["evaluation_comids"] == [1, 2]
    assert selected["excluded_huc8"] == sorted([hucs[0], hucs[1], hucs[2]])
    assert summary["eligible_heldout_comids"] == 2 and summary["unresolved_huc8_rows"] == 1
    assert sum(bool(row["evaluation_comids"]) for row in ledgers) == 1
    rows[0]["fold"] = (selected["fold"] + 1) % 5
    with pytest.raises(ValueError, match="disagree"):
        spatial.fold_ledger(rows)


def reference_frame():
    rows = []
    for i in range(75):
        rows.append({"comid": 1000 + i, "level": "national", "stratum": "national:national",
                     "slope_class": "lt_0.5", "screen": "strict", "_hucs": ("00000001",) if i < 20 else ("00000002",),
                     "woody_wsrp100": 99.0 if i < 20 else float(i), "er_median": 99.0 if i < 20 else 1 + i / 10,
                     "pressure__agriculture_ws": float(i % 7)})
    rows[-1]["screen"] = "relaxed"
    rows[-2]["_hucs"] = ()
    return pd.DataFrame(rows)


def test_refits_remove_whole_watershed_nonstrict_and_unknown_members_including_er():
    frame = reference_frame()
    definitions = {("woody_wsrp100", "national", "national:national", ""): True,
                   ("er_median", "national", "national:national", ""): True,
                   ("er_median", "national", "national:national", "lt_0.5"): True}
    fits, records, excluded = spatial.refit_fold(frame, definitions, {"00000001"}, {1000})
    assert excluded["heldout_watershed_memberships"] == 20
    assert excluded["direct_heldout_comid_memberships"] == 1
    assert excluded["unresolved_huc8_memberships"] == 1 and excluded["nonstrict_memberships"] == 1
    assert excluded["retained_memberships"] == 53
    assert len(fits) == 3
    for record in records:
        assert record["n_members"] == 53 and record["n"] == 53
        assert record["heldout_comid_overlap"] == record["heldout_huc8_overlap"] == 0
        assert record["panel_tier"] == "exploratory"
        expected = curves.fit_curve(frame.iloc[20:73][record["quantity"]].to_numpy(), curves.QUANTITIES[record["quantity"]], "test")
        assert record["points"] == expected["points"]
        assert fits[(record["quantity"], record["level"], record["stratum"], record["split"])] == artifact._curve(record)


def test_unknown_and_conflicting_reference_hucs_are_explicit():
    assert spatial._member_hucs({"comid": 1, "huc12": None}, {}) == ()
    assert spatial._member_hucs({"comid": 1, "huc12": "000000000000"}, {}) == ()
    assert spatial._member_hucs({"comid": 1, "huc12": "020802040101"}, {1: "02080205"}) == ("02080204", "02080205")


def test_unavailable_fit_never_reuses_frozen_curve():
    frame = reference_frame()
    identity = ("woody_wsrp100", "national", "national:national", "")
    fits, records, _ = spatial.refit_fold(frame, {identity: True}, {"00000001", "00000002"}, set())
    assert fits == {} and records[0]["usable"] is False
    assert records[0]["reason"] == "fewer than 30 finite retained values"
    frozen = {"schemaVersion": 1, "sets": {"woody": {"quantity": "woody_wsrp100", "stratifier": "l2",
                "curves": {"national": {"points": [[0, 0], [100, 1]]}, "8.2": {"points": [[0, 0], [100, 1]]}}}}}
    candidate, unavailable, missing = spatial._candidate_artifact(frozen, fits, 0)
    assert candidate["sets"]["woody"]["curves"] == {}
    assert missing == ["woody"] and len(unavailable) == 2
    assert frozen["sets"]["woody"]["curves"]["national"]["points"] == [[0, 0], [100, 1]]


@pytest.fixture
def mini_study(tmp_path):
    root = tmp_path / "national"
    study = root / "review/alternative-studies/synthetic"
    original = root / spatial.ORIGINAL
    hucs = hucs_by_fold()
    ids = list(range(1000, 1240))
    reference = [{"comid": comid, "huc8": hucs[i % 5], "woody_wsrp100": 10 + i % 80,
                  "natural_wsrp100": 20 + i % 70, "erom__q_cv_monthly": .1 + (i % 60) / 20,
                  "agriculture_ws": i % 8} for i, comid in enumerate(ids)]
    members = [{"comid": comid, "huc12": hucs[i % 5] + "0101", "slope_class": ["lt_0.5", "0.5_to_2", "ge_2"][i % 3],
                "screen": "strict", "panel_tier": "complete", "level": level, "stratum": stratum}
               for level, stratum in (("l2", "l2:8.2"), ("nars9", "nars9:CPL"), ("national", "national:national"))
               for i, comid in enumerate(ids)]
    panel = [{"level": level, "stratum": stratum, "screen": "strict", "panel_tier": "complete"}
             for level, stratum in (("l2", "l2:8.2"), ("nars9", "nars9:CPL"), ("national", "national:national"))]
    write_parquet(original / "landscape.parquet", pa.Table.from_pylist(reference))
    write_parquet(original / "values.parquet", pa.Table.from_pylist([{"comid": comid, "er_median": 1 + (i % 80) / 10} for i, comid in enumerate(ids)]))
    write_parquet(original / "panels/panel_members.parquet", pa.Table.from_pylist(members))
    write_parquet(original / "panels/reference_panels.parquet", pa.Table.from_pylist(panel))
    write_parquet(original / "curves/curve_registry.parquet", pa.Table.from_pylist([{"source": "preserved-registry"}]))
    point = {"points": [[0, 0], [100, 1]], "n": 240, "nMembers": 240, "q25": 25, "q50": 50, "q75": 75,
             "x39": 39, "x69": 69, "status": "complete", "panelTier": "complete", "screen": "strict"}
    sets = {sid: {"quantity": quantity, "stratifier": stratifier, "higherIsBetter": True,
                  "curves": {key: deepcopy(point) for key in (["national", "8.2"] if stratifier == "l2" else ["national", "lt_0.5", "0.5_to_2", "ge_2"])}}
            for sid, (quantity, stratifier) in artifact.SET_SPECS.items()}
    frozen = {"schemaVersion": 1, "sets": sets, "provenance": {
        "registry": {"sha256": sha(original / "curves/curve_registry.parquet")},
        "entrenchmentNationalFallback": {"panelMembersSha256": sha(original / "panels/panel_members.parquet"),
                                          "referencePanelsSha256": sha(original / "panels/reference_panels.parquet")}}}
    write_json(study / "snapshot/app-data/reference-curves.json", frozen)
    for number in range(1, 5):
        candidate = deepcopy(frozen)
        for sid in ("corridor-woody", "corridor-natural", "flow-variability"):
            definition = candidate["sets"][sid]
            if number == 2:
                definition["stratifier"] = "nars9"
                definition["curves"]["CPL"] = definition["curves"].pop("8.2")
            elif number == 3:
                definition["stratifier"] = "national"
                definition["curves"].pop("8.2")
            elif number == 4 and sid == "corridor-woody":
                definition["curves"].pop("8.2")
        directory = study / f"candidates/alternative-{number}/app-data"
        write_json(directory / "reference-curves.json", candidate)
        write_json(directory / "screening-methods.json", {"synthetic": "catalog preserved verbatim"})
    observations = field_evaluation.group_folds([{"station_key": f"station-{i}", "comid": ids[i], "huc8": hucs[i], "status": "eligible"} for i in range(5)])
    write_parquet(study / "cohorts/observations.parquet", pa.Table.from_pylist(observations))
    write_parquet(study / "evidence/part.parquet", pa.Table.from_pylist([{"comid": comid, "raw": "stored"} for comid in ids[:7]]))
    return root, study


def test_run_writes_separate_heldout_scores_and_never_changes_frozen_sources(mini_study, monkeypatch):
    root, study = mini_study
    before = {str(p): sha(p) for p in root.rglob("*") if p.is_file()}
    launches = []
    def launch(fold_study, data_dir, destination, *, comids_file):
        ids = read_json(comids_file)
        assert len(ids) == 1
        local = [row["comid"] for p in (fold_study / "evidence").glob("*.parquet") for row in pq.read_table(p).to_pylist()]
        assert local == ids
        fitted = read_json(data_dir / "reference-curves.json")
        assert fitted["provenance"]["frozen_scoring_artifact"] is False
        assert all("national" in row["curves"] for row in fitted["sets"].values())
        assert fitted["sets"]["entrenchment"]["curves"]["national"]["n"] == 192
        launches.append(str(destination))
        write_parquet(destination, pa.Table.from_pylist([{"comid": comid, "eci": .6,
             "rating__light_thermal_regime": "Fair", "fallback__light_thermal_regime": False,
             "curves__light_thermal_regime": json.dumps({"woody": {"fallbackDepth": 1}})} for comid in ids]))
    monkeypatch.setattr(spatial.scorer, "launch", launch)
    result = spatial.run(root, study)
    assert result["status"] == "complete" and len(launches) == 20
    assert result["observations"]["eligible_heldout_comids"] == 5
    assert all(item["exclusions"]["heldout_watershed_memberships"] == 144 for item in result["folds"])
    for number in range(1, 5):
        frame = pq.read_table(study / f"spatial/scores/alternative-{number}.parquet").to_pandas()
        assert len(frame) == 5 and frame.comid.is_unique and set(frame.fold) == set(range(5))
        assert not (study / f"scores/alternative-{number}.parquet").exists()
    assert before == {path: sha(Path(path)) for path in before}
    assert spatial.run(root, study)["input_digest"] == result["input_digest"]
    assert len(launches) == 20


def test_registry_mismatch_fails_before_scoring(mini_study, monkeypatch):
    root, study = mini_study
    write_parquet(root / spatial.ORIGINAL / "curves/curve_registry.parquet", pa.Table.from_pylist([{"source": "current-replacement"}]))
    monkeypatch.setattr(spatial.scorer, "launch", lambda *args, **kwargs: pytest.fail("No scoring with wrong reference provenance"))
    with pytest.raises(ValueError, match="registry does not match"):
        spatial.run(root, study)


def test_current_landscape_cannot_replace_missing_original(mini_study):
    root, study = mini_study
    original = root / spatial.ORIGINAL / "landscape.parquet"
    frame = pq.read_table(original)
    original.unlink()
    write_parquet(root / "analysis/landscape.parquet", frame)
    with pytest.raises(FileNotFoundError, match="current analysis cannot substitute"):
        spatial.run(root, study)
