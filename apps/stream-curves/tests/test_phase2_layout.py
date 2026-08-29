"""Cross-Metric Analysis tab sizing: the dynamic heatmap geometry and label
truncation that keep 20+ metric names readable, and the controls layout flags
that stop the bslib fill chain from opening a dead band above the Compute
button."""

from __future__ import annotations

import pandas as pd

from views.phase2 import _short_labels, build_consistency_heatmap, heatmap_px


def test_heatmap_px_clamps():
    assert heatmap_px(0) == 320
    assert heatmap_px(2) == 320          # floor
    assert heatmap_px(10) == 26 * 10 + 170
    assert heatmap_px(25) == 26 * 25 + 170
    assert heatmap_px(500) == 1200       # cap
    assert heatmap_px(None) == 320


def test_short_labels_truncate_and_uniquify():
    long_a = "Benthic macroinvertebrate total taxa richness alpha"
    long_b = "Benthic macroinvertebrate total taxa richness beta"
    out = _short_labels(["pH", long_a, long_b], max_len=30)
    assert out["pH"] == "pH"
    assert len(out[long_a]) <= 31 and out[long_a].endswith("…")
    assert out[long_a] != out[long_b], "identical truncations must stay distinct"


def test_heatmap_builds_with_many_long_named_metrics():
    n = 25
    metrics = [f"m{i}" for i in range(n)]
    wide = pd.DataFrame({
        "metric": metrics,
        "strat_alpha": [1] * n,
        "strat_beta": [0] * n,
    })
    metric_config = {
        m: {"display_name": f"A deliberately very long resolved metric display name {i}"}
        for i, m in enumerate(metrics)
    }
    fig = build_consistency_heatmap(
        {"consistency_matrix": wide}, metric_config, {}, sig_threshold=0.05
    )
    assert fig is not None
    labels = set(fig.data["metric_label"])
    assert len(labels) == n, "uniquified labels must keep every metric a row"
    assert all(len(x) <= 37 for x in labels)  # 30 + ellipsis + " (NN)" suffix room


def test_heatmap_empty_matrix_returns_none():
    assert build_consistency_heatmap({"consistency_matrix": None}, {}, {}, 0.05) is None
    empty = pd.DataFrame({"metric": []})
    assert build_consistency_heatmap({"consistency_matrix": empty}, {}, {}, 0.05) is None
