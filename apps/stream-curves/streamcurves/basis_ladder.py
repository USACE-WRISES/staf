"""The sources after the station pools: national, modeled and published.

Rules REF-12, REF-13 and REF-14 of the methodology 0.14 hierarchy (owner
decision 2026-09-21), with the six acceptance criteria of ``acceptance``.

A missing curve walks one hierarchy and takes the first source that passes
acceptance:

    local reference                 reference_pool.choose_pool (REF-04)
    regional least-disturbed pools  reference_pool.choose_pool (REF-11)
    comparable national reference   this module, REF-12
    modeled reference               this module, REF-13
    published benchmark             this module, REF-14

The station pools come first because they are this ecoregion's own streams or
its region's; this module is what the hierarchy tries when none of them passes.
Each source is judged by the same rules as every other: enough independent
stations (ACC-01), sampling compatibility (ACC-02, where values are selected),
ecological applicability (ACC-03: the faunal rule, the envelope, and a national
or modeled source's documented limits), stability (ACC-04), and the recovery
test's classification error and directional bias (ACC-05, ACC-06) read from the
committed evidence. Nothing is admitted by this module's own judgement.

A metric that no source supports is withheld, and its record names every source
tried and why each refused, so a documented gap names a blocker.

Pure: frames and evidence in, decisions and curve rows out. No file writes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Optional

import pandas as pd

from . import acceptance
from . import basis_transfer as bt
from . import curve_basis
from . import curves
from . import model_registry as mreg
from . import modeled_reference as mr
from . import published_benchmark as pb
from . import reference_pool as rp

#: the rule each source is recorded under
RULE_NATIONAL, RULE_MODELED, RULE_PUBLISHED = "REF-12", "REF-13", "REF-14"
RUNGS = (RULE_NATIONAL, RULE_MODELED, RULE_PUBLISHED)
#: the evidence key of each national option (basis_validation.yaml)
NATIONAL_OPTIONS = ("3c_matched", "3a_envelope")
MODELED_BASIS = "1B_mixed_local"


def _national_options() -> tuple:
    got = (rp.hierarchy_settings() or {}).get("national_options")
    return tuple(got) if got else NATIONAL_OPTIONS


def _lower(sentence: str) -> str:
    """A refusal sentence as a clause: first letter lowered, final stop kept."""
    s = str(sentence or "").strip()
    if not s:
        return ""
    s = s[0].lower() + s[1:]
    return s if s.endswith(".") else s + "."


def _donor_words(n: int) -> str:
    return f"{n} national donor" + ("" if n == 1 else "s")


def _decision(metric: str, *, status: str, basis: str, region_code: str,
              region_name: Optional[str], n_pool: int, n_usable: int, station_ids,
              n_huc12: int, note: str, risk: str, tried: list, detail: dict,
              disposition: Optional[str] = None) -> rp.PoolDecision:
    return rp.PoolDecision(
        metric=metric, status=status, level=None, region_code=region_code,
        region_name=region_name, family=rp.family_of(metric), n_pool=int(n_pool),
        n_comparable=int(n_usable), n_usable=int(n_usable), n_local=0, n_huc12=int(n_huc12),
        disposition=disposition or ("adequate" if n_usable >= acceptance.settings()["adequate"]
                                    else "exploratory"),
        supported_level=None, transfer_risk=risk, transfer_note=note,
        station_ids=tuple(station_ids), levels_tried=[], basis=basis,
        screen=rp.SCREEN_STRICT, screen_detail=detail, options_tried=tried)


# --------------------------------------------------------------------------- #
# REF-12: comparable national reference
# --------------------------------------------------------------------------- #
def try_national(metric: str, *, frame: pd.DataFrame, values: pd.Series, target_l3: str,
                 validation: Optional[dict] = None, entry: Optional[dict] = None,
                 region_name: Optional[str] = None) -> dict:
    """REF-12. The national strict-screen pool less the target, kept to the
    target's faunal provinces for an assemblage, tried as matched donors and
    then as donors inside the widened envelope. Each option has to pass
    ACC-01, ACC-04, ACC-05 and ACC-06 and lie inside the conditions its
    evidence covered (ACC-03)."""
    out: dict[str, Any] = {"rung": RULE_NATIONAL, "basis": curve_basis.NATIONAL,
                           "admitted": False, "why": "", "decision": None, "values": None,
                           "options": []}
    target = frame[frame["l3"].astype(str) == str(target_l3)]
    profile = rp.family_profile(metric) or {}
    family = rp.family_of(metric)
    fauna = rp.fauna_groups_of(target) if profile.get("fauna") else []
    donors = bt.national_donors_for(metric, target, frame, exclude_l3=target_l3)
    entry = entry or {}
    reasons: list[str] = []
    for option in _national_options():
        got = _national_option(metric, option, target=target, donors=donors, frame=frame,
                               values=values, profile=profile)
        n, vals, measure = got["n"], got["values"], got["measure"]
        ids, n_huc12 = got["ids"], got["n_huc12"]
        head = (f"{n} matched national donors" if option == "3c_matched" else
                f"{n} national donors inside the comparability envelope")
        ok, why = acceptance.sample_ok(n)
        clause = f"{head}, below the floor of {acceptance.settings()['exploratory']}." \
            if not ok else ""
        if ok:
            ok, why, _ = acceptance.stability(vals, entry)
            clause = "" if ok else f"{head}, but the donor pool is not stable, since {why}."
        if ok:
            ok, why = acceptance.evidence(metric, option, validation, family=family)
            clause = "" if ok else f"{head}, but {_lower(why)}"
        if ok:
            ok, why = acceptance.transport_ok(metric, option, validation, family=family,
                                              measure=measure)
            clause = "" if ok else f"{head}, but {_lower(why)}"
        tried = {"option": option, "n": n, "accepted": bool(ok), "why": why or ""}
        out["options"].append(tried)
        if not ok:
            reasons.append(clause)
            continue
        if option == "3c_matched":
            note = (f"{n} least-disturbed stations from the national pool, the three nearest to "
                    f"each of this ecoregion's streams on the natural setting that governs this "
                    f"metric family, none inside this ecoregion.")
        else:
            note = (f"{n} least-disturbed stations from the national pool whose natural setting "
                    f"falls inside this ecoregion's comparability envelope, none inside this "
                    f"ecoregion.")
        if fauna:
            note += " Every donor drains to this ecoregion's faunal province."
        detail = {"option": option, "fauna_groups": list(fauna),
                  "measure": None if measure is None else float(measure)}
        out.update({
            "admitted": True, "values": vals,
            "why": f"{_donor_words(n)} on {acceptance.OPTION_WORDS[option]}, accepted.",
            "decision": _decision(metric, status=rp.STATUS_NATIONAL, basis=curve_basis.NATIONAL,
                                  region_code="national",
                                  region_name="National least-disturbed pool",
                                  n_pool=len(donors), n_usable=n,
                                  station_ids=sorted(set(ids)), n_huc12=n_huc12, note=note,
                                  risk=rp.RISK_MODERATE, tried=list(out["options"]),
                                  detail=detail)})
        return out
    out["why"] = " ".join(reasons)
    return out


def _national_option(metric: str, option: str, *, target: pd.DataFrame,
                     donors: pd.DataFrame, frame: pd.DataFrame, values: pd.Series,
                     profile: dict) -> dict:
    """One national option's donors for the target: ``{n, values, ids, n_huc12,
    measure}``, ``n`` counting distinct donors."""
    if option == "3c_matched":
        matches = bt.gower_matches(metric, target, donors, values)
        summary = bt.matched_summary(matches)
        n = int(summary["n_distinct"])
        vals = (matches["value"].reset_index(drop=True) if len(matches)
                else pd.Series(dtype=float))
        measure = summary.get("distance_median")
        ids = (frame.loc[matches["donor_ix"].unique(), "station_key"].astype(str)
               if len(matches) else pd.Series(dtype=str))
        n_huc12 = int(frame.loc[matches["donor_ix"].unique(), "huc12"].nunique()) \
            if len(matches) and "huc12" in frame.columns else 0
    else:
        spans = rp.national_spans(frame, profile.get("covariates") or [])
        rows, vals = bt.envelope_donors(metric, target, donors, values, spans=spans)
        vals = vals.reset_index(drop=True)
        n = int(len(vals))
        measure = n
        ids = rows["station_key"].astype(str) if "station_key" in rows else pd.Series(dtype=str)
        n_huc12 = int(rows["huc12"].nunique()) if "huc12" in rows else 0
    return {"n": n, "values": vals, "ids": ids, "n_huc12": n_huc12, "measure": measure}


# --------------------------------------------------------------------------- #
# REF-13: modeled reference, from the approved registry
# --------------------------------------------------------------------------- #
def try_modeled(metric: str, *, frame: pd.DataFrame, values: pd.Series, target_l3: str,
                region_name: Optional[str] = None, seed: Optional[int] = None,
                fit: bool = True, registry: Optional[dict] = None,
                validation: Optional[dict] = None) -> dict:
    """REF-13. Only an approved registry specification, only inside its limits."""
    out: dict[str, Any] = {"rung": RULE_MODELED, "basis": curve_basis.MODELED,
                           "admitted": False, "why": "", "decision": None,
                           "values": None, "population": None, "candidate": False}
    reg = registry if registry is not None else mreg.load()
    entry = mreg.entry_for(metric, reg)
    if entry is None:
        out["why"] = "The model registry holds no approved specification for this metric."
        return out
    if entry.get("status") != mreg.APPROVED:
        out["candidate"] = True
        out["why"] = ("A modeled specification for this metric passed the recovery test and "
                      "waits for the owner's approval, so it is not used yet.")
        return out
    app = mreg.applicability(entry, frame=frame, values=values, target_l3=target_l3,
                             registry=reg)
    out["applicability"] = app
    if not app["ok"]:
        out["why"] = app["why"]
        return out
    family = rp.family_of(metric)
    if not fit:
        out.update({"admitted": True, "why": "An approved specification applies here.",
                    "decision": rp.PoolDecision(
                        metric=metric, status=rp.STATUS_MODELED, level=None,
                        region_code=str(target_l3), region_name=region_name, family=family,
                        n_pool=0, n_comparable=0, n_usable=0, n_local=int(app["n_local"]),
                        n_huc12=0, disposition="exploratory", transfer_risk=rp.RISK_NONE,
                        transfer_note="", basis=curve_basis.MODELED)})
        return out
    got = mreg.run(entry, frame=frame, values=values, target_l3=target_l3, registry=reg,
                   seed=seed)
    pop = got["population"]
    out["population"] = pop
    if pop.get("values") is None or not len(pop["values"]) or not pop.get("anchors"):
        out["why"] = "The model could not be fitted or produced no usable expectation."
        return out
    iv = got.get("interval") or {}
    if iv.get("q25") is None or iv.get("q75") is None:
        out["why"] = ("The model could not state a resampling interval on its thresholds, so "
                      "its stability cannot be shown.")
        return out
    pop["interval"] = iv
    d = replace(mr.pool_decision(metric, pop, family=family, region_name=region_name),
                screen_detail={"procedure": entry.get("procedure"),
                               "coverage": app.get("coverage"), "gaps": app.get("gaps"),
                               "interval": iv, "registryEvidence": entry.get("evidence")})
    out.update({"admitted": True, "decision": d, "values": pop["values"],
                "why": (f"Approved specification, inside its validated limits; fitted on "
                        f"{pop['n_train']} stations with this ecoregion's own level added.")})
    return out


# --------------------------------------------------------------------------- #
# REF-14: published benchmark, from the verified catalog
# --------------------------------------------------------------------------- #
def try_published(metric: str, *, frame: pd.DataFrame, target_l3: str,
                  region_name: Optional[str] = None) -> dict:
    """REF-14. A catalog lookup, admitted on fitness rather than agreement."""
    out: dict[str, Any] = {"rung": RULE_PUBLISHED, "basis": curve_basis.PUBLISHED,
                           "admitted": False, "why": "", "condition": None, "decision": None,
                           "points": None, "fitness": None}
    target = frame[frame["l3"].astype(str) == str(target_l3)]
    region, _ = pb.majority_region(target, "nars9")
    spec = pb.lookup(metric, target_l3=str(target_l3), region=region)
    if spec is None:
        out["condition"] = ("catalog", "no entry")
        out["why"] = pb.refusal(metric, str(target_l3))
        return out
    got = pb.fitness(metric, frame=target, target_l3=str(target_l3))
    out["fitness"] = got
    if not got["admissible"]:
        failed = [(k, v["why"]) for k, v in got["conditions"].items() if not v["pass"]]
        out["condition"], out["why"] = (failed[0] if failed else
                                        (None, "The fitness test refused this benchmark."))
        return out
    pts = pb.curve_points(metric, got["region"])
    if not pts:
        out["why"] = f"No curve could be built from the criterion for region {got['region']}."
        return out
    d = replace(pb.pool_decision(metric, got["region"], region_code=str(target_l3),
                                 region_name=region_name, family=rp.family_of(metric)),
                screen_detail={"catalogEntry": spec.get("id"), "region": got["region"],
                               "edition": spec.get("edition")})
    out.update({"admitted": True, "decision": d, "points": pts,
                "why": f"Catalog entry {spec.get('id')}, fit for NARS-9 region {got['region']}."})
    return out


# --------------------------------------------------------------------------- #
# the walk
# --------------------------------------------------------------------------- #
def resolve(metrics: Iterable[str], *, frame: pd.DataFrame, values_wide: pd.DataFrame,
            target_l3: str, metric_config: dict, validation: Optional[dict] = None,
            region_name: Optional[str] = None, seed: Optional[int] = None,
            enabled: Iterable[str] = RUNGS, fit: bool = True,
            registry: Optional[dict] = None) -> dict:
    """Try the national, modeled and published sources, in that order, for every
    metric the station pools could not support.

    Returns ``decisions`` and ``curve_rows`` for what was admitted, and
    ``attempts`` for everything tried, so a withheld metric can name every
    source that refused it and why. ``fit=False`` answers which source WOULD
    admit without paying for a model fit; the reference census uses it so the
    census and the build never disagree about which metrics are scored.
    """
    wide = (values_wide.set_index(values_wide["site_id"].astype(str))
            if "site_id" in values_wide.columns else values_wide)
    keys = frame["station_key"].astype(str)
    enabled = tuple(enabled)
    decisions: dict[str, rp.PoolDecision] = {}
    curve_rows: dict[str, Any] = {}
    populations: dict[str, Any] = {}
    attempts: list[dict] = []
    for mk in metrics:
        if mk not in wide.columns:
            attempts.append({"metric": mk, "rung": None, "admitted": False,
                             "why": "the values archive carries no column for this metric"})
            continue
        series = pd.Series(keys.map(pd.to_numeric(wide[mk], errors="coerce")).to_numpy(),
                           index=frame.index)
        cfg = {mk: metric_config.get(mk) or {}}
        got = None
        for rung in enabled:
            if rung == RULE_NATIONAL:
                got = try_national(mk, frame=frame, values=series, target_l3=target_l3,
                                   validation=validation, entry=cfg[mk],
                                   region_name=region_name)
            elif rung == RULE_MODELED:
                got = try_modeled(mk, frame=frame, values=series, target_l3=target_l3,
                                  region_name=region_name, seed=seed, fit=fit,
                                  registry=registry, validation=validation)
            else:
                got = try_published(mk, frame=frame, target_l3=target_l3,
                                    region_name=region_name)
            attempt = {"metric": mk, "rung": got["rung"], "admitted": got["admitted"],
                       "basis": got["basis"], "why": got["why"]}
            if got.get("condition"):
                attempt["condition"] = got["condition"]
            if got.get("options"):
                attempt["options"] = got["options"]
            if got.get("candidate"):
                attempt["candidate"] = True
            attempts.append(attempt)
            if got["admitted"]:
                break
            got = None
        if got is None:
            continue
        if not fit:
            decisions[mk] = got["decision"]
            continue
        if got.get("points") is not None:
            row = _row_from_points(mk, got["points"], cfg[mk])
            if row is not None:
                # the region whose criterion it is, for the bundle's criteriaSource
                row["benchmark_region"] = (got.get("fitness") or {}).get("region")
        else:
            row = _row_from_values(mk, got["values"], cfg)
            populations[mk] = got.get("population")
        if row is None:
            attempts.append({"metric": mk, "rung": got["rung"], "admitted": False,
                             "basis": got["basis"],
                             "why": "the source was accepted but the engine built no curve"})
            continue
        decisions[mk] = got["decision"]
        curve_rows[mk] = row
    return {"decisions": decisions, "curve_rows": curve_rows,
            "populations": populations, "attempts": attempts}


# --------------------------------------------------------------------------- #
# a source the build refused, which the owner accepted (REF-15)
# --------------------------------------------------------------------------- #
#: the fewest distinct stations or donors a forced source may build a curve on
MIN_FORCED_N = 5
#: the rules a station pool is chosen under: the local reference, the regional pools
POOL_RULES = ("REF-04", "REF-11")


def force_source(metric: str, ref: dict, *, frame: pd.DataFrame, values_wide: pd.DataFrame,
                 target_l3: str, config: dict, validation: Optional[dict] = None,
                 region_name: Optional[str] = None, seed: Optional[int] = None,
                 scale_registry: Optional[dict] = None, excluded: Optional[dict] = None,
                 registry: Optional[dict] = None) -> dict:
    """The curve of a source the build refused, because the owner accepted it with
    a recorded rationale (REF-15).

    ``ref`` names the source: ``{"rule", "option"}``, a station pool option under
    REF-04 or REF-11 (``local``, ``regional_l2``, ...), a national option under
    REF-12 (``3c_matched``, ``3a_envelope``), the model registry's specification
    under REF-13 or the catalog's criterion under REF-14. Every check the source
    faces still runs and is recorded in ``failed``; none may refuse it. Returns
    ``{rule, option, row, decision, failed, why}``. ``row`` is None when not even
    a curve can be built (fewer than :data:`MIN_FORCED_N` stations, no
    specification, no criterion), and ``why`` says so: the decision then fails at
    the build and the metric keeps what the build gave it."""
    mk, rule = str(metric), str((ref or {}).get("rule") or "")
    option = str((ref or {}).get("option") or "")
    out: dict[str, Any] = {"rule": rule, "option": option, "row": None, "decision": None,
                           "failed": [], "why": ""}
    wide = (values_wide.set_index(values_wide["site_id"].astype(str))
            if "site_id" in values_wide.columns else values_wide)
    if mk not in wide.columns:
        return {**out, "why": "The values archive carries no column for this metric."}
    by_station = pd.to_numeric(wide[mk], errors="coerce")
    series = pd.Series(frame["station_key"].astype(str).map(by_station).to_numpy(),
                       index=frame.index)
    family = rp.family_of(mk)
    target = frame[frame["l3"].astype(str) == str(target_l3)]
    if rule in POOL_RULES:
        option = option or "local"
        settings = {**rp.floors(), "exploratory": MIN_FORCED_N}
        reg = (scale_registry or {}).get("metrics") or {}
        decision, _ledger = rp.choose_pool(mk, by_station, frame, target_l3,
                                           profile=rp.family_profile(mk), scale_entry=reg.get(mk),
                                           excluded=excluded, settings=settings, accept=None,
                                           only_option=option)
        if decision.status == rp.STATUS_INSUFFICIENT:
            why = next((str(x.get("why")) for x in decision.options_tried or [] if x.get("why")),
                       "no station qualifies")
            return {**out, "option": option,
                    "why": f"The pool could not be formed here: {_lower(why)}"}
        vals = by_station.reindex(list(decision.station_ids)).dropna().reset_index(drop=True)
        failed = acceptance.all_checks(mk, option, vals, config, validation, family=family)
        row = _row_from_values(mk, vals, {mk: config})
    elif rule == RULE_NATIONAL:
        profile = rp.family_profile(mk) or {}
        donors = bt.national_donors_for(mk, target, frame, exclude_l3=target_l3)
        got = _national_option(mk, option, target=target, donors=donors, frame=frame,
                               values=series, profile=profile)
        if got["n"] < MIN_FORCED_N:
            return {**out, "why": (f"{got['n']} national donors, fewer than the "
                                   f"{MIN_FORCED_N} a curve needs.")}
        # the floor counts distinct donors, as try_national does: matched donors
        # repeat, drawn per target stream
        failed = acceptance.all_checks(mk, option, got["values"], config, validation,
                                       family=family, measure=got["measure"], n=got["n"])
        row = _row_from_values(mk, got["values"], {mk: config})
        words = acceptance.OPTION_WORDS.get(option, option)
        decision = _decision(
            mk, status=rp.STATUS_NATIONAL, basis=curve_basis.NATIONAL, region_code="national",
            region_name="National least-disturbed pool", n_pool=len(donors), n_usable=got["n"],
            station_ids=sorted(set(got["ids"])), n_huc12=got["n_huc12"],
            note=(f"{got['n']} least-disturbed stations from the national pool, as "
                  f"{words}, none inside this ecoregion. The build refused this source and "
                  f"the owner accepted it."),
            risk=rp.RISK_HIGH, tried=[{"option": option, "n": got["n"], "accepted": False,
                                       "forced": True}],
            detail={"option": option, "forced": True,
                    "measure": None if got["measure"] is None else float(got["measure"])})
    elif rule == RULE_MODELED:
        reg = registry if registry is not None else mreg.load()
        entry = mreg.entry_for(mk, reg)
        if entry is None:
            return {**out, "why": "The model registry holds no specification for this metric."}
        failed = []
        if entry.get("status") != mreg.APPROVED:
            failed.append({"check": "REF-13", "pass": False,
                           "why": "The specification waits for the owner's approval."})
        app = mreg.applicability(entry, frame=frame, values=series, target_l3=target_l3,
                                 registry=reg)
        if not app["ok"]:
            failed.append({"check": "ACC-03", "pass": False, "why": str(app["why"])})
        got = mreg.run(entry, frame=frame, values=series, target_l3=target_l3, registry=reg,
                       seed=seed)
        pop = got["population"]
        if pop.get("values") is None or not len(pop["values"]) or not pop.get("anchors"):
            return {**out, "failed": failed,
                    "why": "The model could not be fitted or produced no usable expectation."}
        row = _row_from_values(mk, pop["values"], {mk: config})
        decision = replace(mr.pool_decision(mk, pop, family=family, region_name=region_name),
                           screen_detail={"procedure": entry.get("procedure"),
                                          "coverage": app.get("coverage"), "forced": True})
    elif rule == RULE_PUBLISHED:
        region, _share = pb.majority_region(target, "nars9")
        spec = pb.lookup(mk, target_l3=str(target_l3), region=region)
        if spec is None:
            return {**out, "why": pb.refusal(mk, str(target_l3))}
        fit = pb.fitness(mk, frame=target, target_l3=str(target_l3))
        failed = [{"check": k, "pass": False, "why": v["why"]}
                  for k, v in (fit.get("conditions") or {}).items() if not v["pass"]]
        region = fit.get("region") or region
        pts = pb.curve_points(mk, region) if region else None
        if not pts:
            return {**out, "failed": failed,
                    "why": f"The criterion has no bands for NARS-9 region {region}."}
        row = _row_from_points(mk, pts, config)
        if row is not None:
            row["benchmark_region"] = region
        decision = pb.pool_decision(mk, region, region_code=str(target_l3),
                                    region_name=region_name, family=family)
    else:
        return {**out, "why": f"The build cannot compute a source under {rule or 'no rule'}."}
    if row is None:
        return {**out, "option": option, "failed": failed,
                "why": "The source was computed but the engine built no curve from it."}
    return {**out, "option": option, "row": row,
            "decision": decision.to_dict() if hasattr(decision, "to_dict") else decision,
            "failed": [f for f in failed if not f.get("pass")]}


def _row_from_values(metric: str, values: Any, metric_config: dict) -> Optional[dict]:
    """A curve row built by the shipping engine from a source's own population."""
    data = pd.DataFrame({metric: pd.Series(values).astype(float).reset_index(drop=True)})
    built = curves.build_reference_curve(data, metric, metric_config)
    row = built.get("curve_row")
    if row is None or not len(row):
        return None
    out = row.iloc[0].to_dict()
    if str(out.get("curve_status") or "") != "complete":
        return None
    out["curve_points"] = built.get("curve_points")
    out["curve_source"] = "auto"
    return out


