"""Visit-specific field comparisons for the local alternatives study.

Nothing in this module changes a scoring method or the national products. Inputs
are cached EPA targets, the small NRSA archive/desktop tables and study scores.
All outputs go below ``study``. Confidence intervals are paired HUC8-bootstrap
intervals for candidate minus Alternative 1, unless explicitly named ``value_ci``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .. import nrsa, stats
from ..validation import TARGETS
from .io import write_json as _write_json, write_parquet as _write_parquet

NRSA_DIR = nrsa.NRSA_DIR
NRSA_RAW = nrsa.NRSA_RAW
REFERENCE = "alternative-1"
ALTERNATIVES = tuple(f"alternative-{i}" for i in range(1, 5))
MARGIN = 0.01
SEED = 20260915
_PAIRED_CACHE = OrderedDict()
LOW = "low_flow_baseflow_dynamics"
BED = "bed_composition_bedform_dynamics"
CATCHMENT = "catchment_hydrology"
BIO = "population_support"
FUNCTION_TARGETS = {k: (c, list(v)) for k, (c, v) in TARGETS.items()}
FUNCTION_TARGETS[LOW] = (None, [("a__phab_PCT_DR", -1), ("wetted_bankfull_ratio", 1),
                               ("a__phab_XWD_RAT", 1)])
LIMITATIONS = [
    "Alternative 1 and its frozen curves are preserved; these are local comparisons only.",
    "2023-24 is retrospective temporal evaluation, not an untouched prospective holdout.",
    "HUC8 bootstrap intervals describe this unweighted field cohort, not survey population estimates.",
    "Reference R/Im labels describe the 2013-14 designation, not contemporary condition.",
    "Raw monthly EROM estimates are modeled climatology, not flow measured on the field visit.",
    "Wetted width / thalweg depth is a secondary morphology target; it is not wetted / bankfull width.",
    "AUC noninferiority uses a 0.01 margin and paired delta intervals; inconclusive is not equivalent.",
    "Multiple targets and regions are exploratory comparisons without multiplicity adjustment.",
    "Shared agriculture association and contribution removal do not establish independent validity.",
]


def _text(value) -> str:
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def _number(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number.is_integer() and number > 0 else None


def _huc8(value) -> str:
    value = _text(value)
    if value.endswith(".0"):
        value = value[:-2]
    return value.zfill(8) if value.isdigit() and 1 <= len(value) <= 8 else ""


def huc8_fold(huc8: str) -> int | None:
    """Stable five-fold assignment, independent of row order or Python hashing."""
    code = _huc8(huc8)
    return int.from_bytes(hashlib.sha256(("easi-field-fold-v1:" + code).encode()).digest()[:8],
                          "big") % 5 if code else None


def group_folds(rows: list[dict]) -> list[dict]:
    """Return rows with connected station/COMID/HUC8 group and deterministic fold.

    Shared stations or COMIDs link differing HUC8 labels. The smallest HUC8 in a
    connected component names its bootstrap cluster; all linked watersheds stay
    together. No random split or model fitting occurs here.
    """
    parents = {}

    def find(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def tokens(row):
        if _text(row.get("status")).startswith("conflicting_") or row.get("status") == "unmatched_visit":
            return []
        result = []
        if station := _text(row.get("station_key")):
            result.append("station:" + station)
        if comid := _integer(row.get("comid")):
            result.append("comid:" + str(comid))
        if huc := _huc8(row.get("huc8")):
            result.append("huc8:" + huc)
        return result

    for keys in {tuple(tokens(row)) for row in rows}:
        for key in keys:
            left, right = find(keys[0]), find(key)
            if left != right:
                parents[max(left, right)] = min(left, right)
    watersheds = defaultdict(set)
    for key in parents:
        if key.startswith("huc8:"):
            watersheds[find(key)].add(key[5:])
    output = []
    for row in rows:
        keys = tokens(row)
        hucs = watersheds.get(find(keys[0]), set()) if keys else set()
        canonical = min(hucs) if hucs else ""
        output.append({**row, "fold_group": "huc8:" + canonical if canonical else None,
                       "bootstrap_huc8": canonical, "fold": huc8_fold(canonical)})
    return output


def _json(path: Path, value) -> None:
    _write_json(path, value)


def _csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(k for row in rows for k in row)) or ["status"]
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _stamp(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _input(path: Path, *, hash_file=True) -> dict:
    before = _stamp(path)
    sha = hashlib.sha256()
    if hash_file:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(block)
    if before != _stamp(path):
        raise RuntimeError(f"Input changed during hashing: {path}")
    return {"path": str(path.resolve()), "bytes": before[0], "mtime_ns": before[1],
            "sha256": sha.hexdigest() if hash_file else None}


def _unchanged(inputs: list[dict]) -> None:
    for item in inputs:
        if _stamp(Path(item["path"])) != (item["bytes"], item["mtime_ns"]):
            raise RuntimeError(f"Input changed during evaluation: {item['path']}")


def _read(path: Path, columns=None) -> list[dict]:
    available = pq.read_schema(path).names
    selected = [c for c in columns if c in available] if columns is not None else None
    return pq.read_table(path, columns=selected).to_pylist()


def _key(row) -> tuple[str, str, str]:
    return _text(row.get("cycle")), _text(row.get("site_id")), _text(row.get("visit_no")) or "1"


def _archive_targets() -> dict[str, int]:
    return {column: sign for _, continuous in FUNCTION_TARGETS.values() for column, sign in continuous
            if column.startswith("a__")}


def _role(target: str) -> str:
    if target == "a__phab_XWD_RAT":
        return "secondary_morphology"
    if target in ("a__phab_PCT_DR", "wetted_bankfull_ratio"):
        return "primary_separate"
    return "field_target"


def _resolve(entries: list[dict]) -> dict:
    """Collapse exact target duplicates, but never choose among conflicting values."""
    found = {json.dumps(e["value"], sort_keys=True) for e in entries if e["value"] is not None}
    status = "conflicting_target" if len(found) > 1 else "eligible" if found else "missing_target"
    value = json.loads(next(iter(found))) if len(found) == 1 else None
    sources = sorted({json.dumps(e["source"], sort_keys=True) for e in entries})
    return {"value": value, "status": status, "source_records": len(entries),
            "identical_duplicates": max(0, sum(e["value"] is not None for e in entries) - len(found)),
            "sources": [json.loads(s) for s in sources]}


def _deduplicate_observations(rows: list[dict]) -> list[dict]:
    """A second identity gate catches different site IDs at one station visit."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["station_key"] or row["site_id"], row["cycle"], row["visit_no"], row["target"])].append(row)
    result = []
    for _, members in sorted(grouped.items()):
        members.sort(key=lambda row: row["observation_id"])
        relevant = ("comid", "huc8", "date", "protocol", "target_value", "target_class", "status")
        distinct = {json.dumps([row[key] for key in relevant]) for row in members}
        if len(distinct) > 1:
            for row in members:
                result.append({**row, "target_value": None, "target_class": None,
                               "status": "conflicting_station_visit_target", "exclusion_reason": "conflicting_station_visit_target"})
        else:
            row = dict(members[0])
            row["site_ids_json"] = json.dumps(sorted({r["site_id"] for r in members}))
            row["source_json"] = json.dumps([source for r in members for source in json.loads(r["source_json"])], sort_keys=True)
            row["identical_duplicates"] += len(members) - 1
            result.append(row)
    return result


