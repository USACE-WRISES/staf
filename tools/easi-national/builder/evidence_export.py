"""Evidence packages for EASI's reference curves, from the frozen 2026-09-15 baseline.

The 34 operational curves (Alternative 2: three NARS-9 families and the slope-class
entrenchment set) were fit from the baseline snapshot under
``review/2026-09-15-regional/baseline``: the reference panels drawn from its landscape table,
the member values from its landscape and values tables, fit by StreamCurves' curve engine.
This producer copies what a reviewer needs out of that snapshot into self-describing
packages, so the curves can be inspected, refit and their panels regenerated on any machine
without the snapshot. It only reads the snapshot and the live national EROM cache.

    python -m builder.evidence_export --snapshot <baseline dir> --out <dir> [--package ID ...]
    python -m builder.evidence_export --verify <package dir>

Packages (``evidence.json`` + ``data/`` per package, and a deterministic zip of both):

- ``easi-dev-members`` (development, refittable): the panel members and panels verbatim, every
  member's fit input for each fitted quantity, its screen variables and composite pressure,
  and the 12 monthly EROM flows behind the flow-variability quantity;
- ``easi-dev-fits`` (development, reviewable): the curve registry and points verbatim, the fit
  recipe and the map from the 34 operational curves to the fits they came from;
- ``easi-dev-universe`` (development, refittable): every NHDPlus V2 reach in the landscape
  table's own order with the columns the panels step reads, so the screens and the seeded
  thinning regenerate the same members;
- ``easi-eval-refs`` (evaluation, reviewable): the controlled alternatives study's receipts and
  field summary, and the NRSA archive StreamCurves ships;
- ``easi-operational-ref`` (operational, reviewable): the identity of the national dataset
  built with the operational method (hashes only).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

from builder import REPO_ROOT, STREAM_CURVES_APP, bootstrap_stream_curves

bootstrap_stream_curves()

SCHEMA = "staf-evidence-package"
SCHEMA_VERSION = 1
VERSION = "2026.09.15-baseline"
LIVE_EROM = Path(r"D:\Data\easi-national\national\erom.parquet")
STUDY = Path(r"D:\Data\easi-national\review\alternative-studies\2026-09-15-controlled-alternatives")
ROLLOUT = Path(r"D:\Data\easi-national\review\alternative-2-rollout")
OPERATIONAL_BUILD = "3d8a4711c5414d4e9e76ca2233815783"
OPERATIONAL_METHOD = "b2e3033116e3"
MONTHS = tuple(f"qe_{m:02d}" for m in range(1, 13))
PARQUET = "application/vnd.apache.parquet"
ZIP_TIME = (2026, 9, 15, 0, 0, 0)

# the 34 operational curves: set -> (quantity, where the fits come from)
OPERATIONAL_SETS = {
    "corridor-natural": ("natural_wsrp100", "nars9"),
    "corridor-woody": ("woody_wsrp100", "nars9"),
    "flow-variability": ("q_cv_monthly", "nars9"),
    "entrenchment": ("er_median", "slope_class"),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git_commit() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    return r.stdout.strip()


# --------------------------------------------------------------------------- #
# writing packages
# --------------------------------------------------------------------------- #
class Package:
    """One package being written: data files, then its manifest and zip."""

    def __init__(self, out: Path, package_id: str):
        self.id = package_id
        self.dir = out / package_id
        if self.dir.exists():
            shutil.rmtree(self.dir)
        (self.dir / "data").mkdir(parents=True)
        self.files: dict[str, dict] = {}

    def _record(self, rel: str, extra: dict) -> None:
        path = self.dir / rel
        self.files[rel] = {"bytes": path.stat().st_size, "sha256": sha_file(path), **extra}

    def copy(self, source: Path, name: str, *, expect_sha: str | None = None, **extra) -> str:
        rel = f"data/{name}"
        shutil.copyfile(source, self.dir / rel)
        self._record(rel, extra)
        if expect_sha and self.files[rel]["sha256"] != expect_sha:
            raise RuntimeError(f"{source} does not match the snapshot's record")
        return rel

    def parquet(self, table, name: str, **extra) -> str:
        import pyarrow.parquet as pq
        rel = f"data/{name}"
        pq.write_table(table, self.dir / rel, compression="zstd", compression_level=9,
                       row_group_size=262144, write_statistics=True)
        self._record(rel, {"mediaType": PARQUET, "rows": table.num_rows,
                           "columns": list(table.schema.names), **extra})
        return rel

    def json(self, obj, name: str, **extra) -> str:
        rel = f"data/{name}"
        (self.dir / rel).write_bytes((json.dumps(obj, indent=1, sort_keys=True, allow_nan=False)
                                      + "\n").encode("utf-8"))
        self._record(rel, {"mediaType": "application/json", **extra})
        return rel

    def finish(self, **manifest) -> dict:
        data_digest = "sha256:" + sha(canonical({k: v["sha256"] for k, v in sorted(self.files.items())}))
        doc = {"schema": SCHEMA, "schemaVersion": SCHEMA_VERSION, "packageId": self.id,
               "version": VERSION, "dataDigest": data_digest,
               "files": dict(sorted(self.files.items())), **manifest}
        body = (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False)
                + "\n").encode("utf-8")
        (self.dir / "evidence.json").write_bytes(body)
        digest = package_digest(doc)
        zpath = self.dir.parent / f"{self.id}-{VERSION}-{digest[:8]}.evidence.zip"
        for old in self.dir.parent.glob(f"{self.id}-{VERSION}-*.evidence.zip"):
            old.unlink()
        write_zip(self.dir, zpath)
        return {"packageId": self.id, "packageDigest": "sha256:" + digest, "dataDigest": data_digest,
                "zip": zpath.name, "zipBytes": zpath.stat().st_size, "zipSha256": sha_file(zpath),
                "bytes": sum(v["bytes"] for v in self.files.values())}


def package_digest(doc: dict) -> str:
    """The package's identity: its canonical manifest without the producer block (who ran the
    export and when), so exporting the same data and description again gives the same digest."""
    return sha(canonical({k: v for k, v in doc.items() if k != "producer"}))


def write_zip(folder: Path, zpath: Path) -> None:
    """Deterministic: sorted entries, fixed times; parquet stored, text deflated."""
    names = ["evidence.json"] + sorted(str(p.relative_to(folder)).replace("\\", "/")
                                       for p in (folder / "data").rglob("*") if p.is_file())
    tmp = zpath.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w") as z:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = (zipfile.ZIP_STORED if name.endswith(".parquet")
                                  else zipfile.ZIP_DEFLATED)
            z.writestr(info, (folder / name).read_bytes())
    tmp.replace(zpath)


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #
def snapshot_records(snapshot: Path) -> tuple[dict, str, dict]:
    """path -> sha256 for every file the snapshot recorded when it was frozen."""
    manifest = snapshot.parent / "baseline_manifest.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    return {f["path"].replace("\\", "/"): f["sha256"] for f in doc["files"]}, sha_file(manifest), doc


def producer_block(snapshot: Path, manifest_sha: str, manifest_doc: dict) -> dict:
    return {"tool": "tools/easi-national/builder/evidence_export.py", "commit": git_commit(),
            "snapshot": {"path": str(snapshot), "baselineManifestSha256": manifest_sha,
                         "createdUtc": manifest_doc.get("created_utc"),
                         "gitCommit": manifest_doc.get("git_commit")},
            "created": now()}


def _members(root) -> "object":
    import pyarrow.parquet as pq
    from builder.analysis import panels
    return pq.read_table(panels.members_path(root))


def member_values(root, members) -> tuple[object, dict, list]:
    """One row per member COMID: the fit input of every fitted quantity (as the curves step
    reads it, gates applied), the screen and pressure variables, and the composite pressure
    the circularity check used (ranked over every member row, as ``fit_all`` ranks it)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from builder.analysis import curves, panels, screens
    from builder.analysis.values import landscape_path
    member_rows = np.asarray(members.column("comid").to_numpy(), dtype=np.int64)
    comids = np.unique(member_rows)
    columns = {"comid": comids}
    dictionary, missing = {}, []
    for q in curves.QUANTITIES.values():
        values = curves._quantity_values(root, q, comids)
        if values is None:
            missing.append({"item": f"quantity {q.key}", "why": f"column {q.column!r} is not in the snapshot",
                            "remedy": "none needed: the curves step skipped it too"})
            continue
        columns[q.key] = values
        dictionary[q.key] = {"definition": q.label or q.key, "source": f"{q.source}.{q.column}",
                             "higherIsBetter": q.higher_is_better,
                             "domain": [q.domain[0], q.domain[1]],
                             **({"gate": {"column": q.gate[0], "minimum": q.gate[1]}} if q.gate else {})}
    pressure_rows = curves._pressure(root, member_rows)
    first = {}
    for c, p in zip(member_rows.tolist(), pressure_rows.tolist()):
        if c in first and not ((np.isnan(first[c]) and np.isnan(p)) or first[c] == p):
            raise RuntimeError(f"member {c} has two composite pressures")
        first.setdefault(c, p)
    columns["composite_pressure"] = np.asarray([first[c] for c in comids.tolist()], dtype=float)
    dictionary["composite_pressure"] = {
        "definition": "mean percentile rank (0 to 1, higher is more pressure) of the pressure "
                      "variables, ranked over every panel member row of every level, as the "
                      "curves step computes it for the pressure check (|rho| <= 0.30)"}
    schema = pq.read_schema(landscape_path(root)).names
    wanted = [c for c in sorted({*panels.SCREEN_COLUMNS}) if c in schema and c not in columns]
    table = pq.read_table(landscape_path(root), columns=["comid", *wanted])
    tc = np.asarray(table.column("comid").to_numpy(), dtype=np.int64)
    order = np.argsort(tc, kind="stable")
    pos = np.searchsorted(tc[order], comids)
    for name in wanted:
        col = table.column(name).to_numpy(zero_copy_only=False)[order][pos]
        columns[f"screen__{name}"] = col
        dictionary[f"screen__{name}"] = {"definition": f"landscape.{name}, as the screens read it"}
    return pa.table(columns), dictionary, missing


