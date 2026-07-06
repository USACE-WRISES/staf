"""Distill the STAF metric library into DEEP's ``data/`` bundle.

DEEP does not invent a schema; it consumes the STAF detailed metric library
(``staf/docs/assets/data``). This script resolves, for each of the predefined
state-SQT assessments, every metric's reference curve and inlines the points so
DEEP is fully self-contained at runtime (no curve-file lookups, and the same
shape as an uploaded bundle).

Resolution per metric:
    assessment metricId
      -> metric-library/index.json (name, discipline, detailsRef)
      -> metrics/<metricId>.json  (detailed profile -> curveSetRefs[0]; inputs, how-to text)
      -> curves/<curveSetRef>.json (curves[0].layers[])
      -> layer whose sourceMetadata.sourceCitation == the assessment's per-metric
         citation (fallback: activeLayer, else first layer)
      -> layer.points  == the inlined [{x, y}] reference curve

Outputs (into ``data/``):
    deep-functions.json          20 functions (order injected from array index)
    deep-outcome-mapping.json    per-function Physical/Chemical/Biological D/i/- codes
    deep-assessments.json        the predefined registry, curves inlined
    bundles/<assessmentId>.deep.json   each assessment as a standalone upload bundle

Anything that fails to resolve is recorded under the assessment's
``unresolvedMetrics`` and reported in the coverage summary, rather than silently
dropped — surfacing STAF data gaps instead of hiding them.

Usage:
    py scripts/build_deep_data.py [--staf-data PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEEP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAF_DATA = DEEP_ROOT.parent / "staf" / "docs" / "assets" / "data"
DEFAULT_OUT = DEEP_ROOT / "data"


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


class Resolver:
    """Resolves metricId -> inlined reference curve against the STAF metric library."""

    def __init__(self, metric_library: Path):
        self.ml = metric_library
        self.index_by_id = {
            m["metricId"]: m for m in load_json(self.ml / "index.json")["metrics"]
        }
        self._detail_cache: dict[str, dict | None] = {}
        self._curve_cache: dict[str, dict | None] = {}

    def _detail(self, metric_id: str) -> dict | None:
        if metric_id in self._detail_cache:
            return self._detail_cache[metric_id]
        candidates: list[Path] = []
        entry = self.index_by_id.get(metric_id)
        if entry and entry.get("detailsRef"):
            candidates.append(self.ml / entry["detailsRef"])
        candidates.append(self.ml / "metrics" / f"{metric_id}.json")
        detail = next((load_json(c) for c in candidates if c.exists()), None)
        self._detail_cache[metric_id] = detail
        return detail

    def _curve(self, ref: str) -> dict | None:
        if ref not in self._curve_cache:
            path = self.ml / "curves" / f"{ref}.json"
            self._curve_cache[ref] = load_json(path) if path.exists() else None
        return self._curve_cache[ref]

    @staticmethod
    def _detailed_profile(detail: dict | None) -> dict | None:
        profiles = (detail or {}).get("profiles", []) or []
        for p in profiles:
            if p.get("tier") == "detailed":
                return p
        for p in profiles:
            if "detailed" in str(p.get("profileId", "")):
                return p
        return profiles[0] if profiles else None

    @staticmethod
    def _curve_refs(profile: dict | None) -> list[str]:
        if not profile:
            return []
        scoring = profile.get("scoring") or {}
        refs = (scoring.get("rubric") or {}).get("curveSetRefs") or []
        if not refs:
            refs = (profile.get("curveIntegration") or {}).get("curveSetRefs") or []
        return refs

    @staticmethod
    def _matching_layers(curve_doc: dict, citation: str) -> list[dict]:
        """All curve layers for the assessment's source citation (multiple = strata)."""
        curves = curve_doc.get("curves") or []
        if not curves:
            return []
        c0 = curves[0]
        layers = c0.get("layers") or []
        if not layers:
            return []
        matches = [L for L in layers
                   if (L.get("sourceMetadata") or {}).get("sourceCitation") == citation]
        if matches:
            return matches
        active = c0.get("activeLayerId")
        return [next((L for L in layers if L.get("id") == active), layers[0])]

    def resolve(self, metric_id: str, citation: str, origin: str) -> tuple[dict | None, str | None]:
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
            pts = [{"x": p["x"], "y": p["y"]} for p in (layer.get("points") or [])
                   if p.get("x") is not None and p.get("y") is not None]
            if pts:
                sm = layer.get("sourceMetadata") or {}
                built.append({"layerName": layer.get("name", ""),
                              "stratum": sm.get("stratification", "") or "", "points": pts})
        if not built:
            return None, f"empty points in {refs[0]}"

        # De-duplicate by stratum (several layers may share a blank stratum).
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
            entry["curve"] = {"layerName": b["layerName"], "stratification": b["stratum"],
                              "points": b["points"]}
        else:
            active = next((b["stratum"] for b in distinct if not b["stratum"]), distinct[0]["stratum"])
            act = next((b for b in distinct if b["stratum"] == active), distinct[0])
            entry["curve"] = {"layerName": act["layerName"], "stratification": act["stratum"],
                              "points": act["points"]}  # default layer (back-compat)
            entry["curveLayers"] = [{"stratum": b["stratum"], "layerName": b["layerName"],
                                     "points": b["points"]} for b in distinct]
            entry["activeStratum"] = active
        return entry, None


