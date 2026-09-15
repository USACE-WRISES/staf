"""Descriptive exact-COMID gage comparisons, without new ecological thresholds."""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
import csv
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np

from .io import info, now, read_json, safe_study, write_json
from .acquisition import gage_qc, QC_POLICY

YEARS = tuple(range(2000, 2025))
STORED_COLUMNS = ("erom__q_cv_monthly", "erom__q_min_ratio", "erom__qe_ma", "sc__bfiws")
UNITS_TO_CFS = {"ft^3/s": 1., "ft3/s": 1., "cfs": 1., "m^3/s": 35.31466672148859, "m3/s": 35.31466672148859}


def _tokens(value):
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in values if v is not None and str(v).strip()]


def daily_records(rows):
    """Resolve dates conservatively; conflicting same-day series are excluded."""
    by_day, rejected = defaultdict(list), Counter()
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("time", ""))[:10])
            value = float(row.get("value"))
        except (TypeError, ValueError):
            rejected["missing_or_nonnumeric_date_value"] += 1
            continue
        if day.year not in YEARS:
            rejected["outside_period"] += 1
            continue
        factor = UNITS_TO_CFS.get(str(row.get("unit_of_measure", "")))
        if factor is None:
            rejected["unsupported_unit"] += 1
            continue
        if not math.isfinite(value) or value < 0:
            rejected["nonfinite_or_negative_discharge"] += 1
            continue
        approvals = _tokens(row.get("approval_status", row.get("approvals_status")))
        qualifiers = _tokens(row.get("qualifier"))
        by_day[day].append({"date": day, "value_cfs": value * factor,
                            "approved": bool(approvals) and all(x.lower() == "approved" for x in approvals),
                            "qualifiers": qualifiers, "approval_status": approvals})
    resolved = []
    for day, observations in sorted(by_day.items()):
        if len({r["value_cfs"] for r in observations}) != 1:
            rejected["conflicting_duplicate_days"] += 1
            continue
        first = observations[0]
        resolved.append({**first, "approved": all(r["approved"] for r in observations),
                         "qualifiers": sorted({q for r in observations for q in r["qualifiers"]}),
                         "approval_status": sorted({q for r in observations for q in r["approval_status"]}),
                         "source_observations": len(observations)})
    return resolved, dict(rejected)


def flow_metrics(days):
    """Daily-weighted annual mean, 12 monthly means, population monthly CV."""
    months = defaultdict(list)
    for row in days:
        months[row["date"].month].append(row["value_cfs"])
    annual = float(np.mean([r["value_cfs"] for r in days])) if days else None
    means = {str(month): float(np.mean(values)) for month, values in sorted(months.items())}
    enough = len(means) == 12 and annual is not None and annual > 0
    monthly = np.asarray(list(means.values()))
    return {"days_used": len(days), "months_represented": len(means), "annual_mean_cfs": annual,
            "monthly_means_cfs": means, "monthly_cv": float(np.std(monthly, ddof=0) / np.mean(monthly)) if enough else None,
            "min_month_annual_ratio": float(np.min(monthly) / annual) if enough else None,
            "eligibility": "all_12_months_represented" if enough else "missing_months_or_nonpositive_mean"}


def summarize_gage(rows):
    days, rejected = daily_records(rows)
    annual = []
    for year in YEARS:
        selected = [r for r in days if r["date"].year == year]
        approved = [r for r in selected if r["approved"] and not r["qualifiers"]]
        expected = 366 if calendar.isleap(year) else 365
        month_counts = Counter(r["date"].month for r in selected)
        annual.append({"year": year, "expected_days": expected, "usable_days": len(selected),
                       "daily_coverage": len(selected) / expected, "approved_unqualified_days": len(approved),
                       "qualified_days": sum(bool(r["qualifiers"]) for r in selected),
                       "not_approved_days": sum(not r["approved"] for r in selected),
                       "complete_months": sum(month_counts[m] == calendar.monthrange(year, m)[1] for m in range(1, 13)),
                       "full_year": len(selected) == expected, **flow_metrics(selected),
                       "approved_unqualified": flow_metrics(approved)})
    expected_total = sum(r["expected_days"] for r in annual)
    return {"period": "2000-2024", "source_rows": len(rows), "rejected": rejected,
            "usable_days": len(days), "expected_days": expected_total,
            "coverage": len(days) / expected_total, "years_with_data": sum(r["usable_days"] > 0 for r in annual),
            "full_years": sum(r["full_year"] for r in annual),
            "qualifier_counts": dict(Counter(q for r in days for q in r["qualifiers"])),
            "approval_counts": dict(Counter(q for r in days for q in r["approval_status"])),
            "climatology": flow_metrics(days),
            "approved_unqualified_climatology": flow_metrics([r for r in days if r["approved"] and not r["qualifiers"]]),
            "annual": annual}


