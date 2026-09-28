"""The K2b cross-section quality record of every stored reach (WP-R6c, 2026-09-28).

Reads the published evidence (``staging/evidence_<huc4>.parquet``: ``comid``, ``geomorph``,
``bankfull`` and the identity columns), builds each reach's geometry the way the app scores
it (``easi.national.records.reach_geomorph``: the stored geomorph block plus the Bieger
fit-range flags of the stored bankfull block) and asks ``easi.geomorph.cross_section_quality``
for its record, the function the evaluator reads through ``metrics.base.xs_evidence``. One
row per reach, whatever its tier: the flags and the tokens they matched, the counts behind
them (sections, capped sections, sections whose cap is the bank detector's floor), the DEM
resolution, the depth statistics and the two ratios. Nothing is scored and nothing under the
data root is written: the output folder is the caller's.

    python -m builder.analysis.xs_quality --staging D:/Data/easi-national/staging
        --out <folder> [--workers 4]

Writes ``xs_quality.parquet`` and ``xs_quality.json`` (the inputs' stamps, the code hashes,
the counts by status, flag and token). ``reliable_for(frame, quantity)`` is the reader the
reliable-member selection and the coverage table share: a reach is reliable for a quantity
under K2b when its cross sections carry the quantity, no ``low_quality`` flag is set and
the quantity's own ratio is inside its physical range, which is exactly when an E5b package
rates the quantity.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

STATUS_NONE, STATUS_EMPTY, STATUS_OK = "none", "empty", "ok"
COLUMNS = ("comid", "huc4", "xs_status", "quality", "sections", "bhr_sections", "er_sections",
           "capped_sections", "median_capped", "cap_unreachable_sections", "cap_unreachable_depth_m",
           "cap_is_floor", "bhr_at_cap", "detection", "dem_res_m", "bankfull_extrapolated", "edge_limited",
           "bhr", "er", "bhr_in_range", "er_in_range", "flags", "low_quality_tokens", "reasons",
           "depth_min_m", "depth_median_m", "shallow_sections")
#: the flags an E5 or E5b applicability rule withholds a quantity on: the reach's low_quality
#: flag and the quantity's own range flag (``screening_methods._withhold_flags``)
RANGE_FLAG = {"er": "out_of_range_er", "bhr": "out_of_range_bhr"}
QUANTITY_RATIO = {"er_median": "er", "bhr_median": "bhr", "er": "er", "bhr": "bhr"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _median(values: list) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    return float(vals[n // 2]) if n % 2 else float((vals[n // 2 - 1] + vals[n // 2]) / 2.0)


def quality_row(comid, huc4, geomorph_json, bankfull_json, totdasqkm=None, lat=None, lon=None) -> dict:
    """One reach's row from its stored evidence columns (the JSON text of ``geomorph`` and
    ``bankfull``), through the app's own functions."""
    from easi import geomorph as gm
    from easi.national import records
    row = {name: None for name in COLUMNS}
    row.update(comid=int(comid), huc4=None if huc4 is None else str(huc4))
    if geomorph_json is None:
        row["xs_status"] = STATUS_NONE
        return row
    geom = json.loads(geomorph_json) if isinstance(geomorph_json, (str, bytes)) else dict(geomorph_json or {})
    if not geom:
        row["xs_status"] = STATUS_EMPTY
        return row
    bankfull = (json.loads(bankfull_json) if isinstance(bankfull_json, (str, bytes)) else bankfull_json) or None
    record = {"geomorph": geom, "bankfull": bankfull, "totdasqkm": totdasqkm, "lat": lat, "lon": lon}
    geom = records.reach_geomorph(record)
    quality = gm.cross_section_quality(geom)
    row["xs_status"] = STATUS_OK
    if quality is None:
        return row
    sections = gm._sections(geom)
    depths = [gm._section_depth(s) for s in sections if s.get("bank_height_ratio") is not None]
    depths = [d for d in depths if d is not None]
    floor = float(quality["capUnreachableDepthM"])
    row.update({
        "quality": quality.get("quality"),
        "sections": int(quality["sections"]), "bhr_sections": int(quality["bhrSections"]),
        "er_sections": int(quality["erSections"]), "capped_sections": int(quality["cappedSections"]),
        "median_capped": bool(quality["medianCapped"]),
        "cap_unreachable_sections": int(quality["capUnreachableSections"]),
        "cap_unreachable_depth_m": floor, "cap_is_floor": bool(quality["capIsFloor"]),
        "bhr_at_cap": bool(quality["bhrAtCap"]), "detection": quality["detection"],
        "dem_res_m": None if quality["demResolutionM"] is None else float(quality["demResolutionM"]),
        "bankfull_extrapolated": bool(quality["bankfullExtrapolated"]),
        "edge_limited": bool(quality["edgeLimited"]),
        "bhr": quality["bhr"], "er": quality["er"],
        "bhr_in_range": quality["bhrInRange"], "er_in_range": quality["erInRange"],
        "flags": ",".join(quality["flags"]),
        "low_quality_tokens": ",".join((quality.get("matched") or {}).get("low_quality") or []),
        "reasons": json.dumps(quality.get("reasons") or {}, sort_keys=True),
        "depth_min_m": min(depths) if depths else None,
        "depth_median_m": _median(depths),
        "shallow_sections": sum(1 for d in depths if d <= floor + 1e-9),
    })
    return row


