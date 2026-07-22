"""Coverage and no-data rollup behavior."""
from __future__ import annotations

import re

import app

from easi import assessment, config, screening_methods
from easi.metrics import geomorphology


def _rows(rated: set[str], *, excluded: set[str] | None = None,
          partial: set[str] | None = None, overridden: set[str] | None = None):
    excluded = excluded or set()
    partial = partial or set()
    overridden = overridden or set()
    rows = []
    for metric in config.easi_metrics()["metrics"]:
        mid = metric["metricId"]
        rating = "Good" if mid in rated or mid in overridden else None
        status = ("excluded" if mid in excluded else
                  "override" if mid in overridden else
                  "ok" if rating else "not_assessed")
        rows.append({
            "metricId": mid,
            "functionId": metric["functionId"],
            "rating": rating,
            "status": status,
            "completeness": "partial" if mid in partial else
                            "complete" if rating else "not_assessed",
        })
    return rows


def test_excluded_metrics_do_not_enter_coverage_denominator():
    mids = list(config.metrics_by_id())
    report = assessment._finalize(
        _rows({mids[0]}, excluded=set(mids[1:])), 20, {})
    assert report["coverage"]["overall"] == {
        "rated": 1, "selected": 1, "fraction": 1.0}
    assert report["provisionalCoverage"] is False


def test_partial_and_manual_override_count_as_covered():
    mids = list(config.metrics_by_id())
    report = assessment._finalize(
        _rows({mids[0]}, partial={mids[0]}, overridden={mids[1]}), 20, {mids[1]})
    assert report["coverage"]["overall"]["rated"] == 2


def test_low_coverage_sets_provisional_flag():
    mids = list(config.metrics_by_id())
    report = assessment._finalize(_rows(set(mids[:5])), 20, {})
    assert report["coverage"]["overall"]["fraction"] == 0.25
    assert report["provisionalCoverage"] is True
    assert "overall" in report["coverage"]["limited"]


def test_no_data_domains_are_none():
    # Select a single function and exclude every other function. Outcomes with no
    # mapped denominator remain None rather than being represented as failed.
    mids = list(config.metrics_by_id())
    report = assessment._finalize(
        _rows({mids[0]}, excluded=set(mids[1:])), 20, {})
    for outcome, coverage in report["coverage"]["outcomes"].items():
        if coverage["selectedWeight"] == 0:
            assert coverage["fraction"] is None
            assert report["subIndices"][outcome] is None


def test_html_summary_renders_missing_values_as_gray_dashes():
    rendered = str(app._summary_plots({
        "functionScores": {},
        "subIndices": {"physical": None, "chemical": None, "biological": None},
        "ecosystemConditionIndex": None,
    }))
    # every bar's value cell reads as a dash, never 0.00
    values = re.findall(r'class="easi-bar-val">([^<]*)<', rendered)
    assert values and all(v.strip() == "—" for v in values)
    # and every bar is empty and neutral — missing evidence is not a Non-Functioning zero
    fills = re.findall(r'class="easi-bar-fill" style="width:([^;]+);background:([^;]+);"',
                       rendered)
    assert fills and all(w == "0.0%" and c == "#d7dce5" for w, c in fills)


def test_complete_coverage_reports_evidence_profile_and_proxy_message():
    mids = list(config.metrics_by_id())
    rows = _rows(set(mids))
    for index, row in enumerate(rows):
        tiers = ("observed", "connected-nearby", "published-model", "screening-proxy")
        row["scoring"] = {
            "sourceTier": tiers[index % len(tiers)],
            "evidenceFamily": "incision_geometry" if index < 2 else "",
            "usedFallback": index % 4 == 3,
        }
        row["name"] = row["metricId"]
    report = assessment._finalize(rows, 20, {})
    assert report["coverage"]["overall"]["fraction"] == 1.0
    assert report["coverage"]["completeWithProxies"] is True
    assert report["coverage"]["statusMessage"] == (
        "Complete screening coverage — includes proxy-derived ratings")
    assert sum(report["evidenceProfile"].values()) == 20
    assert report["correlationNotes"][0]["evidenceFamily"] == "incision_geometry"


def test_mink_brook_complete_coverage_acceptance_rollup():
    """Lock the reviewed Mink Brook acceptance fixture to the stated rollup."""
    ratings = [
        "Good", "Fair", "Fair", "Good",       # hydrology
        "Good", "Poor", "Fair", "Good",      # hydraulics
        "Poor", "Poor", "Fair", "Fair",      # geomorphology
        "Good", "Good", "Good", "Fair",      # physicochemistry
        "Good", "Good", "Good", "Good",      # biology
    ]
    rows = []
    for metric, rating in zip(config.easi_metrics()["metrics"], ratings,
                              strict=True):
        rows.append({
            "metricId": metric["metricId"],
            "name": metric["functionName"],
            "functionId": metric["functionId"],
            "rating": rating,
            "status": "ok",
            "completeness": "complete",
            "scoring": {
                "sourceTier": "screening-proxy",
                "evidenceFamily": "acceptance_fixture",
                "usedFallback": True,
            },
        })

    report = assessment._finalize(rows, 20, {})

    assert report["coverage"]["overall"]["fraction"] == 1.0
    assert all(
        item["fraction"] == 1.0
        for item in report["coverage"]["outcomes"].values()
    )
    assert report["subIndices"] == {
        "physical": 0.57,
        "chemical": 0.74,
        "biological": 0.80,
    }
    assert report["ecosystemConditionIndex"] == 0.70


def test_observed_bank_evidence_replaces_and_preserves_proxy():
    rows = _rows(set())
    bank = next(row for row in rows
                if row["metricId"] == geomorphology.BANK_EROSION_ID)
    proxy = screening_methods.evaluate(
        geomorphology.BANK_EROSION_ID, {"bhr": 1.2},
        source_tier="screening-proxy", evidence_family="incision_geometry",
        used_fallback=True)
    bank.update({
        "name": "Bank erosion and armoring",
        "generatedRating": "Good", "rating": "Good", "status": "ok",
        "generatedValueText": "BHR 1.2", "valueText": "BHR 1.2",
        "scoring": proxy.trace,
    })
    base = assessment._finalize(rows, 20, {})
    revised = assessment.apply_observed_evidence(base, {
        geomorphology.BANK_EROSION_ID: {
            "erodingBankPct": 65, "armoredBankPct": 0,
            "annualRetreatContext": "0.2 ft/year",
        }})
    row = next(item for item in revised["metricRows"]
               if item["metricId"] == geomorphology.BANK_EROSION_ID)
    assert row["rating"] == "Poor" and row["status"] == "observed"
    assert row["generatedRating"] == "Good"
    assert row["proxyResult"]["rating"] == "Good"
    assert row["scoring"]["observedOverridesProxy"] is True
