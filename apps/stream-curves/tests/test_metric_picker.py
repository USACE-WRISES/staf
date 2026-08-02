"""Unit tests for the unified metric picker (streamcurves/metric_picker.py)."""
from __future__ import annotations

import re

import pandas as pd

from streamcurves import metric_picker as mp
from streamcurves.metric_map import metric_map_default_codes, metric_map_entries


def _table():
    # StreamStats included so the named set spans every source; MMW omitted
    # (key-gated) — its absence must not change the NRSA/StreamCat rows.
    from streamcurves.datasources.streamstats import ss_core_bcs
    return mp.build_metric_picker_table(streamstats=ss_core_bcs())


def test_columns_are_the_declared_schema():
    t = _table()
    assert list(t.columns) == mp.PICKER_COLUMNS


def test_no_named_label_shows_the_nan_bug():
    # The old picker rendered "XBKA (nan)"; the builder must never emit "nan".
    t = _table()
    named = t[t["named"]]
    assert len(named) > 0
    for col in ("name", "units"):
        assert not named[col].astype(str).str.lower().str.contains("nan").any()


def test_xbka_resolves_to_a_readable_name_and_function():
    t = _table()
    row = t[t["code"] == "phab_XBKA"]
    assert len(row) == 1
    r = row.iloc[0]
    assert r["name"] == "Bank angle"
    assert r["units"] == "degrees"
    assert r["source"] == "NRSA"
    assert "Channel and floodplain dynamics" in r["functions"]
    assert "Geomorphology" in r["disciplines"]
    assert bool(r["recommended"]) is True
    assert bool(r["named"]) is True


def test_streamcat_rows_are_all_named():
    t = _table()
    sc = t[t["source_key"] == "streamcat"]
    assert len(sc) >= 30
    assert bool(sc["named"].all())


def test_named_nrsa_are_exactly_the_crosswalked_codes():
    # NRSA rows are named only when metric_map.yaml gives them a label.
    t = _table()
    mm_codes = {
        str(c) for c in metric_map_entries()
        .loc[metric_map_entries()["source"] == "nrsa", "code"]
    }
    named_nrsa = set(t[(t["source_key"] == "nrsa") & (t["named"])]["code"])
    assert named_nrsa <= mm_codes
    # and the long tail stays unnamed
    unnamed = t[~t["named"]]
    assert (unnamed["source_key"] == "nrsa").all()
    assert len(unnamed) > 700


def test_recommended_matches_metric_map_defaults():
    t = _table()
    rec = set(t[t["recommended"]]["code"])
    expected = set(metric_map_default_codes("nrsa")) | set(
        metric_map_default_codes("streamcat")) | set(
        metric_map_default_codes("streamstats"))
    # every recommended row is a crosswalk default, and the NRSA/StreamCat
    # defaults that exist in the catalogs are all flagged.
    assert rec <= expected
    for src in ("nrsa", "streamcat"):
        present = set(t[t["source_key"] == src]["code"])
        for c in metric_map_default_codes(src):
            if c in present:
                assert c in rec


def test_default_selection_covers_all_twenty_functions():
    t = _table()
    codes = mp.default_selected_codes(t)
    summ = mp.coverage_summary(codes)
    assert summ["total"] == 20
    assert summ["n_covered"] == 20
    assert all(cov == tot for cov, tot in summ["per_discipline"].values())


def test_coverage_by_function_attributes_metrics_by_label():
    fn = mp.coverage_by_function(["phab_XBKA"], label_of=lambda c: "Bank angle")
    assert fn.get("Channel and floodplain dynamics") == ["Bank angle"]


def test_empty_selection_covers_nothing():
    summ = mp.coverage_summary([])
    assert summ["n_covered"] == 0
    assert summ["total"] == 20


def test_split_selection_maps_codes_back_to_sources():
    t = _table()
    picked = ["phab_XBKA", "pctimp2019", "phab_XBKA"]  # NRSA + StreamCat + dup
    ss_codes = list(t[t["source_key"] == "streamstats"]["code"])[:1]
    split = mp.split_selection_by_source(picked + ss_codes, t)
    assert split["nrsa"] == ["phab_XBKA"]           # deduped
    assert "pctimp2019" in split["streamcat"]
    assert split["streamstats"] == ss_codes


def test_split_ignores_unknown_codes():
    t = _table()
    split = mp.split_selection_by_source(["not_a_real_code"], t)
    assert split == {"nrsa": [], "streamcat": [], "streamstats": [], "mmw": []}


def test_units_split_off_a_trailing_parenthetical():
    assert mp._split_units("Bank angle (degrees)") == ("Bank angle", "degrees")
    assert mp._split_units("EPT taxa richness") == ("EPT taxa richness", "")
    assert mp._split_units(None) == ("", "")


