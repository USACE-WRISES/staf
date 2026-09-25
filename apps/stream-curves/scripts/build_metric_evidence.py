"""Generate config/metric_evidence_table.csv, the metric and function evidence table.

Round 0 of the STAF national assessment campaign asks for one table that puts every
metric of every STAF function beside what the registries, the NRSA archive and the
published assessments already record about it, plus the curated evidence record of
config/metric_evidence.yaml. Nothing here is a scientific judgment: the generator
projects existing records, and the curated file is the only place a person writes.

    py -3.12 scripts/build_metric_evidence.py            # write the table
    py -3.12 scripts/build_metric_evidence.py --check    # exit 1 when it is stale
    py -3.12 scripts/build_metric_evidence.py --init     # add a skeleton entry to
                                                          # config/metric_evidence.yaml for
                                                          # every mapped metric key that
                                                          # lacks one, then write the table

The build refuses to run while the curated file lacks an entry for a mapped metric key
(or holds one for a key that is no longer mapped), naming the keys, unless --init is
given. tests/test_metric_evidence.py compares the committed table with a fresh build.

ROWS
  One row per (metric key, function) pair: every metric listed under a function in
  config/metric_map.yaml, whatever its source (nrsa, streamcat, streamstats, mmw,
  site_engine). A metric listed under several functions has several rows. Rows are
  sorted by metric key, then function; the file is UTF-8 with LF line endings and
  carries no timestamp, path or commit.

KEYS
  The metric key is the metric_map code (StreamCat base names such as pctimp2019, NRSA
  keys such as phab_XEMBED). Registries keyed by the watershed column (pctimp2019ws,
  bfiws) are looked up with the ws suffix added, and a key that ends in ws is also
  tried without it: the tolerance metric_map._code_candidates and
  field_methods._candidates apply. A fixed criterion also matches by its recorded
  streamcat base name.

COLUMNS AND THEIR SOURCES
  metric_key, function, discipline, role, default_selected, source, label
      config/metric_map.yaml, read through streamcurves.metric_map so an unset role
      takes the app's default (metric for nrsa, both for the landscape sources).
  direction, curve_form, expected_shape, signed_scale, domain_low, domain_high,
  low_tail, transformation, caveat
      config/nrsa_response_directions.yaml and config/landscape_response_directions.yaml.
      direction is higher_is_better, lower_is_better, optimum (curve_form optimum),
      excluded (excluded_from_scoring), predictor_only (the registry demotes the code to
      a predictor), review (a review: entry, none today), or empty when neither registry
      lists the metric. curve_form is the declared form, or monotone (the registry
      default) when a scoring direction exists. expected_shape is the declared value or
      the registry's own derivation (optimum, monotone_increasing, monotone_decreasing).
      signed_scale (default false) and transformation (default none) are written for
      every scored entry; low_tail only when declared.
  field_method, field_effort
      config/field_methods.yaml: method (whitespace normalized) and where. The file has
      no effort field, so field_effort carries the sampling extent it records (11
      transects, whole reach, desktop), the closest thing to effort in the sources.
  transfer_family, fauna_rule, search_order
      config/reference_transfer.yaml: metric_family, the family's fauna flag
      (same_faunal_province or none) and its search_order.
  fixed_criterion, fixed_bands
      config/fixed_criteria.yaml: the entry key and its Good, Fair and Poor band labels.
  benchmark_entry, benchmark_refusal
      config/published_benchmarks.yaml: the entry ids declared for the metric and the
      refusal ids naming it; a refusal limited to some ecoregions carries them in
      brackets (ohio-epa-biocriteria[l3:55]).
  model_registry_status
      config/model_registry.yaml: approved when an approved entry exists, else
      candidate, else empty.
  scale_level, scale_split
      config/metric_scale_registry.yaml: supported_level and split.
  basis_accepted_sources
      config/basis_validation.yaml: the bases (2r_l3, 2r_l2, 2r_nars9, 2r_l1,
      1B_mixed_local, 3a_envelope, 3b_adjusted, 3c_matched) whose acceptance.accepted
      is true, semicolon-joined; none when the metric was tested and nothing was
      accepted; empty when the file does not test the metric. The file's pooled family
      records are not summarized.
  epa_short_name_1314, epa_short_name_1819, epa_short_name_2324
      data/nrsa/metric_crosswalk.csv: DATASET.COLUMN per cycle, the EPA file and column
      the value is read from. Empty where EPA published no column for the cycle (the
      2018-19 benthic and fish metrics are computed from EPA's counts: see origin).
  origin_1314, origin_1819, origin_2324
      data/nrsa/value_origins.csv: how the cycle's values entered the archive.
  pooled_cycles
      the cycles value_origins records values for, less the cycles
      config/nrsa_cycle_compatibility.yaml keeps out of the pool (ACC-02).
  n_frame_values, n_strict_values
      data/nrsa/values.parquet joined to data/nrsa/station_screen.parquet over the
      national frame of reference_pool.national_frame with the governed DATA-10 frame
      (nrsa_dataset.governed_frame: NHDPlus V2 stream order 1 to 5, the WADEABLE
      protocol where the order is unknown, canals out; 3267 stations): the stations
      with a value under the DATA-11 policy (nrsa_dataset.latest_values, the newest
      compatible cycle's index visit), and those of them that pass the strict
      least-disturbed-v1 screen (pass_strict). For a landscape metric the station
      screen's own column (the key plus ws) is counted over the same frame where the
      screen carries it (the screen variables and the natural-setting covariates:
      impervious, crops, wetland, road density, runoff, precipitation, base flow,
      elevation); otherwise empty. The screen's wadeable flag alone (order 1 to 5, no
      protocol fallback) is EASI's narrower frame of 3190 stations and is not used.
  curve12_auc_min, curve12_auc_median
      the seven latest published bundles (LIBRARY_BUNDLES, assessment.deep.json): each
      metric entry's discrimination block (rule CURVE-12), aucRefVsPressure taken once
      per bundle and metric, then the min and the median across the bundles that carry
      the metric with an evaluable value.
  red_partners
      the same versions' session.streamcurves.json, fields.metric_redundancy (the RED-01
      pair table, every pair at or above the moderate floor): partner:red01(n) when the
      RED-01 strong flag stood in n bundles, partner:red02(n) otherwise (the moderate
      band, or a strong pair the low-n or two-valued guard kept from the flag). Partners
      are written as metric keys. Two of the seven versions (Northeastern Highlands v9,
      Southeastern Plains v2) record no pair at all.
  staf_library_ids
      config/staf_metric_library.json: the library ids whose app_metric_key is the key.
  construct_class, spatial_support, easi_role, disposition, status
      config/metric_evidence.yaml, the curated record (easi_role semicolon-joined).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from streamcurves.paths import CONFIG_DIR, DATA_DIR  # noqa: E402

CURATED_PATH = CONFIG_DIR / "metric_evidence.yaml"
TABLE_PATH = CONFIG_DIR / "metric_evidence_table.csv"
NRSA_DIR = DATA_DIR / "nrsa"
LIBRARY_DIR = APP_ROOT.parent / "library" / "assessments"

SCHEMA = "metric-evidence/1"
CYCLES = ("1314", "1819", "2324")

#: The seven latest published regional assessments the curve evidence is read from.
#: Pinned on purpose: a new version changes the table only when this list is updated,
#: and the test checks the pins against each assessment's manifest.
LIBRARY_BUNDLES = (
    ("central-basin-and-range", 1),
    ("central-great-plains", 1),
    ("northern-lakes-and-forests", 1),
    ("eastern-corn-belt-plains", 8),
    ("northeastern-highlands", 9),
    ("southeastern-plains", 2),
    ("interior-plateau", 6),
)

CONSTRUCT_CLASSES = ("direct_function_measure", "ecological_response", "pressure_proxy",
                     "natural_setting_covariate")
SPATIAL_SUPPORTS = ("site_reach", "riparian_corridor", "catchment", "watershed")
EASI_ROLES = ("screen_variable", "fixed_criterion_input", "proxy", "none")
DISPOSITIONS = ("retain", "revise", "add", "replace", "omit")
STATUSES = ("hypothesis", "reviewed")

COLUMNS = [
    "metric_key", "function", "discipline", "role", "default_selected", "source", "label",
    "direction", "curve_form", "expected_shape", "signed_scale", "domain_low", "domain_high",
    "low_tail", "transformation", "caveat",
    "field_method", "field_effort",
    "transfer_family", "fauna_rule", "search_order",
    "fixed_criterion", "fixed_bands",
    "benchmark_entry", "benchmark_refusal",
    "model_registry_status",
    "scale_level", "scale_split",
    "basis_accepted_sources",
    "epa_short_name_1314", "epa_short_name_1819", "epa_short_name_2324",
    "origin_1314", "origin_1819", "origin_2324",
    "pooled_cycles", "n_frame_values", "n_strict_values",
    "curve12_auc_min", "curve12_auc_median",
    "red_partners", "staf_library_ids",
    "construct_class", "spatial_support", "easi_role", "disposition", "status",
]

CURATED_HEADER = """\
# metric_evidence.yaml
# The curated metric and function evidence record of the STAF national assessment
# campaign (Round 0). Hand-maintained: scripts/build_metric_evidence.py READS this
# file and projects it, with the generated registries, the NRSA archive and the
# published assessments, into config/metric_evidence_table.csv. `--init` appends a
# skeleton entry for every mapped metric key that lacks one and never rewrites an
# entry, so curated text and comments are kept. Keep `metrics:` the last section.
#
# One entry per metric key of config/metric_map.yaml (the code of every metric listed
# under a function, whichever its source). Fields:
#   construct_class        direct_function_measure | ecological_response |
#                          pressure_proxy | natural_setting_covariate
#   spatial_support        site_reach | riparian_corridor | catchment | watershed
#   easi_role              list of screen_variable | fixed_criterion_input | proxy | none.
#                          The per-metric circularity record: a metric whose value
#                          enters EASI's fixed pressure screen least-disturbed-v1 is a
#                          screen_variable, one whose value EASI scores on its fixed
#                          criteria is a fixed_criterion_input, one EASI scores as a
#                          proxy is a proxy, and none otherwise.
#   evidence               list of {citation, note}
#   limitations            list of sentences
#   disposition            retain | revise | add | replace | omit
#   disposition_rationale  why, in a sentence or two
#   status                 hypothesis | reviewed
#   reviewed_by            n/a until reviewed, then the reviewer's initials
# A null field is one nobody has curated yet; the generator never fills it in.
"""

CURATED_NOTES = ("Curated evidence per metric key of metric_map.yaml, projected into "
                 "metric_evidence_table.csv by scripts/build_metric_evidence.py. Null "
                 "fields are not yet curated.")

SKELETON_FIELDS = (
    ("construct_class", "null"),
    ("spatial_support", "null"),
    ("easi_role", "[]"),
    ("evidence", "[]"),
    ("limitations", "[]"),
    ("disposition", "null"),
    ("disposition_rationale", "null"),
    ("status", "hypothesis"),
    ("reviewed_by", "n/a"),
)


class EvidenceError(RuntimeError):
    """A source the table needs is missing or malformed."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _yaml(path: Path) -> Any:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=loader) or {}


