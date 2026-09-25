"""Audit how the three NRSA cycles harmonize for every mapped metric.

Writes ``config/nrsa_harmonization_audit.csv`` (one row per mapped NRSA metric
and cycle, plus a ``pooled`` row for the DATA-11 selection) and
``config/nrsa_harmonization_audit.md`` (counts only). Both are generated:
``--check`` compares the committed files with a fresh build and exits 1 when
they are stale, and ``tests/test_harmonization_audit.py`` gates the same way.
The audit reads the archive under ``data/nrsa/`` and never writes there, because
every file there is hashed into the manifest that every pooled build digests.

    py -3.12 scripts/nrsa/build_harmonization_audit.py            # write both files
    py -3.12 scripts/nrsa/build_harmonization_audit.py --check    # exit 1 when stale

Rows
----
The metrics are those ``config/metric_map.yaml`` maps with ``source: nrsa``
(``verify_inputs.mapped_nrsa_metrics``). Each has a row per archive cycle
(``1314``, ``1819``, ``2324``), whether or not the cycle may pool, and one
``pooled`` row. Rows are sorted by (metric_key, cycle); ``pooled`` sorts last.

Populations
-----------
* *cycle rows* describe the archive as stored: every site-visit row of the
  cycle in ``values.parquet`` (index visits, second visits and the two
  ``R``-coded repeat rows alike), before any ACC-02 removal.
* *frame* is the run's own reference frame (rule DATA-10 through
  ``nrsa_dataset.governed_frame`` and ``reference_pool.national_frame``: stream
  order 1 to 5, the wadeable protocol where the order is unknown, non-canal).
  It is NOT the screen table's order-only ``wadeable`` column, which marks fewer
  stations; the summary states both counts. *strict* is the frame's stations
  that pass the ``least-disturbed-v1`` strict screen.
* the *pooled row* is the DATA-11 selection (``nrsa_dataset.latest_values`` over
  every archive station: per metric, the newest compatible cycle's index visit
  that carries a value, with ACC-02 cycle restrictions and EPA's below-protocol
  fish samples removed).

Columns
-------
metric_key, cycle
    The metric and the cycle, or ``pooled``.
eligible
    ``yes`` when ``config/nrsa_cycle_compatibility.yaml`` lets the metric pool
    the cycle (``nrsa_dataset.cycle_compatibility``; a metric it does not list
    pools every cycle). On the pooled row: ``yes`` when at least one eligible
    cycle carries values.
pooled_cycles
    Pooled row only: the eligible cycles that carry values, which DATA-11 reads.
epa_short_name
    The EPA column the cycle's values were read from (``metric_crosswalk.csv``);
    on the pooled row the dictionary's short name.
epa_definition
    EPA's definition of that column in that cycle, from
    ``reference/cycle_comparison.csv`` via ``verify_inputs.epa_definitions``.
    Empty when EPA published no definition for the cycle (2018-19 benthic and
    fish metrics come from the legacy snapshot, not from an EPA file).
epa_definition_comparison
    EPA's own comparison flag for the short name across cycles (same file). A
    short name EPA lists in two indicator groups (CHLA_RESULT: the 2013-14
    periphyton file and the water-column files) carries both groups' flags.
units
    ``metric_dictionary.csv`` ``units``, else ``data/nrsa_metric_catalog.csv``
    ``units``. Empty for count and proportion metrics, which neither file gives
    a unit for.
origin
    ``value_origins.csv``: ``epa_published`` or ``legacy_r_app`` (the 2018-19
    benthic and fish values, backfilled from the bundled snapshot). Pooled row:
    the distinct origins of the pooled cycles.
origin_rationale
    For an origin other than ``epa_published``, the key of the note in
    ``nrsa_cycle_compatibility.yaml`` that records why (the metric's own key, or
    a family note naming the assemblage and the cycle). Empty when the origin is
    EPA's; a non-EPA origin with no note is a problem.
n_values, nonfinite_n
    Finite non-null values in the cycle over every site-visit row, and the
    non-null values that are not finite (``inf`` from a division by zero: three
    2013-14 width-to-depth ratios). ``notna()`` keeps an infinite value, so
    DATA-11 would select it and a curve would read it; every statistic here
    leaves it out and the row reports it as a problem. Pooled row: the sums
    over the pooled cycles for ``n_values``, the selected values for
    ``nonfinite_n``.
n_stations_frame, n_stations_strict
    Distinct frame (strict) stations whose index visit of the cycle carries a
    value. Pooled row: frame (strict) stations with a selected value.
frame_stations_sampled, strict_stations_sampled
    The denominators: frame (strict) stations with a visit record in the cycle.
    Pooled row: the whole frame (every frame station was sampled in some cycle).
missingness_in_frame
    ``1 - n_stations_frame / frame_stations_sampled``.
protocol_wadeable_n, protocol_boatable_n
    ``n_stations_frame`` split by the sampling protocol of the visit the value
    was read from (``site_visits.parquet``). A visit with no protocol counts in
    neither.
zero_share
    Share of the row's values that are exactly zero.
domain_min, domain_max, out_of_domain_n
    The physical domain ``config/nrsa_response_directions.yaml`` declares and
    the number of the row's values outside it. All three empty when no domain is
    declared.
detection_limit_handling
    What EPA's metadata for the cycle (``reference/metadata_detail_<cycle>.csv``)
    publishes beside the value column: its ``_MDL`` (method detection limit),
    ``_RL`` (reporting limit) and ``_NARS_FLAG`` (``ND`` = non-detect) columns.
    The metadata states no substitution rule for a non-detect in the value
    column, and the archive carries only the value columns, so non-detects
    cannot be counted here. ``not documented`` when the metadata has none of
    those columns for the metric (every field and biological metric). Pooled
    row: the union of the pooled cycles' columns.
min, q25, median, q75, max
    Of the row's values (linear-interpolated quantiles).
cycle_median_ratio
    Pooled row: the largest over the smallest non-zero absolute cycle median
    across the cycles with values, the unit check ``verify_inputs`` makes; a
    ratio of 10 or more is a problem.
n_selected
    Pooled row: archive stations with a selected value.
share_not_newest
    Pooled row: share of selected values not read from the station's newest
    sampled cycle (any cycle with a visit record).
multi_cycle_stations
    Pooled row: stations with a usable value in more than one cycle.
median_abs_rel_change
    Pooled row: median, over every (station, other cycle) pair of those
    stations, of the symmetric relative difference between the selected value
    and the other cycle's value: ``|a - b| / ((|a| + |b|) / 2)``, taken as 0
    when both are 0. Empty when no station has two cycles.
repeat_pairs_n, repeat_median_abs_diff, repeat_within_10pct_share
    Precision from repeat visits: pairs of visit 1 and visit 2 of the same EPA
    site in the same cycle with both values present; the median absolute
    difference in the metric's units (what rule CURVE-09's measurement floor is
    compared against); and the share of pairs whose symmetric relative
    difference is at most 0.10 (two zeros count as agreeing; a zero against a
    non-zero does not). The two ``R``-coded rows carry no values and are not
    paired. A cycle whose values were backfilled from the legacy snapshot
    (origin ``legacy_r_app``) has no pairs: the backfill copies the snapshot's
    one per-site value onto every visit, so its second visits agree exactly and
    say nothing about precision. Pooled row: the pairs of the pooled cycles
    taken together.
problems
    ``; ``-separated: a pooled row with no eligible cycle carrying values; an
    eligible cycle with no values; cycle medians differing tenfold or more; an
    origin other than EPA's with no recorded rationale; non-finite values.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(APP_ROOT))

from verify_inputs import CYCLES, UNIT_RATIO_LIMIT, epa_definitions, mapped_nrsa_metrics  # noqa: E402
from streamcurves import nrsa_dataset as nd  # noqa: E402
from streamcurves import reference_pool as rp  # noqa: E402

DATA = APP_ROOT / "data" / "nrsa"
CONFIG = APP_ROOT / "config"
CSV_PATH = CONFIG / "nrsa_harmonization_audit.csv"
MD_PATH = CONFIG / "nrsa_harmonization_audit.md"

POOLED = "pooled"
KEYS = ["station_key", "cycle", "site_id", "visit_no"]
WITHIN_SHARE = 0.10

COLUMNS = [
    "metric_key", "cycle", "eligible", "pooled_cycles", "epa_short_name", "epa_definition",
    "epa_definition_comparison", "units", "origin", "origin_rationale",
    "n_values", "nonfinite_n", "n_stations_frame", "n_stations_strict", "frame_stations_sampled",
    "strict_stations_sampled", "missingness_in_frame", "protocol_wadeable_n",
    "protocol_boatable_n", "zero_share", "domain_min", "domain_max", "out_of_domain_n",
    "detection_limit_handling", "min", "q25", "median", "q75", "max", "cycle_median_ratio",
    "n_selected", "share_not_newest", "multi_cycle_stations", "median_abs_rel_change",
    "repeat_pairs_n", "repeat_median_abs_diff", "repeat_within_10pct_share", "problems",
]

# the words a family note in nrsa_cycle_compatibility.yaml uses for a metric prefix
FAMILY_WORDS = {"bent_": "benthic", "fish_": "fish", "chem_": "chem", "phab_": "phab"}
CYCLE_WORDS = {"1314": "2013_14", "1819": "2018_19", "2324": "2023_24"}
METADATA_SUFFIXES = ("_MDL", "_RL", "_NARS_FLAG")


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def load_inputs() -> dict:
    metrics = mapped_nrsa_metrics()
    values = pd.read_parquet(DATA / "values.parquet", columns=KEYS + metrics)
    for column in metrics:
        values[column] = values[column].astype("float64")
    visits = pd.read_parquet(DATA / "site_visits.parquet",
                             columns=KEYS + ["protocol"])
    visits["protocol"] = visits["protocol"].astype(object).where(
        visits["protocol"].notna(), "").astype(str).str.strip().str.upper()
    crosswalk = pd.read_csv(DATA / "metric_crosswalk.csv", dtype=str)
    origins = pd.read_csv(DATA / "value_origins.csv", dtype={"cycle": str})
    dictionary = pd.read_csv(DATA / "metric_dictionary.csv", dtype=str).set_index("metric_key")
    catalog = pd.read_csv(APP_ROOT / "data" / "nrsa_metric_catalog.csv", dtype=str).set_index("name")
    directions = (yaml.safe_load((CONFIG / "nrsa_response_directions.yaml")
                                 .read_text(encoding="utf-8")) or {}).get("metrics") or {}
    compat_doc = yaml.safe_load((CONFIG / "nrsa_cycle_compatibility.yaml")
                                .read_text(encoding="utf-8")) or {}
    metadata = {}
    for cycle in CYCLES:
        md = pd.read_csv(DATA / "reference" / f"metadata_detail_{cycle}.csv", dtype=str)
        metadata[cycle] = set(md["epa_short_name"].astype(str).str.upper())

    max_order, protocols = nd.governed_frame("wadeable")
    frame, _ = rp.national_frame(max_stream_order=max_order, protocols=protocols)
    frame_keys = set(frame["station_key"].astype(str))
    strict_keys = set(frame.loc[frame["pass_strict"].astype(bool), "station_key"].astype(str))

    return {
        "metrics": metrics, "values": values, "visits": visits, "crosswalk": crosswalk,
        "origins": origins, "dictionary": dictionary, "catalog": catalog,
        "directions": directions, "compat": nd.cycle_compatibility(),
        "notes": compat_doc.get("notes") or {}, "metadata": metadata,
        "frame_keys": frame_keys, "strict_keys": strict_keys,
        "stations": sorted(set(pd.read_parquet(DATA / "stations.parquet",
                                               columns=["station_key"])["station_key"].astype(str))),
    }


# --------------------------------------------------------------------------- #
# small derivations
# --------------------------------------------------------------------------- #
def symmetric_rel_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype="float64")
    b = np.asarray(b, dtype="float64")
    den = (np.abs(a) + np.abs(b)) / 2.0
    out = np.zeros(len(a), dtype="float64")
    nz = den > 0
    out[nz] = np.abs(a - b)[nz] / den[nz]
    return out


def quantiles(arr: np.ndarray) -> dict:
    if len(arr) == 0:
        return {k: None for k in ("min", "q25", "median", "q75", "max")}
    q = np.percentile(arr, [0, 25, 50, 75, 100])
    return {"min": q[0], "q25": q[1], "median": q[2], "q75": q[3], "max": q[4]}


def origin_rationale(metric: str, cycle: str, notes: dict) -> str:
    if metric in notes:
        return metric
    family = next((w for p, w in FAMILY_WORDS.items() if metric.startswith(p)), "")
    for key in sorted(notes):
        if family and family in str(key) and CYCLE_WORDS.get(cycle, "") in str(key):
            return str(key)
    return ""


def detection_columns(short: str, present: set) -> list[str]:
    """The limit and flag columns EPA's metadata lists beside ``short``."""
    if not short:
        return []
    base = short.upper().removesuffix("_RESULT")
    return [base + s for s in METADATA_SUFFIXES if base + s in present]


