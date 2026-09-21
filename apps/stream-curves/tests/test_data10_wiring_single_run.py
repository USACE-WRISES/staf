"""Rule DATA-10 (the wadeable reference frame) reaches a single run.

Before 2026-09-19 only ``run_region_batch.py stage`` applied the frame:
``regional_agent.run`` had no frame parameters and ``run_regional_analysis.py``
had no ``--reference-frame`` flag, so the two supported entry points built their
curves from two different candidate populations and nothing said so.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from streamcurves import nrsa_dataset as nd
from streamcurves import regional_agent as ra

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _source(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_governed_frame_reads_the_methodology_values():
    max_order, protocols = nd.governed_frame("wadeable")
    assert max_order == 5
    assert protocols == ("WADEABLE",)


def test_the_all_frame_drops_both_values():
    assert nd.governed_frame("all") == (None, None)
    assert nd.governed_frame("ALL") == (None, None)


@pytest.mark.parametrize("name", [
    "nrsa_max_stream_order", "nrsa_protocols", "nrsa_keep_sites",
    "exclude_sites", "predictor_source", "engine_config"])
def test_run_accepts_every_frame_and_site_argument_run_evidence_takes(name):
    """``run`` is ``run_evidence`` then ``assemble``: an argument the first half
    takes and ``run`` does not forward is an argument a single run cannot use."""
    assert name in inspect.signature(ra.run_evidence).parameters
    assert name in inspect.signature(ra.run).parameters


def test_the_single_run_cli_declares_and_passes_the_frame():
    text = _source("run_regional_analysis.py")
    for flag in ("--reference-frame", "--include-site", "--exclude-site"):
        assert f'"{flag}"' in text, f"run_regional_analysis.py does not declare {flag}"
    assert "nrsa_dataset.governed_frame(args.reference_frame)" in text
    start = text.index("ra.run(args.l3")
    window = text[start:start + 1400]
    for arg in ("nrsa_max_stream_order=", "nrsa_protocols=", "nrsa_keep_sites=",
                "exclude_sites="):
        assert arg in window, f"ra.run(...) does not pass {arg}"


def test_both_clis_read_the_same_governed_frame():
    batch = _source("run_region_batch.py")
    assert "nrsa_dataset.governed_frame(choice)" in batch
    assert 'threshold("reference_panel.max_stream_order")' not in batch, (
        "the batch runner grew its own copy of the governed frame again")
