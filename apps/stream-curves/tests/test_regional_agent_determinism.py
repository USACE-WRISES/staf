"""Two runs of the same region must produce the same assessment.

That is the whole reproducibility contract, and it is what makes running the
agent across many ecoregions defensible: a reviewer can re-derive any published
assessment from its recorded inputs. Offline (no screen, no StreamCat) so the
check is about the pipeline, not about a live service being up.
"""

from __future__ import annotations

import pytest

from streamcurves import library
from streamcurves import provenance as pv
from streamcurves import regional_agent as ra

L3 = "55"  # Eastern Corn Belt Plains: the smallest pilot, so this stays quick


@pytest.fixture(scope="module")
def two_runs() -> tuple[dict, dict]:
    # diagnostics_n_boot=20 keeps two full runs fast; determinism must hold at
    # any resample count because every seed derives from the run identity.
    kwargs = dict(do_screen=False, use_streamcat=False, diagnostics_n_boot=20)
    return (ra.run(L3, "Eastern Corn Belt Plains", **kwargs),
            ra.run(L3, "Eastern Corn Belt Plains", **kwargs))


def test_same_inputs_give_the_same_content_digest(two_runs):
    first, second = two_runs
    assert first["bundle"] is not None
    assert library.content_digest(first["bundle"]) == library.content_digest(second["bundle"])


def test_same_inputs_give_the_same_inputs_digest(two_runs):
    first, second = two_runs
    a = pv.build_run_manifest(first, started_at="a", finished_at="a")
    b = pv.build_run_manifest(second, started_at="b", finished_at="b")
    assert a["inputsDigest"] == b["inputsDigest"]


def test_metric_order_is_stable(two_runs):
    first, second = two_runs
    assert list(first["metric_config"]) == list(second["metric_config"])
    assert list(first["curve_rows"]) == list(second["curve_rows"])


def test_seeded_diagnostics_are_identical_between_runs(two_runs):
    """The determinism contract covers the resampling diagnostics too."""
    first, second = two_runs
    assert first["run_seed"] == second["run_seed"]
    assert first["diagnostics"] == second["diagnostics"]
    assert first["confidence"] == second["confidence"]


def test_stratifier_eligibility_is_stable(two_runs):
    """Level order comes from the registry declaration, never from the data, so a
    reordered input frame cannot reshuffle the classes."""
    first, second = two_runs
    a = first["stratifiers"]["eligibility"]
    b = second["stratifiers"]["eligibility"]
    assert list(a["stratification"]) == list(b["stratification"])
    assert list(a["level_counts"]) == list(b["level_counts"])
    assert first["stratifiers"]["eligible"] == second["stratifiers"]["eligible"]


def test_screening_results_are_stable(two_runs):
    import pandas.testing as pdt

    first, second = two_runs
    a = first["stratifiers"]["all_layer1_results"]
    b = second["stratifiers"]["all_layer1_results"]
    assert sorted(a) == sorted(b)
    for metric in a:
        pdt.assert_frame_equal(a[metric], b[metric], rtol=1e-9)


def test_redundancy_pairs_are_stably_ordered(two_runs):
    first, second = two_runs
    a, b = first["redundancy"], second["redundancy"]
    assert list(zip(a["metric_a"], a["metric_b"])) == list(zip(b["metric_a"], b["metric_b"]))


def test_the_rule_set_applied_is_stable(two_runs):
    first, second = two_runs
    a = pv.build_provenance(first, pv.build_run_manifest(first), timestamp="t")
    b = pv.build_provenance(second, pv.build_run_manifest(second), timestamp="t")
    assert a["rules_applied"] == b["rules_applied"]
    assert a["counts"] == b["counts"]
    assert ([i["item_id"] for i in a["reviewQueue"]["items"]]
            == [i["item_id"] for i in b["reviewQueue"]["items"]])
