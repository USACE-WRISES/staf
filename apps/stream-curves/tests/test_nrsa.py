"""Tests for streamcurves.nrsa (port of app/helpers/nrsa_metrics.R).

Exercises the REAL bundled data/nrsa_metric_catalog.csv + data/nrsa_metrics.parquet.
"""

import math

import pandas as pd

from streamcurves import nrsa


# --------------------------------------------------------------------------- #
# catalog / values loaders (real bundled data)
# --------------------------------------------------------------------------- #
def test_load_catalog_columns_and_core_bool():
    cat = nrsa.load_nrsa_catalog()
    assert list(cat.columns) == nrsa.CATALOG_COLUMNS
    assert len(cat) > 0
    # core is a logical (True/False/None), not the raw "TRUE"/"FALSE" strings
    assert set(cat["core"].dropna().unique()).issubset({True, False})
    assert "chem_ANC" in cat["name"].tolist()


def test_load_values_shape_and_site_id_string():
    v = nrsa.load_nrsa_values()
    assert "site_id" in v.columns
    assert v.shape == (1920, 789)
    assert v["site_id"].dtype == object or str(v["site_id"].dtype) == "string"
    assert isinstance(v["site_id"].iloc[0], str)


# --------------------------------------------------------------------------- #
# provenance labels
# --------------------------------------------------------------------------- #
def test_category_label_and_source_for():
    assert nrsa.nrsa_category_label("chem") == "NRSA: Water chemistry"
    assert nrsa.nrsa_category_label("fish") == "NRSA: Fish"
    assert nrsa.nrsa_category_label("zzz") == "NRSA"
    assert nrsa.nrsa_source_for("chem_ANC") == "NRSA: Water chemistry"
    assert nrsa.nrsa_source_for("phab_XEMBED") == "NRSA: Physical habitat"


# --------------------------------------------------------------------------- #
# attach_nrsa_metrics (synthetic values for deterministic join semantics)
# --------------------------------------------------------------------------- #
def test_attach_nrsa_metrics_left_join_with_na():
    sites = pd.DataFrame({"site_id": ["a", "b", "c"]})
    values = pd.DataFrame(
        {"site_id": ["a", "c"], "chem_X": [1.0, 3.0], "chem_Y": [10.0, 30.0]}
    )
    out = nrsa.attach_nrsa_metrics(sites, ["chem_X", "not_there"], values)
    # only existing columns attached; selection order preserved
    assert "chem_X" in out.columns and "chem_Y" not in out.columns
    xs = out["chem_X"].tolist()
    assert xs[0] == 1.0 and math.isnan(xs[1]) and xs[2] == 3.0


def test_attach_nrsa_metrics_noop_paths():
    sites = pd.DataFrame({"site_id": ["a"]})
    values = pd.DataFrame({"site_id": ["a"], "chem_X": [1.0]})
    # nothing selected that exists
    assert nrsa.attach_nrsa_metrics(sites, ["nope"], values) is sites
    # missing site_id column
    assert nrsa.attach_nrsa_metrics(pd.DataFrame({"x": [1]}), ["chem_X"], values).equals(
        pd.DataFrame({"x": [1]})
    )


def test_attach_nrsa_metrics_real_values():
    v = nrsa.load_nrsa_values()
    real_id = v["site_id"].iloc[0]
    sites = pd.DataFrame({"site_id": [real_id, "__no_such_site__"]})
    out = nrsa.attach_nrsa_metrics(sites, ["chem_ANC"], v)
    assert "chem_ANC" in out.columns
    assert pd.isna(out["chem_ANC"].iloc[1])  # unmatched site -> NA


# --------------------------------------------------------------------------- #
# nrsa_catalog_role_for
# --------------------------------------------------------------------------- #
def test_role_for_measured_metric():
    assert nrsa.nrsa_catalog_role_for("chem_ANC") == "metric"
    assert nrsa.nrsa_catalog_role_for("__nope__") is None


def test_role_for_landscape_is_predictor():
    cat = nrsa.load_nrsa_catalog()
    land = cat[cat["category"] == "Landscape"]
    if len(land):
        assert nrsa.nrsa_catalog_role_for(land["name"].iloc[0]) == "predictor"


def test_role_for_raw_name_only_when_unambiguous():
    cat = nrsa.load_nrsa_catalog()
    # a raw_name that maps to exactly one category resolves; a colliding one does not
    counts = cat["raw_name"].value_counts()
    unique_raw = counts[counts == 1].index
    if len(unique_raw):
        rn = unique_raw[0]
        cats = cat[cat["raw_name"] == rn]["category"].iloc[0]
        expected = "metric" if cats in (
            "Water chemistry", "Physical habitat", "Benthic macroinvertebrates", "Fish"
        ) else ("predictor" if cats == "Landscape" else None)
        assert nrsa.nrsa_catalog_role_for(rn) == expected
