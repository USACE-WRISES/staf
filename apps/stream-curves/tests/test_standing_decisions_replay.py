"""The standing-decision policy must reproduce the published versions' recorded decisions.

Offline replay against the published provenance of every latest DEEP version and the
three pilot versions (Northeastern Highlands v4, Eastern Corn Belt Plains v3, Interior
Plateau v1): every decision the policy makes must match the recorded class and action
(no mismatch; an alias names an earlier class name for the same decision; an
owner-written record with the same action is an owner_action_match), an item the record
left open that the policy now decides is pinned here by name with the entry that decided
it (policy_decides_open, policy 1.3), and the items the policy leaves open are exactly
the owner-only decisions listed. This is the acceptance test for any edit to
config/methodology/standing_decisions.yaml.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from streamcurves import decisions as dec

LIBRARY = Path(__file__).resolve().parents[2] / "library" / "assessments"
#: the legacy entries (policy 1.3): enabled only to replay the pilots that carry them
OPTIONAL = ["ref02-accept-best-available", "data03-thin-metric-finalized",
            "data06-insufficient-finalized", "curve07-thin-metric-finalized"]
#: the published latest versions on 2026-09-26, each pinned below
LATEST = {"northeastern-highlands": 9, "interior-plateau": 6, "eastern-corn-belt-plains": 8,
          "central-great-plains": 1, "northern-lakes-and-forests": 1,
          "central-basin-and-range": 1, "southeastern-plains": 2}


def _version_dir(slug: str, version: int) -> Path:
    vdir = LIBRARY / slug / f"v{version}"
    if not (vdir / "provenance.json").exists():
        pytest.skip(f"{slug} v{version} not present in the library")
    return vdir


def _stricter(rep) -> set:
    return {r["item_id"] for r in rep.rows if r["outcome"] == dec.STRICTER_OPEN}


def _alias(rep) -> dict:
    return {r["item_id"]: (r["published_class"], r["policy_class"])
            for r in rep.rows if r["outcome"] == dec.ALIAS_MATCH}


def _owner_action(rep) -> dict:
    return {r["item_id"]: r["policy_class"] for r in rep.rows if r["outcome"] == dec.OWNER_ACTION_MATCH}


# --------------------------------------------------------------------------- #
# the pilots
# --------------------------------------------------------------------------- #
def test_policy_reproduces_northeastern_highlands_v4():
    rep = dec.replay(_version_dir("northeastern-highlands", 4), dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 14, "stricter_open": 2}
    # the keep-both decision was on a same-function pair (policy 1.3 keeps both only
    # across functions), and the portfolio set is the owner's
    assert _stricter(rep) == {"RED-01:chem_COND|chem_PH", "SELECT-01:water-soil-quality"}
    assert rep.policy_decides_open() == {}


def test_policy_reproduces_eastern_corn_belt_plains_v3():
    rep = dec.replay(_version_dir("eastern-corn-belt-plains", 3), dec.load_policy(),
                     enabled=OPTIONAL)
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 59, "alias_match": 6, "stricter_open": 2}
    # policy 1.3: the fallback fast-water curve and its missing interval are now the
    # policy's (the pilot recorded them under curve07-fallback-curve-preliminary and
    # curve06-no-interval-fallback-curve); the decision-flip influence and the
    # same-function keeper choice stay the owner's
    assert _stricter(rep) == {"CURVE-04:phab_PCT_FAST", "RED-01:phab_PCT_SAFN|phab_XEMBED"}
    aliases = _alias(rep)
    assert aliases["CURVE-07:phab_PCT_FAST"] == ("curve07-fallback-curve-preliminary",
                                                 "curve07-fallback-accepted")
    assert sum(1 for k in aliases if k.startswith("RED-06:")) == 5
    assert rep.policy_decides_open() == {}
    # the pH removal was an owner-written decision on a record outside the queue
    assert [o["subject"] for o in rep.owner_only_records] == ["chem_PH"]
    assert rep.owner_only_records[0]["rationale_origin"] == "owner_written"


def test_policy_reproduces_interior_plateau_v1():
    """The first batch-built pilot (methodology 0.7): the policy reproduces all
    38 of its recorded policy and fallback decisions; the 3 it leaves open are
    exactly the recorded ai-drafted, owner-approved residual."""
    rep = dec.replay(_version_dir("interior-plateau", 1), dec.load_policy(),
                     enabled=OPTIONAL)
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 36, "alias_match": 2, "stricter_open": 3}
    assert _stricter(rep) == {"CURVE-04:pcthbwet2019ws",
                              "RED-01:phab_PCT_SAFN|phab_XEMBED",
                              "SELECT-01:bed-composition-bedform-dynamics"}
    assert _alias(rep) == {
        "CURVE-07:pcthbwet2019ws": ("curve07-fallback-curve-preliminary", "curve07-fallback-accepted"),
        "CURVE-07:phab_PCT_FAST": ("curve07-fallback-curve-preliminary", "curve07-fallback-accepted")}
    assert rep.policy_decides_open() == {}
    assert rep.owner_only_records == []
    # substrate diameter and embeddedness correlate at |rho| ~ 0.96 there, above
    # the band the policy accepts on its own, so the acceptance stays the owner's
    assert rep.portfolio_approvals_not_derived == ["bed-composition-bedform-dynamics"]


def test_without_the_optional_entries_the_fallback_region_stays_open():
    rep = dec.replay(_version_dir("eastern-corn-belt-plains", 3), dec.load_policy())
    assert rep.mismatches() == []
    open_ids = _stricter(rep)
    assert "REF-02:reference_screen" in open_ids
    assert {"DATA-03:phab_SINU", "DATA-06:phab_SINU", "CURVE-07:phab_SINU"} <= open_ids


def test_replay_lists_portfolio_approvals_the_policy_cannot_derive():
    rep = dec.replay(_version_dir("northeastern-highlands", 4), dec.load_policy())
    # The published queue predates the bundle-count fix, so two functions that
    # needed an approval had no queue item for the policy to act on.
    assert set(rep.portfolio_approvals_not_derived) == {"low-flow-baseflow-dynamics",
                                                        "water-soil-quality"}


# --------------------------------------------------------------------------- #
# the published latest versions under policy 1.3
# --------------------------------------------------------------------------- #
def test_every_latest_version_replays_without_a_mismatch():
    """Whatever the library holds as latest: no policy decision contradicts a
    recorded class or action."""
    policy = dec.load_policy()
    seen = 0
    for man in sorted(LIBRARY.glob("*/manifest.json")):
        doc = json.loads(man.read_text(encoding="utf-8"))
        vdir = man.parent / f"v{int(doc.get('latestVersion') or 0)}"
        if not (vdir / "provenance.json").exists() or not (vdir / "assessment.deep.json").exists():
            continue
        prov = json.loads((vdir / "provenance.json").read_text(encoding="utf-8"))
        if not (prov.get("reviewQueue") or {}).get("items"):
            continue
        rep = dec.replay(vdir, policy)
        assert rep.mismatches() == [], f"{vdir}: {rep.mismatches()}"
        seen += 1
    assert seen >= len(LATEST)


def test_central_basin_and_range_v1_thin_inversions_are_the_policys():
    """Published Preliminary with seven CURVE-12 items open, every one on a group
    below the DATA-04 floor: policy 1.3 decides them (curve12-inverted-thin). The
    owner's D9 decisions on the inverted fallback curve (CURVE-07, CURVE-12 on 66
    and 60 stations, CURVE-06) stay the owner's, as does the same-function pair."""
    rep = dec.replay(_version_dir("central-basin-and-range", LATEST["central-basin-and-range"]),
                     dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 29, "policy_decides_open": 7, "stricter_open": 4}
    assert rep.policy_decides_open() == {
        f"CURVE-12:{m}": "curve12-inverted-thin"
        for m in ("bent_HPRIME", "chem_PH", "pctwet2019ws", "phab_LSUB_DMM", "phab_PCT_SAFN",
                  "phab_RP100_cm", "phab_XBKA")}
    assert _stricter(rep) == {"CURVE-07:fish_NAT_TOTLNTAX", "CURVE-12:fish_NAT_TOTLNTAX",
                              "CURVE-06:fish_NAT_TOTLNTAX", "RED-01:phab_PCT_SAFN|phab_XEMBED"}


