"""Tests for the import-wizard pure helpers (M7). The reactive/ipyleaflet flow
is browser-verified; these cover the data loaders and provenance tagging."""

from __future__ import annotations

import numpy as np
import pandas as pd

from views.import_map import (
    _compile_coverage_mapping,
    _js1,
    _nrsa_comids,
    _polygon_rings,
    _re_lat,
    _re_lon,
    _region_choices,
    _region_features,
    _restore_candidate_sites,
    _state_choices,
    _view_for_extent,
    build_col_provenance,
    wizard_seed_from_state,
)


def test_js1_escapes_quotes_and_backslashes():
    # onclick payloads embed the code in a single-quoted JS literal.
    assert _js1("a'b") == "a\\'b"
    assert _js1("a\\b") == "a\\\\b"


def test_compile_coverage_mapping_marks_functions_and_drops_id_cols():
    # Shared coverage renderer: a mapped metric lands under its STAF function;
    # id columns are never chipped.
    cov = pd.DataFrame({"metric": ["phab_XBKA", "site_id", "lat", "comid"]})
    html = str(_compile_coverage_mapping(cov))
    assert "Channel and floodplain dynamics" in html  # the 20-function skeleton
    assert "phab_XBKA" in html                         # the covered metric chip
    # every discipline heading is present (full matrix, not just covered rows)
    for disc in ("Hydrology", "Geomorphology", "Biology"):
        assert disc in html


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


def test_wizard_seed_from_state_maps_each_region_kind():
    eco = wizard_seed_from_state(
        {"kind": "ecoregion", "code": "58", "name": "Northeastern Highlands"}
    )
    assert eco == {
        "region_kind": "ecoregion",
        "region_code": "58",
        "region_name": "Northeastern Highlands",
        "user_polygon": None,
        "region_approach": "ecoregion",
    }

    state_seed = wizard_seed_from_state({"kind": "state", "code": "CO", "name": "Colorado"})
    assert state_seed["region_kind"] == "state"
    assert state_seed["region_approach"] == "state"
    assert state_seed["region_code"] == "CO"

    rings = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    poly = wizard_seed_from_state({"kind": "polygon", "code": "USER", "polygon": rings})
    assert poly["region_approach"] == "draw"
    assert poly["user_polygon"] == rings

    # polygon geometry never leaks onto non-polygon kinds
    eco_with_poly = wizard_seed_from_state(
        {"kind": "ecoregion", "code": "58", "polygon": rings}
    )
    assert eco_with_poly["user_polygon"] is None


def test_wizard_seed_from_state_handles_missing_and_unknown():
    assert wizard_seed_from_state(None)["region_kind"] == "none"
    assert wizard_seed_from_state(None)["region_approach"] == "none"
    assert wizard_seed_from_state({"kind": "galaxy"})["region_approach"] == "none"


# --- sites-map view --------------------------------------------------------- #
def test_view_for_extent_frames_an_ecoregion_cluster():
    # Eastern Corn Belt Plains extents. This used to render at zoom 3 (the whole
    # Atlantic) because ipyleaflet's async fit_bounds decremented zoom against an
    # unsized map; the view must now be computed up front and stay regional.
    center, zoom = _view_for_extent(38.270, 42.464, -87.477, -82.133)
    assert 40.0 < center[0] < 40.8
    assert -85.2 < center[1] < -84.4
    assert 6 <= zoom <= 8


def test_view_for_extent_clamps_a_single_site():
    # A degenerate extent must not divide by zero or zoom to street level.
    center, zoom = _view_for_extent(40.32, 40.32, -84.63, -84.63)
    assert center == (40.32, -84.63)
    assert zoom == 12


def test_view_for_extent_never_zooms_out_past_the_floor():
    center, zoom = _view_for_extent(-40.0, 60.0, -170.0, 170.0)
    assert zoom == 3