def build_functions(staf_data: Path) -> list[dict]:
    funcs = load_json(staf_data / "functions.json")
    return [
        {
            "id": f["id"],
            "name": f["name"],
            "category": f["category"],
            "order": i,
            "function_statement": f.get("function_statement", ""),
            "short_description": f.get("short_description", ""),
            "long_description": f.get("long_description", ""),
            "assessment_context": f.get("assessment_context", ""),
        }
        for i, f in enumerate(funcs)
    ]


def build_assessments(metric_library: Path, resolver: Resolver) -> tuple[dict, list[str]]:
    adapted = load_json(metric_library / "detailed-adapted-assessments.json")
    out_assessments: list[dict] = []
    summary_lines: list[str] = []

    for a in adapted.get("assessments", []):
        citations = a.get("metricSourceCitationsById", {})
        default_citation = a.get("sourceCitation", "")
        mbf_out: list[dict] = []
        unresolved: list[dict] = []
        total = 0

        for fn in a.get("metricsByFunction", []):
            fid = fn["functionId"]
            metrics_out: list[dict] = []
            for m in fn.get("metrics", []):
                total += 1
                mid = m["metricId"]
                citation = citations.get(mid, default_citation)
                entry, err = resolver.resolve(mid, citation, m.get("assignmentOrigin", ""))
                if entry:
                    metrics_out.append(entry)
                else:
                    unresolved.append({"functionId": fid, "metricId": mid, "reason": err})
            mbf_out.append({
                "functionId": fid,
                "functionName": fn.get("functionName", ""),
                "discipline": fn.get("discipline", ""),
                "metrics": metrics_out,
            })

        resolved = total - len(unresolved)
        out_assessments.append({
            "assessmentId": a["assessmentId"],
            "assessmentName": a.get("assessmentName", a["assessmentId"]),
            "stateCode": a.get("stateCode", ""),
            "stateName": a.get("stateName", ""),
            "sourceCitation": default_citation,
            "applicability": a.get("applicability", ""),
            "functionCount": len([f for f in mbf_out if f["metrics"]]),
            "metricsByFunction": mbf_out,
            "unresolvedMetrics": unresolved,
        })
        summary_lines.append(
            f"  {a['assessmentId']:<18} {resolved:>2}/{total:<2} metrics resolved, "
            f"{out_assessments[-1]['functionCount']:>2}/20 functions scored"
            + (f"  ({len(unresolved)} unresolved)" if unresolved else "")
        )

    doc = {
        "schemaVersion": 1,
        "tier": "detailed",
        "generatedFrom": "STAF metric library (detailed-adapted-assessments.json)",
        "assessments": out_assessments,
    }
    return doc, summary_lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build DEEP's data/ bundle from the STAF metric library.")
    parser.add_argument("--staf-data", type=Path, default=DEFAULT_STAF_DATA,
                        help="Path to staf/docs/assets/data (default: sibling staf checkout).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output data/ directory.")
    args = parser.parse_args(argv)

    staf_data: Path = args.staf_data
    metric_library = staf_data / "metric-library"
    if not (metric_library / "index.json").exists():
        print(f"ERROR: STAF metric library not found at {metric_library}", file=sys.stderr)
        print("Pass --staf-data pointing at staf/docs/assets/data", file=sys.stderr)
        return 2

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    # 1. Functions (order injected).
    write_json(out / "deep-functions.json", build_functions(staf_data))

    # 2. Outcome mapping (copied through).
    write_json(out / "deep-outcome-mapping.json", load_json(staf_data / "cwa-mapping.json"))

    # 3. Predefined assessments with inlined curves.
    resolver = Resolver(metric_library)
    assessments_doc, summary = build_assessments(metric_library, resolver)
    write_json(out / "deep-assessments.json", assessments_doc)

    # 4. Per-assessment standalone bundles (same shape DEEP accepts on upload).
    for a in assessments_doc["assessments"]:
        write_json(out / "bundles" / f"{a['assessmentId']}.deep.json",
                   {"schemaVersion": 1, "tier": "detailed", **a})

    print(f"Wrote DEEP data to {out}")
    print(f"  deep-functions.json           ({len(build_functions(staf_data))} functions)")
    print(f"  deep-outcome-mapping.json")
    print(f"  deep-assessments.json         ({len(assessments_doc['assessments'])} assessments)")
    print("Coverage:")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