def _json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _csv_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _text(value: Any) -> str:
    """One line, whitespace normalized."""
    return " ".join(str(value if value is not None else "").split())


def key_candidates(code: str) -> list[str]:
    """The key as given, with the StreamCat watershed suffix added, and without it
    when it already carries one (metric_map._code_candidates, field_methods._candidates)."""
    code = str(code)
    out = [code, code + "ws"]
    if code.endswith("ws"):
        out.append(code[:-2])
    return list(dict.fromkeys(out))


def lookup(mapping: dict, code: str) -> tuple[Optional[str], Any]:
    """``(key, value)`` of the first candidate ``mapping`` holds, else ``(None, None)``."""
    for cand in key_candidates(code):
        if cand in mapping:
            return cand, mapping[cand]
    return None, None


def deep_slug(x: Any) -> str:
    """The bundle's metric id stem (mirrors streamcurves.deep_export.deep_slug)."""
    s = str(x).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"^-+|-+$", "", s)


def _num(x: Any) -> str:
    """A number for the table: integers plain, floats trimmed, never a timestamp."""
    if x is None:
        return ""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    f = float(x)
    if f != f:
        return ""
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return ("%.6f" % f).rstrip("0").rstrip(".")


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, (list, tuple)):
        return ";".join(_cell(x) for x in v)
    return _text(v)


