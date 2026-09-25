"""The packet's traceability section (5a) renders whatever shape the batch records.

Campaign Round 1: the batch records ``ledger`` as the ledger file's name, ``refit`` as a
block, ``evidence`` as the package reference and ``value_policy`` as a string; the section
must render every combination and never raise (a raise here failed every stage at its last
step in the Round 1 gate).
"""
from __future__ import annotations

import pytest

from streamcurves import review_packet as rp


def _lines(**packet):
    return rp._traceability_lines(packet)


def test_nothing_recorded_renders_nothing():
    assert _lines() == []
    assert _lines(refit=None, evidence=None, ledger=None, value_policy=None) == []


@pytest.mark.parametrize("ledger", ["rebuild_ledger.json", {"counts": {"refitted": 3, "carried": 2}},
                                    {"rows": [{"disposition": "refitted"}, {"disposition": "carried"},
                                              "not a row"]}])
def test_the_ledger_renders_as_a_file_or_as_counts(ledger):
    text = "\n".join(_lines(ledger=ledger))
    assert "5a." in text
    if isinstance(ledger, str):
        assert "rebuild_ledger.json" in text
    else:
        assert "refitted" in text and "carried" in text


@pytest.mark.parametrize("refit", ["all", {"mode": "all"},
                                   {"mode": "missing", "carry_forward": {"assessmentId": "x", "version": 5},
                                    "held": [{"metric": "chem_PH", "pool": 12}, "phab_SINU"]}])
def test_the_refit_block_renders_every_shape(refit):
    text = "\n".join(_lines(refit=refit))
    assert "Refit mode" in text
    if isinstance(refit, dict) and refit.get("held"):
        assert "chem_PH (pool 12)" in text and "phab_SINU" in text
        assert "version 5" in text


def test_evidence_and_policy_render():
    text = "\n".join(_lines(evidence={"packageId": "deep-dev-l3-55", "packageDigest": "sha256:abc",
                                      "reproducibility": "refittable"},
                            value_policy="newest-nonnull-v2"))
    assert "deep-dev-l3-55" in text and "refittable" in text and "newest-nonnull-v2" in text
    assert "deep-dev-l3-55" in "\n".join(_lines(evidence="deep-dev-l3-55"))


def test_the_whole_packet_renders_with_batch_shapes():
    packet = {
        "region": {"name": "Test", "code": "55"},
        "screening": {"method": "pressure-screen", "n_retained": 3, "n_candidates": 4,
                      "pool_disposition": "exploratory", "counts": {}},
        "reference_tier": "least_disturbed", "ref02_triggered": False, "run_seed": 1,
        "n_boot": 20, "inputs_digest": "sha256:0", "review_flags": [],
        "policy": {"version": "1.2", "sha256": "sha256:1", "enabled": [], "applied_ids": []},
        "queue_counts": {}, "open_items": [], "decisions_applied": [], "curves": [],
        "excluded": [], "coverage": {}, "portfolio": [], "source_reports": [],
        "promote_command": "promote", "prior_version_diff": None,
        "refit": {"mode": "all"}, "evidence": {"packageId": "p", "packageDigest": "sha256:2",
                                              "reproducibility": "reviewable"},
        "ledger": "rebuild_ledger.json", "value_policy": "newest-nonnull-v2",
    }
    text = rp.packet_markdown(packet)
    assert "## 5a. Refit, value policy, evidence and ledger" in text
    assert "rebuild_ledger.json" in text and "newest-nonnull-v2" in text