def member_erom(root, comids: np.ndarray) -> tuple[object, dict]:
    """The 12 monthly EROM flows and the mean annual flow of every member COMID from the
    live national cache, beside the stored CV the fits used and its recomputation."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from builder.analysis.values import landscape_path
    live = pq.read_table(LIVE_EROM, columns=["comid", "qe_ma", *MONTHS])
    lc = np.asarray(live.column("comid").to_numpy(), dtype=np.int64)
    order = np.argsort(lc, kind="stable")
    pos = np.searchsorted(lc[order], comids)
    found = lc[order][np.minimum(pos, len(lc) - 1)] == comids
    cols = {"comid": comids}
    for name in ("qe_ma", *MONTHS):
        vals = np.asarray(live.column(name).to_numpy(zero_copy_only=False), dtype=float)[order]
        out = np.full(len(comids), np.nan)
        out[found] = vals[np.minimum(pos, len(lc) - 1)][found]
        cols[name] = out
    stored = pq.read_table(landscape_path(root), columns=["comid", "erom__q_cv_monthly"])
    sc = np.asarray(stored.column("comid").to_numpy(), dtype=np.int64)
    so = np.argsort(sc, kind="stable")
    spos = np.searchsorted(sc[so], comids)
    cols["q_cv_monthly_stored"] = np.asarray(stored.column("erom__q_cv_monthly").to_numpy(
        zero_copy_only=False), dtype=float)[so][spos]
    months = np.vstack([cols[m] for m in MONTHS])
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = months.mean(axis=0)
        recomputed = months.std(axis=0) / mean
    both = np.isfinite(recomputed) & np.isfinite(cols["q_cv_monthly_stored"])
    diff = np.abs(recomputed[both] - cols["q_cv_monthly_stored"][both])
    stored_type = str(stored.schema.field("erom__q_cv_monthly").type)
    # the landscape stores the CV in float32: the months reproduce it exactly at that precision
    as_stored = (recomputed[both].astype(np.float32) == cols["q_cv_monthly_stored"][both].astype(np.float32)
                 if stored_type == "float" else recomputed[both] == cols["q_cv_monthly_stored"][both])
    check = {"members": int(len(comids)), "withMonths": int(found.sum()),
             "comparable": int(both.sum()), "storedType": stored_type,
             "identicalAtStoredPrecision": int(as_stored.sum()),
             "maxAbsDifferenceFloat64": float(diff.max()) if diff.size else None,
             "formula": "population standard deviation of the 12 monthly means over their mean",
             "nullsAgree": bool(np.array_equal(~np.isfinite(recomputed), ~np.isfinite(cols["q_cv_monthly_stored"])))}
    return pa.table(cols), check


def export_members(root, out: Path, records: dict, producer: dict, universe_digest: str | None) -> dict:
    import pyarrow.parquet as pq
    from builder.analysis import curves, panels
    pkg = Package(out, "easi-dev-members")
    pkg.copy(panels.members_path(root), "panel_members.parquet",
             expect_sha=records.get("analysis/panels/panel_members.parquet"), mediaType=PARQUET,
             rows=pq.read_metadata(panels.members_path(root)).num_rows)
    pkg.copy(panels.panels_path(root), "reference_panels.parquet",
             expect_sha=records.get("analysis/panels/reference_panels.parquet"), mediaType=PARQUET,
             rows=pq.read_metadata(panels.panels_path(root)).num_rows)
    members = _members(root)
    values, dictionary, missing = member_values(root, members)
    pkg.parquet(values, "member_values.parquet")
    erom, erom_check = member_erom(root, np.asarray(values.column("comid").to_numpy(), dtype=np.int64))
    pkg.parquet(erom, "member_erom.parquet")
    dictionary.update({m: {"units": "cfs", "definition": f"EROM mean flow, month {m[-2:]}"} for m in MONTHS})
    dictionary["qe_ma"] = {"units": "cfs", "definition": "EROM mean annual flow"}
    dictionary["q_cv_monthly_stored"] = {"definition": "the monthly-flow CV the snapshot stored and "
                                                          "the flow-variability curves were fit on"}
    levels = {}
    for lv, st in zip(members.column("level").to_pylist(), members.column("stratum").to_pylist()):
        levels.setdefault(lv, set()).add(st)
    return pkg.finish(
        title="EASI reference panel members and their values",
        description="Every reference panel member of every level with the value each fitted "
                    "quantity read, the screen and pressure variables and the composite "
                    "pressure; refits every fit in the curve registry, the 34 operational "
                    "curves included.",
        roles=["development"], reproducibility="refittable",
        coverage={"levels": {k: len(v) for k, v in sorted(levels.items())},
                  "memberRows": members.num_rows, "memberComids": values.num_rows,
                  "quantities": sorted(k for k in dictionary if k in curves.QUANTITIES),
                  "operationalCurves": 34},
        dictionary=dictionary,
        sources=[{"id": "baseline-snapshot", "path": "analysis/panels, analysis/landscape.parquet, "
                                                     "analysis/values.parquet",
                  "citation": "EASI national builder, 2026-09-15 regional baseline"},
                 {"id": "erom-live-cache", "path": str(LIVE_EROM), "sha256": sha_file(LIVE_EROM),
                  "citation": "NHDPlus V2 EROM monthly flows (national cache)"}],
        recipe={"fitRecipe": "apps/stream-curves/streamcurves/easi_method/fit_recipe.py",
                "engine": engine_block(), "constants": constants_block()},
        checks={"eromMonthsReproduceStoredCv": erom_check},
        dependsOn=([{"packageId": "easi-dev-universe", "dataDigest": universe_digest}]
                   if universe_digest else []),
        redistribution={"status": "public-derived", "notes": "Derived from public federal datasets "
                                                             "(NHDPlus V2, StreamCat, EROM)."},
        limitations=["Values are the snapshot's derived quantities, not raw source records.",
                     "The composite pressure is a rank within the member rows, not a national rank."],
        unavailable=missing, producer=producer)


def engine_block() -> dict:
    engine = STREAM_CURVES_APP / "streamcurves" / "curves.py"
    raw = engine.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    return {"path": "apps/stream-curves/streamcurves/curves.py", "sha256": sha(raw),
            "sha256_lf": sha(lf), "sha256_crlf": sha(lf.replace(b"\n", b"\r\n")),
            "frozenArtifactEngineSha256": "a44a89f86edff23ed46fda2dfbd79b34ce41a334c2221436f49116b8eb9e1adb"}


def constants_block() -> dict:
    from builder.analysis import curves, panels, screens
    return {"indexBands": list(curves.INDEX_BANDS), "pressureRhoMax": curves.PRESSURE_RHO_MAX,
            "splitFloor": curves.SPLIT_FLOOR, "panelFloors": {"complete": panels.FLOOR_COMPLETE,
                                                              "exploratory": panels.FLOOR_EXPLORATORY},
            "minStratum": panels.MIN_STRATUM, "thinningSeed": panels.SEED,
            "screen": {"id": "least-disturbed-v1", "strict": screens.STRICT, "relaxed": screens.RELAXED,
                       "frame": screens.FRAME_RULES, "pressureVariables": list(screens.PRESSURE_VARIABLES)}}


def quantity_specs() -> dict:
    from builder.analysis import curves
    out = {}
    for q in curves.QUANTITIES.values():
        out[q.key] = {"source": q.source, "column": q.column, "higherIsBetter": q.higher_is_better,
                      "domain": list(q.domain), "functions": list(q.functions), "kind": q.kind,
                      "zeroInflated": q.zero_inflated, "geometry": q.geometry, "split": q.split,
                      "cap": q.cap, "gate": list(q.gate) if q.gate else None, "label": q.label}
    return out


def export_fits(root, out: Path, records: dict, producer: dict, members_digest: str) -> dict:
    import pyarrow.parquet as pq
    from builder.analysis import curves
    pkg = Package(out, "easi-dev-fits")
    pkg.copy(curves.registry_path(root), "curve_registry.parquet",
             expect_sha=records.get("analysis/curves/curve_registry.parquet"), mediaType=PARQUET,
             rows=pq.read_metadata(curves.registry_path(root)).num_rows)
    pkg.copy(curves.points_path(root), "curve_points.parquet",
             expect_sha=records.get("analysis/curves/curve_points.parquet"), mediaType=PARQUET,
             rows=pq.read_metadata(curves.points_path(root)).num_rows)
    live = REPO_ROOT / "apps" / "easi" / "data" / "reference-curves.json"
    artifact = json.loads(live.read_text(encoding="utf-8"))
    mapping = []
    for set_id, (quantity, source) in OPERATIONAL_SETS.items():
        for key in sorted(artifact["sets"][set_id]["curves"]):
            if source == "nars9":
                where = ({"level": "national", "stratum": "national:national", "split": ""}
                         if key == "national" else {"level": "nars9", "stratum": f"nars9:{key}", "split": ""})
                method = "registry"
            elif key == "national":
                where = {"level": "national", "stratum": "national:national", "split": ""}
                method = "pooled-national-panel"
            else:
                where = {"level": "national", "stratum": "national:national", "split": key}
                method = "registry"
            mapping.append({"set": set_id, "curve": key, "quantity": quantity, **where, "method": method})
    pkg.json({"curves": mapping, "operationalArtifact": {"path": "apps/easi/data/reference-curves.json",
                                                          "sha256": sha_file(live),
                                                          "methodVersion": OPERATIONAL_METHOD},
              "notes": ["The three regional families use the registry's usable unsplit NARS-9 fits "
                        "and their national curve (the Alternative 2 substitution).",
                        "The national entrenchment curve pools every national panel member with an "
                        "entrenchment ratio, members without a slope class included.",
                        "Curve values are the fits rounded to six decimals."]},
             "operational_curves.json")
    pkg.json({"quantities": quantity_specs(), "constants": constants_block(), "engine": engine_block()},
             "recipe.json")
    reg = pq.read_table(curves.registry_path(root))
    return pkg.finish(
        title="EASI curve fits and their recipe",
        description="The curve registry (every quantity, level and stratum fit, usable or not, with "
                    "its reason) and knots verbatim, the recipe that produced them and the map from "
                    "the 34 operational curves to their fits.",
        roles=["development"], reproducibility="reviewable",
        coverage={"fits": reg.num_rows, "usable": int(sum(bool(x) for x in reg.column("usable").to_pylist())),
                  "operationalCurves": len(mapping)},
        dictionary={"curve_registry.parquet": {"definition": "one row per fit: quantity, level, stratum, "
                                                             "split, status, n, quartiles, crossings, "
                                                             "pressure rho, usable and reason"}},
        sources=[{"id": "baseline-snapshot", "path": "analysis/curves",
                  "citation": "EASI national builder, 2026-09-15 regional baseline"}],
        recipe={"fitRecipe": "apps/stream-curves/streamcurves/easi_method/fit_recipe.py",
                "engine": engine_block()},
        dependsOn=[{"packageId": "easi-dev-members", "dataDigest": members_digest}],
        redistribution={"status": "public-derived", "notes": ""},
        limitations=["A fit's usability records the rules of analysis version 0.1.0."],
        unavailable=[], producer=producer)


UNIVERSE_COLUMNS = ("comid", "huc12", "state", "l3", "l2", "l1", "nars9", "slope_class", "fcode_class",
                    "da_class", "wadeable", "totdasqkm", "streamorde", "in_scored_set")


def universe_table(root):
    import pyarrow.parquet as pq
    from builder.analysis import panels
    from builder.analysis.values import landscape_path
    schema = pq.read_schema(landscape_path(root)).names
    needed = sorted({*UNIVERSE_COLUMNS, *panels.SCREEN_COLUMNS})
    return pq.read_table(landscape_path(root), columns=[c for c in needed if c in schema])


def regenerate_members(table) -> "object":
    """The panels step over a universe table: every level's members, as the builder draws them."""
    import pandas as pd
    from builder.analysis import panels
    frame = table.to_pandas()
    for name in frame.columns:
        if str(frame[name].dtype) == "float32":
            frame[name] = frame[name].astype("float64")
    out = [panels.select_panels(frame, level)[1] for level in panels.LEVELS]
    return pd.concat(out, ignore_index=True)