# --------------------------------------------------------------------------- #
# the mapped metrics
# --------------------------------------------------------------------------- #
def mapped_entries() -> list[dict]:
    """One dict per (metric key, function) of metric_map.yaml, in file order."""
    from streamcurves import metric_map
    df = metric_map.metric_map_entries()
    rows = []
    for r in df.itertuples(index=False):
        rows.append({
            "metric_key": str(r.code), "function": str(r.function_name),
            "discipline": str(r.discipline), "role": str(r.role),
            "default_selected": bool(r.default_selected), "source": str(r.source),
            "label": str(r.label),
        })
    return rows


def mapped_keys(entries: Optional[list[dict]] = None) -> list[str]:
    entries = entries if entries is not None else mapped_entries()
    return sorted({e["metric_key"] for e in entries})


# --------------------------------------------------------------------------- #
# the curated record
# --------------------------------------------------------------------------- #
def load_curated(path: Optional[Path] = None) -> dict:
    p = Path(path or CURATED_PATH)
    if not p.exists():
        raise EvidenceError(f"{p.name} is missing; run scripts/build_metric_evidence.py --init")
    doc = _yaml(p)
    if not isinstance(doc, dict):
        raise EvidenceError(f"{p.name} is not a mapping")
    return doc


def validate_curated(doc: dict, keys: list[str]) -> list[str]:
    """Everything wrong with the curated file for these mapped keys, in words."""
    problems: list[str] = []
    if doc.get("schema") != SCHEMA:
        problems.append(f"schema is {doc.get('schema')!r}, expected {SCHEMA!r}")
    metrics = doc.get("metrics")
    if metrics is None:
        metrics = {}
    if not isinstance(metrics, dict):
        return problems + ["metrics is not a mapping"]
    have = {str(k) for k in metrics}
    want = set(keys)
    missing = sorted(want - have)
    stale = sorted(have - want)
    if missing:
        problems.append("no curated entry for mapped metric key(s): " + ", ".join(missing)
                        + " (run scripts/build_metric_evidence.py --init)")
    if stale:
        problems.append("curated entry for key(s) no longer in metric_map.yaml: "
                        + ", ".join(stale))

    def vocab(key: str, field: str, value: Any, allowed: tuple) -> None:
        if value is not None and value not in allowed:
            problems.append(f"{key}.{field} is {value!r}; allowed: {', '.join(allowed)}")

    for key in sorted(have & want):
        e = metrics.get(key)
        if not isinstance(e, dict):
            problems.append(f"{key}: entry is not a mapping")
            continue
        vocab(key, "construct_class", e.get("construct_class"), CONSTRUCT_CLASSES)
        vocab(key, "spatial_support", e.get("spatial_support"), SPATIAL_SUPPORTS)
        vocab(key, "disposition", e.get("disposition"), DISPOSITIONS)
        status = e.get("status")
        if status not in STATUSES:
            problems.append(f"{key}.status is {status!r}; allowed: {', '.join(STATUSES)}")
        roles = e.get("easi_role")
        if roles is None:
            roles = []
        if not isinstance(roles, list):
            problems.append(f"{key}.easi_role is not a list")
        else:
            for r in roles:
                vocab(key, "easi_role", r, EASI_ROLES)
        evidence = e.get("evidence")
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list):
            problems.append(f"{key}.evidence is not a list")
        else:
            for i, item in enumerate(evidence):
                if not isinstance(item, dict) or not set(item) <= {"citation", "note"}:
                    problems.append(f"{key}.evidence[{i}] is not a {{citation, note}} mapping")
        limitations = e.get("limitations")
        if limitations is None:
            limitations = []
        if not isinstance(limitations, list) or not all(isinstance(x, str) for x in limitations):
            problems.append(f"{key}.limitations is not a list of sentences")
        rationale = e.get("disposition_rationale")
        if rationale is not None and not isinstance(rationale, str):
            problems.append(f"{key}.disposition_rationale is not text")
        if not isinstance(e.get("reviewed_by"), str):
            problems.append(f"{key}.reviewed_by is not text (n/a until reviewed)")
    return problems


