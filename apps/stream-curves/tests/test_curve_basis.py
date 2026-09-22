"""The basis ladder: what a curve rests on, and what it may claim for it."""
from __future__ import annotations

import pytest

from streamcurves import confidence as conf
from streamcurves import curve_basis as cb
from streamcurves import methodology
from streamcurves import reference_pool as rp


def test_every_basis_has_a_label_a_statement_and_a_place_on_the_ladder():
    for basis in cb.ORDER:
        assert cb.label_for(basis), basis
        assert cb.statement_for(basis), basis
        assert cb.rank(basis) < len(cb.ORDER)
    assert len(set(cb.LABELS.values())) == len(cb.LABELS) == len(cb.ORDER) + 1


def test_an_owner_entered_curve_has_its_own_words_and_no_place_on_the_ladder():
    assert cb.OWNER not in cb.ORDER and cb.rank(cb.OWNER) == len(cb.ORDER)
    assert cb.label_for(cb.OWNER) == "Owner-entered"
    assert cb.statement_for(cb.OWNER) and cb.limit_for(cb.OWNER)
    assert cb.cap_for(cb.OWNER) is None
    assert cb.resolve(cb.OWNER, criteria_basis="fixed") == cb.OWNER


def test_the_ladder_runs_from_the_regions_own_stations_to_someone_elses_criterion():
    assert cb.ORDER == (cb.REGIONAL, cb.NATIONAL, cb.MODELED, cb.PUBLISHED)
    assert cb.rank(cb.REGIONAL) < cb.rank(cb.NATIONAL) < cb.rank(cb.MODELED) < cb.rank(cb.PUBLISHED)


def test_an_unknown_basis_sorts_last_rather_than_raising():
    assert cb.rank("something-new") == len(cb.ORDER)
    assert cb.label_for("something-new") == ""
    assert cb.label_for(None) == ""


def test_only_the_regions_own_station_pool_carries_no_cap_of_its_own():
    assert cb.cap_for(cb.REGIONAL) is None
    assert cb.cap_for(cb.NATIONAL) == 79
    assert cb.cap_for(cb.MODELED) == 59
    assert cb.cap_for(cb.PUBLISHED) == 59


def test_every_cap_this_module_names_exists_in_the_methodology_config():
    caps = methodology.threshold("confidence_rules.caps")
    for basis, reason in cb.CAP_REASONS.items():
        assert reason in caps, reason
        assert float(caps[reason]) == float(cb.CAPS[basis]), basis


# --------------------------------------------------------------------------- #
# bundles written before the ladder existed
# --------------------------------------------------------------------------- #
def test_a_bundle_written_before_the_ladder_resolves_rather_than_guesses():
    # the five EASI screening thresholds were published criteria all along
    assert cb.from_legacy("fixed") == cb.PUBLISHED
    # everything else was fitted to a pool of stations
    assert cb.from_legacy("reference") == cb.REGIONAL
    assert cb.from_legacy(None) == cb.REGIONAL


def test_an_explicit_basis_wins_over_the_legacy_derivation():
    assert cb.resolve(cb.MODELED, criteria_basis="reference") == cb.MODELED
    assert cb.resolve(None, criteria_basis="fixed") == cb.PUBLISHED
    assert cb.resolve("nonsense", criteria_basis="fixed") == cb.PUBLISHED


# --------------------------------------------------------------------------- #
# the pool decision carries it into the bundle
# --------------------------------------------------------------------------- #
def _decision(**kw):
    base = dict(metric="chem_PTL", status="borrowed_l1", level="l1", region_code="8",
                region_name="Eastern Temperate Forests", family="water_chemistry",
                n_pool=148, n_comparable=20, n_usable=14, n_local=0, n_huc12=12,
                disposition="exploratory", transfer_risk=rp.RISK_HIGH)
    base.update(kw)
    return rp.PoolDecision(**base)


def test_a_station_pool_is_the_regional_rung_by_default():
    d = _decision()
    assert d.basis == cb.REGIONAL
    assert d.to_dict()["basis_label"] == "Regional reference"


def test_the_bundle_block_states_the_basis_and_its_label():
    rec = rp.reference_support_record(_decision())
    assert rec["basis"] == cb.REGIONAL
    assert rec["basisLabel"] == "Regional reference"
    assert rec["basisStatement"]


def test_a_borrowed_pool_with_no_local_station_does_not_claim_one():
    """Interior Plateau and Eastern Corn Belt Plains borrow every curve. The note
    used to read "0 of them inside this ecoregion", which is arithmetic rather
    than English; what matters is that it never implies a local pool."""
    note = rp._transfer_note("l1", "Eastern Temperate Forests", "8", n_usable=14, n_local=0,
                             covariates=["bfiws", "runoffws"], lithology=True,
                             risk=rp.RISK_HIGH, supported="l3")
    assert "none of them inside this ecoregion" in note
    assert "0 of them" not in note
    local = rp._transfer_note("l2", "Interior Plateau", "8.3", n_usable=29, n_local=3,
                              covariates=["bfiws"], lithology=False,
                              risk=rp.RISK_MODERATE, supported="l3")
    assert "3 of them inside this ecoregion" in local