def export_universe(root, out: Path, producer: dict, *, check: bool = True) -> dict:
    import pyarrow.parquet as pq
    from builder.analysis import panels
    pkg = Package(out, "easi-dev-universe")
    table = universe_table(root)
    pkg.parquet(table, "universe.parquet", order="the landscape table's row order (the thinning key "
                                                "depends on it)")
    checks = {}
    if check:
        t0 = time.perf_counter()
        members = regenerate_members(table)
        stored = pq.read_table(panels.members_path(root)).to_pandas()
        cols = list(stored.columns)
        same = (len(members) == len(stored)
                and members[cols].reset_index(drop=True).equals(stored[cols].reset_index(drop=True)))
        checks["panelsRegenerateMembers"] = {"memberRows": int(len(members)), "identical": bool(same),
                                              "seconds": round(time.perf_counter() - t0, 1)}
        if not same:
            raise RuntimeError("the universe does not regenerate the stored panel members")
    return pkg.finish(
        title="EASI reference universe",
        description="Every NHDPlus V2 reach of the landscape table, in its own row order, with the "
                    "strata, frame and screen columns the panels step reads: rerunning the screens "
                    "and the seeded one-per-HUC12 thinning regenerates every level's panel members.",
        roles=["development"], reproducibility="refittable",
        coverage={"reaches": table.num_rows, "levels": list(panels.LEVELS)},
        dictionary={c: {"definition": f"landscape.{c}"} for c in table.schema.names},
        sources=[{"id": "baseline-snapshot", "path": "analysis/landscape.parquet",
                  "citation": "EASI national builder, 2026-09-15 regional baseline"}],
        recipe={"panels": "tools/easi-national/builder/analysis/panels.py (select_panels, seed 7)",
                "constants": constants_block()},
        checks=checks, dependsOn=[],
        redistribution={"status": "public-derived", "notes": "StreamCat and NHDPlus V2 derivatives."},
        limitations=["The landscape values are the snapshot's; regenerating them from StreamCat, "
                     "NHDPlus and EROM needs the builder's pipeline and network access."],
        unavailable=[{"item": "the StreamCat, NHDPlus V2 and EROM source records behind the landscape "
                              "table", "why": "large public federal datasets, re-downloadable",
                      "remedy": "run the builder's landscape stage for a HUC8 or the country "
                                "(tools/easi-national); a HUC8 spot check is recorded with this package"}],
        producer=producer)