def build_observations(root: Path, study: Path) -> dict:
    """Build a long ledger from raw targets and exact archive visits, never ``frame.first``.

    Identical duplicate visits/targets collapse. Conflicting visit metadata or
    target values remain in the ledger with an exclusion reason and no usable
    target. Ratios are computed only from the same identified visit.
    """
    root, study = Path(root), Path(study)
    if root.resolve() == study.resolve():
        raise ValueError("The study must be a separate directory below or outside the data root")
    paths = [NRSA_DIR / "site_visits.parquet", NRSA_DIR / "values.parquet",
             root / "analysis/nrsa/nrsa_desktop.parquet"]
    paths.extend(NRSA_RAW / relative for group in nrsa.RAW_FILES.values() for relative in group
                 if (NRSA_RAW / relative).exists())
    reference_file = NRSA_RAW / nrsa.SITE_FILE_1314
    if reference_file.exists():
        paths.append(reference_file)
    if len(paths) == 3:
        raise FileNotFoundError("No cached raw NRSA target files are available")
    inputs = [_input(p) for p in paths]
    by_path = {p["path"]: p for p in inputs}

    def source(path, column, row_number, original):
        return {"path": str(path.resolve()), "sha256": by_path[str(path.resolve())]["sha256"],
                "column": column, "row": row_number, "raw_value": _text(original)}

    metadata = defaultdict(list)
    fields = ("station_key", "comid", "huc8", "date_col", "protocol", "ag_eco9", "us_l3code", "unique_id")
    for row in _read(paths[0]):
        normalized = {k: _text(row.get(k)) for k in fields}
        normalized.update(comid=_integer(row.get("comid")), huc8=_huc8(row.get("huc8")))
        metadata[_key(row)].append(normalized)
    visits = {}
    for key, rows in metadata.items():
        unique = {json.dumps(row, sort_keys=True) for row in rows}
        visits[key] = (json.loads(min(unique)), len(unique) != 1, len(rows))
    desktop_rows = _read(paths[2], ["station_key", "comid", "desktop_source", "huc8"])
    desktop = defaultdict(list)
    for row in desktop_rows:
        desktop[_text(row.get("station_key"))].append(row)
    # Only missing HUC8 identities need the national strata lookup. Read two
    # columns filtered to the field COMIDs, never the whole national table.
    missing_hucs = {meta[0].get("comid") for meta in visits.values() if not meta[0].get("huc8")}
    missing_hucs |= {_integer(r.get("comid")) for rows in desktop.values() for r in rows if not _huc8(r.get("huc8"))}
    missing_hucs.discard(None)
    strata_hucs = {}
    strata_file = root / "analysis/strata.parquet"
    if missing_hucs and strata_file.exists():
        receipt = _input(strata_file, hash_file=False)
        strata_rows = pq.read_table(strata_file, columns=["comid", "huc8"],
                                    filters=[("comid", "in", sorted(missing_hucs))]).to_pylist()
        seen = defaultdict(set)
        for row in strata_rows:
            if huc := _huc8(row.get("huc8")):
                seen[_integer(row.get("comid"))].add(huc)
        strata_hucs = {comid: next(iter(hucs)) for comid, hucs in seen.items() if len(hucs) == 1}
        receipt.update(columns=["comid", "huc8"], selected_comids=len(missing_hucs),
                       selected_rows=len(strata_rows), selected_sha256=hashlib.sha256(
                           json.dumps(sorted(strata_rows, key=lambda r: (r["comid"], str(r["huc8"]))), sort_keys=True).encode()).hexdigest())
        inputs.append(receipt)
    grouped = defaultdict(list)

    def add(key, target, value, provenance):
        grouped[(key, target)].append({"value": value, "source": provenance})

    for cycle, relatives in nrsa.RAW_FILES.items():
        for relative in relatives:
            path = NRSA_RAW / relative
            if not path.exists():
                continue
            for row_number, row in enumerate(nrsa._read_csv(path), 2):
                key = cycle, _text(row.get("SITE_ID")), _text(row.get("VISIT_NO")) or "1"
                if not key[1]:
                    continue
                for column, target in nrsa.CLASS_TARGETS.items():
                    if column in row:
                        add(key, "t__" + target, nrsa.recode_class(column, row[column]),
                            source(path, column, row_number, row[column]))
                for column, target in nrsa.CONTINUOUS_TARGETS.items():
                    if column in row:
                        add(key, "x__" + target, _number(row[column]), source(path, column, row_number, row[column]))
    needed = {name[3:] for name in _archive_targets()} | {
        "phab_XWIDTH", "phab_XBKF_W", "phab_XINC_H", "phab_XBKF_H"}
    archive_rows = _read(paths[1], ["station_key", "cycle", "site_id", "visit_no", *sorted(needed)])
    for row_number, row in enumerate(archive_rows):
        key = _key(row)
        if key in visits and _text(row.get("station_key")) != visits[key][0]["station_key"]:
            visits[key] = visits[key][0], True, visits[key][2]
        for column in sorted(needed):
            if column in row:
                value = _number(row[column])
                if column == "phab_PCT_DR" and value is not None and not 0 <= value <= 100:
                    value = None
                add(key, "a__" + column, value, source(paths[1], column, row_number, row[column]))
    if reference_file.exists():
        site_visits = defaultdict(list)
        for key in visits:
            if key[0] == "1314":
                site_visits[key[1]].append(key)
        for row_number, row in enumerate(nrsa._read_csv(reference_file), 2):
            value = _text(row.get("RT_NRSA"))
            for key in site_visits.get(_text(row.get("SITE_ID")), []):
                add(key, "reference_2013", value if value in ("R", "In", "Im") else None,
                    source(reference_file, "RT_NRSA", row_number, value))
    resolved = {key: _resolve(entries) for key, entries in grouped.items()}
    for key in sorted({key for key, _ in resolved}):
        for target, numerator, denominator in (
            ("wetted_bankfull_ratio", "a__phab_XWIDTH", "a__phab_XBKF_W"),
            ("inc_ratio", "a__phab_XINC_H", "a__phab_XBKF_H"),
        ):
            parts = [resolved.get((key, column)) for column in (numerator, denominator)]
            good = all(p and p["status"] == "eligible" for p in parts)
            valid = good and parts[0]["value"] >= 0 and parts[1]["value"] > 0
            ratio = _number(parts[0]["value"] / parts[1]["value"]) if valid else None
            valid = valid and ratio is not None
            resolved[(key, target)] = {
                "value": ratio if valid else None,
                "status": "eligible" if valid else "invalid_or_missing_same_visit_ratio",
                "source_records": sum(p["source_records"] for p in parts if p), "identical_duplicates": 0,
                "sources": [s for p in parts if p for s in p["sources"]],
            }
        # Require the complete chemistry class block; missing classes are not Fair.
        parts = [resolved.get((key, "t__" + c)) for c in ("ntl", "ptl", "anc", "sal")]
        labels = [p["value"] for p in parts if p and p["status"] == "eligible"]
        label = "Poor" if "Poor" in labels else ("Good" if set(labels) == {"Good"} else "Fair") if len(labels) == 4 else None
        resolved[(key, "t__chem_any")] = {
            "value": label, "status": "eligible" if label else "incomplete_chemistry_targets",
            "source_records": sum(p["source_records"] for p in parts if p), "identical_duplicates": 0,
            "sources": [s for p in parts if p for s in p["sources"]],
        }
    signs = _archive_targets() | {"wetted_bankfull_ratio": 1, "inc_ratio": -1}
    output = []
    for (key, target), item in sorted(resolved.items()):
        meta, conflict, visit_count = visits.get(key, ({}, False, 0))
        station = meta.get("station_key", "")
        routes = desktop.get(station, [])
        route_comids = {_integer(r.get("comid")) for r in routes if _integer(r.get("comid"))}
        comid = meta.get("comid") or (next(iter(route_comids)) if len(route_comids) == 1 else None)
        hucs = {_huc8(r.get("huc8")) for r in routes if _huc8(r.get("huc8"))}
        huc = meta.get("huc8") or (next(iter(hucs)) if len(hucs) == 1 else "") or strata_hucs.get(comid, "")
        reason = ("conflicting_visit" if conflict else "unmatched_visit" if not meta else
                  "missing_station" if not station else "conflicting_desktop_comid" if
                  len(route_comids) > 1 or route_comids and comid not in route_comids else
                  "missing_comid" if not comid else "missing_huc8" if not huc else "")
        status = reason or item["status"]
        value = item["value"] if status == "eligible" else None
        identity = [*key, station, target]
        output.append({
            "observation_id": hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24],
            "cycle": key[0], "site_id": key[1], "visit_no": key[2], "station_key": station,
            "unique_id": meta.get("unique_id"), "comid": comid, "huc8": huc,
            "fold": huc8_fold(huc), "date": meta.get("date_col"), "protocol": meta.get("protocol"),
            "nars9": meta.get("ag_eco9"), "l3": meta.get("us_l3code"),
            "route": ";".join(sorted({_text(r.get("desktop_source")) for r in routes})),
            "target": target, "kind": "class" if target.startswith("t__") or target == "reference_2013" else "continuous",
            "target_value": value if isinstance(value, (int, float)) else None,
            "target_class": value if isinstance(value, str) else None,
            "sign": signs.get(target, 1), "role": _role(target),
            "status": status, "exclusion_reason": "" if status == "eligible" else status,
            "visit_source_records": visit_count, "source_records": item["source_records"],
            "identical_duplicates": item["identical_duplicates"],
            "source_json": json.dumps(item["sources"], sort_keys=True),
        })
    if not output:
        raise ValueError("No identifiable cached NRSA observations")
    output = group_folds(_deduplicate_observations(output))
    _unchanged(inputs)
    directory = study / "cohorts"
    directory.mkdir(parents=True, exist_ok=True)
    _write_parquet(directory / "observations.parquet", pa.Table.from_pylist(output))
    summary = {"schema_version": 1, "rows": len(output), "status_counts": dict(sorted(Counter(r["status"] for r in output).items())),
               "targets": dict(sorted(Counter(r["target"] for r in output).items())),
               "stations": len({r["station_key"] for r in output if r["station_key"]}),
               "inputs": inputs, "fold_definition": "Connected station/COMID/HUC8 components; hash the smallest component HUC8, first 8 SHA256 bytes modulo 5",
               "duplicate_policy": "Collapse identical targets; exclude conflicting targets and visit metadata.",
               "chemistry_rule": "Any observed Poor is Poor; otherwise all four targets are required.",
               "limitations": LIMITATIONS}
    _json(directory / "cohort_summary.json", summary)
    return summary


