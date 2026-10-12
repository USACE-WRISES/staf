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
- Of those, the files the browser reads a few rows of at a time (each region's StreamCat rows, V2
  attributes and point extras; the grid's files) are written again with the same rows and values and a
  page index, so a read takes the pages holding its rows (``pages.py``).
- ``library/...``: the assessment library's files the apps read (``library`` release: the catalogs, the
  DEEP bundles, the calculators, EASI's method packages), downloaded and checked against the release.
- ``points/...``: the bundle's national point tables (NID, NWIS gages, WQP stations and results)
  laid out by place in small row groups (``points.py``), so a lookup reads only the groups near the
  point; same rows and values as ``bundle/tables/``.
- ``topo/...``: each region's stream network topology on its own (``topo.py``), read in one request where
  the lines file takes dozens.
- ``engine/...``: the data files the STAF site engine ships with (``libs/site_engine``): the base-flow
  index grid, the EPA Level III ecoregions and the physiographic divisions.
- The JSON and GeoJSON files (read whole: DEEP's bundles and region list, the catalogs, the engine's
  polygons) are stored gzipped, since the host sends every file as it is; their entries keep the
  content's size and sha256 (``raw_bytes``, ``raw_sha256``) beside the stored file's.
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
#: the app's copy of the manifest (``app_index``), written just before it
INDEX = "web-index.json.gz"
BUNDLE = Path(os.environ.get("STAF_WEB_BUNDLE") or (REPO / "apps" / "hr-data" / "data"))
XS_RELEASE = Path(os.environ.get("STAF_WEB_XS") or r"D:\Data\xs-grid\release")
LIBRARY = Path(os.environ.get("STAF_WEB_LIBRARY") or r"D:\Data\staf-web\library-release")
OUT = Path(os.environ.get("STAF_WEB_OUT") or r"D:\Data\staf-web\site")
ENGINE = REPO / "libs" / "site_engine" / "site_engine"
#: the site engine's own data files the browser reads, by their logical names
ENGINE_FILES = (("bfi48grd.tif", "data/bfi48grd.tif"), ("ecoregions_l3.geojson", "_extracted/data/ecoregions_l3.geojson"),
                ("physio_divisions.geojson", "_extracted/data/physio_divisions.geojson"))
#: bundle files the browser never reads
LEAVE_OUT = ("tables/dem1m_tiles.parquet", "tables/dem19_quads.parquet")
#: files the app reads whole, stored gzipped (the host sends every file as it is): a third to a tenth the bytes
GZIP_SUFFIXES = (".json", ".geojson")
GZIP_MIN = 1024
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
- `points/`: the bundle's national point tables (dams, stream gages, water-quality stations and
  results) sorted by place in small row groups, so a lookup near a site reads a few kilobytes.
- `library/`: the STAF assessment library (`library`): DEEP's regional assessments, calculators and
  EASI's method packages.
- `engine/`: the data files the STAF site engine ships with: the USGS base-flow index grid (Wolock
  2003), the EPA Level III ecoregions and the physiographic divisions.

The files the app reads a few rows of at a time (each region's stream lines, StreamCat rows, V2
attributes and point extras, and the cross-section files) are written again with the same rows and values
in small pages with a page index, so a read takes kilobytes; `topo/` holds each region's network topology
on its own. JSON and GeoJSON files are stored gzipped (`.gz`).

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


