"""Merge the per-VPU parts into ``data/manifest.json`` and copy data for a deploy."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import bootstrap_hrslim

bootstrap_hrslim()
from hrslim import fmt  # noqa: E402


def parts(root: Path) -> list[dict]:
    out = []
    for path in sorted((root / "parts").glob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def build(root: Path) -> dict:
    found = parts(root)
    if not found:
        raise SystemExit(f"no converted VPUs under {root / 'parts'}")
    recipes = [p["recipe"] for p in found]
    recipe = recipes[0]
    mixed = [p["vpu"] for p in found if p["recipe"] != recipe]
    manifest = {
        "format": fmt.FORMAT_VERSION,
        "recipe": recipe,
        "recipe_mismatch": mixed,
        "vpus": dict((p["vpu"], p["entry"]) for p in found),
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def build2(root: Path) -> dict:
    """Merge the version 2 parts into ``data/manifest.json`` and write the national
    cross-region links table (every flowline whose downstream reach lies in another
    region: target hydroseq, region, row)."""
    import pyarrow.parquet as pq
    from hrslim import fmt2

    found = [p for p in parts(root) if p.get("format") == fmt2.FORMAT_VERSION]
    if not found:
        raise SystemExit(f"no version 2 regions under {root / 'parts'}")
    data_dir = root / "data"
    links = fmt2.links_table([(p["vpu"], pq.read_table(data_dir / p["entry"]["lines"]["file"], columns=["dn_out"]))
                              for p in found])
    links_path = data_dir / fmt2.LINKS_FILE
    pq.write_table(links, links_path, compression="zstd", use_dictionary=["vpu"])
    manifest = {
        "format": fmt2.FORMAT_VERSION,
        "recipe": found[0]["recipe"],
        "recipe_mismatch": [p["vpu"] for p in found if p["recipe"] != found[0]["recipe"]],
        "vpus": dict((p["vpu"], p["entry"]) for p in found),
        "links": {"file": links_path.name, "rows": links.num_rows, "bytes": links_path.stat().st_size},
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def manifest_files(manifest: dict) -> list[str]:
    """Every data file a manifest lists (either format)."""
    names = []
    for entry in manifest["vpus"].values():
        if manifest.get("format") == 2:
            names += [entry["lines"]["file"], entry["catchments"]["file"], entry["arcs"]["file"],
                      entry["steps"]["file"]]
        else:
            names += [entry["lines"]["file"]] + [c["file"] for c in entry["catchments"].values()]
            if entry.get("qa"):
                names.append(entry["qa"]["file"])
    if manifest.get("links"):
        names.append(manifest["links"]["file"])
    return names


def bundle_sources() -> dict:
    """The vintages of the sources behind the precomputed values (``sources.json``), as the site
    engine reports them (``site_engine.metrics.precomputed``)."""
    from . import sources
    path = sources.SOURCES_DIR / "sources.json"
    recorded = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = {}
    for key, entry in recorded.items():
        if isinstance(entry, dict):
            out[key] = dict((k, entry[k]) for k in ("vintage", "recorded", "file") if entry.get(k))
    return out


def write_coverage_absent(path: Path, vpus: list[str], simplify_deg: float = 0.001) -> int:
    """The outlines of the HR regions the bundle lacks (``hrslim/vpu_index.geojson``), so the
    site engine asks the service near them (``site_engine.bundle``). A region of the index counts
    as held only when the bundle has the whole region (one of Alaska's subregion packages leaves
    its region absent, so the service answers there)."""
    import shapely
    from shapely.geometry import mapping, shape

    from . import HR_DATA_APP
    index = json.loads((HR_DATA_APP / "hrslim" / "vpu_index.geojson").read_text(encoding="utf-8"))
    held = set(vpus)
    feats = []
    for f in index.get("features") or []:
        code = str((f.get("properties") or {}).get("vpu") or "")
        if not code or code in held:
            continue
        geom = shapely.set_precision(shape(f["geometry"]).simplify(simplify_deg), 1e-5)
        if not geom.is_empty:
            feats.append({"type": "Feature", "properties": {"vpu": code}, "geometry": mapping(geom)})
    text = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":"))
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def pack(root: Path, dest: Path, *, tolerance: float | None = None) -> dict:
    """Copy the manifest and the files it lists into ``dest`` (the data app's
    ``data/`` folder; version 1 can keep one catchment tolerance only). Files of
    another build already in ``dest`` are removed first."""
    manifest = json.loads((root / "data" / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") == 2:
        dest.mkdir(parents=True, exist_ok=True)
        for old in list(dest.glob("*.parquet")) + list(dest.glob("*.bin")) + list(dest.glob("manifest.json")):
            old.unlink()
        total = 0
        for name in manifest_files(manifest):
            shutil.copy2(root / "data" / name, dest / name)
            total += (root / "data" / name).stat().st_size
        manifest["sources"] = bundle_sources()
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        total += write_coverage_absent(dest / "coverage_absent.geojson", list(manifest["vpus"]))
        # the lean value files, the national tables and the NHDPlus V2 regions (EASI's covered
        # streams, StreamCat and EROM by COMID) ride beside the data (values/, tables/, v2/)
        from .v2 import V2_ROOT
        for sub, src_dir in (("values", root / "values_lean"), ("tables", root / "tables"),
                             ("v2", V2_ROOT / "data")):
            if not src_dir.exists():
                continue
            out = dest / sub
            if out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)
            for path in (sorted(src_dir.glob("*.parquet")) + sorted(src_dir.glob("*.json"))
                         + sorted(src_dir.glob("*.bin"))):
                shutil.copy2(path, out / path.name)
                total += path.stat().st_size
        return {"files": sum(1 for _ in dest.rglob("*.parquet")), "bytes": total}
    if tolerance is not None:
        key = f"{tolerance:g}"
        manifest["recipe"] = dict(manifest["recipe"], catchment_tolerances_m=[float(tolerance)],
                                  default_catchment_tolerance_m=float(tolerance))
        for entry in manifest["vpus"].values():
            entry["catchments"] = dict((k, v) for k, v in entry["catchments"].items() if k == key)
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    for entry in manifest["vpus"].values():
        names = [entry["lines"]["file"]] + [c["file"] for c in entry["catchments"].values()]
        if entry.get("qa"):
            names.append(entry["qa"]["file"])
        for name in names:
            src = root / "data" / name
            shutil.copy2(src, dest / name)
            total += src.stat().st_size
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {"files": sum(1 for _ in dest.glob("*.parquet")), "bytes": total}