def _cohorts(rows: list[dict]):
    """One visit-1 observation per station/target, with no cross-cycle fill."""
    eligible = [r for r in rows if r["status"] == "eligible" and r["visit_no"] == "1"]
    for name, cycles in (("latest_visit1", ("1314", "1819", "2324")),
                         ("development_1314_1819", ("1314", "1819")),
                         ("retrospective_2324", ("2324",))):
        candidates = [r for r in eligible if r["cycle"] in cycles]
        chosen = {}
        for row in sorted(candidates, key=lambda r: (r["cycle"], r["site_id"], r["observation_id"]), reverse=True):
            chosen.setdefault((row["station_key"], row["target"]), row)
        yield name, list(chosen.values())


def _metric(name, index, target, ratings=None):
    if name == "spearman":
        return stats.spearman(index, target)
    if name == "weighted_kappa":
        return stats.weighted_kappa(ratings, target)
    if name == "brier":
        return float(np.mean((index - np.asarray(target, dtype=float)) ** 2)) if len(index) else None
    return stats.auc(index, target)


def paired_statistics(reference, alternative, target, huc8, statistic="auc", *, boot=1000,
                      seed=SEED, reference_ratings=None, alternative_ratings=None) -> dict:
    """Matched finite observations; the same resampled watersheds feed both arms.

    For kappa the caller supplies recognized class labels. AUC requires boolean
    targets. Missing HUC8 identifiers never become artificial singleton clusters.
    """
    if isinstance(boot, bool) or not isinstance(boot, int) or boot < 0:
        raise ValueError("boot must be a nonnegative integer")
    ref, alt = np.asarray(reference, float), np.asarray(alternative, float)
    target = np.asarray(target)
    hucs = np.asarray([_huc8(h) for h in huc8])
    if not (len(ref) == len(alt) == len(target) == len(hucs)):
        raise ValueError("Paired inputs have different lengths")
    valid = np.isfinite(ref) & np.isfinite(alt) & (hucs != "")
    if statistic in ("spearman", "brier"):
        valid &= np.isfinite(np.asarray(target, float))
    elif statistic == "weighted_kappa":
        if reference_ratings is None or alternative_ratings is None:
            raise ValueError("Paired kappa requires both sets of ratings")
        reference_ratings, alternative_ratings = np.asarray(reference_ratings, object), np.asarray(alternative_ratings, object)
        valid &= np.isin(target, stats.ORDER) & np.isin(reference_ratings, stats.ORDER) & np.isin(alternative_ratings, stats.ORDER)
    else:
        if target.dtype.kind != "b":
            raise ValueError("AUC targets must be explicit boolean class masks")
    ref, alt, target, hucs = ref[valid], alt[valid], target[valid], hucs[valid]
    if reference_ratings is not None:
        reference_ratings = np.asarray(reference_ratings)[valid]
        alternative_ratings = np.asarray(alternative_ratings)[valid]
    fingerprint = hashlib.sha256(f"{statistic}:{boot}:{seed}".encode())
    for array in (ref, alt, target, hucs, reference_ratings, alternative_ratings):
        if array is None:
            fingerprint.update(b"none")
        else:
            fingerprint.update(str(array.dtype).encode())
            fingerprint.update(json.dumps(array.tolist(), separators=(",", ":")).encode())
    cache_key = fingerprint.hexdigest()
    if cache_key in _PAIRED_CACHE:
        _PAIRED_CACHE.move_to_end(cache_key)
        return dict(_PAIRED_CACHE[cache_key])
    labels, inverse = np.unique(hucs, return_inverse=True)
    members = [np.flatnonzero(inverse == i) for i in range(len(labels))]
    full = np.arange(len(ref))
    identical = np.array_equal(ref, alt) and (reference_ratings is None or
                                             np.array_equal(reference_ratings, alternative_ratings))

    def values(indices):
        base = _metric(statistic, ref[indices], target[indices],
                       reference_ratings[indices] if reference_ratings is not None else None)
        return base, base if identical else _metric(statistic, alt[indices], target[indices],
                                                    alternative_ratings[indices] if alternative_ratings is not None else None)

    base, candidate = values(full)
    differences = []
    rng = np.random.default_rng(seed)
    # Multiplicity weights reproduce the expanded watershed sample exactly,
    # while preparing tie/category ranks only once per paired comparison.
    from .bootstrap_metrics import prepare_auc, prepare_spearman, prepare_kappa
    if statistic == "spearman":
        prepared = (prepare_spearman(ref, target), prepare_spearman(alt, target))
    elif statistic == "weighted_kappa":
        prepared = (prepare_kappa(reference_ratings, target), prepare_kappa(alternative_ratings, target))
    elif statistic.startswith("auc"):
        prepared = (prepare_auc(ref, target), prepare_auc(alt, target))
    else:
        prepared = None
    if len(labels) >= 2 and base is not None and candidate is not None:
        for _ in range(boot):
            selected = rng.integers(0, len(members), size=len(members))
            if prepared is None:
                ids = np.concatenate([members[i] for i in selected])
                b, c = values(ids)
            else:
                multiplicities = np.bincount(selected, minlength=len(labels))[inverse]
                b = prepared[0].evaluate(multiplicities)
                c = b if identical else prepared[1].evaluate(multiplicities)
            if b is not None and c is not None and math.isfinite(b) and math.isfinite(c):
                differences.append(c - b)
    lo, hi = stats.interval(differences)
    auc_endpoint = statistic.startswith("auc")
    positive_n = int(target.sum()) if auc_endpoint else None
    negative_n = len(target) - positive_n if auc_endpoint else None
    supported = len(ref) >= 30 and (not auc_endpoint or min(positive_n, negative_n) >= 10)
    enough = supported and len(differences) >= max(30, math.ceil(boot * .8)) and boot >= 30
    ni = ("noninferior" if lo is not None and lo >= -MARGIN else
          "inferior" if hi is not None and hi < -MARGIN else "inconclusive") if enough and auc_endpoint else "inconclusive" if auc_endpoint else "not_applicable"
    result = {"statistic": statistic, "reference": base, "alternative": candidate,
            "delta": candidate - base if base is not None and candidate is not None else None,
            "ci_low": lo, "ci_high": hi, "n": len(ref), "n_huc8": len(labels),
            "boot_requested": boot, "boot_valid": len(differences),
            "n_positive": positive_n, "n_negative": negative_n, "support_floor_met": supported,
            "noninferiority_margin": MARGIN if auc_endpoint else None, "noninferiority_status": ni,
            "interpretation": "paired HUC8 delta interval" if enough else "insufficient bootstrap support; descriptive only"}
    _PAIRED_CACHE[cache_key] = dict(result)
    while len(_PAIRED_CACHE) > 2048:
        _PAIRED_CACHE.popitem(last=False)
    return result