def detection_note(columns) -> str:
    order = {s: i for i, s in enumerate(METADATA_SUFFIXES)}
    found = sorted(set(columns), key=lambda c: (next(order[s] for s in order if c.endswith(s)), c))
    if not found:
        return "not documented"
    return ("EPA publishes " + ", ".join(found)
            + "; no substitution rule for a non-detect in the value column is stated")


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if not np.isfinite(v):
            return ""
        s = f"{float(v):.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-0") else s
    return str(v)


def finite_split(col: np.ndarray) -> tuple[np.ndarray, int]:
    """``(finite values, count of non-null values that are not finite)``."""
    col = np.asarray(col, dtype="float64")
    nonnull = ~np.isnan(col)
    finite = np.isfinite(col)
    return col[finite], int((nonnull & ~finite).sum())


def value_stats(arr: np.ndarray, domain: tuple) -> dict:
    """Statistics of finite values; the caller counts the non-finite ones."""
    out = {"n_values": int(len(arr)), "zero_share": None, "out_of_domain_n": None}
    if len(arr):
        out["zero_share"] = float((arr == 0).mean())
    lo, hi = domain
    if lo is not None or hi is not None:
        bad = np.zeros(len(arr), dtype=bool)
        if lo is not None:
            bad |= arr < float(lo)
        if hi is not None:
            bad |= arr > float(hi)
        out["out_of_domain_n"] = int(bad.sum())
    out.update(quantiles(arr))
    return out


