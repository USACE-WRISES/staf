"""The USGS packages: the S3 inventory and reading layers straight from a zip.

GDAL chains two virtual file systems: ``/vsicurl/`` reads the zip on S3 with
HTTP range requests and ``/vsizip/`` reads inside it, so the FileGDB driver opens
the geodatabase without a download or an unzip and only the requested tables
cross the network.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests

from . import config

_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
#: ``NHDPLUS_H_0710_HU4_GDB.zip``, ``NHDPLUS_H_0108_HU4_20220324_GDB.zip`` (dated
#: reprocessed), ``NHDPLUS_H_0418i_HU4_GDB.zip`` (Great Lakes units), Alaska HU8s.
_NAME = re.compile(r"^NHDPLUS_H_(\d{4}i?|\d{8})_HU(4|8)(?:_(\d{8}))?_GDB\.zip$")


def list_packages(timeout: float = 60.0) -> list[dict]:
    """Every ``VPU/Current/GDB`` zip: name, key, url, bytes, vpu, date."""
    out = []
    token = None
    while True:
        params = [("list-type", "2"), ("prefix", config.S3_PREFIX)]
        if token:
            params.append(("continuation-token", token))
        r = requests.get(config.S3_BASE, params=params, timeout=timeout)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for c in root.findall(_NS + "Contents"):
            key = c.find(_NS + "Key").text
            name = key.rsplit("/", 1)[-1]
            m = _NAME.match(name)
            if not m:
                continue
            out.append({"vpu": m.group(1), "unit": "HU" + m.group(2), "date": m.group(3),
                        "name": name, "key": key, "url": config.S3_BASE + key,
                        "bytes": int(c.find(_NS + "Size").text),
                        "modified": c.find(_NS + "LastModified").text})
        nxt = root.find(_NS + "NextContinuationToken")
        if nxt is None:
            break
        token = nxt.text
    return sorted(out, key=lambda p: p["vpu"])


def inventory(root: Path, *, refresh: bool = False) -> list[dict]:
    """The package list, cached in ``<root>/inventory.json``."""
    path = root / "inventory.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    pkgs = list_packages()
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pkgs, indent=1), encoding="utf-8")
    return pkgs


def package(vpu: str, root: Path) -> dict:
    for pkg in inventory(root):
        if pkg["vpu"] == vpu:
            return pkg
    raise KeyError(f"no VPU/Current package for {vpu}")


def gdb_path(pkg: dict) -> str:
    """The geodatabase inside the zip; its folder carries the zip's name (dated
    packages have a dated folder)."""
    return "/vsizip//vsicurl/" + pkg["url"] + "/" + pkg["name"][:-4] + ".gdb"


def read_layer(pkg: dict, layer: str, columns: list[str], *, geometry: bool,
               path: Optional[str] = None):
    """A layer's columns by lower-case name (older packages spell fields in
    CamelCase, the 2022 reprocessed ones in lower case); columns come back lower
    case, geometry 2D."""
    config.apply_gdal_env()
    import pyogrio
    src = path or gdb_path(pkg)
    fields = list(pyogrio.read_info(src, layer=layer)["fields"])
    by_lower = dict((f.lower(), f) for f in fields)
    missing = [c for c in columns if c.lower() not in by_lower]
    if missing:
        raise KeyError(f"{layer} in {pkg['name']} lacks {missing}")
    df = pyogrio.read_dataframe(src, layer=layer, columns=[by_lower[c.lower()] for c in columns],
                                read_geometry=geometry, force_2d=geometry)
    df.columns = [c if c == "geometry" else c.lower() for c in df.columns]
    return df