def footer_bytes(path: Path) -> int | None:
    """A parquet file's footer with its length and magic (the tail a reader needs first), else None."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size < 12:
            return None
        f.seek(-8, 2)
        tail = f.read(8)
    if tail[4:] != b"PAR1":
        return None
    return int.from_bytes(tail[:4], "little") + 8


def gzipped(src: Path, cache: Path, logical: str) -> Path:
    """``src`` gzipped into ``cache`` (level 9 and no timestamp, so a build writes the same bytes), written
    again when its source changes."""
    import gzip
    dst = Path(cache) / (logical + ".gz")
    stamp = dst.with_name(dst.name + ".json")
    st = Path(src).stat()
    key = {"source_bytes": st.st_size, "source_mtime_ns": st.st_mtime_ns}
    if dst.exists() and stamp.exists() and json.loads(stamp.read_text(encoding="utf-8")) == key:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(gzip.compress(Path(src).read_bytes(), compresslevel=9, mtime=0))
    os.replace(tmp, dst)
    stamp.write_text(json.dumps(key), encoding="utf-8")
    return dst


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


def engine_files(engine: Path = ENGINE) -> list:
    """The site engine's data files (``ENGINE_FILES``) that exist in this checkout."""
    out = []
    for logical, rel in ENGINE_FILES:
        path = Path(engine) / rel
        if path.is_file():
            out.append((logical, path, "engine", None))
    return out


def dataset_regions(bundle_manifest: dict) -> dict:
    """What the app reads of a bundle dataset's manifest (``engine/bundle.ts`` datasetRegions): each
    region's bounds, grid, files, id and hydroseq ranges and vpuid."""
    def file(v):
        return v if v is None or isinstance(v, str) else v.get("file")
    out = {}
    for vpu, r in sorted(bundle_manifest.get("vpus", {}).items()):
        out[vpu] = {"bounds": r.get("bounds"), "grid": r.get("grid"), "lines": file(r.get("lines")),
                    "catchments": file(r.get("catchments")), "arcs": file(r.get("arcs")), "steps": file(r.get("steps")),
                    "id_range": r.get("id_range"), "hydroseq_range": r.get("hydroseq_range"), "vpuid": r.get("vpuid"),
                    "v2attrs": file(r.get("v2attrs"))}
    return out


