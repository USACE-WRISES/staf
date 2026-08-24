"""The metric name registry.

The bundled NRSA catalog's ``label`` column is the bare mnemonic, so both paths
that seed ``metric_config`` wrote a code where a name belongs. These pin the
dictionary's shape, the lookup rules, and the guarantee that a name a person
typed is never overwritten.
"""
from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import curve_svg as cs
from streamcurves import metric_names as mn

# metrics the published assessments actually build curves from
IN_USE = [
    "phab_XEMBED", "phab_BFWD_RAT", "phab_SINU", "phab_PCT_SAFN", "phab_LSUB_DMM",
    "phab_XCDENMID", "phab_W1_HALL", "phab_XSLOPE_use", "phab_QR1",
    "chem_PTL", "chem_COND", "chem_PH", "chem_ANC", "chem_CHLA",
    "bent_EPT_NTAX", "bent_TOTLNTAX", "bent_HPRIME", "bent_TOLRPIND",
]


@pytest.fixture(scope="module")
def dictionary() -> pd.DataFrame:
    return mn.load_dictionary()


# --------------------------------------------------------------------------- #
# the dictionary itself
# --------------------------------------------------------------------------- #

def test_dictionary_has_every_catalog_metric_and_no_blank_names(dictionary):
    assert len(dictionary) > 780
    for col in mn.DICTIONARY_COLUMNS:
        assert col in dictionary.columns
    assert dictionary.metric_key.is_unique
    assert (dictionary.display_name.astype(str).str.strip() != "").all()
    assert (dictionary.short_name.astype(str).str.strip() != "").all()


def test_no_metric_keeps_its_mnemonic_as_its_name(dictionary):
    """A name that is still the code means the build found no label at all."""
    stuck = [
        r.metric_key for r in dictionary.itertuples()
        if str(r.display_name).strip() == str(r.metric_key).split("_", 1)[-1]
    ]
    assert stuck == [], f"still showing a code: {stuck[:10]}"


def test_short_name_fits_a_tile_header(dictionary):
    over = dictionary[dictionary.short_name.astype(str).str.len() > 40]
    assert len(over) == 0, over.metric_key.tolist()[:10]


# --------------------------------------------------------------------------- #
# lookup
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("key", IN_USE)
def test_every_metric_in_use_resolves_to_a_readable_name(key):
    name = mn.display_name_for(key)
    assert name and name != key
    assert name != key.split("_", 1)[1]
    assert len(name) <= 60


def test_named_lookups_for_a_known_metric():
    assert mn.display_name_for("phab_XEMBED") == "Embeddedness"
    assert mn.units_for("phab_XEMBED") == "%"
    assert "embeddedness" in (mn.description_for("phab_XEMBED") or "").lower()


def test_a_bare_mnemonic_resolves_through_its_namespace():
    assert mn.display_name_for("XEMBED") == mn.display_name_for("phab_XEMBED")


def test_a_streamcat_scale_suffix_falls_back_to_the_base_code():
    assert mn.display_name_for("pctimp2019ws") == mn.display_name_for("pctimp2019")
    assert mn.display_name_for("pctimp2019cat") == mn.display_name_for("pctimp2019")


def test_an_unknown_key_returns_the_default_not_a_guess():
    assert mn.display_name_for("zzz_not_a_metric") is None
    assert mn.display_name_for("zzz_not_a_metric", "fallback") == "fallback"
    assert mn.units_for("zzz_not_a_metric", "") == ""


def test_short_name_falls_back_to_the_display_name_then_the_default():
    assert mn.short_name_for("phab_XEMBED") == "Embeddedness"
    assert mn.short_name_for("zzz_not_a_metric", "code") == "code"


# --------------------------------------------------------------------------- #
# placeholder detection: the whole point is not clobbering a person's edit
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stored", ["", None, "phab_XEMBED", "XEMBED", "  ", "PHAB_XEMBED"])
def test_a_stored_code_counts_as_a_placeholder(stored):
    assert mn.is_placeholder_name(stored, "phab_XEMBED") is True


@pytest.mark.parametrize("stored", ["Embeddedness", "Streambed embeddedness", "My name for it"])
def test_a_real_name_is_never_a_placeholder(stored):
    assert mn.is_placeholder_name(stored, "phab_XEMBED") is False


def test_resolve_metric_config_fills_codes_and_keeps_edits():
    config = {
        "phab_XEMBED": {"display_name": "XEMBED", "units": ""},
        "chem_PTL": {"display_name": "chem_PTL"},
        "phab_SINU": {"display_name": "Bendiness, as I call it", "units": "widgets"},
        "zzz_unknown": {"display_name": "zzz_unknown"},
    }
    out = mn.resolve_metric_config(config)
    assert out["phab_XEMBED"]["display_name"] == "Embeddedness"
    assert out["phab_XEMBED"]["units"] == "%"
    assert out["chem_PTL"]["display_name"] == "Total phosphorus"
    # a person's name and units survive untouched
    assert out["phab_SINU"]["display_name"] == "Bendiness, as I call it"
    assert out["phab_SINU"]["units"] == "widgets"
    # nothing to resolve leaves the entry as it was
    assert out["zzz_unknown"]["display_name"] == "zzz_unknown"
    # the input is not mutated
    assert config["phab_XEMBED"]["display_name"] == "XEMBED"


def test_resolve_metric_config_tolerates_empty_input():
    assert mn.resolve_metric_config(None) == {}
    assert mn.resolve_metric_config({}) == {}


# --------------------------------------------------------------------------- #
# render-time resolution: an old session and a published version must read well
# --------------------------------------------------------------------------- #

def test_a_tile_built_from_a_stored_code_still_shows_a_name():
    tile = cs.tile_from_curve_rows(
        "phab_XEMBED", None,
        metric_entry={"display_name": "XEMBED"},   # what the agent path wrote
        review_entry=None, function_label="Hydraulics: Hyporheic connectivity",
    )
    assert tile["display_name"] == "Embeddedness"
    assert tile["short_name"] == "Embeddedness"
    assert tile["units"] == "%"
    assert tile["description"]
    assert tile["metric"] == "phab_XEMBED"   # the code is still the identity


def test_a_tile_keeps_a_name_someone_typed():
    tile = cs.tile_from_curve_rows(
        "phab_XEMBED", None, metric_entry={"display_name": "Fines in the bed", "units": "pct"},
        review_entry=None, function_label=None,
    )
    assert tile["display_name"] == "Fines in the bed"
    assert tile["units"] == "pct"


def test_the_gallery_tile_leads_with_the_name_and_keeps_the_code():
    tile = cs.tile_from_curve_rows(
        "phab_XEMBED", None, metric_entry={"display_name": "XEMBED"},
        review_entry=None, function_label="Hydraulics: Hyporheic connectivity",
    )
    page = cs.gallery_html([tile], title="t")
    assert '<span class="curve-tile-name">Embeddedness</span>' in page
    assert '<span class="curve-tile-code">phab_XEMBED</span>' in page
