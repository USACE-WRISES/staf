"""The engine vocabulary: immutable tokens, the decided display names, and
plain labels (no em dash, no semicolon)."""
from __future__ import annotations

from site_engine import ENGINE_ID, ENGINE_VERSION, naming


def test_tokens_are_immutable_identifiers():
    assert naming.SITE_ENGINE_TOKEN == "site-engine" == ENGINE_ID
    assert naming.STREAMCAT_TOKEN == "streamcat"
    assert naming.USER_OVERRIDE_TOKEN == "user-override"


def test_display_names_and_labels():
    assert naming.display_name("site-engine") == "STAF site engine"
    assert naming.display_name("streamcat") == "StreamCat lookup engine"
    assert naming.display_name("odd") == "odd"
    assert naming.engine_label() == f"STAF site engine v{ENGINE_VERSION}"
    assert naming.engine_label("0.9.9") == "STAF site engine v0.9.9"
    assert (naming.source_label("site-engine", version="0.2.0",
                                detail="exact watershed")
            == "STAF site engine v0.2.0, exact watershed")
    assert (naming.source_label("streamcat", detail="NHDPlus V2 COMID 5215053")
            == "StreamCat lookup engine, NHDPlus V2 COMID 5215053")
    assert naming.is_engine("site-engine") and naming.is_engine("site-engine v0.2.0")
    assert not naming.is_engine("streamcat") and not naming.is_engine(None)


def test_anchor_label():
    assert naming.anchor_label(None) == ""
    assert naming.anchor_label({"anchorKind": "v2Direct"}) == ""
    routed = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461},
              "routing": {"routedDistanceFt": 1240.4, "daRatio": 1.8,
                          "declined": False}}
    assert (naming.anchor_label(routed)
            == "nearest covered reach, COMID 5214461, 1,240 ft downstream, DA ratio 1.8")
    declined = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 1},
                "routing": {"declined": True, "daRatio": 42.0}}
    assert naming.anchor_label(declined) == "no covered reach within the substitution limit"


def test_labels_are_plain_sentences():
    labels = list(naming.DISPLAY_NAMES.values()) + [
        naming.engine_label(), naming.source_label("streamcat", detail="x"),
        naming.anchor_label({"anchorKind": "hrSurrogate",
                             "scoredReach": {"comid": 1},
                             "routing": {"routedDistanceFt": 10, "daRatio": 2}})]
    for s in labels:
        assert "—" not in s and ";" not in s