def _groups(rows):
    yield "US", rows
    for region in sorted({_text(r.get("nars9")) for r in rows} - {""}):
        yield region, [r for r in rows if _text(r.get("nars9")) == region]
    # Field geography includes stations outside the scored stored footprint.
    woody = [r for r in rows if str(r.get("study_l2")) == "8.2" or r.get("study_woody_82")]
    if woody:
        yield "woody_L2_8.2", woody


def _unique_scores(path: Path, comids: set[int] | None = None) -> dict[int, dict]:
    """Read field COMIDs and numeric scoring columns, excluding bulky curve traces."""
    columns = [name for name in pq.read_schema(path).names if name in ("comid", "eci", "physical", "chemical", "biological")
               or name.startswith(("rating__", "score__", "index__", "route__", "observed__"))]
    filters = [("comid", "in", sorted(comids))] if comids is not None else None
    rows = pq.read_table(path, columns=columns, filters=filters).to_pylist()
    result = {}
    for row in rows:
        comid = _integer(row.get("comid"))
        if comid is None or comid in result:
            raise ValueError(f"Missing or duplicate score COMID in {path}")
        result[comid] = row
    return result


def _numeric(rows, column):
    return np.asarray([_number(r.get(column)) if _number(r.get(column)) is not None else np.nan for r in rows])


