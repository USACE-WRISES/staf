"""The metric and function evidence table (national campaign, Round 0).

``config/metric_evidence_table.csv`` is a projection of the registries, the NRSA
archive, the seven latest published assessments and the curated
``config/metric_evidence.yaml``. These tests keep the committed projection equal to a
fresh build (the drift gate, as ``test_fixed_criteria`` does for its file), keep the
curated file and ``metric_map.yaml`` in step, and pin the file's shape and vocabularies.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
SCRIPT = APP_DIR / "scripts" / "build_metric_evidence.py"

EM_DASH = "\u2014"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_metric_evidence", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bme():
    return _load_script()


@pytest.fixture(scope="module")
def built(bme):
    """One fresh build for the module: rows and the rendered text."""
    rows = bme.build_rows()
    return {"rows": rows, "text": bme.render_csv(rows)}


@pytest.fixture(scope="module")
def committed(bme):
    return bme.TABLE_PATH.read_bytes()


@pytest.fixture(scope="module")
def curated(bme):
    return bme.load_curated()


# --------------------------------------------------------------------------- #
# the drift gate
# --------------------------------------------------------------------------- #
def test_the_committed_table_matches_a_fresh_build(bme, built, committed):
    assert committed.decode("utf-8") == built["text"], bme.check()


def test_check_reports_an_edited_cell(bme, built, tmp_path):
    text = built["text"]
    edited = text.replace("\nbent_EPT_NTAX,Community dynamics,Biology,",
                          "\nbent_EPT_NTAX,Community dynamics,Geology,", 1)
    assert edited != text
    p = tmp_path / "metric_evidence_table.csv"
    p.write_text(edited, encoding="utf-8", newline="\n")
    problems = bme.check(p)
    assert any("bent_EPT_NTAX / Community dynamics: discipline is 'Geology'" in x
               for x in problems), problems


# --------------------------------------------------------------------------- #
# the curated record and the map agree
# --------------------------------------------------------------------------- #
def test_every_mapped_metric_has_a_curated_entry_and_none_is_stale(bme, curated):
    keys = set(bme.mapped_keys())
    have = set(curated["metrics"])
    assert keys - have == set(), sorted(keys - have)
    assert have - keys == set(), sorted(have - keys)
    assert bme.validate_curated(curated, sorted(keys)) == []


def test_a_missing_entry_stops_the_build_and_names_the_key(bme, curated):
    import copy
    doc = copy.deepcopy(curated)
    del doc["metrics"]["phab_XEMBED"]
    with pytest.raises(bme.EvidenceError) as err:
        bme.build_rows(curated=doc)
    assert "phab_XEMBED" in str(err.value) and "--init" in str(err.value)


def test_init_appends_only_the_missing_entries_and_keeps_the_text(bme, tmp_path):
    p = tmp_path / "metric_evidence.yaml"
    added = bme.init_curated(["b_key", "a_key"], p)
    assert added == ["b_key", "a_key"]
    text = p.read_text(encoding="utf-8")
    curated_line = "    construct_class: pressure_proxy  # kept by --init\n"
    text = text.replace("  a_key:\n    construct_class: null\n",
                        "  a_key:\n" + curated_line, 1)
    p.write_text(text, encoding="utf-8", newline="\n")
    assert bme.init_curated(["a_key", "b_key", "c_key"], p) == ["c_key"]
    after = p.read_text(encoding="utf-8")
    assert curated_line in after
    assert after.index("  b_key:") < after.index("  c_key:")
    assert b"\r" not in p.read_bytes()
    doc = bme.load_curated(p)
    assert set(doc["metrics"]) == {"a_key", "b_key", "c_key"}
    assert doc["metrics"]["c_key"] == {
        "construct_class": None, "spatial_support": None, "easi_role": [], "evidence": [],
        "limitations": [], "disposition": None, "disposition_rationale": None,
        "status": "hypothesis", "reviewed_by": "n/a"}
    assert bme.init_curated(["a_key", "b_key", "c_key"], p) == []


# --------------------------------------------------------------------------- #
# the file's shape
# --------------------------------------------------------------------------- #
def test_the_table_is_lf_and_sorted_with_the_declared_columns(bme, committed):
    assert b"\r" not in committed
    assert committed.endswith(b"\n")
    reader = csv.DictReader(io.StringIO(committed.decode("utf-8"), newline=""))
    rows = list(reader)
    assert reader.fieldnames == bme.COLUMNS
    pairs = [(r["metric_key"], r["function"]) for r in rows]
    assert pairs == sorted(pairs)
    assert len(pairs) == len(set(pairs))
    assert rows, "the table is empty"


def test_the_table_carries_no_timestamp_path_or_em_dash(committed):
    text = committed.decode("utf-8")
    assert EM_DASH not in text
    assert "D:\\" not in text and "D:/" not in text and "/Users/" not in text
    assert "2026-" not in text          # no build date, no updatedAt


def test_the_curated_file_is_lf_without_em_dashes(bme):
    data = bme.CURATED_PATH.read_bytes()
    assert b"\r" not in data
    assert EM_DASH not in data.decode("utf-8")


def test_every_mapped_pair_has_a_row_and_nothing_else(bme, built):
    entries = bme.mapped_entries()
    want = sorted({(e["metric_key"], e["function"]) for e in entries})
    have = [(r["metric_key"], r["function"]) for r in built["rows"]]
    assert have == want


# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #
def test_curated_fields_use_the_vocabularies(bme, curated):
    for key, e in curated["metrics"].items():
        assert e.get("construct_class") in (None,) + bme.CONSTRUCT_CLASSES, key
        assert e.get("spatial_support") in (None,) + bme.SPATIAL_SUPPORTS, key
        assert e.get("disposition") in (None,) + bme.DISPOSITIONS, key
        assert e.get("status") in bme.STATUSES, key
        roles = e.get("easi_role") or []
        assert isinstance(roles, list) and set(roles) <= set(bme.EASI_ROLES), key
        for item in e.get("evidence") or []:
            assert isinstance(item, dict) and set(item) <= {"citation", "note"}, key
        assert all(isinstance(x, str) for x in e.get("limitations") or []), key
        assert isinstance(e.get("reviewed_by"), str), key


def test_table_cells_use_the_vocabularies(bme, built):
    directions = {"higher_is_better", "lower_is_better", "optimum", "excluded",
                  "predictor_only", "review", ""}
    shapes = {"monotone_increasing", "monotone_decreasing", "optimum", ""}
    for r in built["rows"]:
        assert r["source"] in {"nrsa", "streamcat", "streamstats", "mmw", "site_engine"}
        assert r["role"] in {"metric", "predictor", "both"}
        assert r["default_selected"] in {"true", "false"}
        assert r["direction"] in directions, r["metric_key"]
        assert r["expected_shape"] in shapes, r["metric_key"]
        assert r["curve_form"] in {"monotone", "optimum", ""}, r["metric_key"]
        assert r["model_registry_status"] in {"approved", "candidate", ""}, r["metric_key"]
        assert r["scale_level"] in {"l3", "l2", "l1", "nars9", "national", ""}, r["metric_key"]
        assert r["construct_class"] in set(bme.CONSTRUCT_CLASSES) | {""}, r["metric_key"]
        assert r["spatial_support"] in set(bme.SPATIAL_SUPPORTS) | {""}, r["metric_key"]
        assert r["disposition"] in set(bme.DISPOSITIONS) | {""}, r["metric_key"]
        assert r["status"] in bme.STATUSES, r["metric_key"]
        roles = [x for x in r["easi_role"].split(";") if x]
        assert set(roles) <= set(bme.EASI_ROLES), r["metric_key"]
        for c in ("n_frame_values", "n_strict_values"):
            assert r[c] == "" or r[c].isdigit(), (r["metric_key"], c)
        if r["n_frame_values"]:
            assert int(r["n_strict_values"]) <= int(r["n_frame_values"]), r["metric_key"]


# --------------------------------------------------------------------------- #
# the sources the table leans on
# --------------------------------------------------------------------------- #
def test_the_pinned_bundles_are_each_assessments_latest_version(bme):
    for assessment_id, version in bme.LIBRARY_BUNDLES:
        manifest = json.loads((bme.LIBRARY_DIR / assessment_id / "manifest.json")
                              .read_text(encoding="utf-8"))
        assert int(manifest["latestVersion"]) == version, assessment_id


def test_the_bundle_id_stem_is_the_exporters(bme):
    from streamcurves import deep_export
    for mk in bme.mapped_keys():
        for cand in bme.key_candidates(mk):
            assert bme.deep_slug(cand) == deep_export.deep_slug(cand)


def test_strict_counts_agree_with_the_scale_registry(bme, built):
    """n_strict_values is the scale registry's n_reference: same frame, same screen,
    same value policy."""
    import yaml
    reg = yaml.safe_load((bme.CONFIG_DIR / "metric_scale_registry.yaml")
                         .read_text(encoding="utf-8"))["metrics"]
    by_key = {r["metric_key"]: r for r in built["rows"]}
    checked = 0
    for reg_key, entry in reg.items():
        mk = next((k for k in by_key if reg_key in bme.key_candidates(k)), None)
        if mk is None or entry.get("n_reference") is None:
            continue
        assert int(by_key[mk]["n_strict_values"]) == int(entry["n_reference"]), mk
        checked += 1
    assert checked >= 20
