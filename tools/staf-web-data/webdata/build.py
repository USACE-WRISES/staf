"""The browser layout of STAF's published data: what the static STAF web app reads.

Browsers cannot read GitHub release downloads (no CORS header), so the release assets are mirrored
to a host a page may read by HTTP range (a Hugging Face dataset; the owner's choice, 2026-10-10).
This module writes that mirror's files from local copies of the releases:

- ``bundle/...``: the STAF data bundle (``staf-data-current``) unzipped. Each file keeps its bundle
  path as its logical name (``lines2_0108.parquet``, ``values/lc2_0108.parquet``, ``v2/...``,
  ``tables/...``) and is stored under a content-hashed name, so a file never changes in place and a
  browser cache keyed by its sha256 never goes stale. What is core, a table or a region's file is
  decided by the bundle packer's own rule (``hrbuild.release._members``).
- ``xs/...``: the cross-section grid (``staf-xs-current``) as it is packed.
- ``library/...``: the assessment library's files the apps read (``library`` release: the catalogs, the
  DEEP bundles, the calculators, EASI's method packages), downloaded and checked against the release.
- ``web-manifest.json`` (written last): every logical name with its stored path, bytes and sha256,
  and the source releases' identities. ``README.md``: the dataset card.

The bundle's 3DEP tile catalogs are left out: a pull names its tiles itself (the grid records them).

Files are hard-linked when the output sits on the same volume (no extra space), else copied.

    python tools/staf-web-data/run.py build --out D:\\Data\\staf-web\\site [--regions 0108 0710]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FORMAT = 1
MANIFEST = "web-manifest.json"
BUNDLE = Path(os.environ.get("STAF_WEB_BUNDLE") or (REPO / "apps" / "hr-data" / "data"))
XS_RELEASE = Path(os.environ.get("STAF_WEB_XS") or r"D:\Data\xs-grid\release")
LIBRARY = Path(os.environ.get("STAF_WEB_LIBRARY") or r"D:\Data\staf-web\library-release")
OUT = Path(os.environ.get("STAF_WEB_OUT") or r"D:\Data\staf-web\site")
#: bundle files the browser never reads
LEAVE_OUT = ("tables/dem1m_tiles.parquet", "tables/dem19_quads.parquet")
CARD = """---
pretty_name: STAF data (browser mirror)
tags:
- hydrology
- streams
- nhdplus
- geospatial
---

# STAF data: the browser mirror