def test_central_great_plains_v1_inverted_fallback_stays_the_owners():
    rep = dec.replay(_version_dir("central-great-plains", LATEST["central-great-plains"]),
                     dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 1, "stricter_open": 4}
    # the wood-volume fallback curve reads inverted on 40 and 122 stations: the
    # owner's D9 acceptance is not automated; the precision floor has no entry
    assert _stricter(rep) == {"CURVE-07:phab_LWDeqVolM100", "CURVE-12:phab_LWDeqVolM100",
                              "CURVE-06:phab_LWDeqVolM100", "CURVE-09:chem_PH"}
    assert rep.policy_decides_open() == {}


def test_eastern_corn_belt_plains_v8_records_a_1_2_stratifier_alias():
    rep = dec.replay(_version_dir("eastern-corn-belt-plains", LATEST["eastern-corn-belt-plains"]),
                     dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 2, "alias_match": 1, "stricter_open": 4}
    # policy 1.2 read the labelled class sizes' digits as counts (24/28/20 read as
    # below the floor); the same action under the complement entry
    assert _alias(rep) == {"STRAT-09:DrainageAreaClass": ("strat09-defer-floors",
                                                          "strat09-advisory-not-applied")}
    assert _stricter(rep) == {"CURVE-07:phab_LWDeqVolM100", "DATA-03:phab_LWDeqVolM100",
                              "SELECT-01:bed-composition-bedform-dynamics",
                              "SELECT-01:low-flow-baseflow-dynamics"}
    assert rep.policy_decides_open() == {}


