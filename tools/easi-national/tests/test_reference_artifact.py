"""The runtime artifact is deterministic and uses the frozen evidence only."""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis import artifact, curves, panels
from builder.paths import DataRoot


def _registry_row(quantity, level="national", stratum="national:national", split="", *, usable=True):
    higher = curves.QUANTITIES[quantity].higher_is_better
    return {
        "quantity": quantity, "level": level, "stratum": stratum, "split": split,
        "higher_is_better": higher, "panel_tier": "complete", "screen": "strict",
        "n_members": 102, "n": 100, "status": "complete", "usable": usable,
        "q25": 1.234567891, "q50": 2.0, "q75": 3.0,
        "x39": 0.65 if higher else 3.3, "x69": 1.15 if higher else 3.03,
        "points_json": json.dumps([[0.0, 0.0], [1.234567891, 0.7], [3.0, 1.0]] if higher
                                  else [[0.0, 1.0], [3.0, 0.7], [4.0, 0.0]]),
    }


def _write_registry(root, rows):
    path = curves.registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    os.utime(path, ns=(1789415453960823000, 1789415453960823000))


@pytest.fixture
def frozen_root(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir(exist_ok=True)
    (root.analysis / "values_meta.json").write_text(json.dumps({
        "method_version": "historical-method", "analysis_version": "0.1.0",
        "built_at": "2026-09-14T19:09:33Z",
    }), encoding="utf-8")
    (root.staging / "manifest.json").write_text(json.dumps({
        "vintage": "2026.09", "method_version": "newer-staging-method",
    }), encoding="utf-8")
    rows = []
    for quantity in ("woody_wsrp100", "natural_wsrp100", "q_cv_monthly"):
        rows.extend([
            _registry_row(quantity), _registry_row(quantity, "l2", "l2:8.3"),
            _registry_row(quantity, "l2", "l2:9.2", usable=False),
            _registry_row(quantity, "l3", "l3:45"),
            _registry_row(quantity, "l2", "l2:8.3", "perennial"),
        ])
    rows.extend(_registry_row("er_median", split=split) for split in sorted(artifact.SLOPE_CLASSES))
    rows.append(_registry_row("bhr_median"))
    _write_registry(root, rows)

    panels.panels_dir(root).mkdir()
    pq.write_table(pa.Table.from_pylist([{
        "level": "national", "stratum": "national:national", "panel_tier": "complete", "screen": "strict",
    }]), panels.panels_path(root))
    pq.write_table(pa.Table.from_pylist([{
        "comid": i, "level": "national", "stratum": "national:national",
        "slope_class": None if i <= 20 else "lt_0.5",
    } for i in range(1, 103)]), panels.members_path(root))
    er_values = np.linspace(0.7, 4.0, 100)
    pq.write_table(pa.table({
        "comid": list(range(1, 104)), "er_median": [*er_values, None, float("inf"), 1000.0],
    }), root.analysis / "values.parquet")
    pq.write_table(pa.table({
        "comid": list(range(1, 104)), "pctimp2019ws": [0.0] * 103,
    }), root.analysis / "landscape.parquet")
    return root, er_values


def test_artifact_is_deterministic_and_leaves_frozen_inputs_unchanged(frozen_root, tmp_path):
    root, _ = frozen_root
    before = {p.relative_to(root.root): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
              for p in root.root.rglob("*") if p.is_file()}
    a = artifact.write_artifact(root, tmp_path / "one.json")
    b = artifact.write_artifact(root, tmp_path / "two.json")
    assert a.read_bytes() == b.read_bytes()
    assert b"\r" not in a.read_bytes()
    data = json.loads(a.read_bytes())
    assert a.read_text(encoding="utf-8") == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    after = {p.relative_to(root.root): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
             for p in root.root.rglob("*") if p.is_file()}
    assert after == before
    provenance = data["provenance"]
    assert provenance["valuesMethodVersion"] == "historical-method"
    assert provenance["datasetVintage"] == "2026.09"
    assert provenance["registry"]["mtimeNs"] == 1789415453960823000
    assert provenance["registry"]["sha256"] == hashlib.sha256(curves.registry_path(root).read_bytes()).hexdigest()
    assert provenance["curveEngineSha256"] == hashlib.sha256(artifact.ENGINE_PATH.read_bytes()).hexdigest()
    assert provenance["screen"]["roadDensityCap"] == 2.0
    assert provenance["panelFloors"] == {"complete": 100, "exploratory": 30, "split": 30}


def test_selects_only_plan_f_usable_curves_and_rounds_six_places(frozen_root):
    root, _ = frozen_root
    data = artifact.build_artifact(root)
    assert data["schemaVersion"] == 1
    assert set(data["sets"]) == set(artifact.SET_SPECS)
    for set_id in ("corridor-woody", "corridor-natural", "flow-variability"):
        selected = data["sets"][set_id]
        assert set(selected["curves"]) == {"8.3", "national"}
        assert selected["stratifier"] == "l2"
        curve = selected["curves"]["8.3"]
        assert curve["n"] == 100 and curve["nMembers"] == 102
        assert curve["q25"] == 1.234568
    assert data["sets"]["corridor-woody"]["curves"]["8.3"]["points"][1][0] == 1.234568
    assert data["sets"]["flow-variability"]["higherIsBetter"] is False
    assert set(data["sets"]["entrenchment"]["curves"]) == {*artifact.SLOPE_CLASSES, "national"}


def test_unsplit_er_fits_stored_members_including_unknown_slope(frozen_root):
    root, er_values = frozen_root
    data = artifact.build_artifact(root)
    fallback = data["sets"]["entrenchment"]["curves"]["national"]
    expected = curves.fit_curve(er_values, curves.QUANTITIES["er_median"], "national:national")
    assert fallback["n"] == 100 and fallback["nMembers"] == 102
    assert fallback["q25"] == round(float(np.quantile(er_values, 0.25)), 6)
    assert fallback["q25"] != round(float(np.quantile(er_values[20:], 0.25)), 6)
    for key in ("q25", "q50", "q75", "x39", "x69"):
        assert fallback[key] == round(expected[key], 6)
    assert fallback["points"] == [[round(x, 6), round(y, 6)] for x, y in expected["points"]]
    assert data["provenance"]["entrenchmentNationalFallback"]["method"] == "pooled-existing-national-panel"


@pytest.mark.parametrize("quantity,set_id", [
    ("woody_wsrp100", "corridor-woody"), ("natural_wsrp100", "corridor-natural"),
    ("q_cv_monthly", "flow-variability"),
])
def test_each_l2_set_requires_usable_national_curve(frozen_root, quantity, set_id):
    root, _ = frozen_root
    rows = pq.read_table(curves.registry_path(root)).to_pylist()
    for row in rows:
        if row["quantity"] == quantity and row["level"] == "national":
            row["usable"] = False
    _write_registry(root, rows)
    with pytest.raises(ValueError, match=f"{set_id} requires a usable national curve"):
        artifact.build_artifact(root)


def test_existing_unsplit_er_is_used_without_fitting(frozen_root, monkeypatch):
    root, _ = frozen_root
    rows = pq.read_table(curves.registry_path(root)).to_pylist()
    rows.append(_registry_row("er_median"))
    _write_registry(root, rows)

    def unexpected(*args, **kwargs):
        pytest.fail("The stored unsplit ER curve should avoid a fallback fit")

    monkeypatch.setattr(artifact, "_national_entrenchment", unexpected)
    data = artifact.build_artifact(root)
    assert data["sets"]["entrenchment"]["curves"]["national"]["q25"] == 1.234568
    assert "entrenchmentNationalFallback" not in data["provenance"]


@pytest.mark.parametrize("values,match", [
    ([None] * 102, "fewer than 30 finite values"),
    ([0.0] * 102, "No usable national entrenchment fallback"),
])
def test_absent_usable_er_fallback_fails(frozen_root, values, match):
    root, _ = frozen_root
    pq.write_table(pa.table({"comid": list(range(1, 103)), "er_median": pa.array(values, pa.float64())}),
                   root.analysis / "values.parquet")
    with pytest.raises(ValueError, match=match):
        artifact.build_artifact(root)


def test_cli_writes_only_requested_output(frozen_root, tmp_path):
    root, _ = frozen_root
    out = tmp_path / "export" / "reference-curves.json"
    assert artifact.main(["--root", str(root.root), "--out", str(out)]) == 0
    assert set(json.loads(out.read_text())["sets"]) == set(artifact.SET_SPECS)
