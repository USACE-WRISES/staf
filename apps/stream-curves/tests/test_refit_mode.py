"""--refit {missing,all} (campaign Round 1, hook H1): missing (the methodology's
carry-forward default) carries every published curve forward, all passes
carry=False to the evidence pass and never reads the canonical library, while
the owner's holds and forced sources still apply; the manifest records
inputs.reference.carryForward and the packet lists the held metrics.

Offline and cheap: the evidence pass itself is never run.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

from streamcurves import methodology
from streamcurves import regional_agent as ra

APP = Path(__file__).resolve().parents[1]
SCRIPT = APP / "scripts" / "run_region_batch.py"
LABEL = "Rehearsal (not an owner decision)"


def _batch():
    scripts = str(SCRIPT.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("run_region_batch_refit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_default_mode_is_the_methodologys_carry_forward():
    assert methodology.carry_forward_default() in methodology.CARRY_FORWARD_CHOICES
    expected = {"published_curves": "missing", "none": "all"}[methodology.carry_forward_default()]
    assert ra.refit_mode(None) == expected and ra.refit_mode("") == expected
    assert ra.refit_mode("all") == "all" and ra.refit_mode("missing") == "missing"
    with pytest.raises(ValueError, match="unknown refit mode"):
        ra.refit_mode("some")
    assert ra.REFIT_MODES == ("missing", "all")


def test_the_carry_forward_record_names_the_version_or_says_off():
    assert ra.carry_forward_record("all", {"carried_from": {"fromVersion": 8}}) == {
        "mode": "off", "fromVersion": None}
    assert ra.carry_forward_record("missing", {"carried_from": {"fromVersion": 8}}) == {
        "mode": "published_curves", "fromVersion": 8}
    assert ra.carry_forward_record("missing", {}) == {"mode": "published_curves", "fromVersion": None}


class _Stop(Exception):
    pass


def _stage_ns(mod, tmp_path, *extra):
    return mod.build_parser().parse_args(
        ["stage", "--l3", "55", "--name", "Eastern Corn Belt Plains", "--out", str(tmp_path / "run"),
         "--nrsa-dataset", "legacy-1819", "--no-screen", "--no-streamcat", "--n-boot", "20",
         "--maintainer", LABEL, *extra])


def test_refit_all_passes_carry_false_and_the_value_policy_reaches_the_pass(tmp_path, monkeypatch):
    mod = _batch()
    seen = {}

    def stop(*args, **kw):
        seen.update(kw)
        raise _Stop()

    monkeypatch.setattr(mod.ra, "run_evidence", stop)
    monkeypatch.setattr(mod, "published_bundle", lambda code: None)
    with pytest.raises(_Stop):
        mod.cmd_stage(_stage_ns(mod, tmp_path, "--refit", "all", "--value-policy", "newest-nonnull-v1"))
    assert seen["carry"] is False and seen["value_policy"] == "newest-nonnull-v1"
    seen.clear()
    with pytest.raises(_Stop):
        mod.cmd_stage(_stage_ns(mod, tmp_path))
    assert seen["carry"] is (ra.refit_mode(None) == "missing")
    assert seen["value_policy"] == mod.nrsa_dataset.DEFAULT_VALUE_POLICY
    # an unknown mode fails loudly before the pass, never runs the other mode
    seen.clear()
    ns = _stage_ns(mod, tmp_path)
    ns.refit = "sometimes"
    assert mod.cmd_stage(ns) == 2 and not seen


def test_a_full_refit_carries_nothing_into_the_region_digest():
    mod = _batch()
    assert mod.carried_for({"refit": "all"}, "55") is None
    # and the digest names the effective mode through the flags
    base = {k: None for k in mod._STAGE_MANY_FLAGS}
    base.update(enable_policy=[], approve_portfolio=[])
    assert mod.region_inputs({**base, "refit": "all"})["flags"]["refit"] == "all"
    assert mod.region_inputs(dict(base))["flags"]["refit"] == ra.refit_mode(None)


def test_the_packet_lists_the_held_metrics_under_every_mode():
    mod = _batch()
    result = {"owner_hold": ["chem_TURB", "bent_EPT_NTAX"],
              "held_by_owner": {"chem_TURB": {"decision": {"status": "local", "n_usable": 12,
                                                          "level": "l3", "region_code": "55",
                                                          "region_name": "ECBP"}}},
              "carried": {"phab_SINU": {}}, "carried_from": {"fromVersion": 8}}
    block = mod.refit_block(result, "all")
    assert block["mode"] == "all" and block["carry_forward"] == {"mode": "off", "fromVersion": None}
    assert block["held"] == ["bent_EPT_NTAX", "chem_TURB"]
    assert block["held_pool_supported"] == {"chem_TURB": {"status": "local", "level": "l3",
                                                         "regionCode": "55", "regionName": "ECBP",
                                                         "nUsable": 12}}
    assert mod.refit_block(result, "missing")["carry_forward"] == {"mode": "published_curves",
                                                                   "fromVersion": 8}
    assert mod.refit_block({}, "missing")["held"] == []


def test_the_manifest_records_the_carry_forward_beside_the_digest():
    mod = _batch()
    manifest = {"inputs": {"reference": {"method": "pressure-screen"}, "refit": {"mode": "all"}},
                "reviewerInputs": {"files": {}}}
    mod.record_stage_inputs(manifest, mode="all", evidence={"carried_from": {"fromVersion": 3}},
                            decisions_root=Path("/x"))
    assert manifest["inputs"]["reference"]["carryForward"] == {"mode": "off", "fromVersion": None}
    assert manifest["reviewerInputs"]["decisionsRoot"] == str(Path("/x"))
    legacy = {"inputs": {}}
    mod.record_stage_inputs(legacy, mode="missing", evidence={}, decisions_root=None)
    assert "reference" not in legacy["inputs"] and legacy["reviewerInputs"]["decisionsRoot"] is None
    assert mod.recorded_refit_mode(manifest) == "all" and mod.recorded_refit_mode({}) == "missing"


def test_both_entry_points_take_the_mode_and_thread_it():
    for script, call in (("run_region_batch.py", "ra.run_evidence("),
                         ("run_regional_analysis.py", "ra.run(")):
        text = (APP / "scripts" / script).read_text(encoding="utf-8")
        assert '"--refit"' in text and "choices=ra.REFIT_MODES" in text, script
        window = text[text.index(call):text.index(call) + 1600]
        assert "carry=" in window, f"{script}: {call} does not pass carry"
    for fn in (ra.run, ra.run_evidence):
        params = inspect.signature(fn).parameters
        assert "carry" in params and "value_policy" in params, fn.__name__
