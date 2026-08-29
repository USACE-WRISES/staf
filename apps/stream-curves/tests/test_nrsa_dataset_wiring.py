"""Threading the NRSA dataset choice through the headless pipeline.

The load-bearing test here is
``test_the_digest_distinguishes_two_datasets``: without it two runs over the same
ecoregion on different data would share an ``inputsDigest``, which is exactly what
that digest promises cannot happen (``provenance.py`` module docstring).

The second is that the legacy default is untouched, because three published
assessments depend on it reproducing.
"""
from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd
from streamcurves import provenance as pv
from streamcurves import regional_agent as ra

MULTI = pytest.mark.skipif(
    not nd.multi_cycle_available(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)",
)

INTERIOR_PLATEAU = "71"


def _result(**over) -> dict:
    base = {
        "region": {"l3_code": INTERIOR_PLATEAU, "name": "Interior Plateau"},
        "screening_method": "functional",
        "screening_counts": {"n_screened": 71, "n_retained": 33},
        "source_reports": [None],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #

def test_the_digest_distinguishes_two_datasets():
    legacy = pv.build_run_manifest(_result(), argv=[])
    pooled = pv.build_run_manifest(
        _result(nrsa_dataset=nd.MULTI_CYCLE_DATASET_ID), argv=[])
    assert legacy["inputsDigest"] != pooled["inputsDigest"]


def test_the_legacy_digest_ignores_the_dataset_block_entirely():
    """Anything published before the dataset existed must still reproduce, so the
    default must not contribute to the digest at all."""
    plain = pv.build_run_manifest(_result(), argv=[])
    named = pv.build_run_manifest(_result(nrsa_dataset=nd.LEGACY_DATASET_ID), argv=[])
    assert plain["inputsDigest"] == named["inputsDigest"]
    # and the two legacy files are still fingerprinted exactly as before
    for key in ("nrsa_values", "nrsa_sites"):
        assert plain["inputs"][key]["sha256"]


def test_the_digest_distinguishes_bootstrap_depths():
    """CURVE-06/RED-06/STRAT-06 evidence depends on the depth, so two runs at
    different depths must not share an inputsDigest."""
    a = pv.build_run_manifest(_result(diagnostics_n_boot=1000), argv=[])
    b = pv.build_run_manifest(_result(diagnostics_n_boot=200), argv=[])
    assert a["inputsDigest"] != b["inputsDigest"]


def test_the_canonical_bootstrap_depth_adds_no_digest_key():
    """Every published version ran at 1000 before the depth entered the digest,
    so the canonical depth (and an absent value) must not contribute."""
    assert pv.DIGEST_DEFAULT_N_BOOT == 1000
    plain = pv.build_run_manifest(_result(), argv=[])
    canonical = pv.build_run_manifest(_result(diagnostics_n_boot=1000), argv=[])
    assert plain["inputsDigest"] == canonical["inputsDigest"]


def test_both_entry_points_default_to_the_canonical_depth():
    import re
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("run_regional_analysis.py", "run_region_batch.py"):
        text = (scripts / name).read_text(encoding="utf-8")
        for m in re.finditer(r'"--n-boot", type=int, default=(\d+)', text):
            assert int(m.group(1)) == pv.DIGEST_DEFAULT_N_BOOT, \
                f"{name} defaults --n-boot to {m.group(1)}, not the canonical depth"


def test_the_manifest_records_which_dataset_was_read():
    legacy = pv.build_run_manifest(_result(), argv=[])
    assert legacy["inputs"]["nrsa_dataset"]["datasetId"] == nd.LEGACY_DATASET_ID

    pooled = pv.build_run_manifest(
        _result(nrsa_dataset=nd.MULTI_CYCLE_DATASET_ID,
                nrsa_cycles=["1819", "2324"],
                nrsa_policy=nd.POLICY_MOST_RECENT), argv=[])
    record = pooled["inputs"]["nrsa_dataset"]
    assert record["datasetId"] == nd.MULTI_CYCLE_DATASET_ID
    assert record["cycles"] == ["1819", "2324"]
    assert record["policy"] == nd.POLICY_MOST_RECENT


@MULTI
def test_the_pooled_manifest_pins_the_archive_by_digest():
    """One digest over data/nrsa/manifest.json covers every file in the archive."""
    pooled = pv.build_run_manifest(
        _result(nrsa_dataset=nd.MULTI_CYCLE_DATASET_ID), argv=[])
    record = pooled["inputs"]["nrsa_dataset"]
    assert str(record["manifestDigest"]).startswith("sha256:")
    assert record["fileCount"] > 15
    assert record["totalBytes"] > 1_000_000


@MULTI
def test_changing_the_cycles_changes_the_digest():
    a = pv.build_run_manifest(
        _result(nrsa_dataset=nd.MULTI_CYCLE_DATASET_ID, nrsa_cycles=["1819"]), argv=[])
    b = pv.build_run_manifest(
        _result(nrsa_dataset=nd.MULTI_CYCLE_DATASET_ID,
                nrsa_cycles=["1819", "2324"]), argv=[])
    assert a["inputsDigest"] != b["inputsDigest"]


# --------------------------------------------------------------------------- #
# candidate selection
# --------------------------------------------------------------------------- #

def test_select_candidates_still_returns_a_frame():
    """The old contract: callers and tests outside the agent rely on it."""
    panel = ra.select_candidates(INTERIOR_PLATEAU)
    assert isinstance(panel, pd.DataFrame)
    assert len(panel) == 25
    assert {"site_id", "lat", "lon"} <= set(panel.columns)


def test_select_candidates_detailed_adds_a_ledger():
    panel, ledger = ra.select_candidates_detailed(INTERIOR_PLATEAU)
    assert len(panel) == 25
    assert ledger.empty
    assert list(ledger.columns) == ["station_key", "cycle", "reason", "missing"]


@MULTI
def test_a_pooled_selection_pools_the_cycles():
    legacy = ra.select_candidates(INTERIOR_PLATEAU)
    pooled = ra.select_candidates(INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID)
    assert len(pooled) > 2 * len(legacy)
    assert set(pooled["source_cycle"]) <= set(nd.CYCLES_NEWEST_FIRST)


@MULTI
def test_restricting_the_cycles_reaches_the_selection():
    only_new = ra.select_candidates(
        INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID, cycles=["2324"])
    assert set(only_new["source_cycle"]) == {"2324"}


# --------------------------------------------------------------------------- #
# the values source
# --------------------------------------------------------------------------- #

def test_the_legacy_values_source_is_the_bundled_parquet():
    panel = ra.select_candidates(INTERIOR_PLATEAU)
    values = ra._dataset_values(nd.LEGACY_DATASET_ID, panel)
    frame = values(None)
    assert len(frame) > 1900          # every site, not just the region
    assert "site_id" in frame.columns


@MULTI
def test_the_pooled_values_source_follows_the_panel():
    panel = ra.select_candidates(INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID)
    values = ra._dataset_values(nd.MULTI_CYCLE_DATASET_ID, panel)
    frame = values(None)
    assert len(frame) == len(panel)
    assert list(frame["site_id"]) == list(panel["site_id"])
    # asking for a subset returns only those columns, and is cached
    subset = values(["phab_XEMBED"])
    assert list(subset.columns) == ["site_id", "phab_XEMBED"]
    assert values(["phab_XEMBED"]) is subset


# --------------------------------------------------------------------------- #
# the panel summary that reaches the manifest
# --------------------------------------------------------------------------- #

@MULTI
def test_the_panel_summary_reports_the_cycle_and_protocol_mix():
    panel, ledger = ra.select_candidates_detailed(
        INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID)
    summary = ra._panel_summary(panel, ledger)
    assert summary["nCandidates"] == len(panel)
    assert sum(summary["byCycle"].values()) == len(panel)
    # the protocol mix is the thing that explains a wadeable-only metric thinning out
    assert sum(summary["byProtocol"].values()) == len(panel)
    assert set(summary["byProtocol"]) <= {"WADEABLE", "BOATABLE", "unknown"}


def test_the_legacy_panel_summary_stays_minimal():
    panel, ledger = ra.select_candidates_detailed(INTERIOR_PLATEAU)
    summary = ra._panel_summary(panel, ledger)
    assert summary == {"nCandidates": 25, "nExcluded": 0}


# --------------------------------------------------------------------------- #
# end to end, offline
# --------------------------------------------------------------------------- #

@MULTI
def test_a_pooled_evidence_pass_runs_and_records_its_dataset():
    evidence = ra.run_evidence(
        INTERIOR_PLATEAU, "Interior Plateau", do_screen=False, use_streamcat=False,
        diagnostics_enabled=False, nrsa_dataset_id=nd.MULTI_CYCLE_DATASET_ID)
    assert evidence["nrsa_dataset"] == nd.MULTI_CYCLE_DATASET_ID
    assert evidence["nrsa_policy"] == nd.POLICY_MOST_RECENT
    assert evidence["n_candidates"] > 50
    assert evidence["nrsa_panel_summary"]["byCycle"]
    # all three national stratifiers still materialize on a pooled panel
    columns = set(evidence["data"].columns)
    assert {"DrainageAreaClass", "ChannelSlopeClass", "ElevationClass"} <= columns


def test_a_legacy_evidence_pass_is_unchanged():
    evidence = ra.run_evidence(
        INTERIOR_PLATEAU, "Interior Plateau", do_screen=False, use_streamcat=False,
        diagnostics_enabled=False)
    assert evidence["nrsa_dataset"] == nd.LEGACY_DATASET_ID
    assert evidence["n_candidates"] == 25
    assert evidence["nrsa_policy"] is None


# --------------------------------------------------------------------------- #
# the CLI flag has to actually reach the agent
# --------------------------------------------------------------------------- #

def _source(name: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "scripts" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("script,call", [
    ("run_region_batch.py", "ra.run_evidence("),
    ("run_regional_analysis.py", "ra.run("),
])
def test_the_dataset_flag_is_passed_through_not_just_parsed(script, call):
    """Both scripts once declared --nrsa-dataset and then never passed it, so a
    pooled stage silently ran on the legacy data and reported it as such. Only the
    demonstration run caught it, because the unit tests called the agent directly."""
    text = _source(script)
    assert "--nrsa-dataset" in text, f"{script} does not declare the flag"
    start = text.index(call)
    # the call spans a few lines; the argument must appear inside it
    window = text[start:start + 900]
    assert "nrsa_dataset_id=" in window, f"{script}: {call} does not pass nrsa_dataset_id"
    assert "nrsa_cycles=" in window, f"{script}: {call} does not pass nrsa_cycles"


@pytest.mark.parametrize("script", ["run_region_batch.py", "run_regional_analysis.py"])
def test_new_builds_default_to_the_pooled_archive(script):
    """Every --nrsa-dataset declaration defaults through default_build_dataset_id,
    never through DEFAULT_DATASET_ID (which means "absent" and must stay legacy)."""
    text = _source(script)
    declarations = text.count('"--nrsa-dataset"')
    assert declarations >= 1
    assert text.count("default=nrsa_dataset.default_build_dataset_id()") == declarations
    assert "default=nrsa_dataset.DEFAULT_DATASET_ID" not in text


def test_stage_many_hands_the_dataset_flags_to_each_stage():
    """stage-many builds cmd_stage's namespace by hand and once omitted the two
    dataset attributes; cmd_stage's getattr fallback then silently ran the legacy
    data. The namespace must carry both, and the fallback must stay gone so a
    future hand-built namespace fails loudly instead."""
    text = _source("run_region_batch.py")
    start = text.index("argparse.Namespace(")
    window = text[start:start + 900]
    assert "nrsa_dataset=" in window, "stage-many namespace drops nrsa_dataset"
    assert "nrsa_cycles=" in window, "stage-many namespace drops nrsa_cycles"
    assert 'getattr(a, "nrsa_dataset"' not in text, "the silent legacy fallback is back"
    assert 'getattr(a, "nrsa_cycles"' not in text, "the silent cycles fallback is back"
