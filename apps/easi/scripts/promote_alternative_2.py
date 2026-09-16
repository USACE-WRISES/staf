"""Promote the preserved Alternative 2 assets without refitting or scoring.

Run with the workspace interpreter and --study pointing at the completed study.
Only the live curve, catalog, identity and build-time receipt are written. Source
study files and the historical Alternative 1 snapshot are verified, never edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
STUDY_ID = "2026-09-15-controlled-alternatives"
CURVES_SHA = "a824e2c254dea1c22af62d2a6f5fd3d0862ff0574190111655aa5b34dbce4887"
CATALOG_SHA = "64c0a49da530879ed7c0bb329ace1f12c4b386fa1f02378865c0e882c5331d6e"
BASE_CURVES_SHA = "ad100b39313af9259bd0628728d50ab87513e2c07955bf24d78e9d107863abdd"
BASE_CATALOG_SHA = "ba169b20b7fc57c02a2b065298bd0b26df415094b5705470fc4fa3fab01ab28f"
COMPLETION_SHA = "97a24b44e2314cbe5c001d99bb67ce71343ac99d432f27bec54948342b0fa7dd"
REGISTRY_SHA = "99539cbcc4a2a25a62fd25c6638c2f725751293252a0329f31d66db229977410"
NARS_SHA = "f08b5be1ec4149da224f7fc5185c755f0d612d9115c53a172081c67009ab0300"
REGIONS = {"CPL", "NAP", "NPL", "SAP", "SPL", "TPL", "UMW", "WMT", "XER"}
REGIONAL_SETS = {"corridor-natural", "corridor-woody", "flow-variability"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    if sha(raw) != expected:
        raise ValueError(f"Source hash differs: {path}")
    return raw


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def atomic_write(path: Path, raw: bytes) -> None:
    if path.is_file() and path.read_bytes() == raw:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".promotion.tmp")
    temp.write_bytes(raw)
    os.replace(temp, path)


def promote(study: Path, destination: Path) -> dict:
    study, destination = study.resolve(), destination.resolve()
    if study.name != STUDY_ID:
        raise ValueError(f"Expected preserved study {STUDY_ID}")
    if destination == study or study in destination.parents:
        raise ValueError("Promotion destination must be outside the preserved study")
    source = study / "candidates/alternative-2/app-data"
    baseline = study / "snapshot/app-data"
    completion = json.loads(checked(study / "completion.json", COMPLETION_SHA))
    if completion.get("status") != "complete":
        raise ValueError("Source study is incomplete")
    curves_raw = checked(source / "reference-curves.json", CURVES_SHA)
    catalog_raw = checked(source / "screening-methods.json", CATALOG_SHA)
    base_curves = json.loads(checked(baseline / "reference-curves.json", BASE_CURVES_SHA))
    checked(baseline / "screening-methods.json", BASE_CATALOG_SHA)
    checked(source / "nars-ecoregions-9.geojson.gz", NARS_SHA)
    curves = json.loads(curves_raw)
    sets = curves["sets"]
    if set(sets) != REGIONAL_SETS | {"entrenchment"}:
        raise ValueError("Unexpected reference families")
    if curves["provenance"]["registry"]["sha256"] != REGISTRY_SHA:
        raise ValueError("Reference registry provenance differs")
    for key in REGIONAL_SETS:
        if sets[key]["stratifier"] != "nars9" or set(sets[key]["curves"]) != REGIONS | {"national"}:
            raise ValueError(f"Expected nine NARS regions and national fallback: {key}")
        if sets[key]["curves"]["national"] != base_curves["sets"][key]["curves"]["national"]:
            raise ValueError(f"National fallback changed: {key}")
    if sets["entrenchment"] != base_curves["sets"]["entrenchment"]:
        raise ValueError("Entrenchment reference definitions changed")
    count = sum(len(item["curves"]) for item in sets.values())
    if count != 34:
        raise ValueError("Alternative 2 must contain exactly 34 reference curves")

    # This is the complete allowed catalog change from the preserved candidate.
    # Byte replacement preserves its formatting and every executable definition.
    if catalog_raw.count(b"Level II") != 4:
        raise ValueError("Expected exactly four inherited Level II prose references")
    promoted_catalog = catalog_raw.replace(b"Level II", b"NARS-9")
    unchanged = {}
    for path in sorted(source.iterdir()):
        if not path.is_file() or path.name in {"reference-curves.json", "screening-methods.json"}:
            continue
        source_raw = path.read_bytes()
        if (baseline / path.name).read_bytes() != source_raw:
            raise ValueError(f"Candidate changed an unrelated asset: {path.name}")
        if (destination / path.name).read_bytes() != source_raw:
            raise ValueError(f"Live unrelated asset differs: {path.name}")
        unchanged[path.name] = sha(source_raw)
    identity = {
        "schema_version": 1,
        "alternative_id": "alternative-2",
        "alternative_name": "Alternative 2: NARS-9 references",
        "criteria_family": "regional",
        "curve_count": count,
        "catalog_sha256": sha(promoted_catalog),
        "curves_sha256": CURVES_SHA,
        "nars_geography_sha256": NARS_SHA,
        "source": {
            "study_id": STUDY_ID,
            "study_completion_sha256": COMPLETION_SHA,
            "candidate_catalog_sha256": CATALOG_SHA,
            "original_registry_sha256": REGISTRY_SHA,
            "alternative_1_method_version": "e9f472b31fe5",
            "alternative_1_curves_sha256": BASE_CURVES_SHA,
        },
    }
    outputs = {"reference-curves.json": curves_raw, "screening-methods.json": promoted_catalog,
               "scoring-identity.json": json_bytes(identity)}
    # Validate every target before writing any, including idempotent reruns.
    allowed = {"reference-curves.json": {BASE_CURVES_SHA, CURVES_SHA},
               "screening-methods.json": {BASE_CATALOG_SHA, sha(promoted_catalog)},
               "scoring-identity.json": {sha(outputs["scoring-identity.json"])}}
    for name, hashes in allowed.items():
        target = destination / name
        if target.exists() and sha(target.read_bytes()) not in hashes:
            raise ValueError(f"Live target has unrecognized changes: {target}")
    receipt = {"schema_version": 1, "operation": "promote-frozen-alternative-2",
               "source_study_id": STUDY_ID, "source_completion_sha256": COMPLETION_SHA,
               "source_catalog_sha256": CATALOG_SHA, "source_curves_sha256": CURVES_SHA,
               "catalog_prose_replacement": {"from": "Level II", "to": "NARS-9", "count": 4},
               "unchanged_assets": unchanged,
               "outputs": {name: sha(raw) for name, raw in outputs.items()},
               "preserved_alternative_1": {"method_version": "e9f472b31fe5",
                    "catalog_sha256": BASE_CATALOG_SHA, "curves_sha256": BASE_CURVES_SHA}}
    for name, raw in outputs.items():
        atomic_write(destination / name, raw)
    atomic_write(destination / "source/alternative-2-promotion.json", json_bytes(receipt))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=APP / "data")
    args = parser.parse_args()
    print(json.dumps(promote(args.study, args.destination), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