def test_interior_plateau_v6_well_supported_inversion_stays_open():
    rep = dec.replay(_version_dir("interior-plateau", LATEST["interior-plateau"]), dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 1, "alias_match": 1, "stricter_open": 4}
    assert _alias(rep) == {"STRAT-09:DrainageAreaClass": ("strat09-defer-floors",
                                                          "strat09-advisory-not-applied")}
    # 91 reference and 267 pressured stations: both groups at the floor or above
    assert "CURVE-12:phab_LWDeqVolM100" in _stricter(rep)
    assert rep.policy_decides_open() == {}


def test_northeastern_highlands_v9_under_policy_1_3():
    rep = dec.replay(_version_dir("northeastern-highlands", LATEST["northeastern-highlands"]),
                     dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 2, "alias_match": 1, "stricter_open": 3}
    assert _alias(rep) == {"STRAT-09:ChannelSlopeClass": ("strat09-defer-floors",
                                                          "strat09-advisory-not-applied")}
    assert _stricter(rep) == {"SELECT-01:bed-composition-bedform-dynamics",
                              "SELECT-01:low-flow-baseflow-dynamics",
                              "SELECT-01:water-soil-quality"}
    assert rep.policy_decides_open() == {}


def test_northern_lakes_and_forests_v1_owner_actions_and_one_thin_inversion():
    """Published Preliminary with seven CURVE-12 and two RED-01 items open. Policy
    1.3 decides the one thin inversion (51 reference, 16 pressured) and reproduces
    the owner's D9 actions on the fast-water fallback curve; the six inversions on
    groups at the floor or above and the same-function pairs stay the owner's."""
    rep = dec.replay(_version_dir("northern-lakes-and-forests", LATEST["northern-lakes-and-forests"]),
                     dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 3, "owner_action_match": 2, "policy_decides_open": 1,
                            "stricter_open": 8}
    assert rep.policy_decides_open() == {"CURVE-12:phab_XBKA": "curve12-inverted-thin"}
    assert _owner_action(rep) == {"CURVE-07:phab_PCT_FAST": "curve07-fallback-accepted",
                                  "CURVE-06:phab_PCT_FAST": "curve06-no-interval-flagged"}
    assert _stricter(rep) == {
        f"CURVE-12:{m}" for m in ("bent_EPT_NTAX", "bfiws", "fish_NAT_TOTLNTAX", "pctwet2019ws",
                                  "phab_LSUB_DMM", "phab_RP100_cm")
    } | {"RED-01:chem_COND|chem_PH", "RED-01:phab_PCT_SAFN|phab_XEMBED"}


def test_southeastern_plains_v2_under_policy_1_3():
    rep = dec.replay(_version_dir("southeastern-plains", LATEST["southeastern-plains"]), dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 2, "alias_match": 1}
    assert _alias(rep) == {"STRAT-09:DrainageAreaClass": ("strat09-defer-floors",
                                                          "strat09-advisory-not-applied")}
