"""Report labels for the two engines: the watershed basis, the reach id on
the high-resolution NHD, pending entries, and the CSV provenance columns."""
from __future__ import annotations

from sfari import report, scoring

HR_DELIN = {"delineation": {"comid": 5214461, "nhdplus_id": 750012345, "network": "nhdplus-hr",
                            "gnis_name": "Sugar Run", "huc8": "05060001",
                            "snapped_lat": 40.31125, "snapped_lon": -83.05615,
                            "drainage_area_sqkm": 4.19, "reach_length_ft": 500},
            "watershedBasis": "site-engine",
            "siteEngine": {"status": "ok", "engineVersion": "0.2.0"},
            "siteAnchor": {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461},
                           "routing": {"routedDistanceFt": 1240.0, "daRatio": 1.8,
                                       "declined": False}}}
V2_DELIN = {"delineation": {"comid": 9311402, "network": "nhdplus-v2", "gnis_name": "Wildcat Creek",
                            "snapped_lat": 39.12345, "snapped_lon": -84.51234,
                            "drainage_area_sqkm": 42.7, "reach_length_ft": 200},
            "watershedBasis": "nhdplus-v2-basin"}


def test_watershed_basis_label():
    assert report.watershed_basis_label(HR_DELIN) == "exact watershed (STAF site engine v0.2.0)"
    assert report.watershed_basis_label(V2_DELIN) == "NHDPlus V2 basin (StreamCat lookup engine)"
    assert report.watershed_basis_label({}) == "NHDPlus V2 basin (StreamCat lookup engine)"
    upgraded = {**V2_DELIN, "siteEngine": {"status": "ok", "engineVersion": "0.2.0"}}
    assert report.watershed_basis_label(upgraded) == (
        "NHDPlus V2 basin drawn, watershed metrics from the exact watershed "
        "(STAF site engine v0.2.0)")
    surrogate = {**V2_DELIN, "watershedBasis": "nhdplus-v2-basin-of-surrogate"}
    assert "nearest covered reach" in report.watershed_basis_label(surrogate)
    for d in (HR_DELIN, V2_DELIN, upgraded, surrogate):
        assert "—" not in report.watershed_basis_label(d)


def test_reach_id_uses_the_nhdplusid_on_hr_sites():
    assert report._reach_id_str(HR_DELIN["delineation"]) == "NHDPlusID 750012345"
    assert report._reach_id_str(V2_DELIN["delineation"]) == "COMID 9311402"
    assert report.field_forms_filename(HR_DELIN) == "sfari-field-forms-nhdplusid-750012345.pdf"
    assert report.field_forms_filename(V2_DELIN) == "sfari-field-forms-comid-9311402.pdf"


def test_pending_status_text():
    text, muted = report._value_or_status({"status": "pending"}, {})
    assert text == "Pending: STAF site engine running" and muted is True


def test_csv_carries_origin_and_describes():
    evidence = {"catchment-hydrology-road-density": {
        "metric_id": "catchment-hydrology-road-density", "value": 0.9,
        "value_text": "0.90 km/km2 road density (watershed)", "source": "EPA StreamCat rddens",
        "status": "ok", "origin": "streamcat",
        "anchor_label": "nearest covered reach, COMID 5214461, 1,240 ft downstream, DA ratio 1.8"}}
    csv = report.build_csv(HR_DELIN, {}, {}, evidence, scoring.score_assessment({}))
    lines = csv.splitlines()
    header = next(ln for ln in lines if ln.startswith("Category,"))
    assert header.endswith("Source,Origin,Describes,Note")
    row = next(ln for ln in lines if "Road density" in ln or "road-density" in ln
               or "0.90 km/km2" in ln)
    assert ",streamcat," in row and "nearest covered reach" in row
    assert "Watershed basis" in csv and "exact watershed (STAF site engine v0.2.0)" in csv
