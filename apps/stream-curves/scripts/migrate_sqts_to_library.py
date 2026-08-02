"""Migrate the 8 state SQT assessments into ``apps/library`` as preliminary v1 versions.

The eight adapted state SQTs (AK / CO / MI / MN / NC / SC / WI / WY) live in the STAF
metric library (``docs/assets/data/metric-library/detailed-adapted-assessments.json``),
not in StreamCurves. This script resolves each one's reference curves against the metric
library (the same resolution ``apps/deep/scripts/build_deep_data.py`` performs), attaches a
**state applicability polygon** (via ``streamcurves.geo.region_polygon_geometry``), and
publishes a **preliminary** v1 library version so DEEP can hard-block on a covering polygon.

Idempotent: an assessment already present in the library is skipped unless ``--force``.
``--dry-run`` reports what it would publish and writes nothing.

Usage:
    py scripts/migrate_sqts_to_library.py --dry-run
    py scripts/migrate_sqts_to_library.py [--staf-data PATH] [--library-root PATH] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

APP_DIR = Path(__file__).resolve().parents[1]          # apps/stream-curves
REPO_ROOT = Path(__file__).resolve().parents[3]        # repo root
DEFAULT_STAF_DATA = REPO_ROOT / "docs" / "assets" / "data"
SQT_SUFFIX = "-sqt-adapted"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from streamcurves import deep_export  # noqa: E402
from streamcurves import geo  # noqa: E402
from streamcurves import library as lib  # noqa: E402
from streamcurves import session_io as sio  # noqa: E402


# --------------------------------------------------------------------------- #
# Curve resolution against the STAF metric library
# (compact port of apps/deep/scripts/build_deep_data.py Resolver, kept self-contained
#  so this script does not import across apps)
# --------------------------------------------------------------------------- #
def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Resolver:
    """metricId -> inlined reference curve, resolved against the STAF metric library."""

    def __init__(self, metric_library: Path):
        self.ml = metric_library
        self.index_by_id = {
            m["metricId"]: m for m in _load_json(self.ml / "index.json")["metrics"]
        }
        self._detail_cache: dict[str, Optional[dict]] = {}
        self._curve_cache: dict[str, Optional[dict]] = {}

    def _detail(self, metric_id: str) -> Optional[dict]:
        if metric_id in self._detail_cache:
            return self._detail_cache[metric_id]
        candidates: list[Path] = []
        entry = self.index_by_id.get(metric_id)
        if entry and entry.get("detailsRef"):
            candidates.append(self.ml / entry["detailsRef"])
        candidates.append(self.ml / "metrics" / f"{metric_id}.json")
        detail = next((_load_json(c) for c in candidates if c.exists()), None)
        self._detail_cache[metric_id] = detail
        return detail

    def _curve(self, ref: str) -> Optional[dict]:
        if ref not in self._curve_cache:
            path = self.ml / "curves" / f"{ref}.json"
            self._curve_cache[ref] = _load_json(path) if path.exists() else None
        return self._curve_cache[ref]

    @staticmethod
    def _detailed_profile(detail: Optional[dict]) -> Optional[dict]:
        profiles = (detail or {}).get("profiles", []) or []
        for p in profiles:
            if p.get("tier") == "detailed":
                return p
        for p in profiles:
            if "detailed" in str(p.get("profileId", "")):
                return p
        return profiles[0] if profiles else None

    @staticmethod
    def _curve_refs(profile: Optional[dict]) -> list[str]:
        if not profile:
            return []
        scoring = profile.get("scoring") or {}
        refs = (scoring.get("rubric") or {}).get("curveSetRefs") or []
        if not refs:
            refs = (profile.get("curveIntegration") or {}).get("curveSetRefs") or []
        return refs

    @staticmethod
    def _matching_layers(curve_doc: dict, citation: str) -> list[dict]:
        curves = curve_doc.get("curves") or []
        if not curves:
            return []
        c0 = curves[0]
        layers = c0.get("layers") or []
        if not layers:
            return []
        matches = [
            L for L in layers
            if (L.get("sourceMetadata") or {}).get("sourceCitation") == citation
        ]
        if matches:
            return matches
        active = c0.get("activeLayerId")
        return [next((L for L in layers if L.get("id") == active), layers[0])]

    def resolve(self, metric_id: str, citation: str, origin: str):
        detail = self._detail(metric_id)
        refs = self._curve_refs(self._detailed_profile(detail))
        if not refs:
            return None, "no detailed curveSetRef"
        curve_doc = self._curve(refs[0])
        if not curve_doc:
            return None, f"curve file missing: {refs[0]}"
        layers = self._matching_layers(curve_doc, citation)
        if not layers:
            return None, f"no usable layer in {refs[0]}"

        built = []
        for layer in layers:
            pts = [
                {"x": p["x"], "y": p["y"]}
                for p in (layer.get("points") or [])
                if p.get("x") is not None and p.get("y") is not None
            ]
            if pts:
                sm = layer.get("sourceMetadata") or {}
                built.append(
                    {
                        "layerName": layer.get("name", ""),
                        "stratum": sm.get("stratification", "") or "",
                        "points": pts,
                    }
                )
        if not built:
            return None, f"empty points in {refs[0]}"

        distinct, seen = [], set()
        for b in built:
            if b["stratum"] in seen:
                continue
            seen.add(b["stratum"])
            distinct.append(b)

        axes = curve_doc.get("axes") or {}
        index_entry = self.index_by_id.get(metric_id, {})
        inputs = (detail or {}).get("inputs") or []
        entry = {
            "metricId": metric_id,
            "metricName": (detail or {}).get("name") or index_entry.get("name") or metric_id,
            "discipline": (detail or {}).get("discipline") or index_entry.get("discipline", ""),
            "inputType": inputs[0].get("type", "") if inputs else "",
            "sourceCitation": citation,
            "xLabel": axes.get("xLabel", ""),
            "metricStatement": (detail or {}).get("descriptionMarkdown", ""),
            "howToMeasure": (detail or {}).get("howToMeasureMarkdown", ""),
            "methodContext": (detail or {}).get("methodContextMarkdown", ""),
            "assignmentOrigin": origin,
        }
        if len(distinct) <= 1:
            b = built[0]
            entry["curve"] = {
                "layerName": b["layerName"],
                "stratification": b["stratum"],
                "points": b["points"],
            }
        else:
            active = next((b["stratum"] for b in distinct if not b["stratum"]), distinct[0]["stratum"])
            act = next((b for b in distinct if b["stratum"] == active), distinct[0])
            entry["curve"] = {
                "layerName": act["layerName"],
                "stratification": act["stratum"],
                "points": act["points"],
            }
            entry["curveLayers"] = [
                {"stratum": b["stratum"], "layerName": b["layerName"], "points": b["points"]}
                for b in distinct
            ]
            entry["activeStratum"] = active
        return entry, None


# --------------------------------------------------------------------------- #
# Bundle assembly
# --------------------------------------------------------------------------- #
def _build_bundle(assessment: dict, resolver: Resolver) -> tuple[dict, int, int]:
    """Return ``(bundle, resolved, total)`` for one SQT: a DEEP bundle with inlined
    curves (region is attached by :func:`library.publish_version` from ``meta``)."""
    citations = assessment.get("metricSourceCitationsById", {})
    default_citation = assessment.get("sourceCitation", "")
    mbf_out: list[dict] = []
    resolved = total = 0
    for fn in assessment.get("metricsByFunction", []):
        metrics_out: list[dict] = []
        for m in fn.get("metrics", []):
            total += 1
            mid = m["metricId"]
            entry, _err = resolver.resolve(
                mid, citations.get(mid, default_citation), m.get("assignmentOrigin", "")
            )
            if entry:
                resolved += 1
                metrics_out.append(entry)
        mbf_out.append(
            {
                "functionId": fn["functionId"],
                "functionName": fn.get("functionName", ""),
                "discipline": fn.get("discipline", ""),
                "metrics": metrics_out,
            }
        )
    bundle = {
        "schemaVersion": 1,
        "tier": "detailed",
        "assessmentId": assessment["assessmentId"],
        "assessmentName": assessment.get("assessmentName", assessment["assessmentId"]),
        "stateCode": assessment.get("stateCode", ""),
        "stateName": assessment.get("stateName", ""),
        "sourceCitation": default_citation,
        "applicability": assessment.get("applicability", ""),
        "metricsByFunction": mbf_out,
    }
    bundle["functionCoverage"] = _inherited_coverage(assessment, mbf_out, default_citation)
    return bundle, resolved, total


def _inherited_coverage(assessment: dict, mbf_out: list[dict], citation: str) -> dict:
    """Coverage block for an SQT transcription.

    These assessments are not derived here -- they transcribe a published state
    Stream Quantification Tool, so the functions they omit are omitted by the
    issuing state's workbook. That is a documented inheritance, not a defect, and
    it must be on the record rather than blocking the publish gate: synthesizing
    curves for the gaps would fabricate content the state never published.
    """
    source = citation or assessment.get("sourceCitation") or "the source SQT"
    exclusions = [
        {
            "functionId": fid,
            "reason": "no-suitable-metric",
            "justification": (
                f"Not addressed by {source}, which this assessment transcribes. "
                "Coverage is inherited from the source workbook; no metric was "
                "synthesized for it here."
            ),
            "recordedBy": "STAF SQT migration",
        }
        for fid in (assessment.get("missingFunctionIds") or [])
    ]
    return deep_export.function_coverage(
        mbf_out, deep_export.deep_read_staf_crosswalk(), exclusions)


def _session_payload(assessment: dict, region: dict) -> dict:
    """A minimal round-trippable session stub for the migrated version (the SQTs have no
    StreamCurves working session; only identity + region travel)."""
    name = assessment.get("assessmentName", assessment["assessmentId"])
    return sio.dump_session_fields(
        {
            "session_name": name,
            "region_of_applicability": region,
            "app_data_loaded": False,
        },
        session_name=name,
    )


def _polygon_summary(code: str) -> tuple[Optional[str], int]:
    """(geometry type, outer-ring vertex count) for a state polygon, or (None, 0)."""
    geom = geo.region_polygon_geometry("state", code)
    if not geom:
        return None, 0
    if geom["type"] == "Polygon":
        rings = [geom["coordinates"]]
    else:
        rings = geom["coordinates"]
    verts = sum(len(poly[0]) for poly in rings if poly)
    return geom["type"], verts


# --------------------------------------------------------------------------- #
# Migration driver
# --------------------------------------------------------------------------- #
def migrate(
    staf_data: Path | str = DEFAULT_STAF_DATA,
    *,
    dry_run: bool = False,
    force: bool = False,
    library_root: Path | str | None = None,
) -> list[dict]:
    """Migrate the 8 state SQTs into the assessment library.

    Returns one result dict per SQT: ``assessmentId``, ``stateCode``, ``action``
    (``would-publish`` / ``published`` / ``skipped`` / ``error``), ``version`` (when
    published), ``polygonType`` / ``polygonVertices``, ``metricsResolved`` /
    ``metricsTotal``, and ``reason`` (for skipped / error).
    """
    staf_data = Path(staf_data)
    if library_root is not None:
        os.environ[lib._ENV_ROOT] = str(library_root)

    metric_library = staf_data / "metric-library"
    adapted = _load_json(metric_library / "detailed-adapted-assessments.json")
    sqts = [
        a for a in adapted.get("assessments", [])
        if str(a.get("assessmentId", "")).endswith(SQT_SUFFIX)
    ]
    resolver = Resolver(metric_library)

    if not dry_run:
        root = lib.library_root()
        (root / "assessments").mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for a in sqts:
        aid = a["assessmentId"]
        code = a.get("stateCode", "")
        state_name = a.get("stateName", "")
        region = {"kind": "state", "code": code, "name": state_name}
        poly_type, poly_verts = _polygon_summary(code)
        _bundle, resolved, total = _build_bundle(a, resolver)
        rec: dict = {
            "assessmentId": aid,
            "stateCode": code,
            "polygonType": poly_type,
            "polygonVertices": poly_verts,
            "metricsResolved": resolved,
            "metricsTotal": total,
        }

        existing = lib.latest_version(aid)
        if existing > 0 and not force:
            rec["action"] = "skipped"
            rec["reason"] = f"already present (latest v{existing}); use --force to add a version"
            results.append(rec)
            continue

        if poly_type is None:
            rec["action"] = "error"
            rec["reason"] = f"no state polygon resolved for {code!r}"
            results.append(rec)
            continue

        if dry_run:
            rec["action"] = "would-publish"
            results.append(rec)
            continue

        meta = {
            "assessmentName": a.get("assessmentName", aid),
            "region": region,
            "stateCode": code,
            "stateName": state_name,
            "sourceCitation": a.get("sourceCitation", ""),
            "author": "STAF SQT migration",
            "revisionNotes": "Migrated from the STAF metric library "
            "(detailed-adapted-assessments.json).",
        }
        try:
            version = lib.publish_version(aid, meta, _session_payload(a, region), _bundle)
        except Exception as e:  # noqa: BLE001
            rec["action"] = "error"
            rec["reason"] = str(e)
            results.append(rec)
            continue
        rec["action"] = "published"
        rec["version"] = version
        results.append(rec)

    return results


def _print_summary(results: list[dict], dry_run: bool) -> None:
    verb = "Would migrate" if dry_run else "Migrated"
    print(f"{verb} {len([r for r in results if r['action'] in ('would-publish', 'published')])} "
          f"of {len(results)} state SQT(s):")
    for r in results:
        poly = (
            f"{r['polygonType']} ({r['polygonVertices']} verts)"
            if r.get("polygonType")
            else "NO POLYGON"
        )
        line = (
            f"  {r['assessmentId']:<18} {r['stateCode']:<3} {r['action']:<13} "
            f"{r['metricsResolved']:>2}/{r['metricsTotal']:<2} curves  {poly}"
        )
        if r.get("version"):
            line += f"  -> v{r['version']} (preliminary)"
        if r.get("reason"):
            line += f"  [{r['reason']}]"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate the 8 state SQTs into apps/library as preliminary v1 versions."
    )
    parser.add_argument("--staf-data", type=Path, default=DEFAULT_STAF_DATA,
                        help="Path to docs/assets/data (default: repo checkout).")
    parser.add_argument("--library-root", type=Path, default=None,
                        help="Override the library root (default: the apps/library sibling).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be published; write nothing.")
    parser.add_argument("--force", action="store_true",
                        help="Publish a new version even if the assessment already exists.")
    args = parser.parse_args(argv)

    results = migrate(
        args.staf_data,
        dry_run=args.dry_run,
        force=args.force,
        library_root=args.library_root,
    )
    _print_summary(results, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
