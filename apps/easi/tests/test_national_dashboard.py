"""The condition dashboard's builders: the numbers (spread, histogram
quantiles, shares), the choices, the box-plot and share-bar markup, the
sensitivity ordering, the state comparison, the CSV, and the copy."""
from __future__ import annotations

import csv
import io

from easi.national import dashboard as d


def _hist(**scores):
    hist = [0] * 16
    for key, count in scores.items():
        hist[int(key[1:])] = count
    return hist


def _fn(bands, unrated=0, tiers=None):
    rated = sum(bands)
    hist = _hist(s3=bands[0], s8=bands[1], s13=bands[2])
    mean = round((3 * bands[0] + 8 * bands[1] + 13 * bands[2]) / rated, 3) if rated else None
    return {"rated": rated, "unrated": unrated, "bands": list(bands), "mean": mean, "hist": hist,
            "tiers": tiers or {"screening-proxy": rated}}


def _index(p50, bands, n=1000):
    return {"n": n, "mean": round(p50 - 0.01, 4), "sd": 0.09, "min": 0.2, "max": 0.87, "p5": p50 - 0.17,
            "p10": p50 - 0.13, "p25": p50 - 0.06, "p50": p50, "p75": p50 + 0.06, "p90": p50 + 0.11,
            "p95": p50 + 0.13, "bands": list(bands), "hist": [0] * 20}


def _group(n, eci, catchment, light, provisional=30, tier2=None):
    return {"n": n, "provisional": provisional, "complete": n - 50, "tier2": n if tier2 is None else tier2,
            "indices": {"eci": _index(eci, [10, 800, 190], n), "physical": _index(eci - 0.03, [20, 700, 280], n),
                        "chemical": None, "biological": _index(eci + 0.02, [5, 600, 395], n)},
            "functions": {"catchment-hydrology": catchment, "light-thermal-regime": light}}


def _stats():
    return {
        "schema_version": 1, "generated": "2026-09-11T22:00:00Z", "vintage": "2026.09", "reaches": 1000,
        "measures": {"indices": [{"id": "eci", "name": "Ecosystem Condition Index"}],
                     "functions": [{"id": "catchment-hydrology", "name": "Catchment hydrology", "category": "Hydrology"},
                                   {"id": "light-thermal-regime", "name": "Light & thermal regime",
                                    "category": "Physicochemistry"}],
                     "index_edges": [0.39, 0.69], "score_edges": [5, 10], "score_max": 15},
        "states": {"VA": {"name": "Virginia", "n_total": 1000, "n_scored": 900, "coverage": 0.9},
                   "NC": {"name": "North Carolina", "n_total": 5000, "n_scored": 100, "coverage": 0.02},
                   "WV": {"name": "West Virginia", "n_total": 500, "n_scored": 300, "coverage": 0.6},
                   "MD": {"name": "Maryland", "n_total": 800, "n_scored": 0, "coverage": 0.0}},
        "groups": {
            "US": _group(1000, 0.61, _fn([100, 180, 700], unrated=20), _fn([0, 0, 1000], tiers={"observed": 1000})),
            "VA": _group(900, 0.62, _fn([90, 180, 630]), _fn([0, 0, 900])),
            "NC": _group(100, 0.55, _fn([10, 30, 60]), _fn([0, 0, 100])),
            "WV": _group(300, 0.58, _fn([60, 90, 150]), _fn([0, 0, 300])),
        },
    }


def test_spread_quantiles_and_shares():
    assert d.spread([0, 0, 1000]) == 0.0 and d.spread([5, 5, 5]) == 1.0 and d.spread([]) is None
    assert d.spread([92, 4, 4]) < d.LOW_SPREAD < d.spread([88, 6, 6])   # the chip's edge: about nine in ten
    hist = _hist(s3=1, s8=1, s13=3)
    assert (d.hist_quantile(hist, 5), d.hist_quantile(hist, 25), d.hist_quantile(hist, 50)) == (3.0, 8.0, 13.0)
    assert d.hist_quantile([0] * 16, 50) is None
    assert d.shares([1, 1, 2], 4) == (0.125, 0.125, 0.25, 0.5) and d.shares([0, 0, 0]) == (0.0, 0.0, 0.0, 0.0)


def test_scope_and_measure_choices():
    scopes = d.scope_choices(_stats())
    assert list(scopes) == ["US", "NC", "VA", "WV"]              # everything first, then states by name; MD has no reaches
    assert scopes["US"] == "All screened reaches (1,000)" and scopes["VA"] == "Virginia (900 reaches, 90% screened)"
    measures = d.measure_choices(_stats())
    assert list(measures) == ["Condition indices", "Hydrology", "Physicochemistry"]
    assert list(measures["Condition indices"]) == ["eci", "physical", "chemical", "biological"]
    assert measures["Physicochemistry"] == {"light-thermal-regime": "Light & thermal regime"}


def test_the_index_card_draws_one_box_plot_per_index_over_the_shaded_bands():
    html = str(d.indices_card(_stats(), "US"))
    assert html.count('class="easi-dash-row easi-dash-box-row') == 4 and html.count("easi-dash-axis-row") == 1
    assert html.count('class="easi-dash-median"') == 3 and html.count('class="easi-dash-mean"') == 3   # chemical is empty
    assert html.count('fill-opacity="0.45"') == 12                     # three bands under every row
    assert 'x="39.00%"' in html and 'x="69.00%"' in html               # the band edges
    assert "median 0.610" in html and "Functioning 19.0%" in html       # the tooltip
    assert ">0.61<" in html and ">1,000<" in html                       # the median and n columns
    assert "not computed" in html
    assert html.count("easi-dash-mini") >= 1 and "Share of reaches" in html   # the band-share table
    assert ">19.0%<" in html and ">80.0%<" in html and ">1.0%<" in html    # ECI: F, AR, NF of 1,000
    small = str(d.indices_card(_stats(), "NC"))
    assert "read the distribution with care" in small


