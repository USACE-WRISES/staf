"""The governing methodology, as config the code actually reads.

Both machine-readable files used to live only under notes/, which is neither
tracked by git nor shipped in the app payload, while the agent retyped their
thresholds as module constants. Hashing an untracked file into a run record
fingerprints something nobody can retrieve, and a constant that merely comments
"see the YAML" drifts from it silently.
"""

from __future__ import annotations

import pytest

from streamcurves import methodology
from streamcurves import regional_agent as ra


def test_the_config_files_ship_with_the_app():
    """config/ is in the Posit publish payload; notes/ is not."""
    assert methodology.CONFIG_PATH.exists(), methodology.CONFIG_PATH
    assert methodology.RULE_CATALOG_PATH.exists(), methodology.RULE_CATALOG_PATH
    assert methodology.CONFIG_PATH.is_relative_to(
        methodology.CONFIG_PATH.parent.parent.parent / "config")


def test_the_two_files_agree_on_a_version():
    version = methodology.methodology_version()
    assert version
    catalog_version = methodology.load_rule_catalog()["meta"]["methodology_version"]
    assert catalog_version == version


def test_agent_thresholds_resolve_from_config_not_from_constants():
    config = methodology.load_config()["data_rules"]
    assert ra.MIN_N_AUTO == config["min_n_unstratified"]
    assert ra.MIN_N_EXPLORATORY == config["exploratory_n_unstratified"]
    assert ra.MIN_N_FLOOR == config["insufficient_n_unstratified"]


def test_unknown_rules_and_thresholds_raise():
    """A typo must not silently produce a record for a rule that does not exist."""
    with pytest.raises(KeyError):
        methodology.rule("STRAT-99")
    with pytest.raises(KeyError):
        methodology.threshold("data_rules.no_such_threshold")


def test_the_catalog_covers_every_rule_family():
    families = {rule_id.split("-")[0] for rule_id in methodology.rule_ids()}
    assert families == {"DATA", "RED", "STRAT", "CURVE", "REF", "CONF", "SELECT",
                        "ACC", "COV"}


def test_every_rule_declares_both_statuses():
    for rule_id in methodology.rule_ids():
        rule = methodology.rule(rule_id)
        assert rule.get("threshold_status") in (
            "provisional", "calibrated", "approved"), rule_id
        assert rule.get("implementation_status") in (
            "implemented", "partial", "not_yet_implemented", "superseded"), rule_id
        if rule.get("implementation_status") == "superseded":
            assert rule.get("superseded_by") in methodology.rule_ids(), rule_id


def test_strat00_is_the_approved_implemented_rule_the_agent_relies_on():
    rule = methodology.rule("STRAT-00")
    assert rule["threshold_status"] == "approved"
    assert rule["implementation_status"] == "implemented"


def test_data_derived_binning_stays_out_of_bounds():
    """STRAT-08: the national registry's breakpoints are declared constants, and
    the class count must stay within the configured bin limit."""
    max_bins = methodology.threshold("stratifier_rules.max_data_derived_bins")
    registry = ra.stratifiers.load_national_registry()
    for key, cfg in registry["candidates"].items():
        assert len(cfg["levels"]) <= max_bins, key
        assert len(cfg["group_definitions"]) <= max_bins, key


def test_fingerprints_change_with_content():
    fingerprints = methodology.config_fingerprints()
    assert fingerprints["config_sha256"].startswith("sha256:")
    assert fingerprints["rule_catalog_sha256"].startswith("sha256:")
    assert fingerprints["config_sha256"] != fingerprints["rule_catalog_sha256"]


# --------------------------------------------------------------------------- #
# Mirror verification (Q-07): the config blocks that MIRROR engine constants
# must match them, and drift must be loud.
# --------------------------------------------------------------------------- #
def test_the_shipped_config_carries_no_mirror_drift():
    assert methodology.mirror_drift() == []


def test_verify_mirrors_is_quiet_on_the_clean_config():
    assert methodology.verify_mirrors(strict=True) == []


def test_an_edited_preset_mirror_is_reported_and_raises(monkeypatch):
    clean = methodology.load_config()
    tweaked = {**clean, "easi_presets": {**clean["easi_presets"],
                                         "functional": {"field": "eci", "cmp": ">",
                                                        "value": 0.5}}}
    monkeypatch.setattr(methodology, "load_config", lambda: tweaked)
    drift = methodology.mirror_drift()
    assert any("functional" in d for d in drift)
    with pytest.raises(RuntimeError):
        methodology.verify_mirrors(strict=True)