def app_index(manifest: dict, datasets: dict | None = None, bundle_sources: dict | None = None) -> bytes:
    """What the app reads of the manifest, gzipped: each file's stored path and bytes (and the sha256 of
    the library's content, which the app checks against its pins), the size of each file stored gzipped
    once unzipped (``gzip``), each parquet file's footer with its length and magic (``footers``: the
    first read of a file, made exactly), the regions, and the bundle datasets' regions (``datasets``:
    folder -> ``dataset_regions``) and the bundle's ``sources`` (its vintages), so the app needs neither
    bundle manifest; about 130 KB where the manifests are 1.7 MB, since the host sends none compressed."""
    import gzip
    files = dict((k, [e["path"], e["bytes"]] + ([e.get("raw_sha256", e["sha256"])] if e["kind"] == "library" else []))
                 for k, e in manifest["files"].items())
    unzipped = dict((k, e["raw_bytes"]) for k, e in manifest["files"].items() if e.get("encoding") == "gzip")
    footers = dict((k, e["footer"]) for k, e in manifest["files"].items() if e.get("footer"))
    body = json.dumps({"format": manifest["format"], "built": manifest["built"], "regions": manifest["regions"],
                       "files": files, "gzip": unzipped, "footers": footers, "datasets": datasets or {},
                       "bundle_sources": bundle_sources or {}},
                      separators=(",", ":")).encode("utf-8")
    return gzip.compress(body, compresslevel=9, mtime=0)


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
    # each region's stream network topology on its own (one request where the lines file takes dozens)
    from . import topo
    topo_files = [(name, topo.write(src, out / ".cache" / "topo", name), "region", vpu)
                  for rel, src, kind, vpu in groups[0][1] if (name := topo.logical(rel))]
    if topo_files:
        groups.append(("topo", topo_files))
        sources["topo"] = {"layout": topo.LAYOUT, "columns": list(topo.COLUMNS)}
    # the point tables laid out by place, so a box or radius query reads a few small row groups
    from . import points
    laid = points.write(Path(bundle) / "tables", out / ".cache" / "points", log=log)
    if laid:
        groups.append(("points", [(logical, path, "table", None) for logical, path in laid]))
        sources["points"] = {"layout": points.LAYOUT, "group_rows": points.GROUP, "page_rows": points.PAGE,
                             "result_group_rows": points.RESULT_GROUP, "result_page_rows": points.RESULT_PAGE,
                             "curve": f"hilbert-{points.ORDER}"}
    lib = library_files(library)
    if lib:
        # DEEP's regions sidebar, as DEEP's own code lists the release (deep_regions.py)
        from . import deep_regions
        derived = deep_regions.write(library, out / ".cache" / deep_regions.NAME)
        groups.append(("library", lib + [(deep_regions.NAME, derived, "library", None)]))
        sources["library"] = {"files": len(lib)}
    eng = engine_files()
    if eng:
        groups.append(("engine", eng))
        sys.path.insert(0, str(ENGINE.parent))
        from site_engine import ENGINE_VERSION
        sources["site-engine"] = {"version": ENGINE_VERSION, "files": len(eng)}
    # the files the browser reads a few rows of, written again with a page index (pages.py)
    from . import pages
    groups = [(prefix, [(logical, pages.paged(f"{prefix}/{logical}", src, out / ".cache" / "pages")
                         if prefix in ("bundle", "xs") and pages.rule(f"{prefix}/{logical}") else src, kind, vpu)
                        for logical, src, kind, vpu in items]) for prefix, items in groups]
    sources["pages"] = {"layout": pages.LAYOUT, "level": pages.LEVEL, "dictionary_limit": pages.DICTIONARY_LIMIT,
                        "rules": [[r.pattern, page, group, dictionary] for r, page, group, dictionary in pages.RULES]}
    n = 0
    for prefix, items in groups:
        for logical, src, kind, vpu in items:
            digest = sha256(src)
            extra = {}
            if logical.endswith(GZIP_SUFFIXES) and src.stat().st_size >= GZIP_MIN:
                # read whole: stored gzipped; the entry describes the stored file and keeps the content's size and sha256
                extra = {"encoding": "gzip", "raw_bytes": src.stat().st_size, "raw_sha256": digest}
                src = gzipped(src, out / ".cache" / "gzip", f"{prefix}/{logical}")
                digest = sha256(src)
            folder = Path(logical).parent.as_posix()
            sub = "/".join(p for p in (prefix, vpu, folder if folder != "." else "") if p)
            stored = f"{sub}/{stored_name(Path(logical).name, digest)}{'.gz' if extra else ''}"
            _place(src, out / stored)
            entry = {"path": stored, "bytes": src.stat().st_size, "sha256": digest, "kind": kind, **extra}
            if stored.endswith(".parquet") and (tail := footer_bytes(src)):
                entry["footer"] = tail   # the reader's first request, exactly
            if vpu:
                entry["region"] = vpu
            files[f"{prefix}/{logical}"] = entry
            n += 1
    manifest = {"format": FORMAT, "built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "sources": sources, "regions": sorted(set(e["region"] for e in files.values() if e.get("region"))),
                "files": dict(sorted(files.items()))}
    (out / "README.md").write_text(CARD, encoding="utf-8", newline="\n")
    keep = set(e["path"] for e in files.values()) | {"README.md", MANIFEST, INDEX}
    pruned = 0
    for p in sorted(out.rglob("*")) if regions is None else []:   # a full build: files it no longer names
        rel = p.relative_to(out).as_posix()
        if p.is_file() and rel not in keep and not rel.startswith(".cache/"):
            p.unlink()
            pruned += 1
    if pruned:
        log(f"removed {pruned} stored files no longer in the manifest")
    datasets = {}
    for folder in ("", "v2/"):
        path = Path(bundle) / folder / "manifest.json"
        if path.exists() and f"bundle/{folder}manifest.json" in files:
            datasets[f"bundle/{folder}"] = dataset_regions(json.loads(path.read_text(encoding="utf-8")))
    tmp = out / (INDEX + ".tmp")
    tmp.write_bytes(app_index(manifest, datasets, bundle_manifest.get("sources") or {}))
    os.replace(tmp, out / INDEX)
    tmp = out / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, out / MANIFEST)
    log(f"wrote {n} files ({sum(e['bytes'] for e in files.values()) / 1e9:.2f} GB) and {MANIFEST} to {out}")
    return manifest