def skeleton_entry(key: str) -> str:
    lines = [f"  {key}:"] + [f"    {name}: {value}" for name, value in SKELETON_FIELDS]
    return "\n".join(lines) + "\n"


_TOP_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:")
_METRICS_LINE = re.compile(r"^metrics:\s*(\{\}\s*)?(#.*)?$")


def init_curated(keys: list[str], path: Optional[Path] = None) -> list[str]:
    """Append a skeleton entry for every key the file lacks; create the file when it
    does not exist. Existing entries and comments are never touched. Returns the
    keys added."""
    p = Path(path or CURATED_PATH)
    if not p.exists():
        body = (CURATED_HEADER + f"schema: {SCHEMA}\n" + "notes: >-\n  " + CURATED_NOTES
                + "\nmetrics:\n" + "".join(skeleton_entry(k) for k in keys))
        p.write_text(body, encoding="utf-8", newline="\n")
        return list(keys)
    doc = load_curated(p)
    metrics = doc.get("metrics") or {}
    if not isinstance(metrics, dict):
        raise EvidenceError(f"{p.name}: metrics is not a mapping")
    missing = [k for k in keys if k not in metrics]
    if not missing:
        return []
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    at = [i for i, line in enumerate(lines) if _METRICS_LINE.match(line)]
    if len(at) != 1:
        raise EvidenceError(f"{p.name}: cannot find one top-level `metrics:` line to append "
                            "under; add the entries by hand: " + ", ".join(missing))
    after = [line for line in lines[at[0] + 1:] if _TOP_KEY.match(line)]
    if after:
        raise EvidenceError(f"{p.name}: `metrics:` is not the last top-level section, so "
                            "entries cannot be appended; add them by hand: "
                            + ", ".join(missing))
    lines[at[0]] = "metrics:"
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    text += "".join(skeleton_entry(k) for k in missing)
    p.write_text(text, encoding="utf-8", newline="\n")
    check = load_curated(p).get("metrics") or {}
    still = [k for k in missing if k not in check]
    if still:
        raise EvidenceError(f"{p.name}: appended entries did not parse: " + ", ".join(still))
    return missing


