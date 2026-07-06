"""Batch-run EASI at the SFARI verification sites and assemble the comparison data.

For each site in ``docs/Verification Sites/SFARI Summary.xlsx`` this runs the EASI
pipeline (delineate + assess) at the site's coordinates and records EASI's
sub-indices, ECI, and per-function ratings/scores next to the SFARI field result.
Mink Brook (site "MB") is run with the three expert overrides (High flow dynamics,
Floodplain connectivity, Channel evolution -> Good).

Outputs (under docs/EASI_Documentation/data/):
  easi_site_reports/<site_id>.json   full per-site record (incl. EASI report)
  sfari_easi_comparison.csv          one row per site: EASI vs SFARI sub-indices + ECI
  function_comparison.csv            long format: one row per (site, function)

Resumable: a site whose JSON already exists is reused unless --force is given.

Usage:
  python scripts/run_sfari_sites.py            # all sites (resumes)
  python scripts/run_sfari_sites.py --force    # re-run everything
  python scripts/run_sfari_sites.py --only MB,CC,MC
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
import sys
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import sfari_data  # noqa: E402
from easi import config, pipeline  # noqa: E402

OUT = os.path.join(ROOT, "docs", "EASI_Documentation", "data")
REP_DIR = os.path.join(OUT, "easi_site_reports")

_META = config.metrics_by_id()
# functionId -> display metadata (for the CSV)
FID_META = {m["functionId"]: {"functionName": m["functionName"],
                              "discipline": m["discipline"]}
            for m in _META.values()}
# the 19 EASI functions that have a SFARI counterpart, in metric order
MATCHED_FIDS = [m["functionId"] for m in _META.values()
                if m["functionId"] != sfari_data.EASI_FUNCTION_NO_SFARI]


async def run_one(site: dict, timeout: float = 220.0) -> dict:
    overrides = (sfari_data.MINK_BROOK_OVERRIDES
                 if site["site_id"] == "MB" else None)
    last_err = "unknown"
    for _ in range(2):  # one retry on transient network failure
        try:
            r = await asyncio.wait_for(
                pipeline.run_analysis(site["lat"], site["lon"],
                                      overrides=overrides), timeout=timeout)
            if r.get("status") == "ok":
                return r
            last_err = r.get("message", "unknown")
        except Exception as exc:  # noqa: BLE001
            last_err = repr(exc)
        await asyncio.sleep(3)
    return {"status": "error", "message": last_err}


def easi_functions(rep: dict) -> dict:
    out = {}
    for row in rep.get("metricRows", []):
        out[row["functionId"]] = {
            "rating": row.get("rating"),
            "functionScore": row.get("functionScore"),
            "status": row.get("status"),
            "valueText": row.get("valueText"),
            "metricName": row.get("name"),
        }
    return out


def build_record(site: dict, r: dict) -> dict:
    rec = {
        "site": {k: site[k] for k in ("site_id", "name", "state", "lat", "lon",
                                      "raw_lat", "raw_lon", "sfari_duplicate",
                                      "duplicate_of")},
        "sfari": {"functions": site["sfari_functions"],
                  "planform_change": site["sfari_planform_change"],
                  "sub": site["sfari_sub"], "eci": site["sfari_eci"]},
        "status": r.get("status"),
    }
    if r.get("status") == "ok":
        rep, d = r["report"], r.get("delineation", {})
        rec["easi"] = {
            "subIndices": rep.get("subIndices"),
            "eci": rep.get("ecosystemConditionIndex"),
            "functions": easi_functions(rep),
            "computedCount": rep.get("computedCount"),
            "totalCount": rep.get("totalCount"),
            "overridesApplied": rep.get("overridesApplied"),
        }
        rec["delineation"] = {
            "comid": d.get("comid"), "gnis_name": d.get("gnis_name"),
            "drainage_area_sqkm": d.get("drainage_area_sqkm"),
            "watershed_area_sqkm": d.get("watershed_area_sqkm"),
        }
        rec["report"] = rep  # full report (cross-section + basin) for case studies
    else:
        rec["error"] = r.get("message")
    return rec


def write_csvs(results: dict) -> None:
    # site-level: EASI vs SFARI sub-indices + ECI
    sp = os.path.join(OUT, "sfari_easi_comparison.csv")
    with open(sp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["site_id", "name", "state", "lat", "lon", "sfari_duplicate",
                    "duplicate_of", "status", "comid", "drainage_area_sqkm",
                    "easi_eci", "sfari_eci",
                    "easi_physical", "sfari_physical",
                    "easi_chemical", "sfari_chemical",
                    "easi_biological", "sfari_biological"])
        for rec in results.values():
            s, sf = rec["site"], rec["sfari"]
            e = rec.get("easi") or {}
            esi = e.get("subIndices") or {}
            d = rec.get("delineation") or {}
            w.writerow([
                s["site_id"], s["name"], s["state"], s["lat"], s["lon"],
                s["sfari_duplicate"], s["duplicate_of"], rec.get("status"),
                d.get("comid"), d.get("drainage_area_sqkm"),
                e.get("eci"), sf["eci"],
                esi.get("physical"), sf["sub"]["physical"],
                esi.get("chemical"), sf["sub"]["chemical"],
                esi.get("biological"), sf["sub"]["biological"],
            ])

    # function-level long format (matched functions only)
    fp = os.path.join(OUT, "function_comparison.csv")
    with open(fp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["site_id", "name", "sfari_duplicate", "functionId",
                    "functionName", "discipline", "easi_rating",
                    "easi_score", "sfari_score"])
        for rec in results.values():
            if rec.get("status") != "ok":
                continue
            s = rec["site"]
            efs = rec["easi"]["functions"]
            sfs = rec["sfari"]["functions"]
            for fid in MATCHED_FIDS:
                ef = efs.get(fid, {})
                w.writerow([
                    s["site_id"], s["name"], s["sfari_duplicate"], fid,
                    FID_META.get(fid, {}).get("functionName", fid),
                    FID_META.get(fid, {}).get("discipline", ""),
                    ef.get("rating"), ef.get("functionScore"),
                    sfs.get(fid),
                ])
    print(f"wrote {sp}\nwrote {fp}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    sites = sfari_data.read_sites()
    if args.only:
        keep = {x.strip() for x in args.only.split(",")}
        sites = [s for s in sites if s["site_id"] in keep]
    os.makedirs(REP_DIR, exist_ok=True)

    results: dict[str, dict] = {}
    for i, site in enumerate(sites, 1):
        sid = site["site_id"]
        jpath = os.path.join(REP_DIR, f"{sid}.json")
        if os.path.exists(jpath) and not args.force:
            with open(jpath, encoding="utf-8") as f:
                results[sid] = json.load(f)
            print(f"[{i}/{len(sites)}] {sid} cached "
                  f"(status={results[sid].get('status')})", flush=True)
            continue
        print(f"[{i}/{len(sites)}] {sid} {site['name']} running...", flush=True)
        t0 = time.time()
        r = await run_one(site)
        dt = round(time.time() - t0, 1)
        rec = build_record(site, r)
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1)
        results[sid] = rec
        if r.get("status") == "ok":
            print(f"    ok in {dt}s  ECI EASI={rec['easi']['eci']} "
                  f"SFARI={site['sfari_eci']:.3f}", flush=True)
        else:
            print(f"    ERROR in {dt}s: {r.get('message')}", flush=True)

    write_csvs(results)
    ok = sum(1 for r in results.values() if r.get("status") == "ok")
    print(f"\nCoverage: {ok}/{len(results)} sites ran EASI successfully.")
    fails = [sid for sid, r in results.items() if r.get("status") != "ok"]
    if fails:
        print("Failed:", ", ".join(fails))


if __name__ == "__main__":
    asyncio.run(main())
