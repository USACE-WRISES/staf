"""Precheck: the missing-min_sample_size fallback, and reading a result table.

The fallback matters because a metric_config without ``min_sample_size`` is the
normal shape for a headless session or a ``tables_from_configs`` reconstruction,
and a KeyError there aborts an entire library Open. The readers matter because
the Pre-run validation panel and the workflow strip's section chip both call
them -- they must agree on what counts as a warning.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from streamcurves.precheck import (
    DEFAULT_MIN_SAMPLE_SIZE,
    precheck_summary,
    precheck_warning_rows,
    run_metric_precheck,
)


def _frame(n: int) -> pd.DataFrame:
    return pd.DataFrame({"m1": np.linspace(1.0, 10.0, n)})


# The shape published sessions actually carry: no min_sample_size key.
_NO_FLOOR = {
    "m1": {
        "column_name": "m1",
        "display_name": "Metric one",
        "metric_family": "continuous",
        "units": "",
    }
}


# --- min_sample_size fallback ------------------------------------------------ #
def test_missing_min_sample_size_does_not_raise():
    out = run_metric_precheck(_frame(33), _NO_FLOOR)
    assert len(out) == 1
    assert out.iloc[0]["precheck_status"] == "pass"


def test_missing_min_sample_size_uses_the_registry_default():
    assert DEFAULT_MIN_SAMPLE_SIZE == 10
    # Below the default floor -> flagged; comfortably above -> not.
    assert bool(run_metric_precheck(_frame(9), _NO_FLOOR).iloc[0]["flag_low_n"]) is True
    assert bool(run_metric_precheck(_frame(33), _NO_FLOOR).iloc[0]["flag_low_n"]) is False


def test_explicit_min_sample_size_still_wins():
    cfg = {"m1": {**_NO_FLOOR["m1"], "min_sample_size": 50}}
    assert bool(run_metric_precheck(_frame(33), cfg).iloc[0]["flag_low_n"]) is True


def test_categorical_metric_without_floor_does_not_raise():
    data = pd.DataFrame({"m1": ["a", "b", "a", "b"]})
    cfg = {"m1": {**_NO_FLOOR["m1"], "metric_family": "categorical"}}
    out = run_metric_precheck(data, cfg)
    assert out.iloc[0]["precheck_status"] == "categorical"


# --- reading a precheck table ------------------------------------------------ #
def _row(status, low_n=False, low_var=False, impossible=False, metric="m"):
    return {
        "metric": metric,
        "display_name": metric,
        "n_obs": 33,
        "n_missing": 0,
        "pct_missing": 0.0,
        "flag_low_n": low_n,
        "flag_low_variance": low_var,
        "flag_impossible_values": impossible,
        "precheck_status": status,
    }


def test_warning_rows_empty_when_everything_passes():
    df = pd.DataFrame([_row("pass", metric="a"), _row("pass", metric="b")])
    assert len(precheck_warning_rows(df)) == 0
    s = precheck_summary(df)
    assert s == {"available": True, "n_total": 2, "n_warnings": 0, "counts": {"pass": 2}}


def test_warning_rows_catch_bad_status_and_flags():
    df = pd.DataFrame([
        _row("pass", metric="clean"),
        _row("no_data", metric="empty"),
        _row("pass", low_n=True, metric="thin"),
        _row("pass", low_var=True, metric="flat"),
        _row("pass", impossible=True, metric="impossible"),
    ])
    flagged = set(precheck_warning_rows(df)["metric"])
    assert flagged == {"empty", "thin", "flat", "impossible"}
    assert precheck_summary(df)["n_warnings"] == 4


def test_categorical_is_not_a_warning_unless_flagged():
    df = pd.DataFrame([
        _row("categorical", metric="plain"),
        _row("categorical", low_n=True, metric="thin"),
    ])
    assert set(precheck_warning_rows(df)["metric"]) == {"thin"}


def test_none_flags_are_not_warnings():
    """Flags are tri-state: None means "not computable" (sd of one observation),
    which is an absent verdict, not a problem."""
    df = pd.DataFrame([_row("pass", low_var=None, metric="single")])
    assert len(precheck_warning_rows(df)) == 0


def test_readers_tolerate_absent_and_empty_input():
    for empty in (None, pd.DataFrame(), pd.DataFrame({"other": [1]})):
        assert len(precheck_warning_rows(empty)) == 0
        s = precheck_summary(empty)
        assert s["available"] is False
        assert s["n_warnings"] == 0


def test_summary_counts_every_status_not_just_three():
    """The old panel header hardcoded pass/caution/fail, so categorical and
    no_data rows vanished from the roll-up."""
    df = pd.DataFrame([
        _row("pass", metric="a"),
        _row("categorical", metric="b"),
        _row("no_data", metric="c"),
    ])
    assert precheck_summary(df)["counts"] == {"pass": 1, "categorical": 1, "no_data": 1}