# --------------------------------------------------------------------------- #
# the registries
# --------------------------------------------------------------------------- #
def direction_registry() -> dict:
    nrsa = (_yaml(CONFIG_DIR / "nrsa_response_directions.yaml").get("metrics") or {})
    land = (_yaml(CONFIG_DIR / "landscape_response_directions.yaml").get("metrics") or {})
    both = set(nrsa) & set(land)
    if both:
        raise EvidenceError("a metric is in both direction registries: " + ", ".join(sorted(both)))
    return {**nrsa, **land}


def direction_columns(code: str, registry: dict) -> dict:
    cols = {c: None for c in ("direction", "curve_form", "expected_shape", "signed_scale",
                              "domain_low", "domain_high", "low_tail", "transformation",
                              "caveat")}
    _key, e = lookup(registry, code)
    if not isinstance(e, dict):
        return cols
    hib = e.get("higher_is_better")
    form = e.get("curve_form")
    if e.get("excluded_from_scoring"):
        direction = "excluded"
    elif str(e.get("role") or "").strip().lower() == "predictor":
        direction = "predictor_only"
    elif e.get("review"):
        direction = "review"
    elif form == "optimum":
        direction = "optimum"
    elif hib is True:
        direction = "higher_is_better"
    elif hib is False:
        direction = "lower_is_better"
    else:
        direction = None
    scored = direction in ("higher_is_better", "lower_is_better", "optimum")
    cols["direction"] = direction
    cols["curve_form"] = form or ("monotone" if scored else None)
    if e.get("expected_shape"):
        cols["expected_shape"] = str(e["expected_shape"])
    elif scored:
        cols["expected_shape"] = ("optimum" if direction == "optimum" else
                                  "monotone_increasing" if hib is True else "monotone_decreasing")
    cols["signed_scale"] = bool(e.get("signed_scale", False)) if scored else None
    cols["domain_low"] = e.get("domain_min")
    cols["domain_high"] = e.get("domain_max")
    cols["low_tail"] = e.get("low_tail")
    cols["transformation"] = (str(e["transformation"]) if e.get("transformation")
                              else ("none" if scored else None))
    cols["caveat"] = e.get("caveat")
    return cols


def field_method_columns(code: str, methods: dict) -> dict:
    _key, e = lookup(methods, code)
    if not isinstance(e, dict):
        return {"field_method": None, "field_effort": None}
    return {"field_method": _text(e.get("method")), "field_effort": _text(e.get("where"))}


def transfer_columns(code: str, transfer: dict) -> dict:
    fam_key, fam = lookup(transfer.get("metric_family") or {}, code)
    if fam is None:
        return {"transfer_family": None, "fauna_rule": None, "search_order": None}
    prof = (transfer.get("families") or {}).get(str(fam)) or {}
    return {"transfer_family": str(fam),
            "fauna_rule": "same_faunal_province" if prof.get("fauna") else "none",
            "search_order": list(prof.get("search_order") or [])}


def fixed_columns(code: str, fixed: dict) -> dict:
    metrics = fixed.get("metrics") or {}
    key, e = lookup(metrics, code)
    if e is None:
        for k, entry in metrics.items():
            if str(entry.get("streamcat") or "") == code:
                key, e = k, entry
                break
    if e is None:
        return {"fixed_criterion": None, "fixed_bands": None}
    bands = "; ".join(f"{b.get('rating')} {b.get('label')}" for b in e.get("bands") or [])
    return {"fixed_criterion": key, "fixed_bands": bands}


def benchmark_columns(code: str, catalog: dict) -> dict:
    cands = set(key_candidates(code))
    entries = [str(e.get("id")) for e in catalog.get("entries") or []
               if str(e.get("metric")) in cands]
    refusals = []
    for r in catalog.get("refusals") or []:
        if not cands & {str(m) for m in r.get("metrics") or []}:
            continue
        where = r.get("applies_where") or {}
        scope = ",".join(f"{lvl}:{','.join(str(x) for x in codes)}"
                         for lvl, codes in sorted(where.items()))
        refusals.append(f"{r.get('id')}[{scope}]" if scope else str(r.get("id")))
    return {"benchmark_entry": entries, "benchmark_refusal": refusals}


def model_status(code: str, registry: dict) -> Optional[str]:
    cands = set(key_candidates(code))
    mine = [e for e in registry.get("entries") or [] if str(e.get("metric")) in cands]
    if not mine:
        return None
    return "approved" if any(e.get("status") == "approved" for e in mine) else \
        str(mine[0].get("status") or "")


def scale_columns(code: str, registry: dict) -> dict:
    _key, e = lookup(registry.get("metrics") or {}, code)
    if not isinstance(e, dict):
        return {"scale_level": None, "scale_split": None}
    return {"scale_level": e.get("supported_level"), "scale_split": e.get("split")}