def repeat_stats(pairs: pd.DataFrame, metric: str, cycles) -> dict:
    """Visit 1 against visit 2 over ``cycles``; the caller passes only the
    cycles whose second visits were measured rather than backfilled."""
    sub = pairs[pairs["cycle"].isin(set(cycles))]
    a = sub[metric + "_1"].to_numpy(dtype="float64")
    b = sub[metric + "_2"].to_numpy(dtype="float64")
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if not len(a):
        return {"repeat_pairs_n": 0, "repeat_median_abs_diff": None,
                "repeat_within_10pct_share": None}
    return {"repeat_pairs_n": int(len(a)),
            "repeat_median_abs_diff": float(np.median(np.abs(a - b))),
            "repeat_within_10pct_share": float((symmetric_rel_diff(a, b) <= WITHIN_SHARE).mean())}


# --------------------------------------------------------------------------- #
# the build
# --------------------------------------------------------------------------- #
def build() -> tuple[list[dict], dict]:
    """``(rows, summary)``: the CSV rows as string dicts and the counts the
    summary states."""
    inp = load_inputs()
    metrics, values, visits = inp["metrics"], inp["values"], inp["visits"]
    frame_keys, strict_keys = inp["frame_keys"], inp["strict_keys"]

    # the index visit of every station and cycle, by the run's own order
    index_rows = (values.sort_values(nd.INDEX_VISIT_ORDER)
                  .drop_duplicates(["station_key", "cycle"])
                  .merge(visits, on=KEYS, how="left"))
    index_rows["protocol"] = index_rows["protocol"].fillna("")
    # repeat pairs: visit 1 against visit 2 of one EPA site in one cycle
    pairs = values[values["visit_no"] == "1"].merge(
        values[values["visit_no"] == "2"], on=["station_key", "cycle", "site_id"],
        suffixes=("_1", "_2"))
    sampled = {c: set(visits.loc[visits["cycle"] == c, "station_key"].astype(str)) for c in CYCLES}
    newest_cycle = visits.groupby("station_key")["cycle"].max()
    protocol_by_visit = visits.set_index(KEYS)["protocol"]

    # the raw selection (value policy v1, what every published version reads):
    # the audit exists to report the defects the v2 corrections read out, so it
    # never reads under them
    wide, ledger = nd.latest_values(inp["stations"], dataset=nd.MULTI_CYCLE_DATASET_ID,
                                    metrics=metrics, policy=nd.VALUE_POLICY_V1)
    ledger = ledger.copy()
    ledger["protocol"] = protocol_by_visit.reindex(
        pd.MultiIndex.from_arrays([ledger["station_key"], ledger["source_cycle"],
                                   ledger["site_name"], ledger["visit_no"]])).to_numpy()

    rows: list[dict] = []
    problem_rows = 0
    metrics_with_pairs = 0
    total_pairs = 0
    backfilled_pairs = 0
    not_newest = 0
    selected_total = 0

    for metric in metrics:
        entry = inp["directions"].get(metric) or {}
        domain = (entry.get("domain_min"), entry.get("domain_max"))
        allowed = inp["compat"].get(metric, set(CYCLES))
        dict_row = inp["dictionary"].loc[metric] if metric in inp["dictionary"].index else None
        units = ""
        if dict_row is not None and isinstance(dict_row.get("units"), str):
            units = dict_row["units"]
        elif metric in inp["catalog"].index and isinstance(inp["catalog"].loc[metric].get("units"), str):
            units = inp["catalog"].loc[metric]["units"]
        short_by_cycle = {}
        for r in inp["crosswalk"][inp["crosswalk"]["metric_key"] == metric].itertuples(index=False):
            short_by_cycle[str(r.cycle)] = str(r.source_column)
        dict_short = (str(dict_row["epa_short_name"]) if dict_row is not None
                      and isinstance(dict_row.get("epa_short_name"), str) else metric.split("_", 1)[1])
        epa = epa_definitions((short_by_cycle.get("2324") or short_by_cycle.get("1819")
                               or short_by_cycle.get("1314") or dict_short).upper())
        origin_by_cycle = {str(r.cycle): str(r.origin) for r in
                           inp["origins"][inp["origins"]["metric_key"] == metric].itertuples(index=False)}

        medians = {}
        cycles_with_values = []
        measured_cycles = []          # second visits measured, not backfilled copies
        for cycle in CYCLES:
            col = values.loc[values["cycle"] == cycle, metric].to_numpy(dtype="float64")
            arr, nonfinite = finite_split(col)
            stats = value_stats(arr, domain)
            if len(arr):
                cycles_with_values.append(cycle)
                medians[cycle] = float(np.median(arr))
            idx = index_rows[(index_rows["cycle"] == cycle) & index_rows[metric].notna()]
            in_frame = idx[idx["station_key"].isin(frame_keys)]
            n_frame = int(in_frame["station_key"].nunique())
            n_sampled = len(sampled[cycle] & frame_keys)
            eligible = cycle in allowed
            origin = origin_by_cycle.get(cycle, "")
            rationale = origin_rationale(metric, cycle, inp["notes"]) if origin and origin != "epa_published" else ""
            problems = []
            if eligible and not len(arr) and not nonfinite:
                problems.append("eligible cycle carries no values")
            if origin and origin != "epa_published" and not rationale:
                problems.append(f"origin {origin} has no recorded rationale")
            if nonfinite:
                problems.append(f"{nonfinite} non-finite value(s)")
            if origin == "epa_published":
                measured_cycles.append(cycle)
            rep = repeat_stats(pairs, metric, [cycle] if origin == "epa_published" else [])
            short = short_by_cycle.get(cycle, "")
            row = {
                "metric_key": metric, "cycle": cycle, "eligible": eligible, "pooled_cycles": "",
                "epa_short_name": short,
                "epa_definition": epa["definitions"].get(cycle, "") if short else "",
                "epa_definition_comparison": epa["epa_flag"], "units": units,
                "origin": origin, "origin_rationale": rationale,
                "nonfinite_n": nonfinite, "n_stations_frame": n_frame,
                "n_stations_strict": int(in_frame.loc[in_frame["station_key"].isin(strict_keys),
                                                      "station_key"].nunique()),
                "frame_stations_sampled": n_sampled,
                "strict_stations_sampled": len(sampled[cycle] & strict_keys),
                "missingness_in_frame": (1.0 - n_frame / n_sampled) if n_sampled else None,
                "protocol_wadeable_n": int((in_frame["protocol"] == "WADEABLE").sum()),
                "protocol_boatable_n": int((in_frame["protocol"] == "BOATABLE").sum()),
                "domain_min": domain[0], "domain_max": domain[1],
                "detection_limit_handling": detection_note(
                    detection_columns(short, inp["metadata"][cycle])),
                "cycle_median_ratio": None, "n_selected": None, "share_not_newest": None,
                "multi_cycle_stations": None, "median_abs_rel_change": None,
                **stats, **rep, "problems": "; ".join(problems),
            }
            rows.append(row)
            problem_rows += bool(problems)

        # the pooled row: DATA-11
        pooled_cycles = [c for c in CYCLES if c in allowed and c in cycles_with_values]
        led = ledger[ledger["metric"] == metric]
        selected = wide.set_index("site_id")[metric].dropna()
        sel_keys = set(led["station_key"].astype(str))
        sel_arr, sel_nonfinite = finite_split(selected.to_numpy(dtype="float64"))
        stats = value_stats(sel_arr, domain)
        stats["n_values"] = int(sum(int(np.isfinite(values.loc[values["cycle"] == c, metric]
                                                    .to_numpy(dtype="float64")).sum())
                                for c in pooled_cycles))
        n_not_newest = int((led["source_cycle"].astype(str).to_numpy()
                            != newest_cycle.reindex(led["station_key"]).astype(str).to_numpy()).sum())
        valid = nd.valid_cycle_values(inp["stations"], metric, dataset=nd.MULTI_CYCLE_DATASET_ID)
        valid = valid[np.isfinite(valid["value"].to_numpy(dtype="float64"))]
        rank = {c: i for i, c in enumerate(nd.CYCLES_NEWEST_FIRST)}
        valid = valid.assign(_rank=valid["cycle"].map(rank)).sort_values(
            ["station_key", "_rank"], kind="stable")
        counts = valid.groupby("station_key").size()
        multi = valid[valid["station_key"].isin(counts[counts > 1].index)]
        rel = []
        for _, group in multi.groupby("station_key", sort=True):
            vals = group["value"].to_numpy(dtype="float64")
            rel.extend(symmetric_rel_diff(np.repeat(vals[0], len(vals) - 1), vals[1:]).tolist())
        nonzero = [abs(m) for m in medians.values() if m == m and m != 0]
        ratio = (max(nonzero) / min(nonzero)) if len(nonzero) > 1 else 1.0
        in_frame_led = led[led["station_key"].isin(frame_keys)]
        problems = []
        if not pooled_cycles:
            problems.append("no eligible cycle carries values")
        if ratio >= UNIT_RATIO_LIMIT:
            problems.append(f"cycle medians differ {ratio:.1f}-fold (unit check)")
        if sel_nonfinite:
            problems.append(f"{sel_nonfinite} non-finite selected value(s)")
        origins_pooled = sorted({origin_by_cycle.get(c, "") for c in pooled_cycles} - {""})
        rationales = sorted({origin_rationale(metric, c, inp["notes"]) for c in pooled_cycles
                             if origin_by_cycle.get(c, "") not in ("", "epa_published")} - {""})
        limit_columns = [c for cycle in pooled_cycles
                         for c in detection_columns(short_by_cycle.get(cycle, ""),
                                                    inp["metadata"][cycle])]
        rep = repeat_stats(pairs, metric, [c for c in pooled_cycles if c in measured_cycles])
        rows.append({
            "metric_key": metric, "cycle": POOLED, "eligible": bool(pooled_cycles),
            "pooled_cycles": ",".join(pooled_cycles), "epa_short_name": dict_short,
            "epa_definition": "", "epa_definition_comparison": epa["epa_flag"], "units": units,
            "origin": "; ".join(origins_pooled), "origin_rationale": "; ".join(rationales),
            "nonfinite_n": sel_nonfinite,
            "n_stations_frame": int(in_frame_led["station_key"].nunique()),
            "n_stations_strict": int(in_frame_led.loc[in_frame_led["station_key"].isin(strict_keys),
                                                      "station_key"].nunique()),
            "frame_stations_sampled": len(frame_keys), "strict_stations_sampled": len(strict_keys),
            "missingness_in_frame": (1.0 - in_frame_led["station_key"].nunique() / len(frame_keys))
            if frame_keys else None,
            "protocol_wadeable_n": int((in_frame_led["protocol"] == "WADEABLE").sum()),
            "protocol_boatable_n": int((in_frame_led["protocol"] == "BOATABLE").sum()),
            "domain_min": domain[0], "domain_max": domain[1],
            "detection_limit_handling": detection_note(limit_columns),
            "cycle_median_ratio": ratio, "n_selected": len(sel_keys),
            "share_not_newest": (n_not_newest / len(sel_keys)) if sel_keys else None,
            "multi_cycle_stations": int(counts.gt(1).sum()),
            "median_abs_rel_change": float(np.median(rel)) if rel else None,
            **stats, **rep, "problems": "; ".join(problems),
        })
        problem_rows += bool(problems)
        metrics_with_pairs += rep["repeat_pairs_n"] > 0
        total_pairs += rep["repeat_pairs_n"]
        backfilled_pairs += repeat_stats(
            pairs, metric, [c for c in pooled_cycles if c not in measured_cycles])["repeat_pairs_n"]
        not_newest += n_not_newest
        selected_total += len(sel_keys)

    rows.sort(key=lambda r: (r["metric_key"], r["cycle"]))
    rows = [{k: fmt(r.get(k)) for k in COLUMNS} for r in rows]

    screen = pd.read_parquet(DATA / "station_screen.parquet",
                             columns=["station_key", "wadeable", "fcode_class"])
    order_only = set(screen.loc[screen["wadeable"].astype(bool)
                                & (screen["fcode_class"].astype(object) != "canal"),
                                "station_key"].astype(str))
    summary = {
        "metrics": len(metrics), "rows": len(rows),
        "cycle_rows": sum(r["cycle"] != POOLED for r in rows),
        "pooled_rows": sum(r["cycle"] == POOLED for r in rows),
        "frame": len(frame_keys), "strict": len(strict_keys),
        "order_only_wadeable": len(order_only),
        "frame_beyond_order_only": len(frame_keys - order_only),
        "visits": int(len(visits)),
        "repeat_visit_rows": int((values["visit_no"] == "2").sum()),
        "r_rows": int((values["visit_no"] == "R").sum()),
        "metrics_with_pairs": int(metrics_with_pairs), "pairs": int(total_pairs),
        "backfilled_pairs": int(backfilled_pairs),
        "selected": int(selected_total), "not_newest": int(not_newest),
        "problem_rows": int(problem_rows),
        "problems": [f"{r['metric_key']} {r['cycle']}: {r['problems']}" for r in rows if r["problems"]],
        "no_domain": [m for m in metrics if not any(
            (inp["directions"].get(m) or {}).get(k) is not None for k in ("domain_min", "domain_max"))],
        "no_units": sorted({r["metric_key"] for r in rows if not r["units"]}),
        "empty_definitions": sum(1 for r in rows if r["cycle"] != POOLED and not r["epa_definition"]),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# rendering, checking, writing
# --------------------------------------------------------------------------- #
def render_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    return buf.getvalue()


def render_md(s: dict) -> str:
    lines = [
        "# NRSA harmonization audit", "",
        "Written by `scripts/nrsa/build_harmonization_audit.py` from the multi-cycle archive "
        "under `data/nrsa/`. Do not edit by hand: `--check` and "
        "`tests/test_harmonization_audit.py` compare both files with a fresh build. The "
        "per-metric rows and every column's derivation are in "
        "`nrsa_harmonization_audit.csv` and the script's docstring; this file holds the counts.",
        "",
        f"- Mapped NRSA metrics (`config/metric_map.yaml`, source nrsa): {s['metrics']}. "
        f"Rows: {s['rows']} ({s['cycle_rows']} cycle rows, {s['pooled_rows']} pooled rows).",
        f"- Run frame (rule DATA-10, wadeable, non-canal): {s['frame']:,} stations, "
        f"{s['strict']:,} pass the strict screen. The screen table's order-only `wadeable` "
        f"flag marks {s['order_only_wadeable']:,} non-canal stations; the "
        f"{s['frame_beyond_order_only']} further stations in the frame have no stream order "
        "and were sampled with the wadeable protocol.",
        f"- Site-visits: {s['visits']:,}; second visits in the same cycle: "
        f"{s['repeat_visit_rows']}; `R`-coded repeat rows, not paired: {s['r_rows']}.",
        f"- Metrics with repeat-visit pairs: {s['metrics_with_pairs']} of {s['metrics']}; "
        f"pairs over the pooled cycles of every metric: {s['pairs']:,}. Second visits of a "
        f"backfilled cycle, copies of the first, not counted: {s['backfilled_pairs']:,}.",
        f"- DATA-11 selection: {s['selected']:,} station values over every metric, "
        f"{s['not_newest']:,} not from the station's newest sampled cycle.",
        f"- Rows with problems: {s['problem_rows']}.",
    ]
    lines += [f"  - {p}" for p in s["problems"]]
    lines += [
        "", "Columns the archive cannot fill:", "",
        "- `detection_limit_handling` documents what EPA publishes beside a value column; "
        "the archive carries only the value columns, so non-detects are not counted.",
        f"- `units` for {len(s['no_units'])} count and proportion metrics that neither the "
        "dictionary nor the catalog gives a unit for: " + ", ".join(s["no_units"]) + ".",
        f"- `domain_min`, `domain_max` and `out_of_domain_n` for {len(s['no_domain'])} metrics "
        "with no declared physical domain: " + ", ".join(s["no_domain"]) + ".",
        f"- `epa_definition` on {s['empty_definitions']} cycle rows where EPA published no "
        "definition for that cycle.",
        "",
    ]
    return "\n".join(lines)


def drift(rows: list[dict], summary: dict) -> list[str]:
    """Differences between the committed files and the build in hand."""
    out = []
    for path, text in ((CSV_PATH, render_csv(rows)), (MD_PATH, render_md(summary))):
        if not path.exists():
            out.append(f"{path.name} is missing")
        elif path.read_bytes() != text.encode("utf-8"):
            out.append(f"{path.name} differs from a fresh build")
    return out


def check() -> list[str]:
    """Differences between the committed files and a fresh build."""
    rows, summary = build()
    return drift(rows, summary)


def write(rows: list[dict], summary: dict) -> None:
    # LF on every platform: the files are compared byte for byte
    with open(CSV_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_csv(rows))
    with open(MD_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md(summary))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="compare the committed files with a fresh build; write nothing")
    a = ap.parse_args(argv)
    rows, summary = build()
    if a.check:
        stale = drift(rows, summary)
        for p in summary["problems"]:
            print(f"  - problem: {p}")
        for d in stale:
            print(f"  - {d}")
        print(f"harmonization audit: {summary['rows']} rows, {summary['problem_rows']} row(s) "
              f"with problems, " + ("ok" if not stale else f"{len(stale)} stale file(s)"))
        return 1 if stale else 0
    write(rows, summary)
    for p in summary["problems"]:
        print(f"  - problem: {p}")
    print(f"wrote {CSV_PATH.relative_to(APP_ROOT)} ({summary['rows']} rows) and "
          f"{MD_PATH.relative_to(APP_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
