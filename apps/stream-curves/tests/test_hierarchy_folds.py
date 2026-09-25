"""Campaign Round 2, work package R2a: the fold rule of the hierarchy harness.

A withheld region informs nothing it is scored under: its strict stations
leave every pool (already so), the region leaves the EPA agriculture-limit
calibration of the regional screen (``reference_pool.epa_agriculture_limit``
``withhold_l3``), and the scale registry a cell reads is a per-fold one
(``run_national_scale_analysis.py --withhold-l3``, ``run_hierarchy_test.py
--registry``), with the share of decisions identical to the full registry
reported so the protocol's 0.95 shortcut can be applied.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from streamcurves import reference_pool as rp
from streamcurves import scale_analysis as sa
from tests.test_reference_pool import CFG, SETTINGS, _frame, _stations, _values

APP = Path(__file__).resolve().parents[1]
SCRIPTS = APP / "scripts"


def _script(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HS = {"agriculture_quantile": 0.9, "agriculture_floor": 25.0, "min_epa_reference_sites": 5,
      "screen_id": "x", "widen": 0.05, "enabled": True}


def _epa_table():
    # EPA reference sites of one NARS-9 region in two Level III ecoregions: the
    # heavily farmed 65 would set a high limit on its own
    return pd.DataFrame({"nars9": ["TPL"] * 12,
                         "rt_nrsa": ["R"] * 12,
                         "l3": ["65"] * 6 + ["47"] * 6,
                         "agriculture_ws": [80, 90, 95, 99, 100, 100, 10, 12, 14, 16, 18, 20]})


# --------------------------------------------------------------------------- #
# the agriculture calibration
# --------------------------------------------------------------------------- #
def test_the_withheld_region_leaves_the_agriculture_calibration():
    full = rp.epa_agriculture_limit(_epa_table(), "TPL", HS)
    assert full["rule"] == "epa_reference_range" and full["limit"] > 90
    assert "withheld_l3" not in full
    fold = rp.epa_agriculture_limit(_epa_table(), "TPL", HS, withhold_l3="65")
    assert fold["withheld_l3"] == ["65"] and fold["n_epa_reference"] == 6
    assert fold["rule"] == "floor" and fold["limit"] == 25.0
    # several codes, and a code the table does not hold, both work
    both = rp.epa_agriculture_limit(_epa_table(), "TPL", HS, withhold_l3=["65", "99"])
    assert both["withheld_l3"] == ["65", "99"] and both["limit"] == 25.0
    # nothing withheld reads exactly as before
    assert rp.epa_agriculture_limit(_epa_table(), "TPL", HS, withhold_l3=[]) == full


def test_choose_pool_passes_the_withheld_region_to_the_calibration():
    frame = _frame(_stations("T", 40, l3="55", l2="8.2", strict=False),
                   _stations("M", 4, l3="56", l2="8.2", flat=True))
    d, _ = rp.choose_pool("m_form", _values(frame), frame, "55",
                          profile=rp.family_profile("m_form", CFG), cfg=CFG,
                          settings=SETTINGS, withhold_l3="55")
    assert d.status == rp.STATUS_INSUFFICIENT
    assert d.screen_detail["calibration_withheld_l3"] == ["55"]
    plain, _ = rp.choose_pool("m_form", _values(frame), frame, "55",
                              profile=rp.family_profile("m_form", CFG), cfg=CFG,
                              settings=SETTINGS)
    assert "calibration_withheld_l3" not in plain.screen_detail


# --------------------------------------------------------------------------- #
# the per-fold registry
# --------------------------------------------------------------------------- #
def test_withholding_regions_drops_their_stations_from_the_analysis_inputs():
    scale = _script("run_national_scale_analysis")
    frame = _frame(_stations("A", 5, l3="58"), _stations("B", 4, l3="65"),
                   _stations("C", 3, l3="17"))
    values = pd.DataFrame({"site_id": frame["station_key"].astype(str),
                           "m": range(len(frame))})
    kept_frame, kept_values = scale.withhold_regions(frame, values, ["65"])
    assert set(kept_frame["l3"]) == {"58", "17"} and len(kept_frame) == 8
    assert set(kept_values["site_id"]) == set(kept_frame["station_key"])
    assert not kept_values["site_id"].str.startswith("B").any()
    same_frame, same_values = scale.withhold_regions(frame, values, [])
    assert len(same_frame) == 12 and len(same_values) == 12


def test_the_agreement_shortcut_reports_the_share_of_identical_decisions():
    scale = _script("run_national_scale_analysis")
    full = {"metrics": {"a": {"supported_level": "l3", "split": None},
                        "b": {"supported_level": "l2", "split": "slope"},
                        "c": {"supported_level": "national", "split": None}}}
    same = scale.registry_agreement(full, full)
    assert same["share"] == 1.0 and same["fullRegistryMayStandIn"] is True
    fold = {"metrics": {**full["metrics"], "c": {"supported_level": "l1", "split": None}}}
    got = scale.registry_agreement(fold, full)
    assert got["nMetrics"] == 3 and got["nIdentical"] == 2 and got["share"] == 0.6667
    assert got["differ"] == ["c"] and got["fullRegistryMayStandIn"] is False
    assert got["shortcut"] == scale.AGREEMENT_SHORTCUT == 0.95
    # a fold registry that reaches the shortcut on many metrics may stand in
    many = {"metrics": {f"m{i}": {"supported_level": "l3", "split": None} for i in range(40)}}
    almost = {"metrics": {**many["metrics"], "m0": {"supported_level": "l2", "split": None}}}
    assert scale.registry_agreement(almost, many)["fullRegistryMayStandIn"] is True


def test_the_scale_script_refuses_a_fold_without_an_out_folder(capsys):
    scale = _script("run_national_scale_analysis")
    assert scale.main(["--withhold-l3", "58"]) == 2
    assert "--withhold-l3 needs --out" in capsys.readouterr().out
    assert scale.main(["--withhold-l3", "58", "--out", "x", "--check"]) == 2


# --------------------------------------------------------------------------- #
# the harness reads a cell's fold registry, and never one that saw the cell
# --------------------------------------------------------------------------- #
def _fold_registry(tmp_path: Path, code: str, *, withheld=None, level="l2") -> Path:
    reg = {"version": 1, "analysis_version": "t", "inputs": {"withheldL3": withheld or [code]},
           "rule": {}, "stratifiers": {},
           "metrics": {"a": {"supported_level": level, "split": None},
                       "b": {"supported_level": "l3", "split": None}}}
    folder = tmp_path / f"l3-{code}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "registry_candidate.yaml"
    path.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
    return path


def test_the_harness_resolves_a_registry_per_cell(tmp_path):
    hier = _script("run_hierarchy_test")
    assert hier.parse_registry_args(["58=a.yaml", "65=b.yaml"]) == {"58": Path("a.yaml"),
                                                                    "65": Path("b.yaml")}
    with pytest.raises(ValueError):
        hier.parse_registry_args(["58"])
    path = _fold_registry(tmp_path, "58")
    assert hier.registry_for("58", {"58": path}, None) == path
    assert hier.registry_for("58", None, tmp_path) == path
    assert hier.registry_for("65", None, tmp_path) is None
    assert hier.registry_for("65", {"58": path}, None) is None


def test_the_cell_reads_its_own_fold_and_reports_agreement(tmp_path):
    hier = _script("run_hierarchy_test")
    full = {"metrics": {"a": {"supported_level": "l3", "split": None},
                        "b": {"supported_level": "l3", "split": None}}}
    path = _fold_registry(tmp_path, "58", level="l2")
    cells = hier.load_cell_registries(["58", "65"], {"58": path}, None, full=full)
    fold = cells["58"]
    assert fold["fold"] is True and fold["withheldL3"] == ["58"] and fold["path"] == str(path)
    assert fold["registry"]["metrics"]["a"]["supported_level"] == "l2"
    assert fold["agreement"]["share"] == 0.5 and fold["agreement"]["differ"] == ["a"]
    assert fold["agreement"]["fullRegistryMayStandIn"] is False
    assert fold["sha256"].startswith("sha256:")
    # a cell with no fold registry reads the full one and says so
    plain = cells["65"]
    assert plain["fold"] is False and plain["path"] is None
    assert plain["registry"] is full and "committed registry" in plain["agreement"]["note"]
    assert plain["agreement"]["fullRegistryMayStandIn"] is True


def test_a_registry_that_saw_the_cell_is_refused(tmp_path):
    hier = _script("run_hierarchy_test")
    path = _fold_registry(tmp_path, "58", withheld=["65"])
    with pytest.raises(ValueError, match="does not withhold L3 58"):
        hier.load_cell_registries(["58"], {"58": path}, None, full={"metrics": {}})
    with pytest.raises(FileNotFoundError):
        hier.load_cell_registries(["58"], {"58": tmp_path / "missing.yaml"}, None,
                                  full={"metrics": {}})


def test_the_committed_registry_is_what_a_cell_without_a_fold_reads():
    hier = _script("run_hierarchy_test")
    full = sa.load_registry()
    cells = hier.load_cell_registries(["58"], None, None)
    assert cells["58"]["registry"] is full
    assert cells["58"]["sha256"] == sa.registry_sha256()
    summary = {k: v for k, v in cells["58"].items() if k != "registry"}
    json.dumps(summary)     # what hierarchy.json records must serialize