def scan_file(path) -> "pandas.DataFrame":
    """Every reach of one evidence file as quality rows."""
    import pandas as pd
    import pyarrow.parquet as pq
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    wanted = ["comid", "huc4", "geomorph", "bankfull", "totdasqkm", "lat", "lon"]
    schema = pq.read_schema(path).names
    columns = [c for c in wanted if c in schema]
    table = pq.read_table(path, columns=columns).to_pydict()
    n = len(table["comid"])
    get = lambda name: table.get(name) or [None] * n  # noqa: E731
    rows = [quality_row(c, h, g, b, d, la, lo) for c, h, g, b, d, la, lo in
            zip(table["comid"], get("huc4"), get("geomorph"), get("bankfull"), get("totdasqkm"), get("lat"), get("lon"))]
    return pd.DataFrame(rows, columns=list(COLUMNS))


def evidence_files(staging: Path) -> list[Path]:
    return sorted(p for p in Path(staging).glob("evidence_*.parquet") if p.is_file())


def scan(staging: Path, *, workers: int = 4, files=None):
    """The quality table of every reach under ``staging`` (pandas), files scanned in parallel."""
    import pandas as pd
    paths = list(files) if files is not None else evidence_files(staging)
    if not paths:
        raise FileNotFoundError(f"no evidence_*.parquet under {staging}")
    if workers <= 1 or len(paths) == 1:
        parts = [scan_file(p) for p in paths]
    else:
        with ProcessPoolExecutor(max_workers=int(workers)) as pool:
            parts = list(pool.map(scan_file, paths))
    frame = pd.concat(parts, ignore_index=True)
    return frame.sort_values("comid", kind="stable").reset_index(drop=True)


