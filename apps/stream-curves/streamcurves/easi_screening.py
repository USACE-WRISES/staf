"""EASI reference-condition screening for StreamCurves candidate sites.

Two modes (per the StreamCurves + EASI revision plans):
- ``screen_sites_direct``: run the vendored EASI batch engine in-process. Needs the
  heavy geospatial stack, which the cloud deploy now also carries, so this runs
  anywhere the stack resolves. The engine import stays lazy so this module still
  imports where it does not, and ``engine_available`` gates the UI on the actual
  geospatial modules rather than on the engine package alone.
- ``screen_result_from_zip``: parse a finalized EASI batch ZIP (``batch-results.json``).
  Pure-Python, no engine dependency, so it works anywhere. Still the reliable route
  for large batches, since a direct run holds everything in memory until it finishes.

Both normalize to the EASI ``BatchResult`` dict shape, which ``to_screening_tables``
turns into the stable ``easi_screening_sites`` / ``easi_screening_metrics`` /
``easi_screening_criteria`` tables keyed by the external StreamCurves site id.
"""
from __future__ import annotations

import functools
import importlib.util
import io
import json
import zipfile
from typing import Any, Optional

_VENDOR_ROOT = "streamcurves._vendor.easi"
_ENGINE_MODULE = f"{_VENDOR_ROOT}.batch.api"

# Top-level geospatial modules the direct path needs. The engine imports these
# function-locally so the package stays importable without them, which means
# importing the engine proves nothing about whether a run can actually succeed.
_GEO_REQUIREMENTS = ("pynhd", "py3dep", "pygeohydro", "geopandas", "shapely", "xarray")

# Screening options offered on the Confirm-sites step. Keys MUST be keys of the
# vendored ``qualify.PRESETS``: an unknown name resolves to None, which silently
# marks every site not_evaluable. tests/test_easi_screening.py gates this.
SCREENING_PRESET_CHOICES: dict[str, str] = {
    "functional": "Only Functioning",
    "at_risk_or_better": "Functioning or Functioning-at-Risk",
    "all_sites": "All sites",
}
DEFAULT_SCREENING_PRESET = "functional"

# The reference screen runs on the StreamCat lookup engine only. This is a
# fixed policy, not a setting: "streamcat-legacy" keeps uncovered NRSA sites
# on the surrogate-within-10x routing and the refusal beyond it that every
# published version was screened under, so screening caches, retained sets
# and inputsDigest values stay byte-identical. EASI's own default is "auto"
# (the STAF site engine computes the exact watershed for HR-only streams).
SCREENING_WATERSHED_ENGINE = "streamcat-legacy"


def _batch_config():
    """The vendored ``BatchConfig`` pinned to the screening policy. The kwarg
    is passed directly so a vendored contract without the field fails loudly
    (the drift gate catches the same thing)."""
    from streamcurves._vendor.easi.batch.contracts import BatchConfig  # lazy: geo stack
    return BatchConfig(watershed_engine=SCREENING_WATERSHED_ENGINE)


@functools.lru_cache(maxsize=1)
def missing_engine_requirements() -> tuple[str, ...]:
    """Modules the direct screening path needs that are not importable here.

    ``find_spec`` does not execute module code, so this is cheap enough for the
    render function that gates the Run screening button.
    """
    missing = []
    for mod in (_ENGINE_MODULE, *_GEO_REQUIREMENTS):
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        except (ImportError, ValueError):  # parent package itself is absent
            missing.append(mod)
    return tuple(missing)


def engine_available() -> bool:
    """True if the vendored EASI engine *and* its geospatial deps are importable."""
    return not missing_engine_requirements()


# --- direct (local/desktop) ------------------------------------------------- #
def screen_sites_direct(sites: list[dict], criteria: Optional[Any] = None,
                        *, on_event=None, cancel=None) -> dict:
    """Run the vendored EASI engine over candidate rows -> BatchResult dict.

    ``sites``: rows with at least ``site_id``, ``lat``, ``lon`` (optional ``comid``).
    ``criteria``: a preset name (see ``SCREENING_PRESET_CHOICES``), a serialized
    rule dict, or None (defaults to the Only Functioning preset).
    """
    from streamcurves._vendor.easi.batch import api  # lazy: geo stack
    from streamcurves._vendor.easi.batch.contracts import (BatchRequest,
                                                           SiteRequest)
    reqs = [SiteRequest(site_id=str(s.get("site_id") or ""),
                        lat=float(s["lat"]), lon=float(s["lon"]),
                        comid=s.get("comid"),
                        metadata={k: v for k, v in s.items()
                                  if k not in ("site_id", "lat", "lon", "comid")})
            for s in sites]
    req = BatchRequest(sites=reqs, config=_batch_config(), criteria=criteria)
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
    from streamcurves._vendor.easi.batch.contracts import (BatchRequest,
                                                           SiteRequest)
    reqs = [SiteRequest(site_id=str(s.get("site_id") or ""),
                        lat=float(s["lat"]), lon=float(s["lon"]),
                        comid=s.get("comid"),
                        metadata={k: v for k, v in s.items()
                                  if k not in ("site_id", "lat", "lon", "comid")})
            for s in sites]
    req = BatchRequest(sites=reqs, config=_batch_config(), criteria=criteria)
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
def _band_labeller():
    """``index_band_label`` from the vendored engine (stdlib-only, so cloud-safe).

    Resolved lazily so this module still imports if the vendor tree is absent.
    """
    try:
        from streamcurves._vendor.easi.scoring import index_band_label
        return index_band_label
    except Exception:  # noqa: BLE001 - vendor tree absent
        return lambda v: None


