"""The station-level NRSA biological indices table for Round 2 (outcome O3).

One row per NRSA station (``station_key``): for each index (benthic MMI, O/E, fish
MMI) the score and the condition class of the NEWEST cycle that published one for
that station, and the cycle it came from, read from EPA's own files as
``config/round2/bio_indices_sources.yaml`` names them. Chlorophyll has no published
class in any cycle, so its column is empty and the manifest says so. Nothing is
ever filled: a cycle that published no index for a station leaves the cell empty.

Stations are matched through the archive's own tables (``data/nrsa/site_visits.parquet``,
the (cycle, SITE_ID, VISIT_NO) -> station_key resolution of
``scripts/nrsa/build_station_tables.py``), so a station here is the station every
build and the hierarchy harness know.

    python scripts/build_bio_indices.py --out D:/Data/staf-campaign-2026-09/baseline/bio-indices
    python scripts/build_bio_indices.py --out <root> --raw <nrsa_raw folder> [--protocol <yaml>]

Writes ``bio_indices.csv``, ``bio_indices.parquet`` and ``bio_indices_manifest.json``
(source files with sha256 and the lock's sha256, columns read, station counts per
cycle and index, unmatched site ids, and the not-published statements). The table
never lands under ``apps/``. The build refuses a raw file whose sha256 differs from
the lock unless ``--allow-drift`` is passed, because a republished EPA file is a
different table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_ROOT / "scripts" / "nrsa") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "scripts" / "nrsa"))

from nrsa_io import normalize_visit_no, read_epa_csv  # noqa: E402

DEFAULT_RAW = REPO_ROOT / "notes" / "DEEP_Working" / "nrsa_raw"
DEFAULT_ARCHIVE = APP_ROOT / "data" / "nrsa"
DEFAULT_SOURCES = APP_ROOT / "config" / "round2" / "bio_indices_sources.yaml"
LOCK_FILE = "sources.lock.json"
CYCLES_NEWEST_FIRST = ("2324", "1819", "1314")
INDEX_VISIT = "1"
CLASSES = ("Good", "Fair", "Poor")
INDICES = ("benthic_mmi", "oe", "fish_mmi")
TABLE_NAME = "bio_indices"
MANIFEST_NAME = "bio_indices_manifest.json"
COLUMNS = ["station_key", "us_l3code", "ag_eco9", "huc8", "cycles_sampled",
           "benthic_mmi_cycle", "mmi_bent", "benthic_mmi_class",
           "oe_cycle", "oe_score", "oe_class",
           "fish_mmi_cycle", "mmi_fish", "fish_mmi_class",
           "chlorophyll_class"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_sources(path: Path) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if doc.get("schema") != "bio-indices-sources/1":
        raise SystemExit(f"{path} is not a bio-indices sources table")
    return doc


def recode_class(index: str, value: Any, sources: dict) -> Optional[str]:
    """The published class as Good / Fair / Poor, None for anything else (Not
    Assessed, blank, unknown text). The O/E labels are recoded by the table."""
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in ("not assessed", "na", "nan", "none", "null"):
        return None
    recode = ((sources.get("indices") or {}).get(index) or {}).get("class_recode") or {}
    if recode:
        token = text.replace(" ", "")
        for label, cls in recode.items():
            if str(label).replace(" ", "").upper() == token.upper():
                return str(cls)
        return None
    title = text.capitalize()
    return title if title in CLASSES else None


def _number(value: Any) -> Optional[float]:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def archive_visits(archive: Path) -> pd.DataFrame:
    """(cycle, site_id, visit_no) -> station_key from the archive's visit table."""
    p = archive / "site_visits.parquet"
    if not p.is_file():
        raise SystemExit(f"missing {p}: the archive's station tables are the matching record")
    df = pd.read_parquet(p, columns=["cycle", "site_id", "visit_no", "station_key"])
    df["cycle"] = df["cycle"].astype(str)
    df["site_id"] = df["site_id"].astype(str).str.strip()
    df["visit_no"] = normalize_visit_no(df["visit_no"])
    df["station_key"] = df["station_key"].astype(str)
    return df


def archive_stations(archive: Path) -> pd.DataFrame:
    p = archive / "stations.parquet"
    if not p.is_file():
        raise SystemExit(f"missing {p}")
    df = pd.read_parquet(p, columns=["station_key", "us_l3code", "ag_eco9", "huc8", "cycles_sampled"])
    df["station_key"] = df["station_key"].astype(str)
    return df