def export_eval_refs(out: Path, producer: dict) -> dict:
    pkg = Package(out, "easi-eval-refs")
    refs = []
    for rel in ("completion.json", "manifest.json", "protocol.json", "results/field_summary.json"):
        src = STUDY / rel
        if src.is_file():
            pkg.copy(src, "study_" + rel.replace("/", "_"), mediaType="application/json")
            refs.append(rel)
    nrsa = STREAM_CURVES_APP / "data" / "nrsa" / "manifest.json"
    pkg.json({"nrsaArchive": {"path": "apps/stream-curves/data/nrsa/manifest.json",
                              "sha256": sha_file(nrsa)},
              "study": {"id": STUDY.name, "path": str(STUDY), "copied": refs}}, "references.json")
    return pkg.finish(
        title="EASI evaluation references",
        description="Receipts of the 2026-09-15 controlled alternatives study (the evaluation that "
                    "chose Alternative 2) and the NRSA archive StreamCurves ships; evaluation "
                    "evidence, never development evidence for the curves it evaluates.",
        roles=["evaluation"], reproducibility="reviewable",
        coverage={"studyReceipts": refs},
        dictionary={}, sources=[{"id": "alternatives-study", "path": str(STUDY)}],
        recipe={}, dependsOn=[],
        redistribution={"status": "internal-review", "notes": "Study receipts for the owner's review."},
        limitations=["NRSA stations screened by EASI-derived variables are not independent of EASI "
                     "for agreement statistics."],
        unavailable=[{"item": "the study's per-reach results", "why": "large; reviewable in place",
                      "remedy": "open the study folder named in references.json"}],
        producer=producer)