def _paired_rows(rows, base, candidate, column):
    return [r for r in rows if r["comid"] in base and r["comid"] in candidate and
            _number(base[r["comid"]].get(column)) is not None and
            _number(candidate[r["comid"]].get(column)) is not None]


def _observed(row, column):
    if column.startswith("index__"):
        return bool(row.get("observed__" + column.removeprefix("index__")))
    return any(bool(value) for key, value in row.items() if key.startswith("observed__"))


def _compare(rows, base, candidate, column, statistic, *, boot, positive=None, rating_column=None):
    selected = _paired_rows(rows, base, candidate, column)
    observed_n = sum(_observed(base[r["comid"]], column) or _observed(candidate[r["comid"]], column) for r in selected)
    selected = [r for r in selected if not _observed(base[r["comid"]], column) and
                not _observed(candidate[r["comid"]], column)]
    if statistic == "weighted_kappa":
        selected = [r for r in selected if r["target_class"] in stats.ORDER and
                    base[r["comid"]].get(rating_column) in stats.ORDER and candidate[r["comid"]].get(rating_column) in stats.ORDER]
    if positive is not None:
        target = np.asarray([r["target_class"] in positive for r in selected], bool)
    elif statistic == "weighted_kappa":
        target = np.asarray([r["target_class"] for r in selected], object)
    else:
        target = np.asarray([r["target_value"] * r["sign"] for r in selected], float)
    ref = _numeric([base[r["comid"]] for r in selected], column)
    alt = _numeric([candidate[r["comid"]] for r in selected], column)
    if statistic == "auc_poor":
        ref, alt = -ref, -alt
    kwargs = {}
    if rating_column:
        kwargs = {"reference_ratings": [base[r["comid"]].get(rating_column) for r in selected],
                  "alternative_ratings": [candidate[r["comid"]].get(rating_column) for r in selected]}
    result = paired_statistics(ref, alt, target, [r.get("bootstrap_huc8", r["huc8"]) for r in selected], statistic, boot=boot, **kwargs)
    result["eligible_target_n"] = len(rows)
    result["paired_score_excluded_n"] = len(rows) - len(selected)
    result["observed_override_excluded_n"] = observed_n
    result["observed_override_policy"] = "Exclude same-function observed overrides; exclude any observed override from aggregate outcomes."
    result["n_poor"] = sum(r["target_class"] == "Poor" for r in selected)
    result["n_fair"] = sum(r["target_class"] == "Fair" for r in selected)
    result["n_good"] = sum(r["target_class"] == "Good" for r in selected)
    route_column = "route__" + column.removeprefix("index__")
    for name, scores in (("reference_routes", base), ("alternative_routes", candidate)):
        routes = Counter(_text(scores[r["comid"]].get(route_column)) for r in selected) if column.startswith("index__") else {}
        result[name] = json.dumps(dict(sorted(routes.items())))
    return result