def test_nrsa_comids_seed_the_site_whose_live_snap_is_flaky():
    seeded = _nrsa_comids()
    assert seeded.get("NRS18_OH_10043") == 18509814


# --- candidate-site restore on project re-entry ----------------------------- #
def _candidates():
    return pd.DataFrame({
        "site_id": ["A", "B"], "lat": [40.32, 39.91], "lon": [-84.63, -84.30],
        "state": ["Ohio", "Ohio"], "ag_eco9": ["TPL", "TPL"],
        "huc8": ["H05120101", "H05080001"], ".source": ["nrsa", "nrsa"],
    })


def _screening_rows():
    # Shape of easi_screening_sites: `state` here is the EASI RUN state.
    return pd.DataFrame({
        "site_id": ["A", "B"], "state": ["succeeded", "failed"],
        "eci": [0.5, None], "final_decision": ["retained", "pending"],
    })


def test_restore_prefers_the_saved_candidate_frame():
    got = _restore_candidate_sites(_candidates(), _screening_rows())
    assert list(got["lat"]) == [40.32, 39.91]
    assert list(got["state"]) == ["Ohio", "Ohio"]      # US state, not run state
    assert set(got.columns) == set(_candidates().columns)


def test_restore_never_copies_the_run_state_over_the_us_state():
    # Regression: the old code took `state` from the screening table, so a
    # reopened project silently showed "succeeded"/"failed" as the US state.
    got = _restore_candidate_sites(None, _screening_rows())
    assert "state" not in got.columns
    assert list(got["site_id"]) == ["A", "B"]


def test_restore_recovers_coordinates_from_a_newer_screening_table():
    # Screening rows now carry the request coordinates, so even the fallback
    # path can rebuild a usable candidate frame.
    sc = _screening_rows()
    sc["lat"], sc["lon"] = [40.32, 39.91], [-84.63, -84.30]
    got = _restore_candidate_sites(None, sc)
    assert list(got["lat"]) == [40.32, 39.91]


def test_restore_returns_none_when_nothing_was_saved():
    assert _restore_candidate_sites(None, None) is None
    assert _restore_candidate_sites(pd.DataFrame(), None) is None


def test_restore_fallback_rederives_source_from_known_nrsa_ids():
    # Sessions restored via the screening table lost every site's origin, so
    # the summary said "0 NRSA" and the NRSA metric pool vanished on re-entry.
    got = _restore_candidate_sites(None, _screening_rows(), nrsa_ids={"A"})
    assert list(got[".source"]) == ["nrsa", "upload"]


def test_restore_fallback_keeps_an_existing_source_column():
    sc = _screening_rows()
    sc[".source"] = ["upload", "upload"]
    got = _restore_candidate_sites(None, sc, nrsa_ids={"A", "B"})
    assert list(got[".source"]) == ["upload", "upload"]  # verbatim wins


# --- region-layer reconciliation (the vanishing-polygons bug) --------------- #
class _FakeMap:
    """Records add/remove so the reconciliation rules can be tested without a browser."""

    def __init__(self):
        self.layers = []

    def add(self, layer):
        self.layers.append(layer)

    def remove(self, layer):
        self.layers.remove(layer)


def _reconcile(kind, store, m):
    """Mirror of import_map._apply_region_layers over injectable add/remove.

    The real helper is a server closure; this pins the RULES it must obey, which
    is what the bug was about -- the old code removed all four slots and re-added
    only one, so a spurious re-fire blanked the map and `none` left it empty.
    """
    specs = (("eco", "ecoregion"), ("state", "state"), ("draw", "polygon"))
    for slot, want in specs:
        if kind == want:
            if store.get(slot) is None:
                layer = f"{slot}-layer"
                m.add(layer)
                store[slot] = layer
        elif store.get(slot) is not None:
            m.remove(store[slot])
            store[slot] = None


def _fresh_store():
    return {"eco": None, "state": None, "selected": None, "draw": None}


