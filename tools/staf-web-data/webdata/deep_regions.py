"""DEEP's assessment regions for the static app: the list DEEP's regions sidebar shows.

The Shiny DEEP answers its page's ``coverage_ready`` with every available assessment's region outline
(apps/deep/app.py ``_send_coverage``, from ``deep.assessments.library_region_features``). This module
writes the same payload, ``deep-regions.json``, from the ``library`` release alone, with DEEP's rules
carried here so the static app keeps working once the Shiny apps are retired:

- the catalog (``library.json``, schema 1) as ``deep/remote_library.py`` reads it: entries with a
  usable id, DEEP's type only, versions with a number and a status; a version is eligible when its
  status is draft, preliminary or certified, Show in DEEP is not off (``visibleInDeep: false``) and
  it names a usable bundle asset, whose bytes match the catalog's size and sha256;
- every eligible version's bundle, stamped as DEEP stamps it (``version``, ``lifecycle``,
  ``assessmentRef``), with the assessments DEEP hides (``-sqt-adapted``, ``deep/config.py``) left out;
- one version per assessment, DEEP's default (``deep/library.py default_pointers``): the newest Final
  version, else the newest Preliminary, else the newest Draft;
- its region outline (``deep/assessments.py _region_geometry``) and the sidebar's fields.

A DEEP deployed with a baked registry also serves its baked versions, which the release normally holds
as well; ``tests/test_deep_regions.py`` compares the two while apps/deep is still in the repo.

    python tools/staf-web-data/webdata/deep_regions.py [library folder] [out file]
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

NAME = "deep-regions.json"
SCHEMA = 1
ELIGIBLE = ("draft", "preliminary", "certified")
DEFAULT_ORDER = ("certified", "preliminary", "draft")
HIDDEN_ID_SUFFIXES = ("-sqt-adapted",)
LABELS = {"draft": "Draft", "preliminary": "Preliminary", "under_review": "Under review", "certified": "Final",
          "revised": "Revised", "retired": "Retired"}
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def _sha(value):
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    text = text[len("sha256:"):] if text.startswith("sha256:") else text
    return text if _SHA_RE.fullmatch(text) else None


def _asset(raw):
    if not isinstance(raw, dict):
        return None
    name, size, sha = raw.get("name"), _as_int(raw.get("size")), _sha(raw.get("sha256"))
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or size is None or size < 0 or sha is None:
        return None
    return {"name": name, "size": size, "sha256": sha}


def eligible_versions(doc: dict) -> list[dict]:
    """``[{"id", "version", "status", "bundle"}]``: the catalog's eligible versions, each assessment's
    ascending, in catalog order (``remote_library.parse_catalog``)."""
    schema = _as_int(doc.get("schema"))
    if schema is None or schema < 1 or schema > SCHEMA:
        raise ValueError(f"library.json schema {doc.get('schema')!r} is not one this reads")
    out, seen = [], set()
    for entry in doc.get("assessments") or []:
        aid = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(aid, str) or not _ID_RE.fullmatch(aid) or aid in seen:
            continue
        if str(entry.get("type") or entry.get("assessmentType") or "deep") != "deep":
            continue
        seen.add(aid)
        by_number = {}
        for raw in entry.get("versions") if isinstance(entry.get("versions"), list) else []:
            number = _as_int(raw.get("version")) if isinstance(raw, dict) else None
            status = raw.get("status") if isinstance(raw, dict) else None
            status = status.strip().lower() if isinstance(status, str) else ""
            if number is None or number < 1 or not status or number in by_number:
                continue
            visible = raw.get("visibleInDeep") is not False
            bundle = _asset((raw.get("assets") or {}).get("bundle")) if isinstance(raw.get("assets"), dict) else None
            if status in ELIGIBLE and visible and bundle is None:
                continue                      # an eligible version without a usable bundle is skipped
            by_number[number] = {"id": aid, "version": number, "status": status,
                                 "eligible": status in ELIGIBLE and visible, "bundle": bundle}
        out.extend(v for n, v in sorted(by_number.items()) if v["eligible"])
    return out


def _region_geometry(region):
    poly = (region or {}).get("polygon")
    if not poly:
        return None
    if isinstance(poly, dict) and poly.get("type") and poly.get("coordinates"):
        return poly
    if isinstance(poly, list) and poly:
        first = poly[0]
        if first and isinstance(first[0], (int, float)):
            return {"type": "Polygon", "coordinates": [poly]}
        return {"type": "Polygon", "coordinates": poly}
    return None


def _lifecycle(record: dict) -> str:
    lib = record.get("library") or {}
    for src in (record, lib):
        for key in ("lifecycle", "status"):
            v = src.get(key)
            if v and str(v).strip().lower() in ELIGIBLE:
                return str(v).strip().lower()
    return "preliminary"


def payload(library: Path) -> dict:
    """``{"features": [...]}`` as DEEP's server sends it (``deep_coverage``)."""
    library = Path(library)
    doc = json.loads((library / "library.json").read_text(encoding="utf-8-sig"))
    records = []
    for v in eligible_versions(doc):
        path = library / v["bundle"]["name"]
        data = path.read_bytes()
        if len(data) != v["bundle"]["size"] or hashlib.sha256(data).hexdigest() != v["bundle"]["sha256"]:
            raise ValueError(f"{path.name} does not match library.json")
        bundle = json.loads(data.decode("utf-8-sig"))
        if not isinstance(bundle, dict) or bundle.get("assessmentId") != v["id"]:
            continue
        record = dict(bundle)
        record.update(version=v["version"], lifecycle=v["status"], assessmentRef=f"{v['id']}@v{v['version']}")
        if not str(v["id"]).endswith(HIDDEN_ID_SUFFIXES):
            records.append(record)
    # each assessment's default version (deep/library.py default_pointers)
    versions: dict = {}
    for r in records:
        life = str(r.get("lifecycle") or "preliminary").strip().lower()
        versions.setdefault(r["assessmentId"], {s: [] for s in ELIGIBLE})[life if life in ELIGIBLE else "preliminary"].append(int(r["version"]))
    default = {aid: next(max(d[s]) for s in DEFAULT_ORDER if d[s]) for aid, d in versions.items()}
    features = []
    for r in records:
        if r["version"] != default[r["assessmentId"]]:
            continue
        region = r.get("region") or (r.get("library") or {}).get("region") or {}
        geom = _region_geometry(region)
        if not geom:
            continue
        life = _lifecycle(r)
        features.append({
            "assessmentId": r.get("assessmentId"),
            "name": region.get("name", "") or r.get("assessmentName", r.get("assessmentId")) or r.get("assessmentId"),
            "code": str(region.get("code") or ""),
            "version": (r.get("library") or {}).get("version"),
            "status": LABELS.get(life) or life.title(),
            "lifecycle": life,
            "certified": life == "certified",
            "geometry": geom,
        })
    return {"features": features}


def write(library: Path, out: Path) -> Path:
    data = payload(library)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    lib = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\Data\staf-web\library-release")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(r"D:\Data\staf-web\derived") / NAME
    path = write(lib, dst)
    print(f"{path}: {len(json.loads(path.read_text(encoding='utf-8'))['features'])} regions, "
          f"{path.stat().st_size / 1e6:.2f} MB")
