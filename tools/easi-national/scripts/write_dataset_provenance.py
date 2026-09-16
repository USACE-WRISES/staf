"""Write the provenance record of the EASI development dataset.

The record answers, from the files themselves, what a reader needs to reproduce
or cite the 16-state development dataset: states and reaches, source datasets
and their vintages, the date generated, the source commit and method digest,
the processing assumptions, the stages that ran and the outputs.

Reads the data root (chunk definitions, national caches), a completed bundle
(``<bundle>/staging/manifest.json``, ``completion.json``, ``stats.json``, the
rollout receipts beside it) and the builder's own constants. Writes
``provenance.json`` and ``development-dataset.md`` into ``--out``.

    python scripts/write_dataset_provenance.py --bundle D:/Data/easi-national/review/alternative-2-rollout \
        --out tools/easi-national/docs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
REPO = TOOL.parent.parent
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(REPO / "apps" / "easi"))

from builder import config, pipeline  # noqa: E402

# The first commit whose scoring digest is the release method. Recorded as a
# literal because the digest identifies the method, not the commit.
METHOD_FIRST_COMMIT = "02f39a8"
DATASET_TAG = config.DATASET_TAG

# Source datasets and how each entered the build. Vintages that a file cannot
# state (a portal pull has no version) are given as the pull date of the cache.
SOURCES = [
    ("NHDPlus V2 seamless geodatabase (EPA, NHDPlusV21_NationalData_Seamless_Geodatabase_Lower48_07)",
     "flowline geometry, value-added attributes, EROM monthly flows, HUC12 boundaries",
     "national/flowlines.parquet", "national/erom.parquet", "national/huc12.parquet"),
    ("EPA StreamCat API (https://api.epa.gov/StreamCat)",
     "watershed, catchment and riparian-corridor landscape summaries; prg_bmmi0809 at the other area of interest",
     "national/streamcat.parquet"),
    ("EPA ATTAINS assessment geodatabase and MapServer",
     "integrated-report category of the assessment unit at or near the reach",
     "national/attains.parquet"),
    ("USGS Nonindigenous Aquatic Species database (NAS API)",
     "established non-native taxa per HUC12",
     "national/nas.parquet"),
    ("USACE National Inventory of Dams (NID FeatureServer)",
     "mapped dams within one mile of the reach anchor",
     "national/nid.parquet"),
    ("Water Quality Portal (WQX3 result search, monthly national pull)",
     "total nitrogen and total phosphorus results within five miles and ten years",
     "national/wqp/wqp_results.parquet"),
    ("USGS 3DEP elevation (1 m lidar projects, 1/9 arc-second quads, 10 m seamless)",
     "reach cross-sections for the bank-height and entrenchment ratios",
     "national/dem/1m/tiles.parquet", "national/dem/19/quads.parquet"),
    ("Census cartographic state boundaries (1:500k)",
     "the state of every flowline by its midpoint",
     "national/comid_state.parquet"),
    ("EPA NARS nine aggregate ecoregions and Level III ecoregions (app data)",
     "the regional criteria and the reference-curve stratum",
     None),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parquet_rows(path: Path):
    try:
        import pyarrow.parquet as pq
        return pq.read_metadata(path).num_rows
    except Exception:
        return None


def mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()
    except Exception:
        return "unknown"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=config.DEFAULT_ROOT)
    ap.add_argument("--bundle", type=Path, required=True, help="a completed bundle root with staging/")
    ap.add_argument("--out", type=Path, default=TOOL / "docs")
    args = ap.parse_args()
    root, bundle = args.root, args.bundle
    staging = bundle / "staging"
    manifest = read_json(staging / "manifest.json")
    completion = read_json(staging / "completion.json")
    stats = read_json(staging / "stats.json")
    build = read_json(bundle / "build.json") if (bundle / "build.json").exists() else {}
    timing = read_json(bundle / "timing.json") if (bundle / "timing.json").exists() else {}
    producer = {}
    for cand in (bundle / "verification" / "producer_provenance.json",):
        if cand.exists():
            producer = read_json(cand)

    # --- states from the chunk definitions -----------------------------------
    chunks = []
    for chunk_json in sorted((root / "chunks").glob("*/chunk.json")):
        c = read_json(chunk_json)
        chunks.append({"id": c["id"], "kind": c["kind"], "label": c.get("label"),
                       "states": c.get("states"), "huc8s": len(c.get("huc8s", [])),
                       "n_comids": c.get("n_comids"), "vpus": c.get("vpus"), "created": c.get("created")})
    state_chunks = [c for c in chunks if c["kind"] == "state"]
    requested = sorted({s for c in state_chunks for s in (c["states"] or [])})
    coverage = {}
    for code, row in (stats.get("states") or {}).items():
        coverage[code] = {"name": row.get("name"), "n_scored": row.get("n_scored"),
                          "n_total": row.get("n_total"), "coverage": row.get("coverage")}
    full = sorted(code for code, row in coverage.items() if (row.get("coverage") or 0) >= 0.5)

    # --- sources with vintages ----------------------------------------------
    sources = []
    for name, role, *files in SOURCES:
        entry = {"source": name, "role": role, "caches": []}
        for rel in files:
            if rel is None:
                continue
            path = root / rel
            entry["caches"].append({"file": rel, "exists": path.exists(), "modified": mtime(path),
                                    "rows": parquet_rows(path) if path.suffix == ".parquet" else None,
                                    "bytes": path.stat().st_size if path.exists() else None})
        sources.append(entry)
    sources.append({"source": "WQP window", "role": "calendar months pulled",
                    "value": f"{config.WQP_MONTHLY_START} to the month before the pull; "
                             f"{config.WQP_YEARS} years before the assessment date are used per reach"})

    # --- assumptions from the builder constants and the curve provenance ------
    curves_prov = {}
    curves_path = REPO / "apps" / "easi" / "data" / "reference-curves.json"
    if curves_path.exists():
        curves_prov = read_json(curves_path).get("provenance", {})
    assumptions = {
        "reach_length_ft": manifest.get("reach_length_ft", config.REACH_LENGTH_FT),
        "reach_source": manifest.get("reach_source"),
        "chunk_buffer_mi": config.CHUNK_BUFFER_MI,
        "wqp_radius_mi": config.WQP_RADIUS_MI,
        "wqp_years": config.WQP_YEARS,
        "nid_radius_mi": config.NID_RADIUS_MI,
        "attains_buffer_m": config.ATTAINS_BUFFER_M,
        "streamcat_aois": list(config.STREAMCAT_AOIS) + ["other (prg_bmmi0809)"],
        "nrsa_as_of": config.NRSA_AS_OF.isoformat(),
        "state_of_a_reach": "the Census state polygon containing the flowline midpoint; nearest polygon for coastal midpoints",
        "cross_sections": manifest.get("dem"),
        "tier": manifest.get("tier"),
        "reference_screen": curves_prov.get("screen"),
        "reference_panel_floors": curves_prov.get("panelFloors"),
        "scores_from": "stored evidence through easi.assessment.assess_preloaded with the application's adapters; no per-reach network call",
    }

    # --- the bundle identity ---------------------------------------------------
    assets = []
    for block in (manifest.get("assets") or {}).values():
        assets.append(block)
    for unit in (manifest.get("units") or {}).values():
        assets.append(unit["evidence"])
    for block in (manifest.get("scores") or {}).values():
        assets.append(block)
    for block in (manifest.get("tiles") or {}).values():
        assets.append(block)
    total_bytes = sum(int(a.get("bytes") or 0) for a in assets)
    units = manifest.get("units") or {}
    record = {
        "record_version": 1,
        "written": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": {
            "name": "EASI development dataset, 16 states, September 2026",
            "status": "development dataset for methodology evaluation and refinement; not an assessment product",
            "release_tag": DATASET_TAG,
            "release_is_prerelease": True,
            "alternative_id": manifest.get("alternative_id"),
            "build_id": manifest.get("build_id"),
            "method_version": manifest.get("method_version"),
            "criteria_set": manifest.get("criteria_set"),
            "schema_version": manifest.get("schema_version"),
            "vintage": manifest.get("vintage"),
            "reaches_scored": manifest.get("reaches_scored"),
            "huc8s_scored": stats.get("huc8s"),
            "huc4_units_published": sum(1 for u in units.values() if (u.get("huc8s_scored") or 0) > 0),
            "huc4_units_complete": sum(1 for u in units.values() if u.get("status") == "complete"),
            "huc4_units_partial": sum(1 for u in units.values() if u.get("status") == "partial"),
            "huc4_units_total": manifest.get("units_total"),
            "tile_partitions": len(manifest.get("tiles") or {}),
            "assets": len(assets) + 1,
            "asset_bytes": total_bytes,
            "manifest_sha256": sha256(staging / "manifest.json"),
            "completion_sha256": sha256(staging / "completion.json"),
            "stats_sha256": sha256(staging / "stats.json"),
            "build_created": build.get("created_at"),
            "build_completed": timing.get("completed_at"),
            "staging_updated": manifest.get("updated"),
        },
        "code": {
            "repository": "https://github.com/USACE-WRISES/staf",
            "record_written_at_commit": git("rev-parse", "--short", "HEAD"),
            "method_first_commit": METHOD_FIRST_COMMIT,
            "builder": "tools/easi-national (builder.worker, builder.alternative2_rollout)",
            "scorer": "apps/easi (easi.assessment.assess_preloaded, easi.national.client.score_record)",
            "chunk_stages": list(pipeline.CHUNK_STAGES),
            "huc8_stages": list(pipeline.HUC8_STAGES),
            "tiles": producer.get("tippecanoe") or producer.get("tippecanoe_version") or "tippecanoe in the staf-tippecanoe Docker image",
            "producer_provenance": producer or None,
        },
        "states": {
            "requested": requested,
            "requested_count": len(requested),
            "chunks": chunks,
            "at_least_half_screened": full,
            "coverage_by_state": coverage,
        },
        "sources": sources,
        "assumptions": assumptions,
        "history": [
            {"method_version": "477bf7771e24", "note": "published to the prerelease 2026-09-14 under the pre-revision criteria (national bands); superseded"},
            {"method_version": "e9f472b31fe5", "note": "Alternative 1, Level II reference curves (62 curves), local staging 2026-09-15; the controlled study's retained control"},
            {"method_version": manifest.get("method_version"), "note": "Alternative 2, NARS-9 reference curves (34 curves), adopted 2026-09-16; the release method"},
        ],
    }

    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / "provenance.json"
    json_path.write_text(json.dumps(record, indent=1, sort_keys=False) + "\n", encoding="utf-8")

    # --- the readable record -----------------------------------------------------
    d = record["dataset"]
    lines = []
    w = lines.append
    w("# EASI development dataset, 16 states, September 2026")
    w("")
    w(f"Provenance record written {record['written']} by `scripts/write_dataset_provenance.py` "
      f"(machine-readable copy: `provenance.json`). This dataset was built to evaluate and refine the "
      "EASI screening criteria across a broad range of settings. It is a development dataset, not a "
      "nationwide assessment product, and every score in it is an automated, unreviewed screening result.")
    w("")
    w("## Identity")
    w("")
    w("| Item | Value |")
    w("|---|---|")
    w(f"| Release | GitHub prerelease `{d['release_tag']}` on USACE-WRISES/staf (always a prerelease) |")
    w(f"| Bundle | build `{d['build_id']}`, {d['alternative_id']} |")
    w(f"| Scoring method digest | `{d['method_version']}` (criteria set {d['criteria_set']}, first at commit `{METHOD_FIRST_COMMIT}`) |")
    w(f"| Reaches scored | {d['reaches_scored']:,} on {d['huc8s_scored']:,} HUC8s in {d['huc4_units_published']} of "
      f"{d['huc4_units_total']} HUC4 units ({d['huc4_units_complete']} complete, {d['huc4_units_partial']} partial at the "
      f"footprint edge), {d['tile_partitions']} tile partitions |")
    w(f"| Built | {d['build_created']} to {d['build_completed']} (staging updated {d['staging_updated']}) |")
    w(f"| Assets | {d['assets']} files, {d['asset_bytes'] / 1e9:.2f} GB |")
    w(f"| manifest.json sha256 | `{d['manifest_sha256']}` |")
    w(f"| completion.json sha256 | `{d['completion_sha256']}` |")
    w(f"| Record written at commit | `{record['code']['record_written_at_commit']}` |")
    w("")
    w("## States")
    w("")
    w(f"Sixteen state batches were requested: {', '.join(requested)}. Every HUC8 that touches a requested "
      "state was processed whole, so the footprint spills into neighbouring states. A reach belongs to the "
      "state containing the midpoint of its flowline.")
    w("")
    w("| Batch | HUC8s | Reaches in batch | VPUs | Created |")
    w("|---|---:|---:|---|---|")
    for c in chunks:
        w(f"| {c['id']} ({c['label']}) | {c['huc8s']} | {c['n_comids']:,} | {', '.join(c['vpus'] or [])} | {c['created']} |")
    w("")
    w("| State | Scored reaches | Reaches in state | Coverage |")
    w("|---|---:|---:|---:|")
    for code in sorted(coverage, key=lambda k: -(coverage[k].get("coverage") or 0)):
        row = coverage[code]
        if (row.get("coverage") or 0) < 0.5:
            continue
        w(f"| {code} {row.get('name') or ''} | {row.get('n_scored') or 0:,} | {row.get('n_total') or 0:,} | {row.get('coverage') or 0:.3f} |")
    w("")
    w("States under half coverage are border spill and are listed in `provenance.json`.")
    w("")
    w("## Source datasets")
    w("")
    w("| Source | Role | Cache | Modified | Rows |")
    w("|---|---|---|---|---:|")
    for s in sources:
        if s.get("caches"):
            for c in s["caches"]:
                w(f"| {s['source']} | {s['role']} | `{c['file']}` | {c['modified'] or 'absent'} | {c['rows'] if c['rows'] is not None else ''} |")
        else:
            w(f"| {s['source']} | {s['role']} | {s.get('value', '')} | | |")
    w("")
    w("## Processing")
    w("")
    w(f"Chunk stages per state batch: {', '.join(pipeline.CHUNK_STAGES)}. HUC8 stages: "
      f"{', '.join(pipeline.HUC8_STAGES)}. Scores are computed from the stored evidence with the "
      "application's own adapters (`easi.assessment.assess_preloaded`); the national path makes no "
      "per-reach network call. Tiles are cut per vector processing unit with tippecanoe in the "
      "`staf-tippecanoe` Docker image. The Alternative 2 bundle was scored by "
      "`builder.alternative2_rollout` from the unchanged Alternative 1 evidence.")
    w("")
    w("| Assumption | Value |")
    w("|---|---|")
    for k, v in assumptions.items():
        w(f"| {k} | {json.dumps(v) if not isinstance(v, str) else v} |")
    w("")
    w("## Method history")
    w("")
    for h in record["history"]:
        w(f"- `{h['method_version']}`: {h['note']}")
    w("")
    w("## Outputs")
    w("")
    w("Per bundle: `manifest.json`, `completion.json`, `stats.json`, `coverage.geojson`, `comid_huc4.parquet`, "
      "one `evidence_<huc4>.parquet` per HUC4 unit, one `scores_<vpu>.parquet` and one `tiles_<vpu>.pmtiles` "
      "per vector processing unit. The evidence files hold every network-derived input per reach; the scores "
      "files hold the 20 function ratings, indices and scores plus the sub-indices and the ECI. Every asset "
      "carries its sha256 in the manifest.")
    w("")
    (args.out / "development-dataset.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {json_path} and development-dataset.md: {d['reaches_scored']:,} reaches, "
          f"{len(requested)} states, method {d['method_version']}")


if __name__ == "__main__":
    main()
