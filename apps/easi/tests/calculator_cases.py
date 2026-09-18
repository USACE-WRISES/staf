"""The calculator parity case set: evidence records scored by the application.

Each case is a stored-evidence record (the ``tests/test_national_preloaded``
fixture with overrides) that ``easi.national.client.score_record`` scores
offline with the application's own adapters, optionally followed by the
override scores (``assessment.rescore``, what the Assessment page's rating
select does on any of the 20 functions) and then the observed-evidence overrides of
``assessment.apply_observed_evidence``. That order is the only one the engine
composes: ``rescore`` rebuilds every row it does not override from its generated
rating, so it would undo an observed row if it ran second. The
calculator's entries are read from the report's scoring traces and the record,
so "same inputs" means the values the engine actually rated, and the expected
results are the report's own ratings, scores, indices and rollup.

``build_cases()`` writes ``tests/data/calculator_cases.json`` (regenerate with
``EASI_WRITE_GOLDEN=1``); ``test_calculator_parity.py`` evaluates the workbook
on every case. The application is authoritative: a mismatch is a workbook
generator defect, never a reason to change the engine.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from easi import assessment, config
from easi import screening_methods as sm
from easi.national import client, records
from test_batch_parity import EROM_MONTHS
from test_national_preloaded import _record

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_calculator as bc  # noqa: E402

FIXTURE = HERE / "data" / "calculator_cases.json"
WORKBOOK = ROOT / "www" / "calculator" / bc.OUT_NAME
BLANK = ""

#: Calculator entry key -> StreamCat field(s) when the trace does not carry it.
SC_FIELD = {
    "chemCatchment": "chemcat", "chemWatershed": "chemws", "prGBmmi": "prg_bmmi0809",
    "hydCat": "hydcat", "sedCat": "sedcat", "connCat": "conncat", "tempCat": "tempcat", "habtCat": "habtcat",
    "hydWs": "hydws", "sedWs": "sedws", "connWs": "connws", "tempWs": "tempws", "habtWs": "habtws",
}
QUANTITY_KEYS = [row[1] for row in bc.QUANTITY_INPUTS]
METRIC_IDS = list(config.metrics_by_id())
METHOD_BY_METRIC = {m["metricId"]: m["methodKey"] for m in sm.catalog()["methods"]}

#: The workbook's route text for the five metrics whose route is not simply
#: the automatic method, keyed by the method that rated the row.
ROUTE_TEXT = {
    "regional-nutrient-condition": "WQP nutrients with regional thresholds",
    "streamcat-chem-integrity-nutrient": "CHEM integrity fallback",
    "attains-regulatory-category": "ATTAINS category",
    "streamcat-chem-integrity-regulatory": "CHEM integrity fallback",
    "streamcat-prg-bmmi": "published benthic model",
    "streamcat-integrity-products": "ICI/IWI integrity fallback",
    "channelized-fcode": "canal or ditch (FCODE)",
    "observed-channel-adjustment": "observed channel class",
    "channel-adjustment-susceptibility": "automatic proxy",
    "observed-bank-condition": "observed bank condition",
    "bhr-bank-instability-susceptibility": "automatic proxy",
}
ROUTED_METRICS = {mid for mid, mk in METHOD_BY_METRIC.items()
                  if mk in ("regional-nutrient-condition", "attains-regulatory-category", "streamcat-prg-bmmi",
                            "channel-adjustment-susceptibility", "bhr-bank-instability-susceptibility")}
COMPLETENESS = {"complete": "complete", "partial": "partial", "not_assessed": "not rated",
                "context_only": "not rated", None: "not rated", "override": bc.SCORE_OVERRIDE_ROUTE}
#: The 20 functions as mNN keys. Every one carries an Override Score in the workbook,
#: because the Assessment page offers its rating select on every function card (the
#: registry's ``overrideable`` flag is not enforced there) and ``rescore`` takes any metric.
FUNCTIONS = {f"m{n:02d}": mid for n, mid in enumerate(METRIC_IDS, 1)}


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------
def make_record(overrides: dict) -> dict:
    """The fixture record with overrides. ``streamcat`` and ``geomorph`` merge
    (a None value removes the field); every other key replaces."""
    rec = _record()
    for key, value in (overrides or {}).items():
        if key in ("streamcat", "geomorph") and isinstance(value, dict) and rec.get(key) is not None:
            merged = dict(rec[key])
            for k, v in value.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            rec[key] = merged
        else:
            rec[key] = copy.deepcopy(value)
    return rec


def months_with_cv(cv: float, mean: float = 100.0) -> list[float]:
    """Twelve flows whose population CV is ``cv``: six at mean + d, six at mean - d."""
    d = cv * mean
    return [mean + d] * 6 + [mean - d] * 6


def erom(months: list[float]) -> dict:
    return {"qe_ma": sum(months) / 12, **{f"qe_{i:02d}": v for i, v in enumerate(months, 1)}}


def wqp(parameter: str, value: float) -> dict:
    return {"parameter": parameter, "value": value, "observation_count": 4, "station_count": 2,
            "nearest_distance_mi": 0.6, "excluded_count": 0, "date_start": "2020-01-01", "date_end": "2024-01-01"}


def attains(category, *, nearby: bool = False) -> dict:
    return {"assessment_unit": "AU-TEST", "assessment_name": "Test unit", "ircategory": category,
            "overallstatus": "Not Supporting" if category in ("4A", "4B", "4C", "5") else "Fully Supporting",
            "distance_m": 850.0 if nearby else 0.0, "match_type": "nearby" if nearby else "intersect"}


def score_case(case: dict) -> dict:
    record = make_record(case.get("record") or {})
    report = client.score_record(record, cross_section=False)
    if case.get("ratings"):
        report = assessment.rescore(report, case["ratings"])
    if case.get("observed"):
        report = assessment.apply_observed_evidence(report, case["observed"])
    return report


# ---------------------------------------------------------------------------
# entries and expectations
# ---------------------------------------------------------------------------
def entries_from(report: dict, case: dict) -> dict:
    """Calculator entries (defined-name -> value) for a scored record."""
    record = make_record(case.get("record") or {})
    traced: dict[str, object] = {}
    for row in report["metricRows"]:
        for inp in (row.get("scoring") or {}).get("inputs") or []:
            key = bc.ALIASES.get(inp.get("key"), inp.get("key"))
            value = inp.get("value")
            if value is None or key is None:
                continue
            if key in traced and traced[key] != value:
                raise AssertionError(f"{key}: the engine rated two values, {traced[key]} and {value}")
            traced[key] = value
    sc = records.streamcat_row(record)
    entries: dict[str, object] = {}
    for key in QUANTITY_KEYS:
        if key in ("tn", "tp"):
            summary = record.get(f"wqp_{key}")
            value = (summary or {}).get("value")
        elif key == "category":
            exact, near = record.get("attains_exact") or {}, record.get("attains_nearby") or {}
            unit = exact if exact.get("assessment_unit") else (near if near.get("assessment_unit") else {})
            value = unit.get("ircategory")
        elif key in traced:
            value = traced[key]
        elif key in SC_FIELD:
            value = sc.get(SC_FIELD[key])
        elif key == "slope":
            value = record.get("slope")
        elif key == "sinuosity":
            value = record.get("sinuosity")
        else:
            value = None
        entries[f"in_{key}"] = BLANK if value is None else value
    strata = report.get("strata") or {}
    entries["ctx_region"] = strata.get("nars9") or BLANK
    entries["ctx_fcode"] = record.get("fcode") if record.get("fcode") is not None else BLANK
    entries["ctx_slope_override"] = BLANK
    observed = case.get("observed") or {}
    chan = observed.get("channel-evolution-channel-evolution-stage-and-trends") or {}
    bank = observed.get("channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition") or {}
    entries["ov_stageClass"] = chan.get("stageClass") or BLANK
    entries["ov_indicators"] = chan.get("indicators") or BLANK
    entries["ov_erodingBankPct"] = bank.get("erodingBankPct") if bank.get("erodingBankPct") is not None else BLANK
    entries["ov_armoredBankPct"] = bank.get("armoredBankPct") if bank.get("armoredBankPct") is not None else BLANK
    # the override scores as typed (not as they came out: an observed entry can outrank one);
    # only the ones given, so a case without any keeps the entries it always had
    ratings = case.get("ratings") or {}
    for mkey, mid in FUNCTIONS.items():
        if mid in ratings:
            entries[bc.score_override_name(mkey)] = ratings[mid]
    assert set(ratings) <= set(FUNCTIONS.values()), "an override on something that is not one of the 20 functions"
    return entries


def expected_from(report: dict) -> dict:
    rows = {row["metricId"]: row for row in report["metricRows"]}
    metrics = {}
    for n, mid in enumerate(METRIC_IDS, 1):
        row = rows[mid]
        sc_ = row.get("scoring") or {}
        # an override score that took effect: the row keeps the trace of the computed
        # rating it replaced, so its status and route come from the row, not the trace
        assessed = row.get("status") == "override"
        item = {
            "rating": row.get("rating"),
            "score": row.get("functionScore"),
            "index": row.get("index"),
            "completeness": "override" if assessed else sc_.get("completeness"),
            "methodKey": sc_.get("methodKey"),
        }
        if assessed:
            item["computed"] = (row.get("effectiveOverride") or {}).get("generatedRating")
        if mid in ROUTED_METRICS:
            item["route"] = (bc.SCORE_OVERRIDE_ROUTE if assessed else
                             ROUTE_TEXT.get(sc_.get("methodKey"), "automatic method")
                             if row.get("rating") in ("Good", "Fair", "Poor") else "not rated")
        metrics[f"m{n:02d}"] = item
    cov = report.get("coverage") or {}
    return {
        "metrics": metrics,
        "subIndicesRaw": report.get("subIndicesRaw"),
        "eciRaw": report.get("ecosystemConditionIndexRaw"),
        "subIndices": report.get("subIndices"),
        "eci": report.get("ecosystemConditionIndex"),
        "rated": (cov.get("overall") or {}).get("rated"),
        "provisional": bool(cov.get("provisional")),
    }


# ---------------------------------------------------------------------------
# the case matrix
# ---------------------------------------------------------------------------
def _band_edges():
    """Every interior band boundary of every banded input, with how to set it."""
    catalog = sm.catalog()
    methods = {m["methodKey"]: m for m in catalog["methods"]}
    edges = []

    def add(label, setter_key, bands):
        for b in bands:
            for edge in (b.get("min"), b.get("max")):
                if edge is not None:
                    edges.append((label, setter_key, float(edge)))

    for m in catalog["methods"]:
        for i in m.get("inputs", []):
            if i.get("bands"):
                add(f"{m['methodKey']}:{i['key']}", i["key"], i["bands"])
        if m.get("bands"):
            key = next(i["key"] for i in m["inputs"] if not i.get("contextOnly"))
            add(f"{m['methodKey']}:{key}", "combined:" + m["methodKey"], m["bands"])
    return edges


SETTERS = {
    "impervious": lambda v: {"streamcat": {"pctimp2019ws": v}},
    "agriculture": lambda v: {"streamcat": {"pctcrop2019ws": v, "pcthay2019ws": 0.0}},
    "roadDensity": lambda v: {"streamcat": {"rddensws": v}},
    "kFactor": lambda v: {"streamcat": {"kffactws": v}},
    "bhr": lambda v: {"geomorph": {"bank_height_ratio": v}},
    "er": lambda v: {"geomorph": {"entrenchment_ratio": v}},
    "slope": lambda v: {"slope": v},
    "sinuosity": lambda v: {"sinuosity": v},
    "prGBmmi": lambda v: {"streamcat": {"prg_bmmi0809": v}},
    "woodyRiparian": lambda v: {"streamcat": {"pctconif2019wsrp100": v, "pctdecid2019wsrp100": 0.0,
                                              "pctmxfst2019wsrp100": 0.0, "pctshrb2019wsrp100": 0.0,
                                              "pctwdwet2019wsrp100": 0.0}},
    "combined:watershed-wetland-extent": lambda v: {"streamcat": {"pctwdwet2019ws": v, "pcthbwet2019ws": 0.0}},
    "combined:road-density-inflow-pressure": lambda v: {"streamcat": {"rddensws": v}},
    "combined:degree-of-regulation": lambda v: {"streamcat": {"damnrmstorws": v * 10.0, "runoffws": 1.0}},
    "combined:bank-height-ratio": lambda v: {"geomorph": {"bank_height_ratio": v}},
    "combined:bhr-bank-instability-susceptibility": lambda v: {"geomorph": {"bank_height_ratio": v}},
    "combined:watershed-agriculture-share": lambda v: {"streamcat": {"pctcrop2019ws": v, "pcthay2019ws": 0.0}},
    "combined:streamcat-prg-bmmi": lambda v: {"streamcat": {"prg_bmmi0809": v}},
    "combined:nas-established-taxa-count": lambda v: {"nas_taxa": [f"Taxon {i}" for i in range(int(v))]},
    "combined:nearby-dam-proximity": lambda v: {"nid_dams": [{"name": f"Dam {i}", "distance_m": 100.0 * (i + 1)}
                                                            for i in range(int(v))]},
}
INTEGER_SETTERS = {"combined:nas-established-taxa-count", "combined:nearby-dam-proximity"}


def build_cases() -> list[dict]:
    cases: list[dict] = []

    ids: set[str] = set()

    def case(cid, group, record=None, observed=None, ratings=None):
        assert cid not in ids, f"duplicate case id {cid}"
        ids.add(cid)
        item = {"id": cid, "group": group, "record": record or {}, "observed": observed}
        if ratings:     # only where given, so every older case keeps its stored form
            item["ratings"] = ratings
        cases.append(item)

    case("base", "base")
    # -- band edges on both sides -------------------------------------------
    seen = set()
    for label, key, edge in _band_edges():
        setter = SETTERS.get(key)
        if setter is None:
            continue
        values = (edge,) if key in INTEGER_SETTERS else (edge - 1e-6, edge, edge + 1e-6)
        for v in values:
            if v < 0:
                continue
            cid = f"edge:{label}:{v:.9g}"
            if cid in seen:
                continue
            seen.add(cid)
            case(cid, "band-edges", setter(v))
    # observed bank bands at their edges (needs both entries)
    for e in (0.0, 5.0, 60.0):
        for a in (0.0, 50.0):
            for de in (-1e-6, 0.0, 1e-6):
                ev, av = e + de, a + de
                if ev < 0 or av < 0:
                    continue
                case(f"edge:observed-bank:{ev:.9g}:{av:.9g}", "band-edges", None, {
                    "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition":
                        {"erodingBankPct": ev, "armoredBankPct": av}})
    # -- curves: every stratum of every set at its crossings and knots --------
    curves = sm.curve_sets()
    for set_id, s in curves.items():
        for stratum, c in s["curves"].items():
            if s["stratifier"] == "nars9":
                strata = {"nars9": None if stratum == "national" else stratum}
            else:
                slope = {"lt_0.5": 0.001, "0.5_to_2": 0.01, "ge_2": 0.05, "national": None}[stratum]
                strata = {"slope": slope}
            xs = [c["x39"] - 1e-4, c["x39"] + 1e-4, c["x69"] - 1e-4, c["x69"] + 1e-4,
                  c["points"][0][0] - 0.5 if c["points"][0][0] > 0.5 else 0.0, c["points"][-1][0] + 1.0]
            xs += [float(p[0]) for p in c["points"]]
            pts = [float(p[0]) for p in c["points"]]
            xs += [(pts[i] + pts[i + 1]) / 2 for i in range(len(pts) - 1)]
            seen_x = set()
            for x in xs:
                x = round(max(0.0, x), 6)
                if set_id in ("corridor-woody", "corridor-natural"):
                    x = round(min(x, 100.0), 1)   # the adapters round corridor covers to one decimal
                if x in seen_x:
                    continue
                seen_x.add(x)
                if set_id in ("corridor-woody", "corridor-natural"):
                    rec = {"streamcat": {"pctconif2019wsrp100": x, "pctdecid2019wsrp100": 0.0,
                                         "pctmxfst2019wsrp100": 0.0, "pctshrb2019wsrp100": 0.0,
                                         "pctgrs2019wsrp100": 0.0, "pctwdwet2019wsrp100": 0.0,
                                         "pcthbwet2019wsrp100": 0.0, "pctimp2019ws": 0.0}}
                elif set_id == "flow-variability":
                    rec = {"erom": erom(months_with_cv(x))}
                else:
                    rec = {"geomorph": {"entrenchment_ratio": x}}
                rec.update(strata)
                case(f"curve:{set_id}:{stratum}:{x:.9g}", "curves", rec)
    # -- integers, zero counts and unavailable queries -------------------------
    for n in (0, 1, 2, 3, 5):
        case(f"count:nas:{n}", "counts", {"nas_taxa": [f"Taxon {i}" for i in range(n)]})
    for n in (0, 1, 2, 4):
        case(f"count:nid:{n}", "counts", {"nid_dams": [{"name": f"Dam {i}", "distance_m": 100.0} for i in range(n)]})
    case("count:nas:unavailable", "counts", {"nas_taxa": None})
    case("count:nid:unavailable", "counts", {"nid_dams": None})
    # -- missing inputs, one at a time ------------------------------------------
    for field in ("pctimp2019ws", "pctcrop2019ws", "pcthay2019ws", "pctwdwet2019ws", "pcthbwet2019ws",
                  "rddensws", "kffactws", "damnrmstorws", "runoffws", "pctconif2019wsrp100",
                  "pctgrs2019wsrp100", "pcthbwet2019wsrp100", "chemcat", "chemws", "prg_bmmi0809", "hydws"):
        case(f"missing:streamcat:{field}", "missing", {"streamcat": {field: None}})
    case("missing:erom", "missing", {"erom": None})
    case("missing:geomorph", "missing", {"geomorph": None})
    case("missing:bhr", "missing", {"geomorph": {"bank_height_ratio": None}})
    case("missing:er", "missing", {"geomorph": {"entrenchment_ratio": None}})
    case("missing:slope", "missing", {"slope": None})
    case("missing:sinuosity", "missing", {"sinuosity": None})
    case("missing:slope-and-sinuosity", "missing", {"slope": None, "sinuosity": None})
    nothing = {"streamcat": {k: None for k in
                             ["pctimp2019ws", "pctwdwet2019ws", "pcthbwet2019ws",
                              "pctcrop2019ws", "pcthay2019ws", "kffactws", "rddensws",
                              "damnrmstorws", "runoffws", "pctconif2019wsrp100",
                              "pctdecid2019wsrp100", "pctmxfst2019wsrp100",
                              "pctgrs2019wsrp100", "pctshrb2019wsrp100",
                              "pctwdwet2019wsrp100", "pcthbwet2019wsrp100", "hydcat",
                              "hydws", "sedcat", "sedws", "chemcat", "chemws", "conncat",
                              "connws", "tempcat", "tempws", "habtcat", "habtws",
                              "prg_bmmiws", "prg_bmmi0809"]},
               "erom": None, "geomorph": None, "slope": None, "sinuosity": None,
               "nas_taxa": None, "nid_dams": None}
    case("missing:everything", "missing", nothing)
    case("edge:runoff-zero", "missing", {"streamcat": {"runoffws": 0.0}})
    case("edge:storage-zero", "band-edges", {"streamcat": {"damnrmstorws": 0.0}})
    case("edge:wetlands-over-cap", "band-edges", {"streamcat": {"pctwdwet2019ws": 80.0, "pcthbwet2019ws": 40.0}})
    case("edge:corridor-over-cap", "band-edges", {"streamcat": {"pctconif2019wsrp100": 60.0, "pctdecid2019wsrp100": 30.0,
                                                                "pctmxfst2019wsrp100": 20.0, "pctgrs2019wsrp100": 10.0}})
    # -- fallback routes --------------------------------------------------------
    for cat in ("1", "2", "3", "4A", "4B", "4C", "5", "4a", " 5 ", "9"):
        case(f"route:attains:{cat!r}", "routes", {"attains_exact": attains(cat)})
    case("route:attains:nearby-5", "routes", {"attains_exact": {}, "attains_nearby": attains("5", nearby=True)})
    case("route:attains:exact-3-nearby-5", "routes", {"attains_exact": attains("3"), "attains_nearby": attains("5", nearby=True)})
    case("route:attains:none-no-chem", "routes", {"attains_exact": {}, "streamcat": {"chemcat": None}})
    case("route:nutrients:both", "routes", {"wqp_tn": wqp("tn", 0.9), "wqp_tp": wqp("tp", 0.05)})
    case("route:nutrients:tn-only", "routes", {"wqp_tn": wqp("tn", 0.9)})
    case("route:nutrients:tp-only", "routes", {"wqp_tp": wqp("tp", 0.2)})
    case("route:nutrients:region-unknown", "routes", {"wqp_tn": wqp("tn", 0.9), "nars9": None})
    case("route:nutrients:none-no-chem", "routes", {"streamcat": {"chemws": None}})
    for region in ("CPL", "NAP", "NPL", "SAP", "SPL", "TPL", "UMW", "WMT", "XER"):
        catalog = sm.catalog()
        nut = next(m for m in catalog["methods"] if m["methodKey"] == "regional-nutrient-condition")
        tn = next(i for i in nut["inputs"] if i["key"] == "tn")["regionalBands"][region]
        tp = next(i for i in nut["inputs"] if i["key"] == "tp")["regionalBands"][region]
        for v in (tn[0] - 1e-6, tn[0], tn[0] + 1e-6, tn[1] - 1e-6, tn[1], tn[1] + 1e-6):
            case(f"route:nutrients:{region}:tn:{v:.9g}", "routes", {"nars9": region, "wqp_tn": wqp("tn", v), "wqp_tp": wqp("tp", tp[0])})
        for v in (tp[0] - 1e-6, tp[0], tp[1], tp[1] + 1e-6):
            case(f"route:nutrients:{region}:tp:{v:.9g}", "routes", {"nars9": region, "wqp_tp": wqp("tp", v)})
    case("route:population:model-out-of-range", "routes", {"streamcat": {"prg_bmmi0809": 1.2}})
    case("route:population:model-missing", "routes", {"streamcat": {"prg_bmmi0809": None}})
    case("route:population:model-missing-component-missing", "routes",
         {"streamcat": {"prg_bmmi0809": None, "tempws": None}})
    case("route:population:products-low", "routes",
         {"streamcat": {"prg_bmmi0809": None, "hydcat": 0.5, "sedcat": 0.5, "chemcat": 0.5}})
    # -- overrides and canals -----------------------------------------------------
    for cls in ("Good", "Fair", "Poor"):
        case(f"override:channel:{cls}:noted", "overrides", None,
             {"channel-evolution-channel-evolution-stage-and-trends": {"stageClass": cls, "indicators": "bank scallops"}})
        case(f"override:channel:{cls}:no-note", "overrides", None,
             {"channel-evolution-channel-evolution-stage-and-trends": {"stageClass": cls, "indicators": ""}})
    case("override:bank:complete", "overrides", None,
         {"channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition": {"erodingBankPct": 30.0, "armoredBankPct": 10.0}})
    case("override:bank:incomplete", "overrides", None,
         {"channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition": {"erodingBankPct": 30.0}})
    for code in (33600, 33601, 33603):
        case(f"canal:{code}", "overrides", {"fcode": code})
    case("canal:33600-no-geometry", "overrides", {"fcode": 33600, "geomorph": None})
    case("canal:33600-observed-good", "overrides", {"fcode": 33600},
         {"channel-evolution-channel-evolution-stage-and-trends": {"stageClass": "Good", "indicators": "stable"}})
    case("canal:46003-not-canal", "overrides", {"fcode": 46003})
    # -- strata ------------------------------------------------------------------
    for region in ("CPL", "NAP", "NPL", "SAP", "SPL", "TPL", "UMW", "WMT", "XER", None):
        case(f"strata:region:{region}", "strata", {"nars9": region})
    for slope in (0.0, 0.004999, 0.005, 0.019999, 0.02, 0.1, None):
        case(f"strata:slope:{slope}", "strata", {"slope": slope})
    # -- rollup extremes -----------------------------------------------------------
    case("rollup:all-good", "rollup", {
        "streamcat": {"pctimp2019ws": 1.0, "pctcrop2019ws": 5.0, "pcthay2019ws": 0.0, "pctwdwet2019ws": 6.0,
                      "pcthbwet2019ws": 2.0, "rddensws": 0.5, "kffactws": 0.2, "damnrmstorws": 0.0, "runoffws": 400.0,
                      "pctconif2019wsrp100": 95.0, "pctdecid2019wsrp100": 3.0, "pctmxfst2019wsrp100": 1.0,
                      "pctgrs2019wsrp100": 0.5, "pctshrb2019wsrp100": 0.3, "pctwdwet2019wsrp100": 0.1,
                      "pcthbwet2019wsrp100": 0.1, "prg_bmmi0809": 0.9},
        "geomorph": {"bank_height_ratio": 1.0, "entrenchment_ratio": 3.0}, "slope": 0.01, "sinuosity": 1.5,
        "erom": erom(months_with_cv(0.05)), "attains_exact": attains("1"), "wqp_tn": wqp("tn", 0.05),
        "wqp_tp": wqp("tp", 0.005), "nas_taxa": [], "nid_dams": []})
    case("rollup:all-poor", "rollup", {
        "streamcat": {"pctimp2019ws": 40.0, "pctcrop2019ws": 60.0, "pcthay2019ws": 0.0, "pctwdwet2019ws": 0.2,
                      "pcthbwet2019ws": 0.1, "rddensws": 5.0, "kffactws": 0.5, "damnrmstorws": 200000.0, "runoffws": 100.0,
                      "pctconif2019wsrp100": 0.0, "pctdecid2019wsrp100": 0.0, "pctmxfst2019wsrp100": 0.0,
                      "pctgrs2019wsrp100": 0.0, "pctshrb2019wsrp100": 0.0, "pctwdwet2019wsrp100": 0.0,
                      "pcthbwet2019wsrp100": 0.0, "prg_bmmi0809": 0.1},
        "geomorph": {"bank_height_ratio": 2.0, "entrenchment_ratio": 0.5}, "slope": 0.0001, "sinuosity": 1.0,
        "erom": erom(months_with_cv(5.0)), "attains_exact": attains("5"), "wqp_tn": wqp("tn", 9.0),
        "wqp_tp": wqp("tp", 2.0), "nas_taxa": ["a", "b", "c", "d"],
        "nid_dams": [{"name": "A", "distance_m": 100.0}, {"name": "B", "distance_m": 200.0}]})
    case("rollup:provisional-13", "rollup", {"streamcat": {"pctwdwet2019ws": None, "rddensws": None, "runoffws": None,
                                                           "pctimp2019ws": None, "kffactws": None},
                                             "geomorph": None, "nas_taxa": None})
    case("rollup:provisional-14", "rollup", {"streamcat": {"pctwdwet2019ws": None, "rddensws": None, "runoffws": None,
                                                           "kffactws": None},
                                             "geomorph": None, "nas_taxa": None})
    # -- override scores (the Assessment page's rating select, on every function) ---
    group = "override-score"
    chan = "channel-evolution-channel-evolution-stage-and-trends"
    bank = "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition"
    for mkey, mid in FUNCTIONS.items():
        for cls in ("Good", "Fair", "Poor"):
            case(f"{group}:{mkey}:{cls}", group, None, None, {mid: cls})
    for cls in ("Good", "Fair", "Poor"):
        case(f"{group}:all:{cls}", group, None, None, {mid: cls for mid in FUNCTIONS.values()})
    # an override where the evidence gave no rating: the function is rated and coverage counts it
    case(f"{group}:over-missing-geometry", group, {"geomorph": None}, None,
         {FUNCTIONS["m06"]: "Fair", FUNCTIONS["m07"]: "Good"})
    case(f"{group}:over-missing-everything", group, nothing, None, {mid: "Fair" for mid in FUNCTIONS.values()})
    case(f"{group}:over-canal", group, {"fcode": 33600}, None, {chan: "Good"})
    # complete observed entries outrank the override; incomplete ones do not
    case(f"{group}:under-observed-channel", group, None,
         {chan: {"stageClass": "Poor", "indicators": "headcut upstream"}}, {chan: "Good"})
    case(f"{group}:over-unnoted-channel", group, None,
         {chan: {"stageClass": "Poor", "indicators": ""}}, {chan: "Good"})
    case(f"{group}:under-observed-bank", group, None,
         {bank: {"erodingBankPct": 70.0, "armoredBankPct": 10.0}}, {bank: "Good"})
    case(f"{group}:over-incomplete-bank", group, None, {bank: {"erodingBankPct": 70.0}}, {bank: "Good"})
    case(f"{group}:beside-observed-bank", group, None,
         {bank: {"erodingBankPct": 30.0, "armoredBankPct": 10.0}}, {chan: "Fair"})
    return cases


def build_fixture() -> dict:
    cases = build_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        report = score_case(c)
        c["entries"] = entries_from(report, c)
        c["expected"] = expected_from(report)
    return {"method_version": __import__("easi.national", fromlist=["method_version"]).method_version(),
            "workbook": bc.OUT_NAME, "cases": cases}


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    data = build_fixture()
    FIXTURE.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    groups = {}
    for c in data["cases"]:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
    print(f"wrote {FIXTURE}: {len(data['cases'])} cases {groups}")
