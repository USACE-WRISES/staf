"""The Validate stage: parsing the field-data CSV, scoring bands, and the exact
library call sequence the page makes (record, validated state, certify)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import documented_exclusions
from streamcurves import curve_svg as cs
from streamcurves import library as lib
from streamcurves import session_io as sio
from streamcurves.deep_export import build_deep_assessment_bundle
from views.validate_page import _score_band, parse_field_data

REGION = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}


# --------------------------------------------------------------------------- #
# parse_field_data (pure)
# --------------------------------------------------------------------------- #
def test_parse_matches_header_aliases_and_metric_codes_case_insensitively():
    df = pd.DataFrame({
        "Station": ["s1", "s1", "s2", "s3"],
        "Metric_Code": ["phab_XEMBED", "chem_PTL", "PHAB_XEMBED", "unknown_x"],
        "Result": [40.0, "n/a", 70.0, 1.0],
    })
    out = parse_field_data(df, {"phab_XEMBED": {}, "chem_PTL": {}})
    assert out["values"]["phab_XEMBED"] == [40.0, 70.0]
    assert "chem_PTL" not in out["values"], "a non-numeric value is dropped"
    assert out["n_dropped"] == 1
    assert out["unmatched"] == ["unknown_x"]
    assert out["n_sites"] == 2
    assert out["sites"]["phab_XEMBED"] == ["s1", "s2"]


def test_parse_works_without_a_site_column():
    df = pd.DataFrame({"metric": ["m1", "m1"], "value": [1.0, 2.0]})
    out = parse_field_data(df, {"m1": {}})
    assert out["values"]["m1"] == [1.0, 2.0]
    assert out["n_sites"] == 0


def test_parse_names_the_missing_columns():
    with pytest.raises(ValueError, match="value"):
        parse_field_data(pd.DataFrame({"metric": ["a"]}), {})
    with pytest.raises(ValueError, match="metric"):
        parse_field_data(pd.DataFrame({"value": [1.0]}), {})


def test_score_bands_follow_the_deep_contract():
    lo, hi = cs.DEEP_INDEX_BANDS
    assert _score_band(hi + 0.01, (lo, hi)) == "good"
    assert _score_band(hi, (lo, hi)) == "good"
    assert _score_band((lo + hi) / 2, (lo, hi)) == "fair"
    assert _score_band(lo - 0.01, (lo, hi)) == "poor"


# --------------------------------------------------------------------------- #
# The library call sequence the page makes
# --------------------------------------------------------------------------- #
@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    return root


def _bundle() -> dict:
    rows = {
        "perImperv": {
            "metric": "perImperv",
            "curve_status": "complete",
            "stratum": np.nan,
            "curve_points": pd.DataFrame(
                {"metric_value": [0, 9, 25, 75], "index_score": [1, 0.7, 0.3, 0]}
            ),
        }
    }
    mapping = pd.DataFrame(
        {"metric_key": ["perImperv"], "discipline": ["Hydrology"],
         "function_label": ["Catchment hydrology"], "sort_order": [1]})
    return build_deep_assessment_bundle(
        rows, mapping, {},
        {"region": REGION, "functionCoverageExceptions": documented_exclusions()})


def test_the_record_then_certify_sequence_flips_every_visible_state(libroot):
    payload = sio.dump_session_fields({"session_name": "ecbp"}, session_name="ecbp")
    version = lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION},
                                  payload, _bundle())
    assert lib.version_validation_state("ecbp", version) == "unvalidated"

    # the page's record flow
    lib.add_validation_record("ecbp", version,
                              {"method": "field data overlay", "checker": "jess",
                               "outcome": "match", "nSites": 12,
                               "nMetricsMatched": 1},
                              actor="jess", note="two case-study reaches")
    n = len(lib._validation_records_for("ecbp", version))
    assert n == 1
    lib.set_version_validation("ecbp", version, "validated", {"n_records": n}, "jess")
    assert lib.version_validation_state("ecbp", version) == "validated"
    entry = next(a for a in lib.list_assessments() if a["assessmentId"] == "ecbp")
    assert entry["validationState"] == "validated"

    # the page's certify flow
    lib.set_version_status("ecbp", version, "certified", "jess",
                           note="Certified after field-data validation.")
    assert lib.version_status("ecbp", version) == "certified"


def test_validated_requires_a_record_first(libroot):
    payload = sio.dump_session_fields({"session_name": "ecbp"}, session_name="ecbp")
    version = lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION},
                                  payload, _bundle())
    with pytest.raises(ValueError, match="validation record"):
        lib.set_version_validation("ecbp", version, "validated", None, "jess")


def test_the_approve_draft_sequence_records_the_review(libroot):
    """The page's Approve as Preliminary flow: a draft (automation output)
    becomes preliminary in place, on the audited history, under the reviewer's
    name."""
    payload = sio.dump_session_fields({"session_name": "ecbp"}, session_name="ecbp")
    version = lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION},
                                  payload, _bundle(), status="draft")
    assert lib.version_status("ecbp", version) == "draft"
    lib.set_version_status("ecbp", version, "preliminary", "jess",
                           note="Reviewed in StreamCurves; approved as preliminary.")
    assert lib.version_status("ecbp", version) == "preliminary"
    hist = lib.read_status("ecbp")["history"]
    assert [r["status"] for r in hist] == ["draft", "preliminary"]
    assert hist[-1]["actor"] == "jess"


def test_the_page_declares_the_approve_flow_and_the_certify_rebake():
    """Source scan (the stagebar idiom): the approve block exists, both status
    writers rebake DEEP (status changes move bake eligibility), and every
    effect is guarded."""
    text = (Path(__file__).resolve().parents[1] / "views" /
            "validate_page.py").read_text(encoding="utf-8")
    assert 'ns("approve_block")' in text
    assert '"preliminary"' in text and "Approve as Preliminary" in text
    approve = text[text.index("def _approve("):]
    assert "_rebake_and_toast" in approve[:600]
    certify = text[text.index("def _certify("):]
    assert "_rebake_and_toast" in certify[:900]
    import re
    effects = re.findall(r"@reactive\.effect\s*\n(.*?)def ", text, re.S)
    assert effects and all("@guard(" in d for d in effects)