def test_the_function_card_stacks_the_rating_shares_and_can_show_boxes():
    html = str(d.functions_card(_stats(), "US"))
    assert "HYDROLOGY" not in html and ">Hydrology<" in html            # the CSS uppercases the heading
    assert "Light &amp; thermal regime" in html
    assert "width:70.00%" in html and "width:2.00%" in html             # 700 of 1,000 Functioning, 20 not rated
    assert ">70%<" in html and ">18%<" in html and ">10%<" in html      # printed inside the wide segments only
    assert html.count('class="easi-dash-seg"') == 5                    # 4 for catchment, 1 for light
    assert ">11.1<" in html and ">13.0<" in html                        # mean scores
    boxes = str(d.functions_card(_stats(), "US", mode="boxes"))
    assert boxes.count('class="easi-dash-row easi-dash-box-row') == 2 and "three values" in boxes
    assert 'x="36.67%"' in boxes                                        # the 5.5 band edge on the 0 to 15 axis


def test_sensitivity_orders_least_spread_first_and_ranges_over_covered_states():
    rows = d.sensitivity_rows(_stats(), "US")
    assert [r["id"] for r in rows] == ["light-thermal-regime", "catchment-hydrology"]
    light, catchment = rows
    assert light["spread"] == 0.0 and light["f"] == 1.0 and light["proxy"] == 0.0
    assert round(catchment["unrated"], 3) == 0.02 and catchment["proxy"] == 1.0
    assert catchment["across"] == (0.5, 0.7, 2)                      # VA and WV; NC is border-only
    html = str(d.sensitivity_card(_stats(), "US"))
    assert html.count('class="easi-dash-chip"') == 1 and "50% to 70% (2 states)" in html
    assert "least spread first" in html


def test_compare_pins_everything_first_sorts_the_rest_and_mutes_border_only_states():
    html = str(d.compare_card(_stats(), "eci"))
    order = [html.index(name) for name in ("All screened reaches", "Virginia", "West Virginia", "North Carolina")]
    assert order == sorted(order)                                        # by median: 0.62, 0.58, 0.55
    assert html.count("easi-dash-box-row muted") == 1 and "(NC)" in html
    assert "300 reaches, 60% screened" in html and "Ecosystem Condition Index by state" in html
    html = str(d.compare_card(_stats(), "catchment-hydrology"))
    assert "Catchment hydrology by state" in html and html.count('class="easi-dash-stack"') == 4
    order = [html.index(name) for name in ("All screened reaches", "Virginia", "North Carolina", "West Virginia")]
    assert order == sorted(order)                                        # by share Functioning: 0.73, 0.60, 0.50


def test_summary_strip_footer_and_empty_states():
    html = str(d.summary_strip(_stats(), "US"))
    assert "1,000" in html and ">2<" in html and "states at least half screened" in html and ">1<" in html
    assert "border-only" in html and "3%" in html and "2026-09-11" in html
    html = str(d.summary_strip(_stats(), "VA"))
    assert "90%" in html and "of 1,000 reaches screened" in html
    assert "midpoint of its NHDPlus V2 flowline" in str(d.footer_note(_stats()))
    assert "not available" in str(d.unavailable({"available": True}))
    assert "not reachable" in str(d.unavailable({"available": False}))


def test_csv_is_long_form_with_everything_first():
    text = d.export_csv(_stats())
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == ["scope", "scope_name", "kind", "measure", "statistic", "value"]
    assert rows[0]["scope"] == "US" and rows[0]["statistic"] == "n" and rows[0]["value"] == "1000"
    scopes = []
    for r in rows:
        if r["scope"] not in scopes:
            scopes.append(r["scope"])
    assert scopes == ["US", "NC", "VA", "WV"]
    by_key = {(r["scope"], r["measure"], r["statistic"]): r["value"] for r in rows}
    assert by_key[("US", "Catchment hydrology", "mean_score")] == "11.061"
    assert by_key[("US", "Catchment hydrology", "share_functioning")] == "0.7"
    assert by_key[("VA", "Ecosystem Condition Index", "p50")] == "0.62"
    assert by_key[("VA", "reaches", "coverage")] == "0.9" and ("US", "reaches", "coverage") not in by_key
    assert by_key[("US", "Light & thermal regime", "tier_observed")] == "1000"


def test_no_em_dashes_anywhere_in_the_dashboard_copy():
    stats = _stats()
    pieces = [str(d.indices_card(stats, "US")), str(d.functions_card(stats, "US")),
              str(d.functions_card(stats, "US", mode="boxes")), str(d.sensitivity_card(stats, "US")),
              str(d.compare_card(stats, "eci")), str(d.compare_card(stats, "light-thermal-regime")),
              str(d.summary_strip(stats, "US")), str(d.footer_note(stats)), d.export_csv(stats),
              str(d.unavailable(None))]
    assert not any("—" in p for p in pieces)


def test_csv_records_the_completed_bundle_identity():
    stats = _stats()
    identity = {"alternative_id": "alternative-2", "build_id": "completed-local-build", "method_version": "b2e3033116e3"}
    stats.update(identity)
    rows = list(csv.DictReader(io.StringIO(d.export_csv(stats))))
    actual = {r["measure"]: r["value"] for r in rows if r["kind"] == "dataset"}
    assert actual == identity
    assert rows[0]["scope"] == "US" and rows[0]["statistic"] == "n"