def basis_sources(code: str, validation: dict) -> Optional[str]:
    _key, e = lookup(validation.get("metrics") or {}, code)
    if not isinstance(e, dict):
        return None
    accepted = sorted(b for b, rec in e.items()
                      if isinstance(rec, dict) and (rec.get("acceptance") or {}).get("accepted"))
    return ";".join(accepted) if accepted else "none"


def library_ids(code: str, library: dict) -> list[str]:
    cands = set(key_candidates(code))
    return sorted(str(m.get("library_id")) for m in library.get("metrics") or []
                  if m.get("app_metric_key") and str(m["app_metric_key"]) in cands)


# --------------------------------------------------------------------------- #
# the NRSA archive
# --------------------------------------------------------------------------- #
def nrsa_crosswalk() -> dict:
    """``{(metric_key, cycle): "DATASET.COLUMN"}``."""
    out = {}
    for r in _csv_rows(NRSA_DIR / "metric_crosswalk.csv"):
        out[(r["metric_key"], str(r["cycle"]))] = f"{r['dataset_id']}.{r['source_column']}"
    return out


def nrsa_origins() -> dict:
    """``{(metric_key, cycle): origin}``."""
    return {(r["metric_key"], str(r["cycle"])): r["origin"]
            for r in _csv_rows(NRSA_DIR / "value_origins.csv")}


def cycle_restrictions() -> dict:
    """``{metric_key: set of cycles it may pool}`` for the restricted metrics."""
    doc = _yaml(CONFIG_DIR / "nrsa_cycle_compatibility.yaml")
    return {str(mk): {str(c) for c in (e or {}).get("cycles") or []}
            for mk, e in (doc.get("metrics") or {}).items() if (e or {}).get("cycles")}


def pooled_cycles(code: str, origins: dict, restrictions: dict) -> list[str]:
    have = [c for c in CYCLES if (code, c) in origins]
    allowed = restrictions.get(code)
    return [c for c in have if allowed is None or c in allowed]


def frame_counts(nrsa_keys: list[str], landscape_keys: list[str]) -> dict:
    """``{metric_key: (n_frame_values, n_strict_values)}`` over the national frame.

    NRSA keys are counted from values.parquet under the DATA-11 value policy; a
    landscape key is counted from the station screen's own column (key plus ws)
    when the screen carries it.
    """
    from streamcurves import nrsa_dataset, reference_pool
    max_order, protocols = nrsa_dataset.governed_frame("wadeable")
    frame, _ledger = reference_pool.national_frame(max_stream_order=max_order,
                                                   protocols=protocols)
    strict = frame["pass_strict"].astype(bool).to_numpy()
    keys = frame["station_key"].astype(str).tolist()
    out: dict[str, tuple[int, int]] = {}
    if nrsa_keys:
        # under the policy every published version reads (v1): the table documents
        # the archive as published builds saw it; a rebuild under another policy
        # is a new edition of the table, made on purpose
        values, _ledger2 = nrsa_dataset.latest_values(keys, metrics=list(nrsa_keys),
                                                      policy=nrsa_dataset.VALUE_POLICY_V1)
        values = values.set_index("site_id").reindex(keys)
        for mk in nrsa_keys:
            if mk in values.columns:
                present = values[mk].notna().to_numpy()
                out[mk] = (int(present.sum()), int(present[strict].sum()))
    for mk in landscape_keys:
        col = next((c for c in key_candidates(mk) if c in frame.columns), None)
        if col is None:
            continue
        present = frame[col].notna().to_numpy()
        out[mk] = (int(present.sum()), int(present[strict].sum()))
    return out


# --------------------------------------------------------------------------- #
# the published assessments
# --------------------------------------------------------------------------- #
def bundle_path(assessment_id: str, version: int, name: str) -> Path:
    return LIBRARY_DIR / assessment_id / f"v{int(version)}" / name


