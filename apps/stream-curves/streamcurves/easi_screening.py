"""EASI reference-condition screening for StreamCurves candidate sites.

Two modes (per the StreamCurves + EASI revision plans):
- ``screen_sites_direct``: run the vendored EASI batch engine in-process. Needs the
  heavy geospatial stack, so it is a local/desktop path. The engine import is lazy
  so this module still imports on the cloud (which uses ZIP-import only).
- ``screen_result_from_zip``: parse a finalized EASI batch ZIP (``batch-results.json``).
  Pure-Python, no engine dependency, so it works anywhere including the cloud deploy.

Both normalize to the EASI ``BatchResult`` dict shape, which ``to_screening_tables``
turns into the stable ``easi_screening_sites`` / ``easi_screening_metrics`` /
``easi_screening_criteria`` tables keyed by the external StreamCurves site id.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Any, Optional

_VENDOR_ROOT = "streamcurves._vendor.easi"


def engine_available() -> bool:
    """True if the vendored EASI engine (and its geospatial deps) can be imported."""
    try:
        __import__(f"{_VENDOR_ROOT}.batch.api")
        return True
    except Exception:  # noqa: BLE001 - missing geo stack on the cloud, etc.
        return False


# --- direct (local/desktop) ------------------------------------------------- #
def screen_sites_direct(sites: list[dict], criteria: Optional[Any] = None,
                        *, on_event=None, cancel=None) -> dict:
    """Run the vendored EASI engine over candidate rows -> BatchResult dict.

    ``sites``: rows with at least ``site_id``, ``lat``, ``lon`` (optional ``comid``).
    ``criteria``: a preset name ("functional"/"reference_condition"), a serialized
    rule dict, or None (defaults to the Functional preset).
    """
    from streamcurves._vendor.easi.batch import api  # lazy: geo stack
    from streamcurves._vendor.easi.batch.contracts import (BatchConfig,
                                                           BatchRequest,
                                                           SiteRequest)
    reqs = [SiteRequest(site_id=str(s.get("site_id") or ""),
                        lat=float(s["lat"]), lon=float(s["lon"]),
                        comid=s.get("comid"),
                        metadata={k: v for k, v in s.items()
                                  if k not in ("site_id", "lat", "lon", "comid")})
            for s in sites]
    req = BatchRequest(sites=reqs, config=BatchConfig(), criteria=criteria)
    result = api.run_batch_sync(req, on_event=on_event, cancel=cancel)
    return result.to_dict()


async def screen_sites_direct_async(sites: list[dict], criteria: Optional[Any] = None,
                                    *, on_event=None, cancel=None) -> dict:
    """Awaitable twin of :func:`screen_sites_direct` (drives ``api.run_batch``).

    Use this from Shiny's event loop (an ``@reactive.extended_task``) so the 71-site
    batch progresses without blocking, and ``cancel`` can interrupt it mid-run. The
    engine still returns a full ``BatchResult`` (including cancelled rows) on cancel.
    """
    from streamcurves._vendor.easi.batch import api  # lazy: geo stack
    from streamcurves._vendor.easi.batch.contracts import (BatchConfig,
                                                           BatchRequest,
                                                           SiteRequest)
    reqs = [SiteRequest(site_id=str(s.get("site_id") or ""),
                        lat=float(s["lat"]), lon=float(s["lon"]),
                        comid=s.get("comid"),
                        metadata={k: v for k, v in s.items()
                                  if k not in ("site_id", "lat", "lon", "comid")})
            for s in sites]
    req = BatchRequest(sites=reqs, config=BatchConfig(), criteria=criteria)
    result = await api.run_batch(req, on_event=on_event, cancel=cancel)
    return result.to_dict()


# --- ZIP import (cloud-safe) ------------------------------------------------ #
def screen_result_from_zip(zip_bytes: bytes) -> dict:
    """Parse ``batch-results.json`` from a finalized EASI batch ZIP."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        try:
            raw = zf.read("batch-results.json")
        except KeyError as exc:
            raise ValueError("not an EASI batch ZIP (no batch-results.json)") from exc
    return json.loads(raw)


# --- normalize to stable screening tables ----------------------------------- #
def to_screening_tables(batch_results: dict) -> dict:
    """Map a BatchResult dict to the three stable StreamCurves screening tables."""
    sites_rows: list[dict] = []
    metric_rows: list[dict] = []
    for s in batch_results.get("sites", []):
        d = s.get("delineation", {})
        sub = s.get("sub_indices", {})
        comp = s.get("completeness", {})
        q = s.get("qualification", {})
        sites_rows.append({
            "site_id": s.get("site_id"),
            "state": s.get("state"),
            "comid": d.get("comid"),
            "stream": d.get("gnis_name"),
            "drainage_area_sqkm": d.get("drainage_area_sqkm"),
            "eci": s.get("eci"),
            "raw_eci": s.get("raw_eci"),
            "physical": sub.get("physical"),
            "chemical": sub.get("chemical"),
            "biological": sub.get("biological"),
            "computed": comp.get("computed"),
            "unavailable": comp.get("unavailable"),
            "auto_decision": q.get("auto"),
            "final_decision": q.get("final"),
            "partial_evidence": q.get("partial_evidence"),
            "reason": "; ".join((q.get("reasons") or [])[:3]),
        })
        for m in s.get("metrics", []):
            metric_rows.append({
                "site_id": s.get("site_id"),
                "metric_id": m.get("metric_id"),
                "function": m.get("function_name"),
                "discipline": m.get("discipline"),
                "rating": m.get("final_rating"),
                "index": m.get("index"),
                "function_score": m.get("function_score"),
                "band": m.get("band"),
                "status": m.get("status"),
                "availability": m.get("availability"),
            })
    criteria = {
        "criteria": batch_results.get("criteria"),
        "config": batch_results.get("config"),
        "generated_ids": batch_results.get("generated_ids"),
    }
    return {
        "easi_screening_sites": sites_rows,
        "easi_screening_metrics": metric_rows,
        "easi_screening_criteria": criteria,
    }


def retained_site_ids(tables: dict) -> list[str]:
    """External site ids whose final decision is ``retained`` (continue to enrichment)."""
    return [r["site_id"] for r in tables.get("easi_screening_sites", [])
            if r.get("final_decision") == "retained"]
