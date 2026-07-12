"""Retry-merge of pulled desktop evidence (A3).

``pipeline.merge_pulled_evidence`` fixes the latent full-replace hazard in the
automatic-pull completion handler: a re-run (Retry) must replace/add the
automatic entries while preserving cross-section (Manning) entries, which are
distinguished only by their ``source`` string. Pure/offline.
"""
from sfari.pipeline import XS_MANNING_SOURCE, merge_pulled_evidence

# One attached cross-section entry (Manning source) + one auto-pulled entry.
XS_ENTRY = {
    "metric_id": "high-flow-dynamics-bed-mobilization-frequency",
    "value_text": "bed shear tau 0.215 lb/ft2",
    "field_value_text": "Bed shear tau 0.215 lb/ft2",
    "source": XS_MANNING_SOURCE,
    "status": "ok",
}
AUTO_STALE = {
    "metric_id": "catchment-hydrology-impervious-surface-area",
    "value_text": "10.0% impervious (watershed)",
    "source": "EPA StreamCat pctimp2019ws",
    "status": "ok",
}


def _existing():
    return {
        "high-flow-dynamics-bed-mobilization-frequency": dict(XS_ENTRY),
        "catchment-hydrology-impervious-surface-area": dict(AUTO_STALE),
    }


def _repull():
    # A fresh pull returns automatic entries only (never the xscalc metricIds):
    # one metric now has a newer value, and a previously-missing one is now present.
    return {
        "catchment-hydrology-impervious-surface-area": {
            "metric_id": "catchment-hydrology-impervious-surface-area",
            "value_text": "12.3% impervious (watershed)",
            "source": "EPA StreamCat pctimp2019ws",
            "status": "ok",
        },
        "catchment-hydrology-road-density": {
            "metric_id": "catchment-hydrology-road-density",
            "value_text": "1.23 km/km2 road density (watershed)",
            "source": "EPA StreamCat rddens",
            "status": "ok",
        },
    }


def test_merge_preserves_cross_section_replaces_auto():
    merged = merge_pulled_evidence(_existing(), _repull())
    # The Manning cross-section entry survives the re-pull untouched.
    assert merged["high-flow-dynamics-bed-mobilization-frequency"] == XS_ENTRY
    assert merged["high-flow-dynamics-bed-mobilization-frequency"]["source"] == XS_MANNING_SOURCE
    # The automatic entry is replaced with the fresh value...
    assert merged["catchment-hydrology-impervious-surface-area"]["value_text"] == \
        "12.3% impervious (watershed)"
    # ...and a newly-available automatic entry is added.
    assert merged["catchment-hydrology-road-density"]["value_text"] == \
        "1.23 km/km2 road density (watershed)"


def test_merge_does_not_mutate_arguments():
    existing, pulled = _existing(), _repull()
    merge_pulled_evidence(existing, pulled)
    assert existing["catchment-hydrology-impervious-surface-area"]["value_text"] == \
        "10.0% impervious (watershed)"          # original still stale
    assert "catchment-hydrology-road-density" not in existing


def test_first_run_equals_pulled():
    # Nothing to preserve yet: merge behaves like the old full-replace.
    pulled = _repull()
    assert merge_pulled_evidence({}, pulled) == pulled


def test_tolerates_none_and_non_dict_entries():
    assert merge_pulled_evidence(None, None) == {}
    # A stray non-dict existing entry is ignored, not crashed on.
    merged = merge_pulled_evidence({"junk": None}, {"a": {"source": "x"}})
    assert merged == {"a": {"source": "x"}}