def export_operational(out: Path, producer: dict) -> dict:
    pkg = Package(out, "easi-operational-ref")
    ref = {"build": OPERATIONAL_BUILD, "methodVersion": OPERATIONAL_METHOD, "path": str(ROLLOUT)}
    for rel in ("staging/manifest.json", "staging/completion.json", "provenance.json", "build.json"):
        p = ROLLOUT / rel
        if p.is_file():
            ref.setdefault("files", {})[rel] = {"bytes": p.stat().st_size, "sha256": sha_file(p)}
    pkg.json(ref, "national_build.json")
    return pkg.finish(
        title="EASI national dataset identity",
        description="The national dataset built with the operational method: its build and method "
                    "identity and the hashes of its manifests. Operational evidence (site inputs); "
                    "no data bytes.",
        roles=["operational"], reproducibility="reviewable",
        coverage={"build": OPERATIONAL_BUILD}, dictionary={}, sources=[{"id": "national-build",
                                                                        "path": str(ROLLOUT)}],
        recipe={}, dependsOn=[], redistribution={"status": "internal-review", "notes": ""},
        limitations=["The national dataset is a development record, not an assessment product."],
        unavailable=[], producer=producer)


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def verify(folder: Path) -> dict:
    doc = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
    bad = []
    for rel, rec in doc["files"].items():
        p = folder / rel
        if not p.is_file() or p.stat().st_size != rec["bytes"] or sha_file(p) != rec["sha256"]:
            bad.append(rel)
    data_digest = "sha256:" + sha(canonical({k: v["sha256"] for k, v in sorted(doc["files"].items())}))
    return {"packageId": doc["packageId"], "files": len(doc["files"]), "damaged": bad,
            "dataDigestOk": data_digest == doc["dataDigest"], "packageDigest": "sha256:" + package_digest(doc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", type=Path, help="the frozen baseline data root")
    ap.add_argument("--out", type=Path, help="where the packages are written")
    ap.add_argument("--package", action="append", default=None,
                    help="only these packages (default: all)")
    ap.add_argument("--skip-universe-check", action="store_true")
    ap.add_argument("--verify", type=Path, help="verify one package folder and exit")
    a = ap.parse_args(argv)
    if a.verify:
        print(json.dumps(verify(a.verify), indent=1))
        return 0
    from builder.paths import DataRoot
    root = DataRoot(a.snapshot)
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    records, manifest_sha, manifest_doc = snapshot_records(a.snapshot)
    producer = producer_block(a.snapshot, manifest_sha, manifest_doc)
    wanted = set(a.package or ["easi-dev-universe", "easi-dev-members", "easi-dev-fits",
                               "easi-eval-refs", "easi-operational-ref"])
    results = {}
    t0 = time.perf_counter()
    universe = members = None
    if "easi-dev-universe" in wanted:
        universe = export_universe(root, out, producer, check=not a.skip_universe_check)
        results["easi-dev-universe"] = universe
        print(json.dumps(universe), flush=True)
    if "easi-dev-members" in wanted:
        members = export_members(root, out, records, producer, universe and universe["dataDigest"])
        results["easi-dev-members"] = members
        print(json.dumps(members), flush=True)
    if "easi-dev-fits" in wanted:
        if members is None:
            members = json.loads((out / "easi-dev-members" / "evidence.json").read_text(encoding="utf-8"))
        results["easi-dev-fits"] = export_fits(root, out, records, producer, members["dataDigest"])
        print(json.dumps(results["easi-dev-fits"]), flush=True)
    if "easi-eval-refs" in wanted:
        results["easi-eval-refs"] = export_eval_refs(out, producer)
        print(json.dumps(results["easi-eval-refs"]), flush=True)
    if "easi-operational-ref" in wanted:
        results["easi-operational-ref"] = export_operational(out, producer)
        print(json.dumps(results["easi-operational-ref"]), flush=True)
    index = out / "index.json"
    prior = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else {}
    prior.update(results)
    index.write_text(json.dumps(prior, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"done in {time.perf_counter() - t0:.1f} s -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