def test_an_edited_curve_band_mirror_is_reported(monkeypatch):
    clean = methodology.load_config()
    tweaked = {**clean, "curve_rules": {**clean["curve_rules"], "index_low_band": 0.25}}
    monkeypatch.setattr(methodology, "load_config", lambda: tweaked)
    assert any("curve engine" in d for d in methodology.mirror_drift())


# --------------------------------------------------------------------------- #
# Config hygiene (campaign Round 1, 2026-09-25): the keys no code read are gone,
# carry_forward is read, and the two describing blocks are mirror-checked.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [
    "reference_pool.levels", "reference_pool.ladder_rule", "reference_pool.review_risks",
    "reference_hierarchy.sources", "reference_hierarchy.option_rank",
])
def test_the_keys_no_code_read_stay_deleted(path):
    assert methodology.threshold(path, "__missing__") == "__missing__", path


def test_the_keys_the_code_reads_are_still_there():
    assert methodology.threshold("reference_pool.envelope_quantiles") == [0.025, 0.975]
    assert methodology.threshold("reference_pool.min_self_coverage") == 0.80
    assert methodology.threshold("reference_hierarchy.national_options")
    assert methodology.threshold("reference_hierarchy.regional_screen")["id"]


def test_carry_forward_default_is_read_from_the_config(monkeypatch):
    assert methodology.carry_forward_default() == "published_curves"
    assert methodology.carry_forward_default() == methodology.threshold(
        "reference_hierarchy.carry_forward")
    clean = methodology.load_config()
    tweaked = {**clean, "reference_hierarchy": {**clean["reference_hierarchy"],
                                                "carry_forward": "sometimes"}}
    monkeypatch.setattr(methodology, "load_config", lambda: tweaked)
    with pytest.raises(ValueError, match="carry_forward"):
        methodology.carry_forward_default()


def test_the_standing_decisions_index_is_mirror_checked(monkeypatch):
    from streamcurves import decisions as dec
    from streamcurves import rules_view as rv
    policy = dec.load_policy()
    index = methodology.load_config()["standing_decisions"]
    assert sorted(index["default_enabled"]) == sorted(rv.default_policy_ids(policy))
    assert sorted(index["enable_per_run_only"]) == sorted(rv.optional_policy_ids(policy))
    clean = methodology.load_config()
    tweaked = {**clean, "standing_decisions": {
        **clean["standing_decisions"],
        "default_enabled": list(clean["standing_decisions"]["default_enabled"]) + ["no-such"]}}
    monkeypatch.setattr(methodology, "load_config", lambda: tweaked)
    assert any(d.startswith("standing_decisions.default_enabled")
               for d in methodology.mirror_drift())


def test_the_lifecycle_block_mirrors_the_library(monkeypatch):
    from streamcurves import library as lib
    life = methodology.load_config()["lifecycle"]
    assert life["version_statuses"] == list(lib.VERSION_STATUSES)
    assert "draft" in life["version_statuses"]
    assert life["default_status"] == lib.DEFAULT_STATUS
    assert life["validation_states"] == list(lib.VALIDATION_STATES)
    clean = methodology.load_config()
    tweaked = {**clean, "lifecycle": {**clean["lifecycle"], "version_statuses": [
        s for s in clean["lifecycle"]["version_statuses"] if s != "draft"]}}
    monkeypatch.setattr(methodology, "load_config", lambda: tweaked)
    assert any(d.startswith("lifecycle.version_statuses") for d in methodology.mirror_drift())


def test_the_calibration_note_records_the_hygiene_pass():
    note = methodology.load_config()["meta"]["calibration_note"]
    assert "2026-09-25 hygiene" in note
    assert methodology.methodology_version() == "0.14-provisional"


# --------------------------------------------------------------------------- #
# Missingness dispositions (DATA-01/02/03 wired as acting thresholds)
# --------------------------------------------------------------------------- #
def test_missingness_dispositions_follow_the_config_bands():
    auto_cut = methodology.threshold("data_rules.max_missingness_auto")
    review_cut = methodology.threshold("data_rules.max_missingness_review")
    assert methodology.missingness_disposition(0.0) == "auto"
    assert methodology.missingness_disposition(auto_cut) == "auto"
    assert methodology.missingness_disposition(auto_cut + 0.01) == "caution"
    assert methodology.missingness_disposition(review_cut) == "caution"
    assert methodology.missingness_disposition(review_cut + 0.01) == "review"
    assert methodology.missingness_disposition(1.0) == "review"
    assert methodology.missingness_disposition(None) == "unknown"
    assert methodology.missingness_disposition(float("nan")) == "unknown"