def curve_evidence(keys: list[str]) -> tuple[dict, dict, list[str]]:
    """``(aucs, partners, unmapped)``: per key the CURVE-12 AUC of each bundle that
    carries it, per key the RED partner tallies ``{partner: {"red01": n, "red02": n}}``,
    and the bundle metric ids no mapped key explains."""
    by_slug = {}
    for mk in keys:
        for cand in key_candidates(mk):
            by_slug.setdefault("spring-" + deep_slug(cand), mk)
    by_run_key = {}
    for mk in keys:
        for cand in key_candidates(mk):
            by_run_key.setdefault(cand, mk)
    aucs: dict[str, list[float]] = {mk: [] for mk in keys}
    partners: dict[str, dict[str, dict[str, int]]] = {mk: {} for mk in keys}
    unmapped: set[str] = set()
    for assessment_id, version in LIBRARY_BUNDLES:
        bundle = _json(bundle_path(assessment_id, version, "assessment.deep.json"))
        seen: set[str] = set()
        for fn in bundle.get("metricsByFunction") or []:
            for entry in fn.get("metrics") or []:
                mid = str(entry.get("metricId"))
                mk = by_slug.get(mid)
                if mk is None:
                    unmapped.add(mid)
                    continue
                if mk in seen:
                    continue
                seen.add(mk)
                auc = (entry.get("discrimination") or {}).get("aucRefVsPressure")
                if auc is not None:
                    aucs[mk].append(float(auc))
        session = _json(bundle_path(assessment_id, version, "session.streamcurves.json"))
        table = ((session.get("fields") or {}).get("metric_redundancy") or {})
        data = table.get("data") or {}
        a_col, b_col = data.get("metric_a") or [], data.get("metric_b") or []
        flags = data.get("red01_spearman_flag") or []
        for a, b, flag in zip(a_col, b_col, flags):
            ka, kb = by_run_key.get(str(a), str(a)), by_run_key.get(str(b), str(b))
            band = "red01" if flag is True else "red02"
            for me, other in ((ka, kb), (kb, ka)):
                if me in partners:
                    tally = partners[me].setdefault(other, {"red01": 0, "red02": 0})
                    tally[band] += 1
    return aucs, partners, sorted(unmapped)


def _partner_text(tally: dict) -> str:
    parts = []
    for partner in sorted(tally):
        counts = tally[partner]
        for band in ("red01", "red02"):
            if counts.get(band):
                parts.append(f"{partner}:{band}({counts[band]})")
    return ";".join(parts)


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #
def build_rows(curated: Optional[dict] = None) -> list[dict]:
    """Every row of the table, sorted, cells as strings. Raises EvidenceError when
    the curated record does not cover the mapped keys."""
    entries = mapped_entries()
    keys = mapped_keys(entries)
    curated = curated if curated is not None else load_curated()
    problems = validate_curated(curated, keys)
    if problems:
        raise EvidenceError("metric_evidence.yaml: " + "; ".join(problems))
    curated_metrics = curated.get("metrics") or {}

    directions = direction_registry()
    methods = _yaml(CONFIG_DIR / "field_methods.yaml").get("metrics") or {}
    transfer = _yaml(CONFIG_DIR / "reference_transfer.yaml")
    fixed = _yaml(CONFIG_DIR / "fixed_criteria.yaml")
    benchmarks = _yaml(CONFIG_DIR / "published_benchmarks.yaml")
    models = _yaml(CONFIG_DIR / "model_registry.yaml")
    scale = _yaml(CONFIG_DIR / "metric_scale_registry.yaml")
    validation = _yaml(CONFIG_DIR / "basis_validation.yaml")
    library = _json(CONFIG_DIR / "staf_metric_library.json")
    crosswalk = nrsa_crosswalk()
    origins = nrsa_origins()
    restrictions = cycle_restrictions()

    source_of = {}
    for e in entries:
        source_of.setdefault(e["metric_key"], e["source"])
    nrsa_keys = [k for k in keys if source_of[k] == "nrsa"]
    landscape_keys = [k for k in keys if source_of[k] != "nrsa"]
    counts = frame_counts(nrsa_keys, landscape_keys)
    aucs, partners, _unmapped = curve_evidence(keys)

    per_key: dict[str, dict] = {}
    for mk in keys:
        cols: dict[str, Any] = {}
        cols.update(direction_columns(mk, directions))
        cols.update(field_method_columns(mk, methods))
        cols.update(transfer_columns(mk, transfer))
        cols.update(fixed_columns(mk, fixed))
        cols.update(benchmark_columns(mk, benchmarks))
        cols["model_registry_status"] = model_status(mk, models)
        cols.update(scale_columns(mk, scale))
        cols["basis_accepted_sources"] = basis_sources(mk, validation)
        is_nrsa = source_of[mk] == "nrsa"
        for c in CYCLES:
            cols[f"epa_short_name_{c}"] = crosswalk.get((mk, c)) if is_nrsa else None
            cols[f"origin_{c}"] = origins.get((mk, c)) if is_nrsa else None
        cols["pooled_cycles"] = pooled_cycles(mk, origins, restrictions) if is_nrsa else None
        n_frame, n_strict = counts.get(mk, (None, None))
        cols["n_frame_values"], cols["n_strict_values"] = n_frame, n_strict
        got = aucs.get(mk) or []
        cols["curve12_auc_min"] = round(min(got), 3) if got else None
        cols["curve12_auc_median"] = round(statistics.median(got), 4) if got else None
        cols["red_partners"] = _partner_text(partners.get(mk) or {})
        cols["staf_library_ids"] = library_ids(mk, library)
        cur = curated_metrics.get(mk) or {}
        cols["construct_class"] = cur.get("construct_class")
        cols["spatial_support"] = cur.get("spatial_support")
        cols["easi_role"] = list(cur.get("easi_role") or [])
        cols["disposition"] = cur.get("disposition")
        cols["status"] = cur.get("status")
        per_key[mk] = cols

    rows = []
    for e in entries:
        row = {c: "" for c in COLUMNS}
        row.update({k: _cell(v) for k, v in e.items()})
        row.update({k: _cell(v) for k, v in per_key[e["metric_key"]].items()})
        rows.append(row)
    rows.sort(key=lambda r: (r["metric_key"], r["function"]))
    seen = set()
    for r in rows:
        pair = (r["metric_key"], r["function"])
        if pair in seen:
            raise EvidenceError(f"metric_map.yaml lists {pair[0]} twice under {pair[1]}")
        seen.add(pair)
    return rows