The data the static STAF web app (EASI, SFARI and DEEP; the Stream Tiered Assessment Framework,
https://usace-wrises.github.io/staf/) reads by HTTP range. Every file mirrors an asset of a GitHub
release of https://github.com/USACE-WRISES/staf, which stays the source:

- `bundle/`: the STAF data bundle (`staf-data-current`), unzipped: NHDPlus HR flowlines, catchments
  and their shared borders, precomputed per-catchment values, the NHDPlus V2 crosswalk with
  StreamCat, and national point tables (WQP, NID, NWIS, NAS), for the lower 48.
- `xs/`: the national cross-section grid (`staf-xs-current`, published region by region): every
  section's scoring numbers, what drawing or pulling a section needs, one profile per segment.
- `library/`: the STAF assessment library (`library`): DEEP's regional assessments, calculators and
  EASI's method packages.

Files are stored under content-hashed names and never change in place; `web-manifest.json` (updated
last) maps each logical name to its file, size and sha256. Built from public federal datasets (USGS,
EPA, USACE, NOAA, USDA, FWS); see the STAF repository for sources and methods.
"""


def _hrbuild():
    path = str(REPO / "tools" / "hr-slim")
    if path not in sys.path:
        sys.path.insert(0, path)
    from hrbuild import release
    return release


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def stored_name(logical: str, digest: str) -> str:
    """``lines2_0108.parquet`` -> ``lines2_0108.39ec800c34a4.parquet``: the content picks the name."""
    stem, dot, ext = logical.rpartition(".")
    return f"{stem}.{digest[:12]}.{ext}" if dot else f"{logical}.{digest[:12]}"


def _place(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    tmp = dst.with_name(dst.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def bundle_files(bundle: Path = BUNDLE, regions: list | None = None) -> list:
    """``[(logical, source path, kind, region)]`` for the bundle's files (``regions`` limits which
    regions' files are included; the core and the tables always are)."""
    core, tables, by_region = _hrbuild()._members(Path(bundle))
    out = [(rel, Path(bundle) / rel, "core", None) for rel in core if rel not in LEAVE_OUT]
    out += [(rel, Path(bundle) / rel, "table", None) for rel in tables if rel not in LEAVE_OUT]
    for vpu, members in by_region.items():
        if regions is None or vpu in regions:
            out += [(rel, Path(bundle) / rel, "region", vpu) for rel in members]
    return out


def xs_files(xs_release: Path = XS_RELEASE, regions: list | None = None) -> tuple:
    """``(files, release)``: the cross-section release's files and its ``release.json``."""
    path = Path(xs_release) / "release.json"
    if not path.exists():
        return [], {}
    rel = json.loads(path.read_text(encoding="utf-8"))
    out = [("release.json", path, "core", None)]
    for name, entry in sorted(rel.get("assets", {}).items()):
        vpu = entry.get("region")
        if regions is None or vpu in regions:
            out.append((name, Path(xs_release) / name, "region", vpu))
    return out, rel


def library_files(library: Path = LIBRARY) -> list:
    """The downloaded ``library`` release files (catalogs, DEEP bundles, calculators, method packages)."""
    folder = Path(library)
    if not folder.exists():
        return []
    return [(p.name, p, "library", None) for p in sorted(folder.iterdir()) if p.is_file()]


def build(out: Path = OUT, *, bundle: Path = BUNDLE, xs_release: Path = XS_RELEASE, library: Path = LIBRARY,
          regions: list | None = None, log=print) -> dict:
    """Write the browser layout into ``out``; returns the manifest."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files: dict = {}
    sources: dict = {}
    bundle_manifest = json.loads((Path(bundle) / "manifest.json").read_text(encoding="utf-8"))
    sources["staf-data-current"] = {"format": bundle_manifest.get("format"), "built": bundle_manifest.get("built")}
    groups = [("bundle", bundle_files(bundle, regions))]
    xs, xs_rel = xs_files(xs_release, regions)
    if xs:
        groups.append(("xs", xs))
        sources["staf-xs-current"] = {"format": xs_rel.get("format"), "packed": xs_rel.get("packed"),
                                      "sampling_version": (xs_rel.get("grid") or {}).get("sampling_version"),
                                      "regions": len(xs_rel.get("regions") or {})}
    lib = library_files(library)
    if lib:
        groups.append(("library", lib))
        sources["library"] = {"files": len(lib)}
    n = 0
    for prefix, items in groups:
        for logical, src, kind, vpu in items:
            digest = sha256(src)
            folder = Path(logical).parent.as_posix()
            sub = "/".join(p for p in (prefix, vpu, folder if folder != "." else "") if p)
            stored = f"{sub}/{stored_name(Path(logical).name, digest)}"
            _place(src, out / stored)
            entry = {"path": stored, "bytes": src.stat().st_size, "sha256": digest, "kind": kind}
            if vpu:
                entry["region"] = vpu
            files[f"{prefix}/{logical}"] = entry
            n += 1
    manifest = {"format": FORMAT, "built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "sources": sources, "regions": sorted(set(e["region"] for e in files.values() if e.get("region"))),
                "files": dict(sorted(files.items()))}
    (out / "README.md").write_text(CARD, encoding="utf-8", newline="\n")
    keep = set(e["path"] for e in files.values()) | {"README.md", MANIFEST}
    pruned = 0
    for p in sorted(out.rglob("*")) if regions is None else []:   # a full build: files it no longer names
        rel = p.relative_to(out).as_posix()
        if p.is_file() and rel not in keep and not rel.startswith(".cache/"):
            p.unlink()
            pruned += 1
    if pruned:
        log(f"removed {pruned} stored files no longer in the manifest")
    tmp = out / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, out / MANIFEST)
    log(f"wrote {n} files ({sum(e['bytes'] for e in files.values()) / 1e9:.2f} GB) and {MANIFEST} to {out}")
    return manifest