def test_reconcile_adds_only_the_layer_for_the_current_kind():
    for kind, slot in (("ecoregion", "eco"), ("state", "state"), ("polygon", "draw")):
        store, m = _fresh_store(), _FakeMap()
        _reconcile(kind, store, m)
        assert store[slot] is not None
        assert [k for k, v in store.items() if v is not None] == [slot]
        assert m.layers == [f"{slot}-layer"]


def test_reconcile_is_idempotent_on_a_repeat_call():
    # Regression: a re-mounted radio re-sends its default with force=True, firing
    # the effect spuriously. The old remove-all/re-add-one shape rebuilt the layer
    # (and destroyed the selected highlight) every time.
    store, m = _fresh_store(), _FakeMap()
    _reconcile("ecoregion", store, m)
    first = store["eco"]
    _reconcile("ecoregion", store, m)
    assert store["eco"] is first          # same object, not rebuilt
    assert m.layers == ["eco-layer"]      # no churn


def test_reconcile_never_touches_the_selected_highlight():
    store, m = _fresh_store(), _FakeMap()
    store["selected"] = "selected-layer"
    m.add("selected-layer")
    _reconcile("ecoregion", store, m)
    _reconcile("ecoregion", store, m)     # a same-kind re-fire used to wipe this
    assert store["selected"] == "selected-layer"
    assert "selected-layer" in m.layers


def test_reconcile_swaps_cleanly_between_kinds():
    store, m = _fresh_store(), _FakeMap()
    _reconcile("ecoregion", store, m)
    _reconcile("state", store, m)
    assert store["eco"] is None and store["state"] is not None
    assert m.layers == ["state-layer"]


def test_reconcile_none_leaves_no_stale_layer():
    store, m = _fresh_store(), _FakeMap()
    _reconcile("ecoregion", store, m)
    _reconcile("none", store, m)
    assert all(store[k] is None for k in ("eco", "state", "draw"))
    assert m.layers == []


# --- region feature lookup (shared by both maps) ---------------------------- #
def test_region_features_resolves_an_ecoregion_code():
    feats = _region_features("ecoregion", "58")
    assert len(feats) == 1
    assert str(feats[0]["properties"]["US_L3CODE"]) == "58"


def test_region_features_resolves_a_state_abbreviation():
    feats = _region_features("state", "OH")
    assert len(feats) == 1
    assert feats[0]["properties"]["state"] == "OH"


def test_region_features_is_empty_when_there_is_nothing_to_draw():
    assert _region_features("ecoregion", None) == []
    assert _region_features("ecoregion", "") == []
    assert _region_features("ecoregion", "not-a-code") == []
    assert _region_features("none", None) == []
    assert _region_features("galaxy", "58") == []
    assert _region_features("polygon", "USER", None) == []


_RING = [[-84.7, 40.1], [-84.3, 40.1], [-84.3, 40.5], [-84.7, 40.1]]


def test_region_features_accepts_both_shapes_a_drawn_region_is_held_in():
    # The draw control stores bare rings as numpy arrays; a restored session may
    # carry a full Polygon geometry instead (see wizard_seed_from_state's own
    # test above). Both must draw, or reopening a drawn project shows no region.
    from_rings = _region_features("polygon", "USER", [np.asarray(_RING, dtype=float)])
    from_geom = _region_features(
        "polygon", "USER", {"type": "Polygon", "coordinates": [_RING]}
    )
    for feats in (from_rings, from_geom):
        assert len(feats) == 1
        assert feats[0]["geometry"]["type"] == "Polygon"
        assert feats[0]["geometry"]["coordinates"] == [_RING]


def test_polygon_rings_drops_degenerate_rings_and_foreign_geometry():
    assert _polygon_rings([[[0, 0], [1, 1]]]) == []          # 2 points enclose nothing
    assert _polygon_rings({"type": "MultiPolygon", "coordinates": [[_RING]]}) == []
    assert _polygon_rings([]) == []
    assert _polygon_rings(None) == []


