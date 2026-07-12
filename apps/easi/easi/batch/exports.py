"""Batch ZIP export.

Produces one ZIP for a whole ``BatchResult``:
  manifest.json         run metadata + file listing
  batch-results.json    compact results (no heavyweight geometry/imagery)
  run-diagnostics.json   timings, retries, throttling
  summary.csv           one row per site (identity, ECI, bands, qualification)
  metrics.csv           one row per site x metric
  exclusions.csv        every failed / cancelled / excluded site + reason
  sites/<id>/result.json + report.csv + report.geojson (+ report.pdf if requested)

Every submitted site is included (successes, failures, cancellations, exclusions,
and manual overrides). The compact ``batch-results.json`` deliberately omits the
per-site cross-section image and basin geometry; those live in the per-site
artifacts, rebuilt from the private ``_artifacts`` source stored on each site.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile

from .. import report as easi_report
from .contracts import BatchResult, SiteResult

_MANIFEST_FILES = ["batch-results.json", "run-diagnostics.json", "summary.csv",
                   "metrics.csv", "exclusions.csv", "sites/<id>/result.json",
                   "sites/<id>/report.csv", "sites/<id>/report.geojson"]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name))[:80] or "site"


def _summary_csv(batch: BatchResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["site_id", "state", "comid", "stream", "drainage_area_sqkm",
                "eci", "physical", "chemical", "biological",
                "computed", "unavailable", "auto_decision", "final_decision",
                "partial_evidence"])
    for s in batch.sites:
        d, sub = s.delineation, s.sub_indices
        w.writerow([s.site_id, s.state, d.comid, d.gnis_name, d.drainage_area_sqkm,
                    s.eci, sub.get("physical"), sub.get("chemical"),
                    sub.get("biological"), s.completeness.computed,
                    s.completeness.unavailable, s.qualification.auto,
                    s.qualification.final, s.qualification.partial_evidence])
    return buf.getvalue()


def _metrics_csv(batch: BatchResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["site_id", "metric_id", "discipline", "function", "rating",
                "generated_rating", "index", "function_score", "band",
                "confidence", "source", "source_mode", "status", "availability",
                "missing_reason"])
    for s in batch.sites:
        for m in s.metrics:
            w.writerow([s.site_id, m.metric_id, m.discipline, m.function_name,
                        m.final_rating, m.generated_rating, m.index,
                        m.function_score, m.band, m.confidence, m.source,
                        m.source_mode, m.status, m.availability, m.missing_reason])
    return buf.getvalue()


def _exclusions_csv(batch: BatchResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["site_id", "state", "auto_decision", "final_decision", "reason"])
    for s in batch.sites:
        excluded = (s.state in ("failed", "cancelled")
                    or s.qualification.auto in ("excluded", "not_evaluable")
                    or s.qualification.final in ("excluded", "pending"))
        if not excluded:
            continue
        reason = (s.issues[0].message if s.issues
                  else "; ".join(s.qualification.reasons[:3]))
        w.writerow([s.site_id, s.state, s.qualification.auto,
                    s.qualification.final, reason])
    return buf.getvalue()


def _site_artifact_result(site: SiteResult) -> dict | None:
    """The single-site result dict the report builders expect, if available."""
    art = site.metadata.get("_artifacts")
    if art and art.get("report"):
        return art
    return None


def build_batch_zip(batch: BatchResult, *, include_pdf: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({
            "engine": "easi.batch",
            "schema_version": batch.schema_version,
            "site_count": len(batch.sites),
            "criteria": batch.criteria,
            "generated_ids": batch.generated_ids,
            "files": _MANIFEST_FILES,
        }, indent=1))
        zf.writestr("batch-results.json",
                    json.dumps(batch.to_dict(), indent=1, default=str))
        zf.writestr("run-diagnostics.json", json.dumps(batch.diagnostics, indent=1))
        zf.writestr("summary.csv", _summary_csv(batch))
        zf.writestr("metrics.csv", _metrics_csv(batch))
        zf.writestr("exclusions.csv", _exclusions_csv(batch))

        for site in batch.sites:
            base = f"sites/{_safe(site.site_id)}/"
            zf.writestr(base + "result.json",
                        json.dumps(site.to_dict(), indent=1, default=str))
            result = _site_artifact_result(site)
            if result is None:
                continue
            try:
                zf.writestr(base + "report.csv", easi_report.build_csv(result))
            except Exception:  # noqa: BLE001 - a bad artifact must not break the ZIP
                pass
            try:
                zf.writestr(base + "report.geojson",
                            easi_report.build_geojson(result).encode("utf-8"))
            except Exception:  # noqa: BLE001
                pass
            if include_pdf:
                try:
                    zf.writestr(base + "report.pdf", easi_report.build_pdf(result))
                except Exception:  # noqa: BLE001 - PDF is best-effort
                    pass
    return buf.getvalue()