def _agreement(cohort, observations, scores, boot):
    rows = []
    base = scores[REFERENCE]
    specs = []
    for function, (cls, continuous) in FUNCTION_TARGETS.items():
        if cls:
            specs.extend((function, "t__" + cls, statistic, sign) for statistic, sign in
                         (("auc_poor", {"Poor"}), ("auc_good", {"Good"}), ("weighted_kappa", None)))
        specs.extend((function, target, "spearman", None) for target, _ in continuous)
    for function in ("eci", "physical", "chemical", "biological"):
        specs.extend(((function, "t__bent_mmi", "auc_good_vs_poor", {"Good"}),
                      (function, "reference_2013", "auc_reference_vs_impaired", {"R"})))
    by_target = defaultdict(list)
    for row in observations:
        by_target[row["target"]].append(row)
    for function, target, statistic, positive in specs:
        column = function if function in ("eci", "physical", "chemical", "biological") else "index__" + function
        target_rows = by_target[target]
        if statistic == "auc_good_vs_poor":
            target_rows = [r for r in target_rows if r["target_class"] in ("Good", "Poor")]
        elif target == "reference_2013":
            target_rows = [r for r in target_rows if r["target_class"] in ("R", "Im")]
        if not target_rows:
            continue
        for region, group in _groups(target_rows):
            for alternative in ALTERNATIVES[1:]:
                row = {"alternative_id": alternative, "reference_id": REFERENCE, "function": function,
                       "target": target, "role": _role(target), "cohort": cohort, "region": region}
                row.update(_compare(group, base, scores[alternative], column, statistic, boot=boot,
                                    positive=positive, rating_column="rating__" + function if statistic == "weighted_kappa" else None))
                rows.append(row)
    return rows


def _desktop_raw(path: Path) -> dict[int, dict]:
    columns = ["comid", "station_key", "erom__q_cv_monthly", "erom__q_min_ratio", "sc__bfiws",
               "prg_bmmi0809", "sc__prg_bmmi0809", "v__population_support__prGBmmi"]
    grouped = defaultdict(list)
    for row in _read(path, columns):
        if (comid := _integer(row.get("comid"))) is not None:
            grouped[comid].append(row)
    output = {}
    for comid, rows in grouped.items():
        output[comid] = {}
        for col in columns[2:]:
            values = {_number(row.get(col)) for row in rows} - {None}
            output[comid][col] = next(iter(values)) if len(values) == 1 else None
    return output


def _lowflow(cohort, observations, raw, boot):
    result = []
    for target in ("a__phab_PCT_DR", "wetted_bankfull_ratio", "a__phab_XWD_RAT"):
        target_rows = [r for r in observations if r["target"] == target]
        for region, group in _groups(target_rows):
            for candidate, column in (("minimum_monthly_ratio", "erom__q_min_ratio"), ("baseflow_index", "sc__bfiws")):
                selected = [r for r in group if (cv := _number(raw.get(r["comid"], {}).get("erom__q_cv_monthly"))) is not None
                            and cv >= 0 and (value := _number(raw.get(r["comid"], {}).get(column))) is not None
                            and value >= 0 and (column != "sc__bfiws" or value <= 100)]
                reference = [-raw[r["comid"]]["erom__q_cv_monthly"] for r in selected]
                alternative = [raw[r["comid"]][column] for r in selected]
                values = [r["target_value"] * r["sign"] for r in selected]
                row = {"subject": candidate, "measure": "raw_signed_spearman", "reference_subject": "negative_monthly_flow_cv",
                       "target": target, "role": _role(target), "cohort": cohort, "region": region,
                       "eligibility": "Finite incumbent CV and candidate; CV requires all twelve monthly flows.",
                       "candidate_bands": "none", "eligible_target_n": len(group)}
                row.update(paired_statistics(reference, alternative, values, [r.get("bootstrap_huc8", r["huc8"]) for r in selected],
                                             "spearman", boot=boot))
                result.append(row)
    return result


def contribution_removal(scores: dict, removed: str) -> dict:
    """Intentional leave-one-contribution-out rollup, not missing evidence.

    Use the current app's weights and arithmetic, removing the named contribution
    from its mapping. The original observed score remains in the input mapping.
    """
    from easi import config, scoring
    mapping = {key: value for key, value in config.cwa_mapping().items() if key != removed.replace("_", "-")}
    values = {}
    for key, value in scores.items():
        if key.startswith("score__") and (number := _number(value)) is not None:
            if not number.is_integer() or not 0 <= number <= config.FUNCTION_SCORE_MAX:
                raise ValueError("Contribution removal requires actual integer function scores")
            values[key[7:].replace("_", "-")] = int(number)
    roll = scoring.rollup(values, mapping=mapping, weights=config.WEIGHTS)
    return {"eci": roll.ecosystem_condition_index, **roll.sub_indices}


