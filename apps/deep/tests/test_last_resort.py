"""StreamCurves methodology 0.16, REF-17 (owner decision D19 of 2026-09-28): a
regional assessment whose function no other source supports scores it on EASI's
national screening method. DEEP names the basis in the bundle's words and offers
the same bank height ratio measurement under the metric id the bundle uses."""
from deep import reference_support as rs
from deep.metrics import computed


def test_the_adopted_easi_method_is_named_by_its_own_label():
    m = {"basis": "easi-screening-method", "criteriaBasis": "fixed"}
    assert rs.basis_of(m) == rs.BASIS_EASI_SCREENING
    assert rs.basis_label(m) == "Adopted from EASI's national screening method (provisional)"
    # the bundle's own label wins where it carries one
    assert rs.basis_label({**m, "basisLabel": "x"}) == "x"
    assert rs.is_fixed(m)


def test_the_bank_height_ratio_is_offered_under_the_last_resort_metric_id():
    ids = computed.computable_ids()
    assert "spring-bank-height-ratio" in ids
    assert computed._ADAPTERS["spring-bank-height-ratio"] is computed._ADAPTERS[
        "channel-and-floodplain-dynamics-bank-height-ratio-bhr"]


def test_the_corridor_natural_cover_sums_streamcats_riparian_classes(monkeypatch):
    """EASI's organic-matter supply input, as StreamCurves' carbon last resort asks for it."""
    from deep.metrics import computed as c

    class Ctx:
        comid = 123
        extras = {"streamcat_rp100": {n + "wsrp100": 10.0 for n in c._RP100_NATURAL}}

    got = c._ADAPTERS["spring-natural-riparian-cover"](Ctx())
    assert got is not None and abs(got.value - 70.0) < 1e-9
    Ctx.extras = {"streamcat_rp100": {n + "wsrp100": 20.0 for n in c._RP100_NATURAL}}
    assert c._ADAPTERS["spring-natural-riparian-cover"](Ctx()).value == 100.0   # capped
    Ctx.extras = {"streamcat_rp100": {}}
    assert c._ADAPTERS["spring-natural-riparian-cover"](Ctx()) is None
