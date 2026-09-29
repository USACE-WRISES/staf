"""A field-measured cross section entered in DEEP is valid by definition (WP-R6c, deliverable 4).

DEEP rates the bank-height and entrenchment ratios of a site by the assessment's own metric
curves (``deep.curves``), whatever produced the value: a field entry (origin ``field``) or the
desktop prefill the vendored site engine's EASI extract computes from the 3DEP sections
(``metrics.computed._reach_geom``, origin ``desktop``). A hand-entered section is never
withheld. The desktop prefill alone reads the extract's ``cross_section_quality`` (EASI's
``geomorph.py`` byte for byte, synced by ``libs/site_engine/scripts/sync_engine_extracts.py``):
where EASI v2 withholds a geometry rating on the K2b record (a weak reach median, or a ratio
outside its physical range), DEEP offers no 3DEP value and asks for a measured one. DEEP reads no EASI
method file: EASI's ``entrenchment`` curve set and bank-height bands never reach a DEEP
rating, and a refit of them changes nothing here; what reaches DEEP through re-vendoring is
the geometry arithmetic of the extract, which these tests hold equal to EASI's source when
the source is present.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from deep import curves
from deep.models import MeasuredValue

_REPO = Path(__file__).resolve().parents[3]
_EASI_GEOMORPH = _REPO / "apps" / "easi" / "easi" / "geomorph.py"
_EXTRACT = Path(__file__).resolve().parents[1] / "deep" / "_vendor" / "site_engine" / "_extracted" / "geomorph.py"
_DEEP_PKG = Path(__file__).resolve().parents[1] / "deep"
BHR = "channel-and-floodplain-dynamics-bank-height-ratio-bhr"
ER = "floodplain-connectivity-entrenchment-ratio-er"


def _metric(metric_id: str, points) -> dict:
    return {"metricId": metric_id, "metricName": metric_id, "curve": {"points": [{"x": x, "y": y} for x, y in points]}}


def test_a_field_entered_ratio_is_rated_by_the_assessments_curve_never_withheld():
    # a lower-is-better bank-height ratio curve and a higher-is-better entrenchment curve
    bhr_spec = _metric(BHR, [(1.0, 1.0), (1.3, 0.69), (1.5, 0.39), (2.0, 0.0)])
    er_spec = _metric(ER, [(1.0, 0.0), (1.4, 0.39), (2.2, 0.69), (3.0, 1.0)])
    for origin in ("field", "desktop"):
        at_cap = MeasuredValue(metric_id=BHR, value=2.0, origin=origin, source="survey" if origin == "field" else "3dep")
        assert curves.metric_index(at_cap, bhr_spec) == pytest.approx(0.0)        # severe incision, rated Poor, never None
        incised = MeasuredValue(metric_id=BHR, value=1.4, origin=origin)
        assert curves.metric_index(incised, bhr_spec) == pytest.approx(0.54)
        entrenched = MeasuredValue(metric_id=ER, value=1.2, origin=origin)
        assert curves.metric_index(entrenched, er_spec) == pytest.approx(0.195)
        connected = MeasuredValue(metric_id=ER, value=2.6, origin=origin)
        assert curves.metric_index(connected, er_spec) == pytest.approx(0.845)
    # a ratio beyond the curve's domain is clamped, not refused: nothing in DEEP withholds a value
    beyond = MeasuredValue(metric_id=BHR, value=2.7, origin="field")
    assert curves.metric_index(beyond, bhr_spec) == pytest.approx(0.0)
    # an unentered or NA metric is unscored, which is the only way a geometry metric is left out
    assert curves.metric_index(None, bhr_spec) is None
    assert curves.metric_index(MeasuredValue(metric_id=BHR, value=None, na=True), bhr_spec) is None
    # a field edit clears the engine flag, so the train/serve pairing rule never touches a hand-entered section
    edited = MeasuredValue.from_dict({"metricId": BHR, "value": 1.6, "origin": "field", "engine": True})
    assert edited.engine is False and curves.engine_pairing_advisory(edited, bhr_spec) is None


def test_only_the_desktop_prefill_reads_the_cross_section_quality_record():
    """The K2b record lives in the extract (EASI's geomorph); in DEEP only the 3DEP prefill of
    the two ratios reads it (``metrics/computed.py``), so the scoring layer never withholds a
    value that was entered, and the site engine's cross-section metrics never gate on it."""
    callers = []
    for path in sorted(_DEEP_PKG.rglob("*.py")):
        if "_vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "cross_section_quality" in text or "crossSectionQuality" in text or "low_quality" in text:
            callers.append(path.relative_to(_DEEP_PKG).as_posix())
    assert callers == ["metrics/computed.py"]
    extract = _EXTRACT.read_text(encoding="utf-8")
    assert "def cross_section_quality" in extract and "QUALITY_RULES = \"K2b\"" in extract
    engine_xsection = _DEEP_PKG / "_vendor" / "site_engine" / "metrics" / "xsection.py"
    assert "cross_section_quality" not in engine_xsection.read_text(encoding="utf-8")


@pytest.mark.skipif(not _EASI_GEOMORPH.is_file(), reason="EASI source not present")
def test_the_vendored_extract_is_easis_geometry_byte_for_byte():
    """The geometry arithmetic DEEP's desktop prefill runs (bankfull stage, low bank, the cap,
    the two ratios) is EASI's own module, so a section reads the same in both apps; a refit of
    EASI's curves reaches nothing here, and a change to the arithmetic reaches DEEP only through
    the sanctioned sync and vendor scripts."""
    source = _EASI_GEOMORPH.read_bytes().replace(b"\r\n", b"\n")
    extract = _EXTRACT.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(source).hexdigest() == hashlib.sha256(extract).hexdigest(), \
        "re-run libs/site_engine/scripts/sync_engine_extracts.py and apps/deep/scripts/vendor_site_engine.py"
    info = json.loads((_EXTRACT.parent / "EXTRACTS_INFO.json").read_text(encoding="utf-8"))
    assert info["modules"]["geomorph.py"]["source"] == "apps/easi/easi/geomorph.py"


def test_deep_reads_no_easi_method_file():
    """DEEP's geometry ratings come from its assessment bundles (each version's own curves); no
    module reads EASI's screening-methods.json or reference-curves.json, so EASI's entrenchment
    set and bank-height bands are not DEEP's curves and their refit does not change a DEEP rating."""
    readers = []
    for path in sorted(_DEEP_PKG.rglob("*.py")):
        if "_vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "screening-methods.json" in text or "reference-curves.json" in text:
            readers.append(str(path.relative_to(_DEEP_PKG)))
    assert readers == []


def test_the_prefill_steps_aside_where_easi_v2_withholds_the_rating():
    """A weak reach median (here bankfull extrapolated outside the Bieger fit) withholds both
    ratios, as EASI v2 does; a ratio outside its physical range withholds that ratio only; a
    sound reach prefills both. The reasons ride on the context."""
    from deep.metrics import computed as c

    class Ctx:
        def __init__(self, geom):
            self.extras = {"reach_geomorph": geom}

    reach = {"n": 9, "bank_height_ratio": {"n": 9}, "entrenchment_ratio": {"n": 9}}
    sound = {"bank_height_ratio": 1.2, "entrenchment_ratio": 2.5, "reach": reach}
    ctx = Ctx(sound)
    assert c._ADAPTERS[BHR](ctx).value == 1.2 and c._ADAPTERS[ER](ctx).value == 2.5
    assert c._ADAPTERS["spring-bank-height-ratio"](ctx).value == 1.2
    assert "xs_prefill_withheld" not in ctx.extras

    weak = Ctx({**sound, "bankfull_extrapolated": True})
    assert c._ADAPTERS[BHR](weak) is None and c._ADAPTERS[ER](weak) is None
    assert "extrapolated" in weak.extras["xs_prefill_withheld"]["bhr"]
    assert "extrapolated" in weak.extras["xs_prefill_withheld"]["er"]

    impossible = Ctx({**sound, "bank_height_ratio": 0.0})
    assert c._ADAPTERS[BHR](impossible) is None
    assert c._ADAPTERS[ER](impossible).value == 2.5
    assert "at or below zero" in impossible.extras["xs_prefill_withheld"]["bhr"]
