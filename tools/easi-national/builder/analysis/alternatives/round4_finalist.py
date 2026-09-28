"""The finalist step's multiplicity report: BH q-values across the eight primaries.

    python -m builder.analysis.alternatives.round4_finalist --summary <study>/summary.json ...
        [--out <report.json>] [--retrospective <retrospective.json>]

Reads the studies' ``summary.json`` (one per family, runner 1.2.0), takes each family's one
primary comparison (P1 on the family's primary target in the frozen design; P5 for E8), and
reports the Benjamini-Hochberg q-values across them with the addendum's q <= 0.10, beside
each study's own decision by the margins. Reported as supporting evidence only: adoption
rests on the margins. The finalist composition's summary (candidate ``FINALIST``) is listed
apart under ``compositions``: it is not a ninth primary, and its decision is every
component's own rule on the composed arm.

``--retrospective OUT`` writes the one read of the retrospective 2023-24 cohort: per family,
and for the composition, the P1 rows of that cohort on T1 and T2 in both designs, beside the
decision each study took on the development cohort. It is refused when OUT exists, and the
refusal happens before anything else is written: the retrospective cohort is read once.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path

from . import round4 as r4

RETROSPECTIVE_NOTE = (
    "The retrospective cohort (2023-24) is read once, here, after every decision (the eight family "
    "studies and the finalist composition) was taken on the development cohort (2013-19); nothing "
    "here changes a decision or a margin. A target absent from the cohort is recorded as present "
    "false. The composition's rows are the composed arm's, beside the single families' rows.")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_composition(candidate: dict) -> bool:
    return candidate.get("decision_rule") == "composition" or bool(candidate.get("composition"))


def _components(candidate: dict):
    block = candidate.get("composition")
    if isinstance(block, dict):
        return list(block.get("components") or [])
    if isinstance(block, list):
        return list(block)
    return None


def primary_p(candidate: dict) -> tuple:
    """``(p, description)`` of a candidate's primary comparison from its summary block."""
    decision = candidate.get("decision") or {}
    if candidate.get("decision_rule") == "presentation":
        block = candidate.get("P5") or {}
        return block.get("p_two_sided"), "P5 completeness optimism (HUC8 bootstrap, two-sided)"
    primary = decision.get("primary_target") or "T1"
    row = ((candidate.get("P1") or {}).get("designs") or {}).get("frozen", {}).get(primary) or {}
    return row.get("p_two_sided"), f"P1 paired AUC delta on {primary}, frozen design (HUC8 bootstrap, two-sided)"


def benjamini_hochberg(pvalues: dict) -> dict:
    """``{family: q}`` for the families with a p-value: q_(i) = min_{j >= i} p_(j) * m / j."""
    items = [(family, p) for family, p in pvalues.items() if p is not None]
    m = len(items)
    ordered = sorted(items, key=lambda kv: kv[1])
    q = {}
    running = 1.0
    for rank in range(m, 0, -1):
        family, p = ordered[rank - 1]
        running = min(running, p * m / rank)
        q[family] = min(1.0, running)
    return q


def _p1_rows(candidate: dict, design: str) -> dict:
    block = ((candidate.get("P1") or {}).get("designs") or {}).get(design) or {}
    return {t: {k: v for k, v in (block.get(t) or {}).items()
                if k in ("delta", "delta_median", "ci_low", "ci_high", "n", "supported", "present")}
            for t in ("T1", "T2")}


def composition_row(candidate: dict, doc: dict) -> dict:
    """The finalist composition as the multiplicity report lists it, apart from the BH set."""
    decision = candidate.get("decision") or {}
    block = candidate.get("composition") if isinstance(candidate.get("composition"), dict) else {}
    return {"id": candidate["id"], "family_name": candidate.get("family"), "study": doc.get("study_id"),
            "components": _components(candidate), "decision_rule": candidate.get("decision_rule"),
            "adopted_by_margins": decision.get("adopted"), "reasons": decision.get("reasons"),
            "component_rules": {fid: {k: r.get(k) for k in ("decision_rule", "deciding_targets", "passes", "reasons")}
                                for fid, r in (block.get("component_rules") or decision.get("components") or {}).items()},
            "P1_development": {design: _p1_rows(candidate, design) for design in ("frozen", "watershed-held-out")},
            "P2_blocks": (candidate.get("P2") or {}).get("blocks"),
            "P3_documented_gaps_only": (candidate.get("P3") or {}).get("documented_gaps_only"),
            "interaction": block.get("interaction"),
            "component_studies": {fid: {k: cs.get(k) for k in ("study_id", "summary_sha256", "decision")}
                                  for fid, cs in (block.get("component_studies") or {}).items()},
            "note": ("the composition is not one of the eight primaries: it is listed apart from the BH set, and "
                     "its decision is every component's own rule on the composed arm")}