def _csv(path, rows, columns):
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def run(root: Path, study: Path) -> dict:
    import pyarrow.parquet as pq
    from ..stats import spearman
    study = safe_study(root, study)
    folder = (study / "acquisition").resolve()
    if not folder.is_relative_to(study):
        raise ValueError("Gage diagnostics output escapes study")
    manifest_path, matches_path = folder / "manifest.json", folder / "gage-matches.json"
    manifest, match_data = read_json(manifest_path), read_json(matches_path)
    matches = match_data["rows"]
    comids = sorted({int(r["comid"]) for r in matches if r.get("gage_no") and r.get("comid") is not None})
    landscape = Path(root) / "analysis/landscape.parquet"
    stored, source_stamp = {}, None
    missing_columns = list(STORED_COLUMNS)
    if comids and landscape.is_file():
        stat = landscape.stat()
        source_stamp = {"path": str(landscape.resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        names = pq.read_schema(landscape).names
        present = [c for c in STORED_COLUMNS if c in names]
        missing_columns = sorted(set(STORED_COLUMNS) - set(present))
        rows = pq.read_table(landscape, columns=["comid", *present], filters=[("comid", "in", comids)]).to_pylist()
        if len({r["comid"] for r in rows}) != len(rows):
            raise ValueError("Stored gage comparisons contain duplicate COMIDs")
        stored = {r["comid"]: r for r in rows}
    source_receipts = [info(manifest_path), info(matches_path)]
    qc_metadata = folder / "usgs/SWIM_gage_loc_Metadata.xml"
    if qc_metadata.is_file():
        source_receipts.append(info(qc_metadata))
    gages, comparisons, annual_rows = [], [], []
    for source in manifest.get("daily", []):
        if source.get("status") not in ("available", "no_daily_values"):
            continue
        path = Path(source["path"]).resolve()
        if not path.is_relative_to(folder):
            raise ValueError("Daily source is outside this study acquisition")
        receipt = info(path)
        if receipt["sha256"] != source["sha256"]:
            raise ValueError("Cached daily observations differ from acquisition manifest")
        source_receipts.append(receipt)
        data = read_json(path)
        gage = source["gage_no"]
        if data.get("gage_no") != gage or any(
            row.get("monitoring_location_id") != "USGS-" + gage or row.get("parameter_code") != "00060"
            or row.get("statistic_id") != "00003" for row in data["rows"]
        ):
            raise ValueError("Cached daily gage or parameter does not match the requested exact gage")
        result = summarize_gage(data["rows"])
        matching_rows = [r for r in matches if r.get("gage_no") == gage]
        qc = gage_qc(matching_rows[0].get("gage_metadata") if matching_rows else None)
        gages.append({"gage_id": "USGS-" + gage, "source_receipt": receipt, **qc, **result})
        for annual in result["annual"]:
            annual_rows.append({"gage_id": "USGS-" + gage, **qc, **annual})
        matched = sorted({int(r["comid"]) for r in matches if r.get("gage_no") == gage})
        for comid in matched:
            incumbent = stored.get(comid, {})
            periods = [("climatology", result["climatology"], result["coverage"])]
            periods += [(r["year"], r, r["daily_coverage"]) for r in result["annual"] if r["usable_days"]]
            for year, metrics, coverage in periods:
                for measure, column, observed_key in [
                    ("monthly_cv", "erom__q_cv_monthly", "monthly_cv"),
                    ("min_month_annual_ratio", "erom__q_min_ratio", "min_month_annual_ratio"),
                    ("annual_mean_cfs", "erom__qe_ma", "annual_mean_cfs"),
                    ("observed_min_month_ratio_and_bfi_percent", "sc__bfiws", "min_month_annual_ratio"),
                ]:
                    value = incumbent.get(column)
                    value = float(value) if value is not None and math.isfinite(float(value)) else None
                    comparisons.append({"subject": "low_flow_baseflow_dynamics", "measure": measure,
                        "reference": value, "alternative": metrics[observed_key], "n": metrics["days_used"],
                        "gage_id": "USGS-" + gage, "comid": comid, "period": "2000-2024", "year": year,
                        "daily_coverage": coverage, "full_years": result["full_years"],
                        **qc,
                        "eligibility": metrics["eligibility"], "stored_column": column,
                        "source_receipt": str(path), "source_sha256": receipt["sha256"],
                        "interpretation": "different quantities, descriptive association only" if column == "sc__bfiws"
                            else "different vintages and potentially incomplete observed record; descriptive pair"})
    def associate(primary_only):
        associations = []
        for measure in sorted({r["measure"] for r in comparisons}):
            paired = [r for r in comparisons if r["measure"] == measure and r["year"] == "climatology"
                      and r["reference"] is not None and r["alternative"] is not None
                      and (r["primary_eligible"] or not primary_only)]
            # Never repeat station-cycle observations; sensitivity retains flagged exact matches.
            rho = spearman(np.asarray([r["reference"] for r in paired]), np.asarray([r["alternative"] for r in paired])) if len(paired) >= 3 else None
            associations.append({"measure": measure, "statistic": "signed_spearman", "rho": rho, "n": len(paired),
                "cohort": "primary_qc_eligible" if primary_only else "sensitivity_all_exact_matches",
                "interpretation": "descriptive gage pairs; no independence or significance claim; multiple gages may share a COMID"})
        return associations
    associations, sensitivity = associate(True), associate(False)
    if source_stamp:
        stat = landscape.stat()
        if (stat.st_size, stat.st_mtime_ns) != (source_stamp["bytes"], source_stamp["mtime_ns"]):
            raise RuntimeError("Stored landscape changed during gage comparison")
    selected_digest = hashlib.sha256(json.dumps(stored, sort_keys=True, default=str).encode()).hexdigest()
    usable = [r for r in gages if r["usable_days"]]
    result = {"schema_version": 1, "created_at": now(), "status":
              ("partial" if manifest.get("status") == "partial" else "complete") if usable else "no_available_exact_match_daily_records",
              "acquisition_status": manifest.get("status"), "qc_policy": QC_POLICY,
              "gage_count": len(gages), "comparison_pairs": len({(r["gage_id"], r["comid"]) for r in comparisons}),
              "gages_with_usable_days": len(usable), "primary_gages_with_usable_days": sum(r["primary_eligible"] for r in usable),
              "gages": gages, "rows": comparisons, "signed_associations": associations,
              "sensitivity_all_exact_associations": sensitivity,
              "source_receipts": source_receipts, "stored_source": source_stamp,
              "stored_selected_values_sha256": selected_digest, "missing_stored_columns": missing_columns,
              "limits": [
                  "Daily values are finite, nonnegative discharge in recognized units; conflicting same-day observations are excluded.",
                  "Estimated/qualified and unapproved values are retained with counts; approved-unqualified results are also reported separately.",
                  "CV and minimum-month ratios require all 12 calendar months represented, not a minimum coverage threshold. Partial records remain labeled.",
                  "Observed annual means weight available days; monthly CV uses population standard deviation over 12 calendar-month means.",
                  "The 2000-2024 observed climatology differs from EROM reference vintages and may have uneven yearly coverage.",
                  "BFI is an estimated baseflow percentage, not the observed minimum-month ratio or monthly CV. No impairment thresholds are inferred.",
                  "Exact COMID does not prove an identical drainage area, undisturbed flow, or independent ecological validation."]}
    output = folder / "gage-diagnostics.json"
    write_json(output, result)
    _csv(folder / "annual-record-coverage.csv", annual_rows, ["gage_id", "primary_eligible", "qc_final", "qc_exclusion_reasons", "year", "expected_days", "usable_days", "daily_coverage",
         "approved_unqualified_days", "qualified_days", "not_approved_days", "complete_months", "full_year", "months_represented",
         "annual_mean_cfs", "monthly_cv", "min_month_annual_ratio", "eligibility"])
    _csv(folder / "gage-comparison.csv", comparisons, ["subject", "measure", "reference", "alternative", "n", "gage_id", "comid",
         "period", "year", "primary_eligible", "qc_final", "qc_exclusion_reasons", "daily_coverage", "full_years", "eligibility", "stored_column", "source_receipt", "source_sha256", "interpretation"])
    summary_path = folder / "summary.json"
    summary = read_json(summary_path) if summary_path.is_file() else {}
    summary["gage_diagnostics"] = {"path": str(output), "status": result["status"], "gage_count": len(gages),
                                   "gages_with_usable_days": len(usable), "primary_gages_with_usable_days": result["primary_gages_with_usable_days"],
                                   "comparison_pairs": result["comparison_pairs"], "signed_associations": associations}
    write_json(summary_path, summary)
    return result