def _row_from_points(metric: str, points: list[dict], cfg: dict) -> Optional[dict]:
    """A curve row for a published criterion, which states its points directly."""
    if not points:
        return None
    return {"metric": metric, "display_name": cfg.get("display_name") or metric,
            "stratum": "", "curve_status": "complete", "curve_source": "published_benchmark",
            "n_reference": None,
            "curve_points": pd.DataFrame(
                [{"point_order": i + 1, "metric_value": float(p["x"]),
                  "index_score": float(p["y"])} for i, p in enumerate(points)])}


def attempt_table(attempts: Iterable[dict]) -> pd.DataFrame:
    """The walk's own record: one row per metric per source tried."""
    rows = list(attempts)
    cols = ["metric", "rung", "basis", "admitted", "why"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).reindex(columns=cols)


# --------------------------------------------------------------------------- #
# the evidence the sources are judged on
# --------------------------------------------------------------------------- #
def validation_path():
    from .paths import CONFIG_DIR
    return CONFIG_DIR / "basis_validation.yaml"


def load_validation(path=None) -> dict:
    """The committed recovery evidence ACC-05 and ACC-06 read.

    Generated from a pre-registered run by ``scripts/build_basis_validation.py``
    and committed, so a build reads a fixed record rather than re-deciding what
    counts as accepted. Returns ``{metric: {basis: record}}`` with the family
    records under ``acceptance.FAMILIES_KEY``; an absent file means no source
    beyond the local reference has evidence, which is the safe direction.
    """
    from .config import read_yaml
    p = validation_path() if path is None else path
    if not p.exists():
        return {}
    doc = read_yaml(p) or {}
    out = {str(mk): dict(v or {}) for mk, v in (doc.get("metrics") or {}).items()}
    fams = doc.get("families") or {}
    if fams:
        out[acceptance.FAMILIES_KEY] = {str(k): dict(v or {}) for k, v in fams.items()}
    return out


def validation_provenance(path=None) -> dict:
    """What the committed evidence says about itself, for the inputs digest."""
    from .config import read_yaml
    p = validation_path() if path is None else path
    if not p.exists():
        return {}
    doc = read_yaml(p) or {}
    keep = ("version", "generated", "preregistration", "preregistration_sha256", "source")
    return {k: doc[k] for k in keep if k in doc}
