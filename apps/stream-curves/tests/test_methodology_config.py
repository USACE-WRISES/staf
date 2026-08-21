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
    assert families == {"DATA", "RED", "STRAT", "CURVE", "REF", "CONF", "SELECT"}


def test_every_rule_declares_both_statuses():
    for rule_id in methodology.rule_ids():
        rule = methodology.rule(rule_id)
        assert rule.get("threshold_status") in (
            "provisional", "calibrated", "approved"), rule_id
        assert rule.get("implementation_status") in (
            "implemented", "partial", "not_yet_implemented"), rule_id


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
