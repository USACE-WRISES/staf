"""config/staf_metric_library.json is generated (campaign Round 1, 2026-09-25).

The file's header used to name a producer that did not exist. The producer,
``scripts/build_staf_metric_library.py``, rebuilds it from the state SQT registry
plus the crosswalks it records; these tests keep the committed file equal to a
fresh build (the drift gate, as ``test_fixed_criteria`` does for its file), keep
the recorded crosswalks resolvable, and pin what the readers see.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from streamcurves import candidates, staf_library

APP_DIR = Path(__file__).resolve().parents[1]
SCRIPT = APP_DIR / "scripts" / "build_staf_metric_library.py"


@pytest.fixture(scope="module")
def bsl():
    spec = importlib.util.spec_from_file_location("build_staf_metric_library", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def committed(bsl):
    return json.loads(bsl.OUT_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# the drift gate
# --------------------------------------------------------------------------- #
def test_the_committed_file_matches_a_fresh_build(bsl):
    assert bsl.drift() == []


def test_the_file_is_lf_with_a_final_newline(bsl):
    data = bsl.OUT_PATH.read_bytes()
    assert b"\r" not in data and data.endswith(b"}\n")


def test_an_edited_entry_is_reported(bsl, committed, tmp_path):
    edited = json.loads(json.dumps(committed))
    edited["metrics"][0]["additional_function_ids"] = ["reach-inflow"]
    del edited["metrics"][-1]
    edited["metricCount"] = len(edited["metrics"])
    path = tmp_path / "staf_metric_library.json"
    path.write_text(bsl.render(edited), encoding="utf-8", newline="\n")
    problems = bsl.drift(out_path=path)
    assert any(p.startswith(committed["metrics"][0]["library_id"]) for p in problems)
    assert any("is missing from the committed file" in p for p in problems)
    assert any(p.startswith("metricCount") for p in problems)


def test_the_header_names_the_producer_and_counts_its_metrics(committed):
    assert "scripts/build_staf_metric_library.py" in committed["_comment"]
    assert "data/sqt/registry.json" in committed["_comment"]
    metrics = committed["metrics"]
    assert committed["metricCount"] == len(metrics) == 102
    assert committed["functionPairCount"] == sum(
        1 + len(m["additional_function_ids"]) for m in metrics) == 114
    assert committed["multiFunctionCount"] == sum(
        1 for m in metrics if m["additional_function_ids"]) == 11


# --------------------------------------------------------------------------- #
# the recorded crosswalks resolve
# --------------------------------------------------------------------------- #
def test_every_recorded_app_metric_exists_in_metric_map_under_its_source(bsl):
    sources = bsl.metric_map_sources()
    for lib_id, (code, source) in bsl.APP_METRIC_KEYS.items():
        assert sources.get(code) == source, (lib_id, code, source)


def test_every_recorded_function_is_canonical_and_never_the_primary(bsl, committed):
    ids = set(bsl.function_ids())
    assert len(ids) == 20
    by_id = {m["library_id"]: m for m in committed["metrics"]}
    for lib_id, extra in bsl.ADDITIONAL_FUNCTIONS.items():
        assert lib_id in by_id, lib_id
        assert set(extra) <= ids, lib_id
        assert by_id[lib_id]["primary_function_id"] not in extra, lib_id
        assert by_id[lib_id]["additional_function_ids"] == extra, lib_id


def test_a_crosswalk_the_metric_map_does_not_carry_stops_the_build(bsl, monkeypatch):
    monkeypatch.setitem(bsl.APP_METRIC_KEYS, "channel-and-floodplain-dynamics-sinuosity",
                        ("phab_SINU", "streamstats"))
    with pytest.raises(ValueError, match="metric_map.yaml"):
        bsl.build()


# --------------------------------------------------------------------------- #
# what the readers see
# --------------------------------------------------------------------------- #
def test_the_readers_see_the_same_library(bsl, committed):
    staf_library._reset_cache()
    entries = staf_library.staf_metric_library_entries()
    assert len(entries) == committed["functionPairCount"]
    assert entries["library_id"].nunique() == committed["metricCount"]
    multi = entries.groupby("library_id")["function_id"].nunique()
    assert int((multi > 1).sum()) == committed["multiFunctionCount"]
    keyed = {(a, b) for a, b in zip(entries["library_id"], entries["app_metric_key"])
             if isinstance(b, str) and b}
    assert {lib_id for lib_id, _ in keyed} == set(bsl.APP_METRIC_KEYS)
    assert candidates._crosswalk() == frozenset(
        (lib_id, code) for lib_id, (code, _src) in bsl.APP_METRIC_KEYS.items())
