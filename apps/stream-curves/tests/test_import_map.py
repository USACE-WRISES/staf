"""Tests for the import-wizard pure helpers (M7). The reactive/ipyleaflet flow
is browser-verified; these cover the data loaders and provenance tagging."""

from __future__ import annotations

import pandas as pd

from views.import_map import (
    _re_lat,
    _re_lon,
    _region_choices,
    _state_choices,
    build_col_provenance,
)


def test_region_choices_load():
    ch = _region_choices()
    assert len(ch) > 50  # ~85 L3 ecoregions
    # value is the L3 code, label is "Name (code)"
    code, label = next(iter(ch.items()))
    assert label.endswith(f"({code})")


def test_state_choices_load():
    ch = _state_choices()
    assert "OH" in ch and ch["OH"] == "Ohio"
    assert "CA" in ch


def test_lat_lon_column_matchers():
    assert _re_lat("US_Lat") and _re_lat("latitude") and _re_lat("LAT")
    assert not _re_lat("longitude_x")  # 'lat' not present
    assert _re_lon("US_Long") and _re_lon("lon") and _re_lon("LONGITUDE")


def test_build_col_provenance_tags_by_source():
    compiled = pd.DataFrame(
        columns=[
            "site_id", "lat", "lon", "comid", "state", "DA_mi2",
            "pred_BW_ft", "elev_3dep_m", "ss_DRNAREA", "mmw_forest",
            "PctUrbMd2019Ws", "my_upload_col", "unknownish",
        ]
    )
    sc = pd.DataFrame(columns=["COMID", "PctUrbMd2019Ws"])
    src = build_col_provenance(compiled, sc, nrsa_cols=[], upload_cols=["my_upload_col"])
    assert src["site_id"] == "Site"
    assert src["comid"] == "USGS NLDI"
    assert src["DA_mi2"] == "USGS NLDI basin"
    assert src["pred_BW_ft"] == "Regional curve (Bieger)"
    assert src["elev_3dep_m"] == "USGS 3DEP"
    assert src["ss_DRNAREA"] == "USGS StreamStats"
    assert src["mmw_forest"] == "Model My Watershed"
    assert src["PctUrbMd2019Ws"] == "EPA StreamCAT"
    assert src["my_upload_col"] == "Uploaded (user)"
    assert src["unknownish"] == "Other"


def test_build_col_provenance_nrsa_columns():
    compiled = pd.DataFrame(columns=["site_id", "chem_PH", "phab_XSLOPE"])
    src = build_col_provenance(compiled, None, nrsa_cols=["chem_PH", "phab_XSLOPE"], upload_cols=[])
    # NRSA namespaced cols resolve via nrsa_source_for (category label)
    assert src["chem_PH"].startswith("NRSA")
    assert src["phab_XSLOPE"].startswith("NRSA")
