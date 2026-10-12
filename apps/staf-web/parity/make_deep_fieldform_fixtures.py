"""Goldens for DEEP's field worksheet in the static app (tests/deep-fieldform.test.ts): DEEP's own
report.build_field_forms_pdf on library bundles, with desk and field values and a delineated site.

Each case writes ``tests/fixtures/deep_fieldform/<case>.json`` (the bundle's library path, the ref, the
measured state, the delineation) and ``<case>.pdf``; the test makes the worksheet from the same inputs
and compares the files whole (the worksheet is built with invariant=1).

    .venv\\Scripts\\python apps/staf-web/parity/make_deep_fieldform_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "apps" / "deep"))

from deep import assessments, report  # noqa: E402
from deep.metrics import computed  # noqa: E402

OUT = HERE.parent / "tests" / "fixtures" / "deep_fieldform"
LIB = REPO / "apps" / "library" / "assessments"

SITE = {
    "delineation": {"network": "nhdplus-hr", "nhdplus_id": 10000100012345, "comid": 9327042, "snapped_lat": 43.68581234,
                    "snapped_lon": -72.23671234, "reach_length_ft": 1000.0, "gnis_name": "Mink Brook",
                    "drainage_area_sqkm": 12.43},
    "watershedBasis": "site-engine",
    "siteEngine": {"engineVersion": "0.5.0", "status": "ok"},
}
SITE_V2 = {
    "delineation": {"network": "nhdplus-v2", "comid": 9327042, "snapped_lat": "43.7", "snapped_lon": "-72.2",
                    "reach_length_ft": "1234.5", "gnis_name": None, "drainage_area_sqkm": 3.0},
    "watershedBasis": "nhdplus-v2-basin-of-surrogate",
}


def measured_for(bundle: dict, desk: bool) -> dict:
    """Desk values for the computable metrics (site engine, StreamCat, a reference-only pairing), field
    values for some others, one NA."""
    ids = []
    for fn in bundle.get("metricsByFunction") or []:
        for m in fn.get("metrics") or []:
            if m["metricId"] not in ids:
                ids.append(m["metricId"])
    comp = computed.computable_ids()
    out = {}
    for i, mid in enumerate(ids):
        if mid in comp and desk:
            if i % 3 == 0:
                out[mid] = {"value": 3.0, "origin": "desktop", "basis": "site-engine", "engine": True,
                            "source": "STAF site engine v0.5.0 impervious (HR reach watershed, NLCD 2021)"}
            elif i % 3 == 1:
                out[mid] = {"value": 12.75, "origin": "desktop", "basis": "streamcat", "engine": False,
                            "source": "StreamCat PCTIMP2019WS"}
            else:
                out[mid] = {"value": "0.4", "origin": "desktop", "engine": True, "source": "Computed"}
        elif i % 5 == 1:
            out[mid] = {"value": 1.5 + i, "origin": "field", "source": ""}
        elif i % 7 == 2:
            out[mid] = {"value": None, "na": True, "origin": "field"}
    return out


CASES = [
    ("wgcp", "western-gulf-coastal-plain/v1", "western-gulf-coastal-plain@1", True, SITE),
    ("wy", "wy-sqt-adapted/v1", "", False, None),
    ("neh", "northeastern-highlands", "", True, SITE_V2),
    ("ecbp", "eastern-corn-belt-plains/v6", "eastern-corn-belt-plains@6", True, SITE),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"computable": sorted(computed.computable_ids()), "cases": []}
    for name, where, ref, desk, delin in CASES:
        d = LIB / where
        if not (d / "assessment.deep.json").exists():           # the latest version of an assessment
            d = sorted((p for p in d.iterdir() if p.is_dir() and p.name.startswith("v")), key=lambda p: int(p.name[1:]))[-1]
        path = d / "assessment.deep.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        la = assessments.LoadedAssessment.from_dict(bundle)
        measured = measured_for(bundle, desk)
        pdf = report.build_field_forms_pdf(la, ref=ref, measured=measured, delineation=delin or {})
        (OUT / f"{name}.pdf").write_bytes(pdf)
        case = {"name": name, "bundle": path.relative_to(REPO).as_posix(), "ref": ref, "measured": measured, "delineation": delin or {}}
        (OUT / f"{name}.json").write_text(json.dumps(case, indent=1) + "\n", encoding="utf-8", newline="\n")
        meta["cases"].append(name)
        print(f"{name}: {path.relative_to(REPO).as_posix()} -> {len(pdf)} bytes")
    (OUT / "index.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
