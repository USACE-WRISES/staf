"""The field-method text a published bundle carries (``methodContext``).

Every metric a regional build can score has an entry, each entry names a source
that is listed with a citation, the text fits what the DEEP worksheet prints and
reads as plain copy (no em dash, no semicolon), and the exporter writes it into
the bundle for a pressure-screen build only, so a legacy rebuild keeps its digest.
"""
from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import deep_export, field_methods, fixed_criteria
from streamcurves import pressure_evidence as pe
from streamcurves import regional_agent as ra

REGIONAL_METRICS = [
    "bent_EPT_NTAX", "bent_HPRIME", "bent_TOLRPIND", "bent_TOTLNTAX", "chem_CHLA", "chem_COND",
    "chem_NTL_DISS", "chem_PH", "chem_PTL", "chem_TURB", "fish_NAT_TOTLNTAX", "phab_BFWD_RAT",
    "phab_LRBS_use", "phab_LSUB_DMM", "phab_LWDeqVolM100", "phab_PCT_FAST", "phab_PCT_SAFN",
    "phab_RP100_cm", "phab_SINU", "phab_XBKA", "phab_XBKF_H", "phab_XCDENMID", "phab_XCMGW",
    "phab_XEMBED", "phab_XFC_NAT", "bfiws", "pctwet2019ws"]


def test_every_metric_a_regional_build_scores_has_an_entry():
    for mk in REGIONAL_METRICS + list(fixed_criteria.metric_keys()):
        assert field_methods.method_context(mk), mk
    # and every metric with a curated direction in the archive
    directions = ra.load_directions()
    curated = [k for k, v in (directions.get("metrics") or {}).items()
               if isinstance(v, dict) and v.get("higher_is_better") is not None]
    missing = [k for k in curated if not field_methods.method_context(k)
               and k in REGIONAL_METRICS]
    assert not missing


def test_every_entry_names_a_listed_source():
    doc = field_methods.load()
    sources = doc["sources"]
    for key, entry in doc["metrics"].items():
        assert entry.get("source") in sources, key
        assert entry.get("where"), key
    for key, src in sources.items():
        assert src.get("citation"), key
    manual = sources["nrsa-fom-wadeable-2023"]
    assert "EPA-841-B-22-006" in manual["citation"] and manual["checked"]


def test_the_text_fits_the_worksheet_and_reads_as_plain_copy():
    for key in field_methods.load()["metrics"]:
        text = field_methods.method_context(key)
        assert len(text) <= field_methods.WORKSHEET_LIMIT, (key, len(text))
        assert "—" not in text and "–" not in text and ";" not in text, key
        assert text.endswith("."), key
        assert "  " not in text and "\n" not in text, key
    assert field_methods.reach_layout().startswith("Lay out a sampling reach 40 times")


def test_the_watershed_suffix_is_tolerated():
    assert field_methods.method_context("bfiws") == field_methods.method_context("bfi" + "ws")
    assert field_methods.method_context("not_a_metric") == ""
    assert field_methods.annotations(["phab_XEMBED", "not_a_metric"]) == {
        "phab_XEMBED": {"methodContext": field_methods.method_context("phab_XEMBED")}}
    assert field_methods.source_of("phab_XEMBED")["citation"].startswith("USEPA. 2023.")


def test_a_pressure_build_carries_the_text_and_a_legacy_build_does_not():
    rows = {"phab_XEMBED": {"metric": "phab_XEMBED", "curve_status": "complete", "stratum": "",
                            "curve_points": pd.DataFrame({"metric_value": [0, 50],
                                                          "index_score": [1.0, 0.0]})}}
    mapping = pd.DataFrame({"metric_key": ["phab_XEMBED"], "discipline": ["Hydraulics"],
                            "function_label": ["Hyporheic connectivity"], "sort_order": [1]})
    legacy = deep_export.build_deep_assessment_bundle(rows, mapping, {}, {})
    assert legacy["metricsByFunction"][0]["metrics"][0]["methodContext"] == ""

    annotations = pe.reference_annotations({}, ["phab_XEMBED"])
    assert annotations["phab_XEMBED"]["methodContext"] \
        == field_methods.method_context("phab_XEMBED")
    built = deep_export.build_deep_assessment_bundle(
        rows, mapping, {}, {"metricAnnotations": annotations})
    entry = built["metricsByFunction"][0]["metrics"][0]
    assert entry["methodContext"].startswith("At five evenly spaced points")
    fixed = pe.fixed_annotations(["pctimp2019ws"])
    assert "Nothing is measured in the field." in fixed["pctimp2019ws"]["methodContext"]
    assert "methodContext" in pe.SESSION_ANNOTATION_KEYS


@pytest.mark.parametrize("key", ["phab_LRBS_use", "phab_LSUB_DMM", "phab_RP100_cm"])
def test_a_computed_metric_says_it_is_computed(key):
    assert "NRSA physical habitat calculations" in field_methods.method_context(key)
