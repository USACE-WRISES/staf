"""Criteria selection preserves a working legacy catalog and distinct digests."""
import pytest

from easi import config, screening_methods
from easi.national import method_version


@pytest.mark.parametrize("criteria_set", ["regional", "legacy"], indirect=True)
def test_each_criteria_catalog_validates(criteria_set):
    assert config.criteria_set() == criteria_set
    assert screening_methods.validate_catalog() == []
    assert len(config.screening_methods()["methods"]) == 20
    assert config.screening_methods()["ratingIndex"] == config.RATING_INDEX


def test_regional_is_default(monkeypatch):
    monkeypatch.delenv("EASI_CRITERIA_SET", raising=False)
    assert config.criteria_set() == "regional"
    assert config.screening_methods_filename() == "screening-methods.json"


def test_criteria_identity_is_read_at_call_time(monkeypatch):
    config.reset_caches()
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    regional = method_version()
    monkeypatch.setenv("EASI_CRITERIA_SET", "legacy")
    legacy = method_version()
    assert legacy != regional
    assert config.screening_methods_filename() == "screening-methods-legacy.json"
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    assert method_version() == regional
    config.reset_caches()


def test_unknown_criteria_set_fails_clearly(monkeypatch):
    monkeypatch.setenv("EASI_CRITERIA_SET", "reginoal")
    with pytest.raises(ValueError, match="EASI_CRITERIA_SET"):
        config.screening_methods()
    with pytest.raises(ValueError, match="EASI_CRITERIA_SET"):
        method_version()


def test_generated_rating_anchors_match_the_engine():
    metrics = config.easi_metrics()
    assert metrics["ratingIndexDefault"] == config.RATING_INDEX
    assert all(m["indexMidpoints"] == config.RATING_INDEX for m in metrics["metrics"])