def _agriculture(cohort, observations, base, boot):
    results = []
    for removed in (CATCHMENT, BED):
        field_comids = {r["comid"] for r in observations} & base.keys()
        ablated = {comid: contribution_removal(base[comid], removed) for comid in sorted(field_comids)}
        for target, positive in (("t__bent_mmi", {"Good"}), ("reference_2013", {"R"}),
                                 ("t__bedsed", {"Good"}), ("a__phab_LRBS_use", None),
                                 ("a__phab_PCT_SAFN", None), ("a__phab_XEMBED", None)):
            valid = ("R", "Im") if target == "reference_2013" else ("Good", "Poor")
            target_rows = [r for r in observations if r["target"] == target and (positive is None or r["target_class"] in valid)
                           and _number(base.get(r["comid"], {}).get("score__" + removed)) is not None]
            for region, group in _groups(target_rows):
                row = {"subject": "remove_" + removed, "measure": "eci_field_association_removed_contribution", "target": target,
                       "cohort": cohort, "region": region, "removed_function": removed,
                       "interpretation_scope": "Diagnostic removal of an observed weighted contribution; not missing evidence or a proposed replacement score."}
                row.update(_compare(group, base, ablated, "eci", "auc" if positive else "spearman", boot=boot, positive=positive))
                results.append(row)
    # Shared evidence is a dependence diagnostic, not independent validation.
    field_comids = sorted({r["comid"] for r in observations} & base.keys())
    selected = [base[c] for c in field_comids if all(_number(base[c].get("index__" + f)) is not None and
                not _observed(base[c], "index__" + f) for f in (CATCHMENT, BED))]
    ratings = [(r.get("rating__" + CATCHMENT), r.get("rating__" + BED)) for r in selected]
    ratings = [(a, b) for a, b in ratings if a in stats.ORDER and b in stats.ORDER]
    results.append({"subject": "shared_agriculture", "measure": "paired_function_dependence", "cohort": cohort,
                    "region": "US", "n": len(selected), "n_unit": "unique COMIDs", "n_class": len(ratings),
                    "class_agreement": sum(a == b for a, b in ratings) / len(ratings) if ratings else None,
                    "correlation": stats.spearman(_numeric(selected, "index__" + CATCHMENT), _numeric(selected, "index__" + BED)),
                    "interpretation": "Shared evidence association, not independent field validation or evidence for changing weights."})
    # Within a fixed category of the other agriculture function, report what
    # remaining variation relates to each independent bed target. No new model,
    # regression coefficient or causal incremental benefit is claimed.
    for subject, control in ((BED, CATCHMENT), (CATCHMENT, BED)):
        for target in ("t__bedsed", "a__phab_LRBS_use", "a__phab_PCT_SAFN", "a__phab_XEMBED"):
            target_rows = [r for r in observations if r["target"] == target]
            for level in stats.ORDER:
                selected = [r for r in target_rows if base.get(r["comid"], {}).get("rating__" + control) == level]
                positive = {"Good"} if target == "t__bedsed" else None
                row = {"subject": subject, "measure": "conditional_field_association", "control_function": control,
                       "control_rating": level, "target": target, "cohort": cohort, "region": "US"}
                row.update(_compare(selected, base, base, "index__" + subject,
                                    "auc_good" if positive else "spearman", boot=0, positive=positive))
                row["interpretation"] = "Descriptive field association within the other function's fixed category; sparse or constant strata are uninformative, not proof of no added information."
                results.append(row)
    return results


def _route(value) -> str:
    token = _text(value).lower()
    if "integrity" in token or "iwi" in token or "ici" in token:
        return "integrity_fallback"
    if any(s in token for s in ("bmmi", "benthic", "biological-model", "published-model")):
        return "published_model"
    return "other_or_unavailable"


def _model(cohort, observations, base, raw, boot):
    results = []
    target_rows = [r for r in observations if r["target"] == "t__bent_mmi"]
    for region, group in _groups(target_rows):
        for route in ("published_model", "integrity_fallback", "other_or_unavailable"):
            selected = [r for r in group if _route(base.get(r["comid"], {}).get("route__" + BIO)) == route]
            row = {"subject": "population_support", "measure": "current_route_good_auc", "route": route,
                   "target": "t__bent_mmi", "cohort": cohort, "region": region}
            row.update(_compare(selected, base, base, "index__" + BIO, "auc_good", boot=0, positive={"Good"}))
            row["interpretation"] = "Route-specific descriptive agreement; routes have different eligibility."
            results.append(row)
            class_row = {"subject": "population_support", "measure": "current_route_class_kappa", "route": route,
                         "target": "t__bent_mmi", "cohort": cohort, "region": region}
            class_row.update(_compare(selected, base, base, "index__" + BIO, "weighted_kappa", boot=0,
                                      rating_column="rating__" + BIO))
            class_row["interpretation"] = "Route-specific descriptive class agreement; routes have different eligibility."
            results.append(class_row)
        selected, probabilities = [], []
        for observation in group:
            if _route(base.get(observation["comid"], {}).get("route__" + BIO)) != "published_model":
                continue
            if _observed(base[observation["comid"]], "index__" + BIO):
                continue
            record = raw.get(observation["comid"], {})
            probability = next((v for col in ("prg_bmmi0809", "sc__prg_bmmi0809", "v__population_support__prGBmmi")
                                if (v := _number(record.get(col))) is not None), None)
            if probability is not None and 0 <= probability <= 1:
                selected.append(observation)
                probabilities.append(probability)
        current = [_number(base[r["comid"]].get("index__" + BIO)) for r in selected]
        paired = paired_statistics(current, probabilities, np.asarray([r["target_class"] == "Good" for r in selected], bool),
                                   [r.get("bootstrap_huc8", r["huc8"]) for r in selected], "auc_good", boot=boot)
        results.append({"subject": "raw_prg_bmmi0809", "measure": "raw_probability_vs_banded_good_auc",
                        "route": "published_model", "target": "t__bent_mmi", "cohort": cohort, "region": region,
                        "reference_subject": "current_banded_population_support", **paired})
        # Show probability calibration descriptively, without calling the banded index a probability.
        for low, high in ((0., .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.)):
            pairs = [(p, r) for p, r in zip(probabilities, selected) if low <= p and (p < high or high == 1 and p <= high)]
            results.append({"subject": "raw_prg_bmmi0809", "measure": "calibration_bin", "route": "published_model",
                            "target": "t__bent_mmi", "cohort": cohort, "region": region, "bin_low": low, "bin_high": high,
                            "n": len(pairs), "reference": float(np.mean([p for p, _ in pairs])) if pairs else None,
                            "alternative": float(np.mean([r["target_class"] == "Good" for _, r in pairs])) if pairs else None,
                            "interpretation": "Predicted probability versus observed Good share; outcome harmonization and training independence are not established."})
    return results


