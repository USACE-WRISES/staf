"""USGS NWIS gage statistics for SFARI's flow evidence (bundle v2, Phase 3).

SFARI #13, #14 and #15 (``apps/sfari/sfari/datasources/nwis.py``) take the discharge gages with
daily values in a box of +/-0.25 degree around the point, rank them by drainage-area ratio, and
read the first ranked gage with at least 60 days of daily mean flow since 2014-01-01: zero-flow
share, Q10 / Q50 / Q90 (flow exceeded 10, 50, 90 percent of the time) and Q90/Q50. This pulls, once,
every such gage around the regions and computes the same statistics, so the app keeps its box and
ranking and reads the table instead of NWIS (``tables/nwis_gages.parquet``; ``as_of`` records the
end of the record, which grows with every refresh).
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from statistics import median
from typing import Callable

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from . import sources

SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
DV_URL = "https://waterservices.usgs.gov/nwis/dv/"
START = "2014-01-01"
BOX_PAD = 0.25
BATCH = 25


def log_default(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _get(url: str, params: dict, timeout: int = 180):
    for attempt in range(1, 6):
        try:
            r = requests.get(url, params=params, headers=sources.UA, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 404:                  # NWIS answers "no sites" with 404
                return None
        except requests.RequestException:
            pass
        time.sleep(10 * attempt)
    raise RuntimeError(f"NWIS did not answer {url}")


def sites_in_box(w: float, s: float, e: float, n: float) -> list[dict]:
    params = {"format": "rdb", "bBox": f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}", "parameterCd": "00060",
              "siteType": "ST", "hasDataTypeCd": "dv", "siteStatus": "all", "siteOutput": "expanded"}
    r = _get(SITE_URL, params)
    if r is None:
        return []
    lines = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    if len(lines) < 3:
        return []
    header = lines[0].split("\t")
    col = dict((name, i) for i, name in enumerate(header))
    out = []
    for ln in lines[2:]:
        c = ln.split("\t")
        try:
            da = c[col["drain_area_va"]].strip() if "drain_area_va" in col else ""
            out.append({"site": c[col["site_no"]], "name": c[col["station_nm"]].strip(),
                        "lat": float(c[col["dec_lat_va"]]), "lon": float(c[col["dec_long_va"]]),
                        "da_sqmi": float(da) if da else None})
        except (KeyError, ValueError, IndexError):
            continue
    return out


def _percentile(asc: list, p: float) -> float:
    if not asc:
        return 0.0
    k = (len(asc) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(asc) - 1)
    return asc[lo] + (asc[hi] - asc[lo]) * (k - lo)


def daily_stats(sites: list[str]) -> dict:
    """``{site: stats}`` from daily mean discharge since 2014 (SFARI's rule and rounding)."""
    r = _get(DV_URL, {"format": "json", "sites": ",".join(sites), "parameterCd": "00060",
                      "statCd": "00003", "startDT": START})
    out = {}
    if r is None:
        return out
    for ts in r.json().get("value", {}).get("timeSeries", []):
        site = ts.get("sourceInfo", {}).get("siteCode", [{}])[0].get("value")
        vals, last = [], None
        for block in ts.get("values", [])[:1]:                 # the app reads the first series only
            for p in block.get("value", []):
                try:
                    v = float(p.get("value"))
                except (TypeError, ValueError):
                    continue
                if v >= 0:
                    vals.append(v)
                    last = p.get("dateTime", "")[:10]
        if site is None or site in out:
            continue
        if len(vals) < 60:
            out[site] = {"n_days": len(vals), "as_of": last}
            continue
        asc = sorted(vals)
        q50 = median(asc)
        q90 = _percentile(asc, 10.0)
        out[site] = {"n_days": len(asc), "zero_frac": round(sum(1 for v in asc if v <= 0.0) / len(asc), 4),
                     "q10": round(_percentile(asc, 90.0), 1), "q50": round(q50, 1), "q90": round(q90, 2),
                     "baseflow_ratio": round(q90 / q50, 3) if q50 > 0 else None, "as_of": last}
    return out


#: a stopped pull resumes from these (kept in ``out_dir`` until the table is written)
SITES_CACHE = "nwis_sites_cache.json"
DAILY_CACHE = "nwis_daily_cache.jsonl"


def gages(vpus: list[str], out_dir: Path, log: Callable = log_default) -> dict:
    """The gage table for ``vpus``. The gages found per region and each batch's statistics are
    kept as they arrive (``SITES_CACHE``, ``DAILY_CACHE``), so a pull NWIS stops part way (it
    answers 503 at times) starts again where it stopped; both go once the table is written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sites_path, daily_path = out_dir / SITES_CACHE, out_dir / DAILY_CACHE
    by_region: dict = json.loads(sites_path.read_text(encoding="utf-8")) if sites_path.exists() else {}
    for vpu, g in sources.region_outlines([v for v in vpus if v not in by_region]).items():
        w, s, e, n = g.bounds
        w, s, e, n = w - BOX_PAD, s - BOX_PAD, e + BOX_PAD, n + BOX_PAD
        boxes = [(w, s, e, n)]
        if (e - w) * (n - s) > 24:                             # the site service caps a box at 25 sq deg
            mid = (w + e) / 2
            boxes = [(w, s, mid, n), (mid, s, e, n)]
        by_region[vpu] = [site for b in boxes for site in sites_in_box(*b)]
        tmp = sites_path.with_suffix(".part")
        tmp.write_text(json.dumps(by_region), encoding="utf-8", newline="\n")
        tmp.replace(sites_path)
        log(f"[{vpu}] NWIS: {len(by_region[vpu])} gages in the region box")
    found: dict = {}
    for vpu in vpus:
        for site in by_region.get(vpu, []):
            found.setdefault(site["site"], site)
    stats: dict = {}
    if daily_path.exists():
        for line in daily_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                stats[rec["site"]] = rec["stats"]
    ids = [site for site in sorted(found) if site not in stats]
    log(f"NWIS: {len(found)} gages, {len(found) - len(ids)} already read")
    with open(daily_path, "a", encoding="utf-8", newline="\n") as fh:
        for i in range(0, len(ids), BATCH):
            batch = ids[i:i + BATCH]
            got = daily_stats(batch)
            for site in batch:
                stats[site] = got.get(site, {"n_days": 0})
                fh.write(json.dumps({"site": site, "stats": stats[site]}) + "\n")
            fh.flush()
            if (i // BATCH) % 10 == 0:
                log(f"NWIS daily values: {min(i + BATCH, len(ids))}/{len(ids)} gages")
            time.sleep(0.5)
    ids = sorted(found)
    rows = [dict(found[s], **stats.get(s, {"n_days": 0})) for s in ids]
    keys = ("site", "name", "lat", "lon", "da_sqmi", "n_days", "zero_frac", "q10", "q50", "q90", "baseflow_ratio", "as_of")
    table = pa.table(dict((k, pa.array([r.get(k) for r in rows])) for k in keys))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "nwis_gages.parquet"
    pq.write_table(table, path, compression="zstd", compression_level=19)
    usable = sum(1 for r in rows if (r.get("n_days") or 0) >= 60)
    result = {"gages": len(rows), "usable": usable, "start": START, "pulled": date.today().isoformat(),
              "bytes": path.stat().st_size}
    (out_dir / "nwis_gages.json").write_text(json.dumps(dict(result, regions=vpus), indent=1), encoding="utf-8",
                                              newline="\n")
    sites_path.unlink(missing_ok=True)
    daily_path.unlink(missing_ok=True)
    log(f"NWIS gages: {result}")
    return result
