"""The STAF data bundle as release assets (Phase 4, step 6).

The apps fetch the bundle from the rolling ``staf-data-current`` prerelease (always a prerelease,
like ``easi-national-current``): the core on first use, a region the first time a request reads
it (``site_engine.delivery``). ``pack`` turns a bundle folder (the layout of ``apps/hr-data/data``)
into the release's assets:

- ``core.zip`` (a few MB, fetched first): the region manifest, the cross-region links, the
  coverage outlines, the value tables' index (``values/values.json``), the V2 part's manifest,
  links and id index (``v2/idindex.parquet``: each COMID and hydroseq's region, so a V2 lookup
  fetches one region), and the 3DEP tile catalogs (``tables/dem*``, read by the cross-section code).
- ``tables-<file>``: each other national table as it is (``tables/<file>``: WQP, NID, NWIS, NAS),
  fetched the first time a lookup reads it, so an app downloads only the tables it uses.
- ``region-<vpu>.zip``: one region's network, catchments and shared borders, its value tables
  (``values/``) and its V2 slices (``v2/``).
- ``release.json``: the bundle's identity and every asset's size and sha256. It is uploaded last,
  so a client never reads a manifest that names an asset not yet replaced.

The archives are deterministic (sorted members, fixed timestamps), so a rebuilt region that did
not change keeps its sha256 and clients keep their copy. ``publish`` uploads the assets with
``gh release upload --clobber``, ``release.json`` last; publishing needs the owner's go.

    python tools/hr-slim/run.py release pack [--bundle apps/hr-data/data] [--out D:\\Data\\nhdplus-hr\\release]
    python tools/hr-slim/run.py release publish --yes
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

TAG = "staf-data-current"
REPO = "USACE-WRISES/staf"
MANIFEST = "release.json"
CORE = "core.zip"
FORMAT = 1
#: a region's file: ``<kind>2_<vpu>.parquet`` or ``.bin`` (HR in the root, values/, v2/); USGS ships
#: a few Great Lakes units as their own "i" packages (0418i, 0419i, 0424i, 0426i, 0428i)
REGION_FILE = re.compile(r"^[a-z0-9]+2_(?P<vpu>\d{4,8}i?)\.(parquet|bin)$")
CORE_FILES = ("manifest.json", "links2.parquet", "coverage_absent.geojson")
#: national tables that ride in the core rather than as their own assets
CORE_TABLES = ("dem1m_tiles.parquet", "dem19_quads.parquet")
_STAMP = (2026, 1, 1, 0, 0, 0)            # every member's timestamp (deterministic archives)


def region_asset(vpu: str) -> str:
    return f"region-{vpu}.zip"


def table_asset(name: str) -> str:
    return f"tables-{name}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _members(bundle: Path) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """``(core, tables, {vpu: members})``: every file of the bundle, as paths relative to it."""
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    vpus = set(manifest["vpus"])
    core, tables, regions = [], [], dict((v, []) for v in sorted(vpus))
    for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
        rel = path.relative_to(bundle).as_posix()
        top = rel.split("/")[0]
        m = REGION_FILE.match(path.name)
        if m and top in (path.name, "values", "v2"):
            if m.group("vpu") not in vpus:
                raise ValueError(f"{rel}: region {m.group('vpu')} is not in the bundle's manifest")
            regions[m.group("vpu")].append(rel)
        elif top == "tables" and rel.count("/") == 1 and path.name not in CORE_TABLES:
            tables.append(rel)
        elif rel in CORE_FILES or top == "tables" or rel in ("values/values.json", "v2/manifest.json",
                                                              "v2/links2.parquet", "v2/idindex.parquet"):
            core.append(rel)
        else:
            raise ValueError(f"{rel}: not a bundle file (neither core nor a region's)")
    missing = [f for f in CORE_FILES if f not in core]
    if missing:
        raise ValueError(f"the bundle lacks {missing}")
    return core, tables, regions


def _zip(bundle: Path, members: list[str], target: Path) -> dict:
    tmp = target.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w") as zf:
        for rel in sorted(members):
            info = zipfile.ZipInfo(rel, date_time=_STAMP)
            # parquet is compressed already; the borders' steps and the JSON are not
            info.compress_type = zipfile.ZIP_STORED if rel.endswith(".parquet") else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, (bundle / rel).read_bytes())
    tmp.replace(target)
    return {"bytes": target.stat().st_size, "sha256": _sha256(target), "files": len(members)}


def pack(bundle: Path, out: Path) -> dict:
    """The release's assets for ``bundle`` in ``out`` (``release.json`` written last)."""
    bundle, out = Path(bundle), Path(out)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    core, tables, regions = _members(bundle)
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("region-*.zip")) + list(out.glob("tables-*")) + [out / CORE, out / MANIFEST]:
        if old.exists():
            old.unlink()
    assets = {CORE: _zip(bundle, core, out / CORE)}
    for rel in tables:
        name = table_asset(rel.split("/", 1)[1])
        (out / name).write_bytes((bundle / rel).read_bytes())
        assets[name] = {"bytes": (out / name).stat().st_size, "sha256": _sha256(out / name), "file": rel}
    for vpu, members in regions.items():
        assets[region_asset(vpu)] = _zip(bundle, members, out / region_asset(vpu))
    release = {
        "format": FORMAT, "tag": TAG,
        "packed": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "bundle": {"format": manifest.get("format"), "built": manifest.get("built"),
                   "sources": manifest.get("sources") or {}},
        "core": CORE,
        "tables": dict((rel.split("/", 1)[1], table_asset(rel.split("/", 1)[1])) for rel in tables),
        "regions": dict((vpu, region_asset(vpu)) for vpu in regions),
        "assets": assets,
    }
    (out / MANIFEST).write_text(json.dumps(release, indent=1), encoding="utf-8", newline="\n")
    return {"assets": len(assets), "bytes": sum(a["bytes"] for a in assets.values()),
            "core_bytes": assets[CORE]["bytes"], "tables": len(tables), "regions": len(regions)}


def publish(out: Path, *, repo: str = REPO, tag: str = TAG, run=subprocess.run) -> list[list[str]]:
    """Upload the packed assets to the rolling prerelease (created as a prerelease when missing),
    ``release.json`` last. Returns the ``gh`` commands run."""
    out = Path(out)
    release = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    for name, entry in release["assets"].items():
        if _sha256(out / name) != entry["sha256"]:
            raise ValueError(f"{name} changed since it was packed")
    commands = []
    if run(["gh", "release", "view", tag, "--repo", repo], capture_output=True).returncode != 0:
        commands.append(["gh", "release", "create", tag, "--repo", repo, "--prerelease",
                         "--title", "STAF data bundle (rolling)",
                         "--notes", "The STAF data bundle the apps read (always a prerelease)."])
    names = sorted(release["assets"])
    for start in range(0, len(names), 20):
        commands.append(["gh", "release", "upload", tag, "--repo", repo, "--clobber"]
                        + [str(out / n) for n in names[start:start + 20]])
    commands.append(["gh", "release", "upload", tag, "--repo", repo, "--clobber", str(out / MANIFEST)])
    for cmd in commands:
        run(cmd, check=True)
    return commands
