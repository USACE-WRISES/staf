"""The standing-decision policy must reproduce the pilots' recorded decisions.

Offline replay against the published provenance of Northeastern Highlands v4
and Eastern Corn Belt Plains v3: every decision the policy makes must match the
recorded class and action (no mismatch), and the items it leaves open are
exactly the owner-only decisions. This is the acceptance test for any edit to
config/methodology/standing_decisions.yaml.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from streamcurves import decisions as dec

LIBRARY = Path(__file__).resolve().parents[2] / "library" / "assessments"
OPTIONAL = ["ref02-accept-best-available", "data03-thin-metric-finalized",
            "data06-insufficient-finalized", "curve07-thin-metric-finalized"]


def _version_dir(slug: str, version: int) -> Path:
    vdir = LIBRARY / slug / f"v{version}"
    if not (vdir / "provenance.json").exists():
        pytest.skip(f"{slug} v{version} not present in the library")
    return vdir


def test_policy_reproduces_northeastern_highlands_v4():
    rep = dec.replay(_version_dir("northeastern-highlands", 4), dec.load_policy())
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 14, "stricter_open": 2}
    stricter = {r["item_id"] for r in rep.rows if r["outcome"] == dec.STRICTER_OPEN}
    assert stricter == {"RED-01:chem_COND|chem_PH", "SELECT-01:water-soil-quality"}


def test_policy_reproduces_eastern_corn_belt_plains_v3():
    rep = dec.replay(_version_dir("eastern-corn-belt-plains", 3), dec.load_policy(),
                     enabled=OPTIONAL)
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 58, "alias_match": 5, "stricter_open": 4}
    stricter = {r["item_id"] for r in rep.rows if r["outcome"] == dec.STRICTER_OPEN}
    assert stricter == {"CURVE-07:phab_PCT_FAST", "CURVE-04:phab_PCT_FAST",
                        "RED-01:phab_PCT_SAFN|phab_XEMBED", "CURVE-06:phab_PCT_FAST"}
    # the pH removal was an owner-written decision on a record outside the queue
    assert [o["subject"] for o in rep.owner_only_records] == ["chem_PH"]
    assert rep.owner_only_records[0]["rationale_origin"] == "owner_written"


def test_policy_reproduces_interior_plateau_v1():
    """The first batch-built pilot (methodology 0.7): the policy reproduces all
    34 of its own decisions; the 7 it leaves open are exactly the recorded
    ai-drafted, owner-approved residual."""
    rep = dec.replay(_version_dir("interior-plateau", 1), dec.load_policy(),
                     enabled=OPTIONAL)
    assert rep.mismatches() == []
    assert rep.counts() == {"match": 34, "stricter_open": 7}
    stricter = {r["item_id"] for r in rep.rows if r["outcome"] == dec.STRICTER_OPEN}
    assert stricter == {"CURVE-04:pcthbwet2019ws", "CURVE-06:pcthbwet2019ws",
                        "CURVE-06:phab_PCT_FAST", "CURVE-07:pcthbwet2019ws",
                        "CURVE-07:phab_PCT_FAST",
                        "RED-01:phab_PCT_SAFN|phab_XEMBED",
                        "SELECT-01:bed-composition-bedform-dynamics"}
    assert rep.owner_only_records == []
    # substrate diameter and embeddedness correlate at |rho| ~ 0.96 there, above
    # the band the policy accepts on its own, so the acceptance stays the owner's
    assert rep.portfolio_approvals_not_derived == ["bed-composition-bedform-dynamics"]


def test_without_the_optional_entries_the_fallback_region_stays_open():
    rep = dec.replay(_version_dir("eastern-corn-belt-plains", 3), dec.load_policy())
    assert rep.mismatches() == []
    open_ids = {r["item_id"] for r in rep.rows if r["outcome"] == dec.STRICTER_OPEN}
    assert "REF-02:reference_screen" in open_ids
    assert {"DATA-03:phab_SINU", "DATA-06:phab_SINU", "CURVE-07:phab_SINU"} <= open_ids


def test_replay_lists_portfolio_approvals_the_policy_cannot_derive():
    rep = dec.replay(_version_dir("northeastern-highlands", 4), dec.load_policy())
    # The published queue predates the bundle-count fix, so two functions that
    # needed an approval had no queue item for the policy to act on.
    assert set(rep.portfolio_approvals_not_derived) == {"low-flow-baseflow-dynamics",
                                                        "water-soil-quality"}