def read_index_file(path: Path, cycle: str, provides: dict, sources: dict) -> pd.DataFrame:
    """The index visits of one raw file as ``site_id, visit_no`` plus, per index it
    provides, ``<index>_score`` and ``<index>_class`` (missing where the file has no
    such column). Rows of other visits are dropped."""
    wanted = {"SITE_ID", "VISIT_NO", "INDEX_VISIT"}
    for cols in provides.values():
        wanted |= {str(c).upper() for c in cols}
    df = read_epa_csv(path, usecols=lambda c: str(c).replace("\ufeff", "").strip().upper() in wanted)
    if "SITE_ID" not in df.columns:
        raise SystemExit(f"{path} has no SITE_ID column")
    out = pd.DataFrame({"site_id": df["SITE_ID"].astype(str).str.strip()})
    out["visit_no"] = normalize_visit_no(df["VISIT_NO"]) if "VISIT_NO" in df.columns else INDEX_VISIT
    for index, cols in provides.items():
        spec = (sources.get("indices") or {}).get(index) or {}
        score_col, class_col = spec.get("score_column"), spec.get("class_column")
        if score_col and score_col in cols and score_col in df.columns:
            out[f"{index}_score"] = [_number(v) for v in df[score_col].tolist()]
        if class_col and class_col in cols and class_col in df.columns:
            out[f"{index}_class"] = [recode_class(index, v, sources) for v in df[class_col].tolist()]
    out = out[out["visit_no"] == INDEX_VISIT].reset_index(drop=True)
    out["cycle"] = str(cycle)
    return out