def _blocking_issue(site: dict) -> dict:
    """The error that stopped this site (``{}`` when nothing blocked it).

    Only error-severity issues count: a ``partial`` site carries a stack of
    info-level ``metric_unavailable`` issues that are not failures. Cancelled
    sites carry a single info-level ``cancelled`` issue, so fall back for those.
    """
    issues = site.get("issues") or []
    for i in issues:
        if i.get("severity") == "error":
            return i
    if site.get("state") in ("failed", "cancelled"):
        return issues[0] if issues else {}
    return {}


def to_screening_tables(batch_results: dict) -> dict:
    """Map a BatchResult dict to the three stable StreamCurves screening tables."""
    sites_rows: list[dict] = []
    metric_rows: list[dict] = []
    band_label = _band_labeller()
    for s in batch_results.get("sites", []):
        d = s.get("delineation", {})
        sub = s.get("sub_indices", {})
        comp = s.get("completeness", {})
        q = s.get("qualification", {})
        # The engine echoes the request coordinates back on every site, failed
        # ones included. Carry them so the table can stand on its own: the map
        # and a re-run both need the point, not just the resolved reach.
        inp = s.get("input") or {}
        issue = _blocking_issue(s)
        # Band on the raw value the criteria actually used, so the condition label
        # can never disagree with the decision on a 2-decimal display boundary.
        raw = s.get("raw_eci")
        if raw is None:
            raw = s.get("eci")
        reasons = "; ".join((q.get("reasons") or [])[:3])
        sites_rows.append({
            "site_id": s.get("site_id"),
            # NOTE: the EASI run state (succeeded/partial/failed/cancelled), NOT
            # the US state. The candidate frame uses `state` for the latter, so
            # never copy this column across to it.
            "state": s.get("state"),
            "lat": inp.get("lat"),
            "lon": inp.get("lon"),
            "comid": d.get("comid"),
            "stream": d.get("gnis_name"),
            "drainage_area_sqkm": d.get("drainage_area_sqkm"),
            "eci": s.get("eci"),
            "raw_eci": s.get("raw_eci"),
            "condition": band_label(raw) if raw is not None else None,
            "physical": sub.get("physical"),
            "chemical": sub.get("chemical"),
            "biological": sub.get("biological"),
            "computed": comp.get("computed"),
            "unavailable": comp.get("unavailable"),
            "auto_decision": q.get("auto"),
            "final_decision": q.get("final"),
            "partial_evidence": q.get("partial_evidence"),
            "issue_code": issue.get("code") or "",
            "issue": issue.get("message") or "",
            # A site that never scored has no meaningful criteria reason: the
            # predicate text would just say "skip (no data)". Show what broke.
            "reason": (issue.get("message") or reasons
                       if s.get("state") in ("failed", "cancelled") else reasons),
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
        # retries / timeouts / throttled / server_errors / elapsed_s: the only
        # evidence of *why* a run went badly, so keep it reachable by the UI.
        "diagnostics": batch_results.get("diagnostics"),
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


# --- screening outcome accounting ------------------------------------------- #
def _unresolved(row: dict) -> bool:
    """True when the site was never actually assessed against the criteria."""
    return (row.get("final_decision") not in ("retained", "excluded")
            or row.get("state") in ("failed", "cancelled"))


def summarize_screening_rows(rows: list[dict]) -> dict:
    """Counts that keep 'excluded by criteria' separate from 'never assessed'.

    A failed site is not a screened-out site: reporting them together is what made
    an engine outage read as a clean run that excluded everything.
    """
    counts = {"n_screened": len(rows), "n_retained": 0, "n_excluded": 0,
              "n_unresolved": 0, "n_failed": 0, "n_cancelled": 0}
    for r in rows:
        state = r.get("state")
        if state == "failed":
            counts["n_failed"] += 1
        elif state == "cancelled":
            counts["n_cancelled"] += 1
        if r.get("final_decision") == "retained":
            counts["n_retained"] += 1
        elif _unresolved(r):
            counts["n_unresolved"] += 1
        else:
            counts["n_excluded"] += 1
    return counts


def exclusion_records(rows: list[dict]) -> list[dict]:
    """``site_exclusions`` rows with honest provenance.

    Everything not retained is still kept out of enrichment (an unassessed site has
    no data to enrich), but the source distinguishes a deliberate screen-out from a
    site the engine never managed to evaluate.
    """
    out: list[dict] = []
    for r in rows:
        if r.get("final_decision") == "retained":
            continue
        unresolved = _unresolved(r)
        if r.get("reviewer"):
            source = "reviewer"
        elif unresolved:
            source = "unresolved"
        else:
            source = "screening"
        reason = (r.get("issue") if unresolved else None) or r.get("reason")
        out.append({
            "site_id": str(r.get("site_id")),
            "reason": reason or ("not assessed" if unresolved else "screened out"),
            "source": source,
            "note": r.get("reviewer_note"),
        })
    return out
