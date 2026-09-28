"""Coverage of the geometry functions by stream class under K2b (WP-R6c, deliverable 3).

On the stored sample of a Round 4 study (``cohorts/reaches.parquet``: the 100,000 sampled
reaches with weights N_h over n_h) this joins the K2b quality table (``xs_quality``), the
national strata (``analysis/values.parquet``: NARS-9 region, slope class, drainage-area class)
and the study's score tables (the base arm's completeness per function, the E5 arm's withheld
records) and reports, per geometry function and by class, the weighted share of reaches with
a rating under the base, under E5 (the K2 flags as built) and under K2b (the base rating kept
where the quality table says E5b rates the quantity), and the composition of E5's withheld
reaches: regained by K2b (withheld for the capped median only) against still withheld, by the
K2b token (extrapolated bankfull, the cap as the detector's floor, too few sections) and the
impossible ratios. The arithmetic is plain weighted shares (``weighted_share``), tested on a
synthetic sample; the study's own P3 is the authority for the E5b arm and this table is read
beside it.

    python -m builder.analysis.xs_coverage --study <the E5 study folder> --quality <xs_quality.parquet>
        --values D:/Data/easi-national/analysis/values.parquet --out <folder> [--e5b-study <folder>]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from typing import Optional

import numpy as np

#: the geometry functions and the ratio(s) each rates: high-flow dynamics, channel and
#: floodplain dynamics and channel evolution read the bank-height ratio, floodplain
#: connectivity the entrenchment ratio, channel evolution both
FUNCTIONS = {"high_flow_dynamics": ("bhr",), "floodplain_connectivity": ("er",),
             "channel_evolution": ("bhr", "er"), "channel_floodplain_dynamics": ("bhr",)}
CLASSES = ("nars9", "slope_class", "da_class")
RANGE_FLAG = {"bhr": "out_of_range_bhr", "er": "out_of_range_er"}
#: the E5 withheld composition: what K2b does with a reach E5 withheld
REGAINED, EXTRAPOLATED, CAP_FLOOR, FEW_SECTIONS, IMPOSSIBLE, SEVERAL = (
    "regained (capped median only)", "extrapolated bankfull", "cap is the detector's floor",
    "fewer than three sections", "impossible ratio", "several K2b reasons")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def weighted_share(mask, weights) -> Optional[float]:
    """The weighted share of ``mask`` (a boolean array) in a population with ``weights``;
    None for an empty population."""
    mask = np.asarray(mask, dtype=bool)
    weights = np.asarray(weights, dtype=float)
    total = float(weights.sum())
    return float(weights[mask].sum() / total) if total > 0 else None


def rated_under_k2b(quality, ratios) -> np.ndarray:
    """Per row of the quality table, whether an E5b package rates a method reading ``ratios``:
    the cross sections carry every ratio, no ``low_quality`` flag, none of the ratios out of
    range (``screening_methods._withhold_flags`` expands ``out_of_range`` per rated input)."""
    import pandas as pd
    flags = quality["flags"].fillna("").astype(str).str.split(",")
    has = lambda name: flags.apply(lambda items: name in items).to_numpy()  # noqa: E731
    ok = (quality["xs_status"].astype(str) == "ok").to_numpy() & ~has("low_quality")
    for ratio in ratios:
        ok &= pd.to_numeric(quality[ratio], errors="coerce").notna().to_numpy() & ~has(RANGE_FLAG[ratio])
    return ok


def withheld_composition(quality, ratios) -> np.ndarray:
    """Per row of the quality table, the reason class an E5-withheld reach falls in under K2b:
    regained (no K2b flag applies to the method), or the one K2b reason, or several."""
    tokens = quality["low_quality_tokens"].fillna("").astype(str).str.split(",")
    flags = quality["flags"].fillna("").astype(str).str.split(",")
    out = np.empty(len(quality), dtype=object)
    for i, (toks, fl) in enumerate(zip(tokens, flags)):
        reasons = []
        if "bankfull_extrapolated" in toks:
            reasons.append(EXTRAPOLATED)
        if "cap_unreachable" in toks:
            reasons.append(CAP_FLOOR)
        if any(t.startswith("few_sections") for t in toks):
            reasons.append(FEW_SECTIONS)
        if any(RANGE_FLAG[r] in fl for r in ratios):
            reasons.append(IMPOSSIBLE)
        out[i] = REGAINED if not reasons else reasons[0] if len(reasons) == 1 else SEVERAL
    return out


def coverage_table(sample, quality, base_completeness: dict, e5_completeness: dict,
                   e5b_completeness: Optional[dict] = None) -> dict:
    """The coverage table on a weighted sample.

    ``sample``: a pandas frame indexed by comid with ``sample_weight`` and the class columns
    (``nars9``, ``slope_class``, ``da_class``; a missing class reads ``unknown``);
    ``quality``: the K2b quality table indexed by comid (rows for the sample's comids);
    ``base_completeness`` and ``e5_completeness``: ``{function: Series indexed by comid}`` of the
    arms' completeness (``rated`` / ``withheld`` / ``missing``); ``e5b_completeness`` the E5b
    study's when it exists, reported beside the table's own K2b reading.
    """
    import pandas as pd
    weights = sample["sample_weight"].to_numpy(dtype=float)
    q = quality.reindex(sample.index)
    q["flags"] = q["flags"].fillna("")
    q["low_quality_tokens"] = q["low_quality_tokens"].fillna("")
    q["xs_status"] = q["xs_status"].fillna("none")
    out = {"n_sample": int(len(sample)), "population_n": float(weights.sum()), "functions": {}, "by_class": {},
           "withheld_composition": {}, "classes": list(CLASSES)}
    for fn, ratios in FUNCTIONS.items():
        base_rated = (base_completeness[fn].reindex(sample.index).astype(object) == "rated").to_numpy()
        e5_rated = (e5_completeness[fn].reindex(sample.index).astype(object) == "rated").to_numpy()
        e5_withheld = (e5_completeness[fn].reindex(sample.index).astype(object) == "withheld").to_numpy()
        k2b_rated = base_rated & rated_under_k2b(q, ratios)
        comp = withheld_composition(q, ratios)
        entry = {"availability_base": weighted_share(base_rated, weights),
                 "availability_e5": weighted_share(e5_rated, weights),
                 "availability_k2b": weighted_share(k2b_rated, weights),
                 "withheld_e5_share": weighted_share(e5_withheld, weights),
                 "withheld_k2b_share": weighted_share(base_rated & ~k2b_rated, weights),
                 "regained_share": weighted_share(e5_withheld & k2b_rated, weights),
                 "n_base_rated": int(base_rated.sum()), "n_e5_rated": int(e5_rated.sum()), "n_k2b_rated": int(k2b_rated.sum()),
                 "n_e5_withheld": int(e5_withheld.sum()), "n_regained": int((e5_withheld & k2b_rated).sum())}
        if e5b_completeness is not None and fn in e5b_completeness:
            e5b_rated = (e5b_completeness[fn].reindex(sample.index).astype(object) == "rated").to_numpy()
            entry["availability_e5b_study"] = weighted_share(e5b_rated, weights)
            entry["n_e5b_rated"] = int(e5b_rated.sum())
            entry["k2b_reading_matches_study"] = bool(np.array_equal(e5b_rated, k2b_rated))
            entry["n_k2b_reading_differs"] = int((e5b_rated != k2b_rated).sum())
        out["functions"][fn] = entry
        composition = {}
        wsum = float(weights[e5_withheld].sum())
        for label in (REGAINED, EXTRAPOLATED, CAP_FLOOR, FEW_SECTIONS, IMPOSSIBLE, SEVERAL):
            mask = e5_withheld & (comp == label)
            composition[label] = {"n": int(mask.sum()), "weighted": float(weights[mask].sum()),
                                  "share_of_withheld": float(weights[mask].sum() / wsum) if wsum > 0 else None}
        out["withheld_composition"][fn] = composition
        for column in CLASSES:
            if column not in sample.columns:
                continue
            classes = sample[column].astype(object).where(sample[column].notna(), "unknown").to_numpy()
            table = {}
            for value in sorted(set(classes), key=str):
                in_class = classes == value
                w = weights[in_class]
                table[str(value)] = {"n": int(in_class.sum()), "weighted": float(w.sum()),
                                     "availability_base": weighted_share(base_rated[in_class], w),
                                     "availability_e5": weighted_share(e5_rated[in_class], w),
                                     "availability_k2b": weighted_share(k2b_rated[in_class], w),
                                     "regained_share_of_withheld": (float(weights[in_class & e5_withheld & k2b_rated].sum()
                                                                          / weights[in_class & e5_withheld].sum())
                                                                    if weights[in_class & e5_withheld].sum() > 0 else None),
                                     "still_withheld_share": weighted_share((base_rated & ~k2b_rated)[in_class], w)}
            out["by_class"].setdefault(fn, {})[column] = table
    return out


def completeness_columns(scores, functions=FUNCTIONS) -> dict:
    """``{function: completeness Series}`` from a study score table (``completeness__<fn>``, or
    ``rated``/``missing`` from the rating column when the arm carries no completeness)."""
    import pandas as pd
    out = {}
    for fn in functions:
        col = f"completeness__{fn}"
        if col in scores.columns:
            out[fn] = scores[col].astype(object)
        else:
            out[fn] = scores[f"rating__{fn}"].notna().map({True: "rated", False: "missing"})
    return out


def load_inputs(study: Path, quality_path: Path, values_path: Path, *, e5b_study: Optional[Path] = None,
                e5_arm: str = "E5", e5b_arm: str = "E5b"):
    """The frames the table needs: the stored sample with its classes, the quality rows of the
    sample, the base and E5 (and E5b) completeness per function."""
    import pandas as pd
    import pyarrow.parquet as pq
    study = Path(study)
    reaches = pq.read_table(study / "cohorts/reaches.parquet").to_pandas().set_index("comid")
    sample = reaches[reaches["sample"].astype(bool)].copy()
    ids = [int(c) for c in sample.index]
    values = pq.read_table(values_path, columns=["comid", "nars9", "slope_class", "da_class"],
                           filters=[("comid", "in", ids)]).to_pandas().set_index("comid")
    for column in ("slope_class", "da_class"):
        sample[column] = values[column].reindex(sample.index)
    if "nars9" not in sample.columns:
        sample["nars9"] = values["nars9"].reindex(sample.index)
    quality = pq.read_table(quality_path, filters=[("comid", "in", ids)]).to_pandas().set_index("comid")
    base = pq.read_table(study / "scores/alternative-1.parquet").to_pandas().set_index("comid")
    e5 = pq.read_table(study / f"scores/{e5_arm}.parquet").to_pandas().set_index("comid")
    e5b = None
    if e5b_study is not None and (Path(e5b_study) / f"scores/{e5b_arm}.parquet").is_file():
        e5b = pq.read_table(Path(e5b_study) / f"scores/{e5b_arm}.parquet").to_pandas().set_index("comid")
    return sample, quality, completeness_columns(base), completeness_columns(e5), (completeness_columns(e5b) if e5b is not None else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", type=Path, required=True, help="the E5 study folder (cohorts and scores)")
    ap.add_argument("--quality", type=Path, required=True, help="the K2b quality table (xs_quality.parquet)")
    ap.add_argument("--values", type=Path, default=Path("D:/Data/easi-national/analysis/values.parquet"))
    ap.add_argument("--e5b-study", type=Path, default=None, help="the E5b study folder, when its scores exist")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    sample, quality, base, e5, e5b = load_inputs(a.study, a.quality, a.values, e5b_study=a.e5b_study)
    table = coverage_table(sample, quality, base, e5, e5b)
    table.update({"schema": "staf-easi-xs-coverage", "schemaVersion": 1, "writtenAt": _now(),
                  "inputs": {"study": str(a.study), "quality": str(a.quality), "values": str(a.values),
                             "e5b_study": str(a.e5b_study) if a.e5b_study else None},
                  "reading": ("availability_k2b keeps the base rating where the K2b quality table says an E5b package "
                              "rates the method's ratios; the E5b study's own P3 is the authority and is reported "
                              "beside it when given")})
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "xs_coverage.json").write_text(json.dumps(table, indent=1, sort_keys=True, default=float) + "\n",
                                            encoding="utf-8", newline="\n")
    for fn, entry in table["functions"].items():
        print(f"{fn}: base {entry['availability_base']:.4f} E5 {entry['availability_e5']:.4f} K2b {entry['availability_k2b']:.4f}"
              + (f" E5b study {entry['availability_e5b_study']:.4f} (matches {entry['k2b_reading_matches_study']})"
                 if "availability_e5b_study" in entry else ""))
    print(f"wrote {a.out / 'xs_coverage.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