def summary(frame, *, staging: Path, files, seconds: float) -> dict:
    """What the scan was and what it found: the inputs' stamps, the code hashes, the counts."""
    from easi import geomorph as gm
    counts = {"rows": int(len(frame)), "status": frame["xs_status"].value_counts().to_dict()}
    ok = frame[frame["xs_status"] == STATUS_OK]
    counts["flags"] = {}
    for flags in ok["flags"].fillna(""):
        for flag in (f for f in str(flags).split(",") if f):
            counts["flags"][flag] = counts["flags"].get(flag, 0) + 1
    counts["low_quality_tokens"] = {}
    for tokens in ok["low_quality_tokens"].fillna(""):
        for token in (t for t in str(tokens).split(",") if t):
            counts["low_quality_tokens"][token] = counts["low_quality_tokens"].get(token, 0) + 1
    counts["median_capped"] = int(ok["median_capped"].fillna(False).astype(bool).sum())
    counts["cap_is_floor"] = int(ok["cap_is_floor"].fillna(False).astype(bool).sum())
    counts["dem_res_m"] = {str(k): int(v) for k, v in ok["dem_res_m"].value_counts(dropna=False).items()}
    for quantity in ("er_median", "bhr_median"):
        counts[f"reliable_{quantity}"] = int(reliable_for(frame, quantity).sum())
    counts = json.loads(json.dumps(counts, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
    return {"schema": "staf-easi-xs-quality", "schemaVersion": 1, "quality": gm.QUALITY_RULES,
            "staging": str(staging), "files": len(files),
            "inputs": [{"file": Path(p).name, "bytes": Path(p).stat().st_size, "mtime_ns": Path(p).stat().st_mtime_ns}
                       for p in files],
            "code": {"easi/geomorph.py": _sha(Path(gm.__file__)), "builder/analysis/xs_quality.py": _sha(Path(__file__))},
            "rules": {"out_of_range_bhr": "bank-height ratio at or below 0", "out_of_range_er": "entrenchment ratio below 1",
                      "low_quality": ["few_sections (fewer than 3 sections carry the ratio)", "bankfull_extrapolated",
                                      "crest_scan", "cap_unreachable (the median at the cap only with sections at or "
                                      "below the detector's floor depth: 0.15 m on 10 m and 3 m DEMs, 0.05 m on lidar)"]},
            "counts": counts, "seconds": round(float(seconds), 1), "writtenAt": _now(),
            "writer": "tools/easi-national/builder/analysis/xs_quality.py"}


def reliable_for(frame, quantity: str):
    """A boolean mask: the reaches whose cross sections are reliable for ``quantity`` under
    K2b (``er_median`` or ``bhr_median``; ``er`` and ``bhr`` accepted): the cross sections
    exist and carry the ratio, no ``low_quality`` flag is set, and the quantity's own ratio is
    inside its physical range. This is exactly the population an E5b package rates the
    quantity on."""
    import pandas as pd
    ratio = QUANTITY_RATIO[quantity]
    flags = frame["flags"].fillna("").astype(str)
    has_flag = lambda name: flags.str.split(",").apply(lambda items: name in items)  # noqa: E731
    ok = (frame["xs_status"] == STATUS_OK) & pd.to_numeric(frame[ratio], errors="coerce").notna()
    return ok & ~has_flag("low_quality") & ~has_flag(RANGE_FLAG[ratio])


def reliable_three_rules(frame, quantity: str):
    """The brief's three-rule reliability (at least three sections carry the ratio, bankfull
    not extrapolated, not every section by the crest scan), reported beside ``reliable_for``."""
    import pandas as pd
    ratio = QUANTITY_RATIO[quantity]
    sections = pd.to_numeric(frame[f"{ratio}_sections"], errors="coerce").fillna(0)
    ok = (frame["xs_status"] == STATUS_OK) & pd.to_numeric(frame[ratio], errors="coerce").notna()
    return (ok & (sections >= 3) & ~frame["bankfull_extrapolated"].fillna(False).astype(bool)
            & (frame["detection"].fillna("") != "crest_scan"))


def write(frame, out: Path, doc: dict) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, out / "xs_quality.parquet", compression="snappy")
    doc = dict(doc, parquet={"file": "xs_quality.parquet", "bytes": (out / "xs_quality.parquet").stat().st_size,
                             "sha256": _sha(out / "xs_quality.parquet")})
    (out / "xs_quality.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--staging", type=Path, default=Path("D:/Data/easi-national/staging"))
    ap.add_argument("--out", type=Path, required=True, help="the folder to write xs_quality.parquet and .json into")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="scan only the first N evidence files (a rehearsal)")
    a = ap.parse_args(argv)
    files = evidence_files(a.staging)
    if a.limit:
        files = files[: int(a.limit)]
    t0 = time.perf_counter()
    frame = scan(a.staging, workers=a.workers, files=files)
    doc = summary(frame, staging=a.staging, files=files, seconds=time.perf_counter() - t0)
    doc = write(frame, a.out, doc)
    print(json.dumps({k: doc[k] for k in ("quality", "files", "counts", "seconds", "parquet")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