def report(summaries: list[dict]) -> dict:
    rows, compositions = [], []
    pvalues = {}
    for doc in summaries:
        for candidate in doc.get("candidates") or []:
            if is_composition(candidate):
                compositions.append(composition_row(candidate, doc))
                continue
            p, description = primary_p(candidate)
            family = candidate["id"]
            pvalues[family] = p
            rows.append({"family": family, "family_name": candidate.get("family"), "study": doc.get("study_id"),
                         "decision_rule": candidate.get("decision_rule"), "primary": description, "p": p,
                         "adopted_by_margins": (candidate.get("decision") or {}).get("adopted"),
                         "reasons": (candidate.get("decision") or {}).get("reasons")})
    q = benjamini_hochberg(pvalues)
    for row in rows:
        row["q"] = q.get(row["family"])
        row["q_supports"] = None if row["q"] is None else row["q"] <= r4.MARGINS["bh_q"]
    missing = [f for f in r4.FAMILIES if f not in pvalues]
    return {"families_expected": list(r4.FAMILIES), "families_read": sorted(pvalues), "families_missing": missing,
            "q_threshold": r4.MARGINS["bh_q"], "rows": sorted(rows, key=lambda r: r["family"]),
            "compositions": sorted(compositions, key=lambda r: r["id"]),
            "note": ("Benjamini-Hochberg q-values across the eight primaries are reported as supporting evidence; "
                     "adoption rests on each study's margins. A composition is listed apart, outside the BH set: "
                     "its decision is every component's own rule on the composed arm."),
            "complete": not missing}


def retrospective_report(summaries: list[dict]) -> dict:
    """The finalist's one read of the retrospective cohort (2023-24), kept apart from the
    family decisions (which read the development cohort only): per family, and for the
    composition, the P1 rows of the retrospective cohort on T1 and T2 in both designs,
    beside the decision the study made."""
    rows = []
    for doc in summaries:
        for candidate in doc.get("candidates") or []:
            cohorts = (candidate.get("P1") or {}).get("cohorts") or {}
            block = cohorts.get(r4.RETROSPECTIVE_COHORT) or {}
            retro = {design: {t: {k: v for k, v in (block.get(design, {}).get(t) or {}).items()
                                  if k in ("reference", "alternative", "delta", "delta_median", "ci_low",
                                           "ci_high", "n", "boot_valid", "supported", "present")}
                              for t in ("T1", "T2")}
                     for design in ("frozen", "watershed-held-out")}
            rows.append({"family": candidate["id"], "study": doc.get("study_id"),
                         "composition": _components(candidate) if is_composition(candidate) else None,
                         "adopted_by_margins": (candidate.get("decision") or {}).get("adopted"),
                         "deciding_cohort": (candidate.get("decision") or {}).get("deciding_cohort"),
                         "present_targets": {design: [t for t in ("T1", "T2") if retro[design][t].get("present")]
                                             for design in retro},
                         "retrospective": retro})
    ordered = sorted(rows, key=lambda r: (r["composition"] is not None, r["family"]))
    return {"cohort": r4.RETROSPECTIVE_COHORT, "read_at": _now(), "read_once": True,
            "families": [r["family"] for r in ordered if r["composition"] is None],
            "compositions": [r["family"] for r in ordered if r["composition"] is not None],
            "rows": ordered, "note": RETROSPECTIVE_NOTE}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", action="append", required=True, type=Path, help="a study's summary.json (repeat)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--retrospective", type=Path, metavar="OUT",
                    help="also write the separate retrospective-cohort report to OUT (refused when OUT exists: "
                         "the retrospective cohort is read once)")
    a = ap.parse_args(argv)
    if a.retrospective is not None and a.retrospective.exists():
        # refused before anything is written: the file is the record of the one read
        raise SystemExit(f"{a.retrospective} exists: the retrospective cohort is read once")
    summaries = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.summary]
    out = report(summaries)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=1, sort_keys=True))
    if a.retrospective is not None:
        retro = retrospective_report(summaries)
        a.retrospective.parent.mkdir(parents=True, exist_ok=True)
        a.retrospective.write_text(json.dumps(retro, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"retrospective": str(a.retrospective), "families": retro["families"],
                          "compositions": retro["compositions"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
