"""Bounded official-source acquisition, isolated inside an alternative study.

The 2008/09 survey identifiers are potential overlap evidence, not a complete
StreamCat model-training roster. No nearest-gage or network-distance matching is
performed. Daily records retain units, qualifiers and approval status.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
import re
import struct
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .io import info, now, read_json, safe_study, sha, write_json, write_parquet

EPA_UPDATES = "https://www.epa.gov/national-aquatic-resource-surveys/streamcat-and-lakecat-updates"
EPA_DATA = "https://www.epa.gov/national-aquatic-resource-surveys/data-national-aquatic-resource-surveys"
EPA_FILES = {
    "sites_0809.csv": "https://www.epa.gov/sites/default/files/2015-09/siteinfo_0.csv",
    "sites_0809.txt": "https://www.epa.gov/sites/default/files/2015-09/siteinfo_0.txt",
    "benthos_0809.csv": "https://www.epa.gov/sites/default/files/2015-09/bentcond.csv",
    "benthos_0809.txt": "https://www.epa.gov/sites/default/files/2015-09/bentcond.txt",
}
SWIM_CATALOG = "https://water.usgs.gov/catalog/datasets/862d3c17-01c1-48c7-88ac-dcba1e8a5b37/"
SWIM_ITEM = "https://www.sciencebase.gov/catalog/item/5ebe92af82ce476925e44b8f?format=json"
DAILY = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items"
DAILY_DOC = "https://api.waterdata.usgs.gov/docs/ogcapi/"
START, END = "2000-01-01", "2024-12-31"
MAX_BYTES = 64 * 1024 * 1024
MAX_PAGES = 100
QC_POLICY = "SWIM final corrected COMID; recognized final QC; unresolved adverse review notes excluded from primary"


def _retry_after(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0., seconds) if math.isfinite(seconds) else None


def _output(folder: Path, relative: str) -> Path:
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError("Acquisition output escapes its study directory")
    return path


def _official(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "epa.gov" or host.endswith(".epa.gov")
        or host == "usgs.gov" or host.endswith(".usgs.gov")
        or host == "sciencebase.gov" or host.endswith(".sciencebase.gov"))


class Cache:
    """Content-checked downloads; successful requests are resumed without HTTP."""

    def __init__(self, folder: Path, session=None):
        import requests
        self.folder = folder.resolve()
        self.session = session or requests.Session()
        self.sources: list[dict] = []
        self.rate_limited_hosts: set[str] = set()

    def fetch(self, url: str, relative: str, *, attempts=3) -> Path:
        if not _official(url):
            raise ValueError("Only HTTPS official EPA/USGS/ScienceBase sources are allowed")
        path = _output(self.folder, relative)
        receipt = path.with_name(path.name + ".source.json")
        if receipt.is_file() and path.is_file():
            meta = read_json(receipt)
            if meta.get("url") == url and meta.get("sha256") == sha(path):
                self.sources.append(meta)
                return path
            raise RuntimeError(f"Cached source changed: {path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        host = urlsplit(url).hostname
        if host in self.rate_limited_hosts:
            failure = {"url": url, "status": "deferred_rate_limit", "attempted_at": now(),
                       "attempts": 0, "error": "Host returned HTTP 429 earlier in this invocation; resume later from cache"}
            write_json(receipt, failure)
            self.sources.append(failure)
            raise RuntimeError(f"Official source deferred after rate limit: {url}")
        error = None
        response_meta = {}
        retried_rate_limit = False
        for attempt in range(attempts):
            try:
                headers = {"User-Agent": "STAF-EASI-local-alternative-study/1.0"}
                api_key = os.environ.get("USGS_API_KEY")
                if api_key and urlsplit(url).hostname == "api.waterdata.usgs.gov":
                    headers["X-Api-Key"] = api_key
                if host == "api.waterdata.usgs.gov":
                    time.sleep(1.)  # Only uncached service requests consume quota.
                response = self.session.get(url, headers=headers, timeout=(15, 45), stream=True)
                try:
                    response_meta = {"http_status": response.status_code, "rate_limit": {
                        k: response.headers[k] for k in ("Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")
                        if k in response.headers}}
                    if response.status_code == 429:
                        self.rate_limited_hosts.add(host)
                        response_meta["retry_after_seconds"] = _retry_after(response.headers.get("Retry-After"))
                    response.raise_for_status()
                    if not _official(response.url):
                        raise ValueError("Official source redirected outside official hosts")
                    temp = path.with_name(path.name + ".tmp")
                    size = 0
                    with temp.open("wb") as stream:
                        for block in response.iter_content(1 << 20):
                            size += len(block)
                            if size > MAX_BYTES:
                                raise ValueError("Source exceeds the bounded download size")
                            stream.write(block)
                    os.replace(temp, path)
                    meta = {"url": url, "resolved_url": response.url, "retrieved_at": now(),
                            "status": "available", "http_status": response.status_code,
                            "content_type": response.headers.get("Content-Type"), **response_meta, **info(path)}
                    write_json(receipt, meta)
                    self.sources.append(meta)
                    return path
                finally:
                    response.close()
            except (OSError, ValueError, RuntimeError) as exc:
                error = exc
            except Exception as exc:  # requests transport/status failures are recorded, never hidden
                error = exc
            if host in self.rate_limited_hosts:
                seconds = response_meta.get("retry_after_seconds")
                if seconds is not None and seconds <= 60 and attempt + 1 < attempts and not retried_rate_limit:
                    retried_rate_limit = True
                    time.sleep(seconds)
                    self.rate_limited_hosts.remove(host)
                    continue
                break
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 4))
        failure = {"url": url, "status": "unavailable", "attempted_at": now(),
                   "attempts": attempt + 1, "error_type": type(error).__name__, "error": str(error), **response_meta}
        write_json(receipt, failure)
        self.sources.append(failure)
        raise RuntimeError(f"Official source unavailable after {attempt + 1} attempts: {url}") from error


def _comid(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number > 0 and number.is_integer() else None
    except (ValueError, TypeError, OverflowError):
        return None


def cohort(root: Path, study: Path) -> tuple[list[dict], dict]:
    import pyarrow.parquet as pq
    path = study / "cohorts/observations.parquet"
    fallback = False
    if not path.is_file():
        path = root / "analysis/nrsa/nrsa_desktop.parquet"
        fallback = True
    columns = pq.read_schema(path).names
    if "comid" not in columns or "station_key" not in columns:
        raise ValueError("Evaluation cohort requires comid and station_key")
    wanted = [k for k in ("comid", "station_key", "site_id", "master_siteid", "cycle") if k in columns]
    rows = pq.read_table(path, columns=wanted).to_pylist()
    seen, selected = set(), []
    for row in rows:
        if row.get("station_key") is None or not str(row["station_key"]).strip():
            raise ValueError("Evaluation cohort contains a missing station_key")
        row["station_key"] = str(row["station_key"]).strip()
        row["comid"] = _comid(row.get("comid"))
        identity = (str(row.get("station_key")), row["comid"], str(row.get("site_id")), str(row.get("cycle")))
        if identity not in seen:
            seen.add(identity)
            selected.append(row)
    return selected, {**info(path), "fallback_nrsa_desktop": fallback, "observations": len(selected)}


def dbf_rows(path: Path) -> list[dict]:
    """Read the official dBase attributes without requiring shapefile extraction."""
    data = path.read_bytes()
    if len(data) < 33:
        raise ValueError("Truncated SWIM DBF")
    nrows, header_size, row_size = struct.unpack_from("<IHH", data, 4)
    if header_size < 33 or row_size < 1 or len(data) < header_size + nrows * row_size:
        raise ValueError("Invalid SWIM DBF lengths")
    fields = []
    for offset in range(32, header_size - 1, 32):
        if data[offset] == 13:
            break
        block = data[offset:offset + 32]
        fields.append((block[:11].split(b"\0")[0].decode("ascii"), block[16]))
    if sum(size for _, size in fields) + 1 != row_size:
        raise ValueError("Unsupported SWIM DBF record layout")
    result = []
    for index in range(nrows):
        record = data[header_size + index * row_size:header_size + (index + 1) * row_size]
        if record[:1] == b"*":
            continue
        row, offset = {}, 1
        for name, length in fields:
            row[name] = record[offset:offset + length].decode("cp1252").strip()
            offset += length
        result.append(row)
    return result


def gage_qc(metadata: dict | None) -> dict:
    """Conservative study eligibility, separate from the published exact match.

    The official SWIM metadata defines COMID as corrected and MovedCOMID as
    original. Move and OK - Shift are resolved categories, not failed matches.
    Adverse raw notes are retained for sensitivity until their conflict is resolved.
    """
    row = {k.lower(): v for k, v in (metadata or {}).items()}
    final = str(row.get("qc_final", "")).strip()
    reasons = []
    if final not in {"OK", "OK - Shift", "Move", "AutoRef"}:
        reasons.append("unrecognized_or_excluded_final_qc")
    if _comid(row.get("comid")) is None:
        reasons.append("missing_final_comid")
    notes = " ".join(str(row.get(k, "")) for k in ("qc_notes1", "rev_notes2", "ver_notes3", "gage_info")).lower()
    if re.search(r"\bdrop\b|\buncertain\b|not representative|no nhd flowline", notes):
        reasons.append("unresolved_adverse_review_notes")
    return {"primary_eligible": not reasons, "qc_final": final or None,
            "qc_exclusion_reasons": reasons, "qc_policy": QC_POLICY}


def exact_matches(observations: list[dict], gages: list[dict]) -> list[dict]:
    by_comid = {}
    for raw in gages:
        row = {k.lower(): v for k, v in raw.items()}
        comid = _comid(row.get("comid"))
        gage = str(row.get("gage_no") or "").strip()
        if comid is not None and re.fullmatch(r"\d{8,15}", gage):
            by_comid.setdefault(comid, {})[gage] = row
    results = []
    for observation in observations:
        comid = _comid(observation.get("comid"))
        matches = by_comid.get(comid, {})
        if not matches:
            results.append({**observation, "gage_no": None, "match_status": "missing_comid" if comid is None else "unmatched",
                            "match_basis": "exact_published_COMID", "gage_metadata": None})
        for gage, metadata in sorted(matches.items()):
            results.append({**observation, "gage_no": gage, "match_status": "exact_comid",
                            "match_basis": "exact_published_COMID", "gage_metadata": metadata, **gage_qc(metadata)})
    return results


def training_identifiers(sites: Path, benthos: Path) -> list[dict]:
    with sites.open(encoding="utf-8-sig", newline="") as stream:
        site_rows = list(csv.DictReader(stream))
    with benthos.open(encoding="utf-8-sig", newline="") as stream:
        benthos_rows = list(csv.DictReader(stream))
    by_visit = {(r.get("UID"), r.get("SITE_ID"), r.get("VISIT_NO")): r for r in benthos_rows}
    return [{"site_id": r.get("SITE_ID"), "master_siteid": r.get("MASTER_SITEID"),
             "uid": r.get("UID"), "visit_no": r.get("VISIT_NO"), "year": r.get("YEAR"),
             "index_visit": r.get("INDEX_VISIT"), "nars9": r.get("AGGR_ECO9_2015"),
             "benthic_condition": by_visit.get((r.get("UID"), r.get("SITE_ID"), r.get("VISIT_NO")), {}).get("BENT_MMI_COND"),
             "confirmed_model_training_member": None,
             "identifier_basis": "published_0809_survey_site_not_confirmed_training_roster"}
            for r in site_rows if str(r.get("YEAR")) in ("2008", "2009")]


def daily_values(cache: Cache, gage: str) -> list[dict]:
    if not re.fullmatch(r"\d{8,15}", gage):
        raise ValueError("Invalid official gage identifier")
    site = "USGS-" + gage
    url = DAILY + "?" + urlencode({"f": "json", "limit": 10000, "monitoring_location_id": site,
                                    "parameter_code": "00060", "statistic_id": "00003", "time": START + "/" + END})
    rows, seen_urls, seen_ids = [], set(), set()
    for _ in range(MAX_PAGES):
        if url in seen_urls:
            raise ValueError("Repeated daily pagination link")
        seen_urls.add(url)
        parsed = urlsplit(url)
        if parsed.hostname != "api.waterdata.usgs.gov" or not parsed.path.endswith("/collections/daily/items"):
            raise ValueError("Daily pagination escaped the official daily collection")
        key = hashlib.sha256(url.encode()).hexdigest()
        page = read_json(cache.fetch(url, f"daily/raw/{gage}/{key}.json"))
        if page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise ValueError("Invalid daily response")
        for feature in page["features"]:
            p = feature["properties"]
            date = str(p.get("time") or "")[:10]
            if (p.get("monitoring_location_id") != site or p.get("parameter_code") != "00060"
                    or p.get("statistic_id") != "00003" or not START <= date <= END):
                raise ValueError("Daily service returned an out-of-scope observation")
            identity = feature.get("id") or (p.get("time_series_id"), date)
            if identity in seen_ids:
                raise ValueError("Duplicate daily observation across pages")
            seen_ids.add(identity)
            rows.append({"id": feature.get("id"), **p})
        links = [link["href"] for link in page.get("links", []) if link.get("rel") == "next"]
        if not links:
            return rows
        if len(links) != 1:
            raise ValueError("Ambiguous daily pagination")
        url = links[0]
    raise ValueError("Daily pagination exceeded the bounded page count")


def compact_summary(folder: Path, manifest: dict, matches: list[dict]) -> dict:
    """Rebuild the compact consumer view from cached acquisition receipts only."""
    matched = {str(r["station_key"]) for r in matches if r.get("gage_no")}
    missing = {str(r["station_key"]) for r in matches if not r.get("gage_no")}
    gages = {r["gage_no"]: gage_qc(r.get("gage_metadata")) for r in matches if r.get("gage_no")}
    daily = manifest.get("daily", [])
    return {"schema_version": 1, "created_at": manifest["created_at"], "status": manifest["status"],
            "training_independence": "unknown", "training_roster_proof": None,
            "training_roster_status": "complete_model_training_roster_unavailable",
            "training_era_identifiers": manifest["training_era_identifiers"], "cohort": manifest["cohort"],
            "matched_station_keys": len(matched), "unmatched_station_keys": len(missing - matched),
            "partially_matched_station_keys": len(missing & matched),
            "unique_matched_gages": len(gages), "primary_eligible_gages": sum(q["primary_eligible"] for q in gages.values()),
            "sensitivity_only_gages": [{"gage_no": g, **q} for g, q in sorted(gages.items()) if not q["primary_eligible"]],
            "gages_with_daily_values": sum(r.get("rows", 0) > 0 for r in daily),
            "daily_observations": sum(r.get("rows", 0) for r in daily),
            "daily_status_counts": {s: sum(r.get("status") == s for r in daily) for s in sorted({r.get("status") for r in daily})},
            "sources_available": sum(r.get("status") == "available" for r in manifest.get("sources", [])),
            "failures": manifest.get("failures", []), "manifest": str(_output(folder, "manifest.json")),
            "qc_policy": QC_POLICY, "qc_source": manifest.get("qc_source"), "limits": manifest.get("limits", [])}


def acquire(root: Path, study: Path) -> dict:
    """Acquire metadata and exact-match daily records; never modify source caches."""
    import pyarrow as pa
    study = safe_study(root, study)
    folder = study / "acquisition"
    if not folder.resolve().is_relative_to(study):
        raise ValueError("Acquisition directory escapes study")
    observations, cohort_meta = cohort(root, study)
    cache, failures = Cache(folder), []
    downloaded = {}
    urls = {"epa/updates.html": EPA_UPDATES, "epa/data.html": EPA_DATA,
            "usgs/catalog.html": SWIM_CATALOG, "usgs/item.json": SWIM_ITEM,
            "usgs/daily-api.html": DAILY_DOC, **{"epa/" + k: v for k, v in EPA_FILES.items()}}
    for name, url in urls.items():
        try:
            downloaded[name] = cache.fetch(url, name)
        except RuntimeError as exc:
            failures.append(str(exc))
    training = []
    if all("epa/" + name in downloaded for name in ("sites_0809.csv", "benthos_0809.csv")):
        training = training_identifiers(downloaded["epa/sites_0809.csv"], downloaded["epa/benthos_0809.csv"])
    write_json(_output(folder, "training-era-identifiers.json"), {"status": "available" if training else "unavailable",
               "roster_status": "complete_model_training_roster_unavailable", "rows": training})
    identifiers = {str(r[k]).strip() for r in training for k in ("site_id", "master_siteid") if r.get(k)}
    overlap = []
    for row in observations:
        keys = {str(row[k]).strip() for k in ("site_id", "master_siteid", "station_key") if row.get(k)}
        overlap.append({**row, "training_era_identifier_match": sorted(keys & identifiers),
                        "confirmed_training_member": None, "independence": "unknown"})
    write_json(_output(folder, "model-independence.json"), {"status": "unknown", "rows": overlap,
               "note": "Survey identifiers are not the complete model-training roster. A nonmatch does not establish independent evaluation."})
    gages, qc_source = [], None
    if "usgs/item.json" in downloaded:
        item = read_json(downloaded["usgs/item.json"])
        files = item.get("files", []) + [f for facet in item.get("facets", []) for f in facet.get("files", [])]
        metadata = {f.get("downloadUri") or f.get("url") for f in files if f.get("name", "").lower() == "swim_gage_loc_metadata.xml"}
        if len(metadata) == 1:
            try:
                qc_source = info(cache.fetch(next(iter(metadata)), "usgs/SWIM_gage_loc_Metadata.xml"))
            except RuntimeError as exc:
                failures.append(str(exc))
        candidates = {f.get("downloadUri") or f.get("url") for f in files if f.get("name", "").lower() == "swim_gage_loc.dbf"}
        if len(candidates) == 1:
            try:
                gages = dbf_rows(cache.fetch(next(iter(candidates)), "usgs/SWIM_gage_loc.dbf"))
            except (RuntimeError, ValueError) as exc:
                failures.append(str(exc))
        else:
            failures.append("Official SWIM item does not identify one gage attributes file")
    matches = exact_matches(observations, gages)
    if not gages:
        for row in matches:
            row["match_status"] = "metadata_unavailable"
    write_json(_output(folder, "gage-matches.json"), {"cohort": cohort_meta, "rows": matches})
    daily = []
    for gage in sorted({r["gage_no"] for r in matches if r.get("gage_no")}):
        try:
            rows = daily_values(cache, gage)
            path = _output(folder, f"daily/{gage}.json")
            write_json(path, {"gage_no": gage, "start": START, "end": END, "rows": rows})
            daily.append({"gage_no": gage, "status": "available" if rows else "no_daily_values", "rows": len(rows), **info(path)})
        except (RuntimeError, ValueError) as exc:
            daily.append({"gage_no": gage, "status": "unavailable", "error": str(exc)})
            failures.append(str(exc))
    # Keep the machine-readable match table compact; detailed QC fields remain in JSON/raw DBF.
    flat = [{k: r.get(k) for k in ("station_key", "comid", "gage_no", "match_status", "match_basis", "primary_eligible", "qc_final")} for r in matches]
    schema = pa.schema([("station_key", pa.string()), ("comid", pa.int64()), ("gage_no", pa.string()),
                        ("match_status", pa.string()), ("match_basis", pa.string()),
                        ("primary_eligible", pa.bool_()), ("qc_final", pa.string())])
    for row in flat:
        row["station_key"] = str(row["station_key"]) if row["station_key"] is not None else None
    write_parquet(_output(folder, "gage_matches.parquet"), pa.Table.from_pylist(flat, schema=schema))
    matched_keys = {str(r["station_key"]) for r in matches if r.get("gage_no")}
    missing_keys = {str(r["station_key"]) for r in matches if not r.get("gage_no")}
    manifest = {"schema_version": 1, "created_at": now(), "status": "partial" if failures else "complete",
                "cohort": cohort_meta, "sources": cache.sources, "failures": failures,
                "daily": daily, "daily_parameter": "00060", "daily_statistic": "00003", "start": START, "end": END,
                "gage_match_basis": "exact_published_COMID", "unique_matched_gages": len(daily),
                "qc_policy": QC_POLICY, "qc_source": qc_source,
                "matched_station_keys": len(matched_keys), "unmatched_station_keys": len(missing_keys - matched_keys),
                "partially_matched_station_keys": len(missing_keys & matched_keys),
                "training_era_identifiers": len(training), "model_training_roster": "unavailable",
                "model_independence": "unknown", "limits": [
                    "SWIM is a static 2021 publication with historical site eligibility; unmatched does not mean no nearby gage exists.",
                    "Only exact published COMID matches are fetched. No snapping or upstream substitution.",
                    "Daily values retain all source qualifiers and units; no flow statistics or validity claim is made here.",
                    "2008/09 survey membership and model-training membership are not equivalent."]}
    write_json(_output(folder, "manifest.json"), manifest)
    write_json(_output(folder, "summary.json"), compact_summary(folder, manifest, matches))
    return manifest