def test_codes_for_columns_reverse_maps_pulled_columns():
    # Seeds the wizard selection from a restored project's dataset: bare codes
    # for NRSA/StreamCat, ss_ prefix for StreamStats, non-picker columns ignored.
    t = _table()
    ss_code = list(t[t["source_key"] == "streamstats"]["code"])[0]
    cols = ["site_id", "lat", "phab_XBKA", "pctimp2019", f"ss_{ss_code}", "DA_mi2"]
    got = mp.codes_for_columns(cols, t)
    assert got == {"phab_XBKA", "pctimp2019", ss_code}
    # a real DataFrame's .columns (pandas Index) must work too
    df = pd.DataFrame(columns=cols)
    assert mp.codes_for_columns(df.columns, t) == got


def test_codes_for_columns_empty_cases():
    t = _table()
    assert mp.codes_for_columns([], t) == set()
    assert mp.codes_for_columns(["site_id"], t) == set()
    assert mp.codes_for_columns(["phab_XBKA"], pd.DataFrame(columns=mp.PICKER_COLUMNS)) == set()


def test_codes_for_columns_matches_mmw_keys():
    t = mp.build_metric_picker_table(
        nrsa=pd.DataFrame(columns=["name", "label", "category", "units"]),
        streamcat=pd.DataFrame(columns=["name", "label", "domain", "default"]),
        mmw={"mmw_pct_forest": {"label": "Forest cover (%)"}},
    )
    assert mp.codes_for_columns(["mmw_pct_forest"], t) == {"mmw_pct_forest"}


def test_mmw_rows_appear_when_a_catalog_is_supplied():
    t = mp.build_metric_picker_table(
        nrsa=pd.DataFrame(columns=["name", "label", "category", "units"]),
        streamcat=pd.DataFrame(columns=["name", "label", "domain", "default"]),
        mmw={"mmw_pct_forest": {"label": "Forest cover (%)"}},
    )
    row = t[t["code"] == "mmw_pct_forest"].iloc[0]
    assert row["source"] == "MMW"
    assert row["name"] == "Forest cover"
    assert row["units"] == "%"
    assert bool(row["named"]) is True


def test_streamcat_codes_resolve_through_their_area_of_interest_suffix():
    """StreamCat compiles to a suffixed column; a bare-code lookup misses them all.

    The picker seeds a reopened project's selection from its dataset columns, so
    when this missed ``bfi`` in a column named ``bfiws`` every StreamCat box came
    back unchecked over a project that had pulled them -- and the step-4 coverage
    panel disagreed with the step-5 Compile panel, which does strip the suffix.
    """
    t = _table()
    for suffix in ("ws", "cat", "wsrp100", "catrp100"):
        codes = mp.codes_for_columns([f"bfi{suffix}", f"pctimp2019{suffix}"], t)
        assert codes == {"bfi", "pctimp2019"}, f"suffix {suffix!r} did not resolve"

    # Bare codes still resolve, and matching is forward-only: a column is never
    # credited to a code just because it happens to end in ws/cat.
    assert mp.codes_for_columns(["bfi"], t) == {"bfi"}
    assert mp.codes_for_columns(["xyzws", "pctcat", "nonsensecat"], t) == set()


def test_selection_rebuilds_from_a_real_published_dataset():
    """Reopening a project has to recover what it pulled from its columns alone.

    The wizard writes the per-source split only on the step-4 "Next" click, so
    entering Compile from the stage strip reported "0 StreamCAT + 0 NRSA" over a
    21-metric project. This is the reconstruction that replaces it.

    Asserted as invariants rather than fixed counts: this reads the real published
    assessment, whose metric list changes each time it is republished.
    """
    import json
    from pathlib import Path

    from streamcurves import session_io as sio

    payload = (
        Path(__file__).resolve().parents[2]
        / "library" / "assessments" / "northeastern-highlands" / "v2"
        / "session.streamcurves.json"
    )
    if not payload.exists():
        pytest.skip(f"library assessment not present: {payload}")

    fields = sio.decode_session_fields(json.loads(payload.read_text(encoding="utf-8")))
    cols = [str(c) for c in fields["data"].columns]
    t = mp.build_metric_picker_table()
    codes = mp.codes_for_columns(cols, t)

    assert codes, "reopening the published dataset recovered no metrics at all"
    split = mp.split_selection_by_source(set(codes), t)
    assert sum(len(v) for v in split.values()) == len(codes), "a code lost its source"

    # Every StreamCat column the dataset carries must come back as a checked code;
    # this is what the bare-code lookup used to drop.
    suffixed = [c for c in cols if re.search(r"(wsrp100|catrp100|ws|cat)$", c.lower())]
    if suffixed:
        assert split["streamcat"], f"StreamCat columns present but unresolved: {suffixed}"