def combine_cycle(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """One row per (cycle, site_id, visit_no) over a cycle's files: a value comes from
    the first file that carries it (the table lists the class-bearing file first)."""
    if not frames:
        return pd.DataFrame(columns=["cycle", "site_id", "visit_no"])
    out = None
    for f in frames:
        f = f.drop_duplicates(["cycle", "site_id", "visit_no"], keep="first")
        if out is None:
            out = f
            continue
        out = out.merge(f, on=["cycle", "site_id", "visit_no"], how="outer", suffixes=("", "__new"))
        for c in [c for c in out.columns if c.endswith("__new")]:
            base = c[: -len("__new")]
            out[base] = out[base].where(out[base].notna(), out[c])
            out = out.drop(columns=[c])
    return out.reset_index(drop=True)


def build(*, raw: Path, archive: Path, sources: dict, lock: Optional[dict],
          allow_drift: bool = False) -> tuple[pd.DataFrame, dict]:
    """The table and its manifest."""
    visits = archive_visits(archive)
    stations = archive_stations(archive)
    lock_files = (lock or {}).get("files") or {}
    manifest: dict[str, Any] = {"schema": "bio-indices/1", "sources": [], "cycles": {},
                                "indices": {}, "not_published": [], "station_matching": sources.get("station_matching"),
                                "index_visit": sources.get("index_visit"),
                                "archive": {n: ("sha256:" + sha256_of(archive / n)) for n in
                                            ("site_visits.parquet", "stations.parquet")}}
    per_cycle: dict[str, pd.DataFrame] = {}
    drift = []
    for cycle, block in (sources.get("cycles") or {}).items():
        cycle = str(cycle)
        frames = []
        for f in block.get("files") or []:
            path = raw / str(f["path"])
            if not path.is_file():
                raise SystemExit(f"missing raw file {path}; run scripts/nrsa/fetch_nrsa_raw.py")
            sha = sha256_of(path)
            locked = (lock_files.get(str(f.get("lock_key"))) or {}).get("sha256")
            locked_hex = str(locked).split(":", 1)[-1] if locked else None
            rec = {"cycle": cycle, "path": str(f["path"]), "lock_key": f.get("lock_key"),
                   "sha256": sha, "lock_sha256": locked_hex, "lock_match": (locked_hex == sha) if locked_hex else None,
                   "bytes": path.stat().st_size, "columns": {k: list(v) for k, v in (f.get("provides") or {}).items()},
                   "note": f.get("note")}
            if locked_hex and locked_hex != sha:
                drift.append(str(f["path"]))
            frame = read_index_file(path, cycle, f.get("provides") or {}, sources)
            rec["rows_index_visit"] = int(len(frame))
            frames.append(frame)
            manifest["sources"].append(rec)
        combined = combine_cycle(frames)
        keyed = combined.merge(visits[visits["cycle"] == cycle], on=["cycle", "site_id", "visit_no"], how="left")
        unmatched = keyed[keyed["station_key"].isna()]
        keyed = keyed[keyed["station_key"].notna()].copy()
        counts = {"index_visits": int(len(combined)), "matched_stations": int(keyed["station_key"].nunique()),
                  "unmatched_site_ids": sorted(unmatched["site_id"].astype(str).unique().tolist())}
        for index in INDICES:
            s, c = f"{index}_score", f"{index}_class"
            counts[index] = {"with_score": int(keyed[s].notna().sum()) if s in keyed.columns else 0,
                             "with_class": int(keyed[c].notna().sum()) if c in keyed.columns else 0,
                             "score_published": s in keyed.columns, "class_published": c in keyed.columns}
        manifest["cycles"][cycle] = counts
        for text in block.get("not_published") or []:
            manifest["not_published"].append({"cycle": cycle, "statement": str(text)})
        per_cycle[cycle] = keyed
    if drift and not allow_drift:
        raise SystemExit("raw files differ from data/nrsa/sources.lock.json: " + ", ".join(drift)
                         + " (pass --allow-drift to build from them anyway, on the record)")
    manifest["lock_drift"] = drift

    rows: dict[str, dict] = {}
    for index in INDICES:
        picked = 0
        by_cycle: dict[str, int] = {}
        for cycle in CYCLES_NEWEST_FIRST:
            df = per_cycle.get(cycle)
            if df is None:
                continue
            s, c = f"{index}_score", f"{index}_class"
            has = pd.Series(False, index=df.index)
            if s in df.columns:
                has |= df[s].notna()
            if c in df.columns:
                has |= df[c].notna()
            for r in df[has].itertuples(index=False):
                key = str(r.station_key)
                row = rows.setdefault(key, {"station_key": key})
                if row.get(f"{index}_cycle"):
                    continue                     # a newer cycle already answered
                row[f"{index}_cycle"] = cycle
                row[f"{index}_score"] = getattr(r, s, None) if s in df.columns else None
                row[f"{index}_class"] = getattr(r, c, None) if c in df.columns else None
                picked += 1
                by_cycle[cycle] = by_cycle.get(cycle, 0) + 1
        manifest["indices"][index] = {"label": ((sources.get("indices") or {}).get(index) or {}).get("label"),
                                      "stations": picked, "by_newest_cycle": dict(sorted(by_cycle.items()))}
    chl = (sources.get("indices") or {}).get("chlorophyll") or {}
    manifest["indices"]["chlorophyll"] = {"label": chl.get("label"), "stations": 0,
                                          "not_published": chl.get("not_published")}
    if chl.get("not_published"):
        manifest["not_published"].append({"cycle": "all", "statement": str(chl["not_published"])})

    table = pd.DataFrame(list(rows.values()))
    if not len(table):
        table = pd.DataFrame(columns=["station_key"])
    table = table.merge(stations, on="station_key", how="left")
    rename = {"benthic_mmi_score": "mmi_bent", "oe_score": "oe_score", "fish_mmi_score": "mmi_fish"}
    table = table.rename(columns=rename)
    table["chlorophyll_class"] = None
    for c in COLUMNS:
        if c not in table.columns:
            table[c] = None
    table = table[COLUMNS].sort_values("station_key").reset_index(drop=True)
    for c in ("mmi_bent", "oe_score", "mmi_fish"):
        table[c] = pd.to_numeric(table[c], errors="coerce")
    manifest["stations"] = int(len(table))
    manifest["columns"] = COLUMNS
    return table, manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="the data root the table is written to (never under apps/)")
    ap.add_argument("--raw", default=str(DEFAULT_RAW), help="the raw NRSA folder (fetch_nrsa_raw.py)")
    ap.add_argument("--archive", default=str(DEFAULT_ARCHIVE), help="the archive folder (data/nrsa)")
    ap.add_argument("--sources", default=str(DEFAULT_SOURCES))
    ap.add_argument("--protocol", default=None, help="the evaluation protocol, recorded when given")
    ap.add_argument("--allow-drift", action="store_true",
                    help="build from raw files whose sha256 differs from the lock, on the record")
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    apps = APP_ROOT.parent.resolve()
    if apps in out.parents or out == apps:
        raise SystemExit("the table is never written under apps/; pass a data root outside the repo")
    raw, archive = Path(a.raw), Path(a.archive)
    sources = load_sources(Path(a.sources))
    lock_path = archive / LOCK_FILE
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else None
    table, manifest = build(raw=raw, archive=archive, sources=sources, lock=lock, allow_drift=a.allow_drift)
    manifest["sourcesTable"] = {"path": str(Path(a.sources)), "sha256": sha256_of(Path(a.sources))}
    manifest["lock"] = {"path": str(lock_path), "sha256": sha256_of(lock_path) if lock_path.is_file() else None}
    if a.protocol:
        from streamcurves import round2
        manifest["stamp"] = round2.stamp(a.protocol, config_root=None)
    else:
        from streamcurves import code_identity
        manifest["stamp"] = {"code": {"fingerprint": code_identity.fingerprint()}}
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / f"{TABLE_NAME}.csv", index=False, lineterminator="\n")
    table.to_parquet(out / f"{TABLE_NAME}.parquet", index=False)
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=True, default=str) + "\n",
                                     encoding="utf-8", newline="\n")
    print(f"[bio] {manifest['stations']} stations -> {out / (TABLE_NAME + '.csv')}")
    for index, rec in manifest["indices"].items():
        print(f"[bio]   {index:<12} {rec.get('stations'):>5} stations "
              f"{rec.get('by_newest_cycle') or rec.get('not_published') or ''}")
    for s in manifest["not_published"]:
        print(f"[bio]   not published ({s['cycle']}): {s['statement']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