def test_polygon_rings_keeps_holes():
    hole = [[-84.6, 40.2], [-84.5, 40.2], [-84.5, 40.3], [-84.6, 40.2]]
    assert _polygon_rings([_RING, hole]) == [_RING, hole]


# --- selected-region highlight reconciliation ------------------------------- #
def _reconcile_highlight(kind, code, polygon, store, key_store, m):
    """Mirror of import_map._apply_selected_highlight over injectable add/remove.

    The real helper is a server closure, so this pins the RULES while calling the
    real _region_features for the lookup. The regression it guards against -- the
    highlight being applied once the map exists, having been requested before it
    did -- is reactive wiring and is covered in the browser, not here.
    """
    key = (kind, code)
    if kind != "polygon" and key == key_store["v"] and store.get("selected") is not None:
        return
    feats = _region_features(kind, code, polygon)
    if feats:
        if store.get("selected") is not None:
            m.remove(store["selected"])
        layer = f"selected:{kind}:{code}:{len(feats)}"
        m.add(layer)
        store["selected"] = layer
    elif store.get("selected") is not None:
        m.remove(store["selected"])
        store["selected"] = None
    key_store["v"] = key


def _fresh_key():
    return {"v": None}


def test_highlight_adds_the_selected_region():
    store, keys, m = _fresh_store(), _fresh_key(), _FakeMap()
    _reconcile_highlight("ecoregion", "58", None, store, keys, m)
    assert store["selected"] is not None
    assert len(m.layers) == 1
    # and it never touches the base sheet's slots
    assert all(store[k] is None for k in ("eco", "state", "draw"))


def test_highlight_is_idempotent_on_a_repeat_call():
    # It now re-runs on every _maps_built_nonce bump, so a repeat must not
    # re-scan the collection and rebuild a layer that is already correct.
    store, keys, m = _fresh_store(), _fresh_key(), _FakeMap()
    _reconcile_highlight("ecoregion", "58", None, store, keys, m)
    first = store["selected"]
    _reconcile_highlight("ecoregion", "58", None, store, keys, m)
    assert store["selected"] is first
    assert len(m.layers) == 1


def test_highlight_swaps_when_the_selection_changes():
    store, keys, m = _fresh_store(), _fresh_key(), _FakeMap()
    _reconcile_highlight("ecoregion", "58", None, store, keys, m)
    _reconcile_highlight("ecoregion", "61", None, store, keys, m)
    assert len(m.layers) == 1
    assert "61" in store["selected"]


def test_highlight_clears_when_the_selection_is_dropped():
    # Start New Project, and the Ecoregion -> State swap that clears the code.
    store, keys, m = _fresh_store(), _fresh_key(), _FakeMap()
    _reconcile_highlight("ecoregion", "58", None, store, keys, m)
    _reconcile_highlight("ecoregion", None, None, store, keys, m)
    assert store["selected"] is None
    assert m.layers == []


def test_highlight_always_rebuilds_a_redrawn_polygon():
    # kind/code stay ("polygon", "USER") across a redraw, so the fast path must
    # not swallow new geometry.
    store, keys, m = _fresh_store(), _fresh_key(), _FakeMap()
    _reconcile_highlight("polygon", "USER", [_RING], store, keys, m)
    assert store["selected"] is not None
    bigger = [[-90.0, 35.0], [-80.0, 35.0], [-80.0, 45.0], [-90.0, 35.0]]
    _reconcile_highlight("polygon", "USER", [bigger], store, keys, m)
    assert len(m.layers) == 1  # replaced, not stacked


def test_restore_fallback_recognizes_real_nrsa_ids_without_injection():
    sc = _screening_rows()
    sc["site_id"] = ["NRS18_OH_10043", "my-upload-1"]  # id from the evidence file
    got = _restore_candidate_sites(None, sc)
    assert list(got[".source"]) == ["nrsa", "upload"]