def evaluate(root: Path, study: Path, boot=1000, *, scores_dir: Path | None = None,
             results_dir: Path | None = None, cohort_prefix: str = "") -> dict:
    """Evaluate matched field targets, with separate directories for held-out refits.

    Default output is ``study/results``. Spatial callers supply
    ``scores_dir=study/'spatial/scores'``, ``results_dir=study/'spatial/results'``
    and ``cohort_prefix='spatial_refit:'``. Overrides must stay within the study;
    this cannot overwrite the original analysis or a different study.
    """
    root, study = Path(root), Path(study)
    _PAIRED_CACHE.clear()
    if root.resolve() == study.resolve():
        raise ValueError("The study must be separate from the data root")
    observation_path = study / "cohorts/observations.parquet"
    desktop_path = root / "analysis/nrsa/nrsa_desktop.parquet"
    score_directory = Path(scores_dir) if scores_dir is not None else study / "scores"
    results = Path(results_dir) if results_dir is not None else study / "results"
    for directory in (score_directory, results):
        if not directory.resolve().is_relative_to(study.resolve()):
            raise ValueError("Evaluation directories must remain inside the study")
    if cohort_prefix and results.resolve() == (study / "results").resolve():
        raise ValueError("A separate cohort evaluation must not overwrite the frozen comparison results")
    score_paths = {alternative: score_directory / f"{alternative}.parquet" for alternative in ALTERNATIVES}
    inputs = [_input(path) for path in (observation_path, desktop_path, *score_paths.values())]
    observations = _read(observation_path)
    reaches_path = study / "cohorts/reaches.parquet"
    if reaches_path.exists():
        inputs.append(_input(reaches_path))
        metadata = {}
        for row in _read(reaches_path, ["comid", "l2", "state", "sample", "sample_weight", "woody_82"]):
            comid = _integer(row.get("comid"))
            if comid is None or comid in metadata:
                raise ValueError("Study reach metadata requires unique non-null COMIDs")
            metadata[comid] = row
        observations = [{**row, **{"study_" + key: value for key, value in metadata.get(row["comid"], {}).items()
                                   if key != "comid"}} for row in observations]
    station_comids = {comid for row in observations if (comid := _integer(row.get("comid"))) is not None}
    scores = {key: _unique_scores(path, station_comids) for key, path in score_paths.items()}
    raw = _desktop_raw(desktop_path)
    acquisition_path = study / "acquisition/summary.json"
    acquisition = {}
    if acquisition_path.exists():
        inputs.append(_input(acquisition_path))
        acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    agreement, lowflow, agriculture, model = [], [], [], []
    for cohort, rows in _cohorts(observations):
        cohort = cohort_prefix + cohort
        agreement.extend(_agreement(cohort, rows, scores, boot))
        lowflow.extend(_lowflow(cohort, rows, raw, boot))
        agriculture.extend(_agriculture(cohort, rows, scores[REFERENCE], boot))
        model.extend(_model(cohort, rows, scores[REFERENCE], raw, boot))
    _unchanged(inputs)
    tables = {"field_agreement.csv": agreement, "field_lowflow.csv": lowflow,
              "field_agriculture.csv": agriculture, "field_model.csv": model}
    for filename, rows in tables.items():
        _csv(results / filename, rows)
    summary = {"schema_version": 1, "reference_id": REFERENCE, "alternatives": list(ALTERNATIVES),
               "boot_requested": boot, "bootstrap_unit": "HUC8", "seed": SEED,
               "confidence_level": .95, "noninferiority_margin_auc": MARGIN,
               "cohort_prefix": cohort_prefix, "scores_directory": str(score_directory.resolve()),
               "training_independence": acquisition.get("training_independence", "unknown"),
               "acquisition_metadata": acquisition, "inputs": inputs,
               "outputs": {filename: {"path": str((results / filename).resolve()), "rows": len(rows)} for filename, rows in tables.items()},
               "limitations": LIMITATIONS + ["Deterministic folds are identifiers only; this evaluation does not claim to fit or cross-validate a new model."]}
    _json(results / "field_summary.json", summary)
    return summary