def render_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n",
                       quoting=csv.QUOTE_MINIMAL, extrasaction="raise")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in COLUMNS})
    return buf.getvalue()


def parse_csv(text: str) -> tuple[list[str], dict]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = {}
    for r in reader:
        rows[(r.get("metric_key"), r.get("function"))] = r
    return list(reader.fieldnames or []), rows


def check(path: Optional[Path] = None) -> list[str]:
    """Differences between the committed table and a fresh build. Empty means none."""
    p = Path(path or TABLE_PATH)
    try:
        want = render_csv(build_rows())
    except EvidenceError as exc:
        return [str(exc)]
    if not p.exists():
        return [f"{p.name} is missing; run scripts/build_metric_evidence.py"]
    have = p.read_bytes().decode("utf-8")
    if have == want:
        return []
    problems: list[str] = []
    if "\r" in have:
        problems.append(f"{p.name} has CR bytes; the table is written LF")
    have_cols, have_rows = parse_csv(have.replace("\r\n", "\n"))
    want_cols, want_rows = parse_csv(want)
    if have_cols != want_cols:
        problems.append(f"columns differ: file {have_cols} vs build {want_cols}")
    for k in sorted(set(want_rows) - set(have_rows), key=lambda x: (str(x[0]), str(x[1]))):
        problems.append(f"{k[0]} / {k[1]}: row missing from the file")
    for k in sorted(set(have_rows) - set(want_rows), key=lambda x: (str(x[0]), str(x[1]))):
        problems.append(f"{k[0]} / {k[1]}: row in the file is no longer built")
    for k in sorted(set(have_rows) & set(want_rows), key=lambda x: (str(x[0]), str(x[1]))):
        for col in want_cols:
            a, b = have_rows[k].get(col), want_rows[k].get(col)
            if a != b:
                problems.append(f"{k[0]} / {k[1]}: {col} is {a!r} in the file and {b!r} "
                                "from the sources")
    if not problems:
        problems.append(f"{p.name} differs from a fresh build in row order or formatting only")
    return problems


def write_table(path: Optional[Path] = None) -> tuple[Path, list[dict]]:
    p = Path(path or TABLE_PATH)
    rows = build_rows()
    p.write_text(render_csv(rows), encoding="utf-8", newline="\n")
    return p, rows


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="compare the committed table with a fresh build; write nothing")
    ap.add_argument("--init", action="store_true",
                    help="add a skeleton entry to config/metric_evidence.yaml for every "
                         "mapped metric key that lacks one, then write the table")
    a = ap.parse_args(argv)
    if a.check and a.init:
        ap.error("--check and --init are exclusive")
    if a.check:
        problems = check()
        shown = problems[:40]
        for p in shown:
            print(f"  - {p}")
        if len(problems) > len(shown):
            print(f"  ... and {len(problems) - len(shown)} more")
        print("metric evidence table: " + ("ok" if not problems
                                           else f"{len(problems)} difference(s)"))
        return 1 if problems else 0
    try:
        if a.init:
            added = init_curated(mapped_keys())
            print(f"{CURATED_PATH.relative_to(APP_ROOT)}: "
                  + (f"added {len(added)} skeleton entr{'y' if len(added) == 1 else 'ies'}: "
                     + ", ".join(added) if added else "every mapped metric key has an entry"))
        path, rows = write_table()
    except EvidenceError as exc:
        print(f"error: {exc}")
        return 1
    keys = {r["metric_key"] for r in rows}
    functions = {r["function"] for r in rows}
    print(f"wrote {path.relative_to(APP_ROOT)}: {len(rows)} rows, {len(keys)} metric keys, "
          f"{len(functions)} functions")
    _aucs, _partners, unmapped = curve_evidence(sorted(keys))
    if unmapped:
        print("  bundle metrics outside metric_map.yaml (not tabulated): "
              + ", ".join(unmapped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