# --------------------------------------------------------------------------- #
# CONF-03
# --------------------------------------------------------------------------- #
def _evidence(**kw):
    ev = {"transfer_risk": "none", "sample_disposition": "adequate",
          "loo": {"evaluable": True}, "direction_confidence": "high", "shape_ok": True,
          "mapped": True, "units_present": True}
    ev.update(kw)
    return ev


@pytest.mark.parametrize("basis,reason", sorted(cb.CAP_REASONS.items()))
def test_a_basis_below_the_regions_own_stations_caps_its_confidence(basis, reason):
    got = conf.curve_confidence(_evidence(basis=basis))
    assert reason in got["caps_applied"]
    assert got["total"] <= float(cb.CAPS[basis])


def test_the_regions_own_station_pool_is_not_capped_by_basis():
    got = conf.curve_confidence(_evidence(basis=cb.REGIONAL))
    assert not any(r in got["caps_applied"] for r in cb.CAP_REASONS.values())


def test_the_basis_cap_applies_on_top_of_the_transfer_risk_cap_never_instead():
    """A modelled curve is capped at 59 by basis. A borrowed pool at high
    transfer risk is capped at 39. A curve that is somehow both takes the
    lower, because the caps are successive minima and not a lookup."""
    got = conf.curve_confidence(_evidence(basis=cb.MODELED, transfer_risk="high"))
    assert "modeled_reference" in got["caps_applied"]
    assert "borrowed_reference_high_risk" in got["caps_applied"]
    assert got["total"] <= 39


def test_a_curve_with_no_basis_recorded_scores_as_it_did_before_the_ladder():
    """Every published version up to NEH v7 carries no basis. Those runs must
    reproduce, so an absent basis must not introduce a cap."""
    before = conf.curve_confidence(_evidence())
    after = conf.curve_confidence(_evidence(basis=None))
    assert before["total"] == after["total"]
    assert not any(r in before["caps_applied"] for r in cb.CAP_REASONS.values())


# --------------------------------------------------------------------------- #
# CONF-03 has to reach the published artifact, not only the module
# --------------------------------------------------------------------------- #
def test_a_ladder_curve_publishes_with_a_confidence():
    """regional_agent.assemble scores confidence by walking metric_config, and a
    ladder metric is deliberately not in it. Without its own pass a modelled
    curve shipped with no confidence at all and the basis cap never reached a
    reader."""
    from streamcurves import pressure_evidence as pe
    ev = {"reference_support": {
              "bent_HPRIME": {"basis": cb.MODELED, "disposition": "exploratory",
                              "transfer_risk": "none"},
              "chem_PTL": {"basis": cb.PUBLISHED, "disposition": "exploratory",
                           "transfer_risk": "none"}},
          "tier": {"reference_tier": "least_disturbed"}}
    cfg = {"bent_HPRIME": {"units": "", "direction_confidence": "high"},
           "chem_PTL": {"units": "ug/L", "direction_confidence": "high"}}
    got = pe.ladder_confidence(ev, cfg)

    modeled = got["bent_HPRIME"]
    assert modeled["total"] is not None
    assert modeled["total"] <= cb.CAPS[cb.MODELED]
    assert "modeled_reference" in modeled["caps_applied"]
    # a drawn population has no leave-one-site-out, and says so
    assert "no_stability_evidence" in modeled["caps_applied"]

    # someone else's threshold has none of CONF-01's six components, so it
    # carries a label and no number, the convention the fixed criteria use
    published = got["chem_PTL"]
    assert published["total"] is None
    assert published["label"] == "Published benchmark"


def test_a_withheld_card_reads_as_sentences_a_reader_can_follow():
    """The statement rides onto DEEP's "Not assessed" card. It once joined the
    refusals with semicolons behind a full stop (".;") and led each with a rule
    code (REF-08) and a fitness code (PB-1) a reader cannot decode."""
    from streamcurves import pressure_evidence as pe
    ev = {"ladder_attempts": [
        {"metric": "m", "rung": "REF-08", "why": "12 donors, but borrowed donors never validated."},
        {"metric": "m", "rung": "REF-09", "why": "The fitted expectation was not validated"},
        {"metric": "m", "rung": "REF-10", "why": "PB-1 No published criterion matches it."},
    ]}
    text = pe._withheld_statement(ev, "m")
    assert ";" not in text and ".:" not in text and ".." not in text
    assert "REF-0" not in text and "REF-10" not in text and "PB-1" not in text
    for label in ("National reference:", "Modeled reference:", "Published benchmark:"):
        assert label in text
    # a refusal that lacked a full stop gains one, so the sentences do not run together
    assert "not validated. Published benchmark:" in text
