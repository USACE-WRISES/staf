"""The basis ladder: what to try after the ecoregion hierarchy runs out.

Rules REF-08, REF-09 and REF-10 (methodology 0.13, owner decision 2026-09-21).

``reference_pool.choose_pool`` walks Level III, then Level II, then Level I, and
returns ``insufficient`` when no level holds enough comparable least-disturbed
stations with a value. Until now that was the end: the metric was withheld and,
if it was the only metric its function had, the function went unassessed.

This module is what happens next. It tries three further rungs in a fixed order
and stops at the first that admits:

    REF-08  national comparable donors, where the ladder left the hierarchy but
            real least-disturbed stations still exist and transfer demonstrably
    REF-09  a fitted stressor-response expectation, for a metric that reached
            the VALIDATED tier of the governing pre-registration
    REF-10  a published criterion that passes the PB-1 to PB-5 fitness test

Anything that admits carries its rung's label and confidence cap. Anything that
does not is still withheld, and the record says which rungs were tried and why
each refused, so a documented exclusion names a blocker rather than a shrug.

Every rung is gated on evidence that was fixed before it was run. Rungs 8 and 9
read a verdict table from a pre-registered basis run; rung 10 reads the vendored
catalog. None of them may be admitted by this module's own judgement.

Pure: frames and evidence in, decisions and curve rows out. No file writes.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd

from . import basis_recovery as br
from . import basis_transfer as bt
from . import curve_basis
from . import curves
from . import modeled_reference as mr
from . import published_benchmark as pb
from . import reference_pool as rp

#: which basis run a rung reads its verdict from
NATIONAL_BASIS = "3a_envelope"
MODELED_BASIS = "1B_mixed_local"


def _verdict(validation: Optional[dict], metric: str, basis: str) -> Optional[dict]:
    """The verdict recorded for one metric on one basis, or None.

    ``validation`` is ``{metric: {basis: {"verdict": ..., ...}}}``, the shape a
    basis run's ``verdicts.csv`` reduces to.
    """
    entry = (validation or {}).get(metric)
    if not isinstance(entry, dict):
        return None
    got = entry.get(basis)
    return got if isinstance(got, dict) else None


def _validated(validation: Optional[dict], metric: str, basis: str) -> bool:
    got = _verdict(validation, metric, basis)
    return bool(got) and str(got.get("verdict") or "").strip().lower() == "validated"


#: the fewest evaluation regions the recovery test judges a basis on
#: (``basis_recovery.score_verdicts``' ``min_cells``, fixed by the pre-registration)
MIN_CELLS = 4


def _verdict_word(got: Optional[dict]) -> str:
    return str((got or {}).get("verdict") or "").strip().lower()


def _donors(n: int) -> str:
    return (f"{n} comparable national donor carries this metric" if n == 1 else
            f"{n} comparable national donors carry this metric")


def _national_refusal(n: int, got: Optional[dict]) -> str:
    """Why donors that clear the floor still do not admit, in the words of what
    the recovery test found. "Tested and failed" and "too few regions to test"
    are different blockers, so they never share a sentence."""
    v, head = _verdict_word(got), _donors(n)
    if v == br.PROMISING:
        return (f"{head}, and borrowed donors reached promising but not validated in the "
                f"recovery test, so there is no demonstration that they transfer to this "
                f"ecoregion.")
    if v in (br.UNSUPPORTED, br.UNSUPPORTED_COVERAGE):
        return (f"{head}, but borrowed donors did not pass the recovery test for it, so they "
                f"are not shown to transfer to this ecoregion.")
    if v == br.NOT_QUANTIFIED:
        return (f"{head}, but the recovery test could not put an interval on what borrowed "
                f"donors recover for it, so their transfer is not demonstrated.")
    if v == br.NOT_EVALUATED:
        return (f"{head}, but too few evaluation regions carry it for the recovery test to "
                f"judge borrowed donors, so their transfer is not demonstrated.")
    return (f"{head}, but borrowed donors were not part of the recovery test for it, so their "
            f"transfer is not demonstrated.")


def _modeled_refusal(got: Optional[dict]) -> str:
    """Why a fitted expectation does not admit, in the words of what the
    recovery test found for this metric."""
    v = _verdict_word(got)
    if v == br.PROMISING:
        return ("The fitted expectation reached promising but not validated in the recovery "
                "test, so it is documented and not scored.")
    if v == br.UNSUPPORTED:
        return "The fitted expectation did not pass the recovery test for this metric."
    if v == br.UNSUPPORTED_COVERAGE:
        return ("The fitted expectation did not pass the recovery test for this metric, "
                "because its training data did not reach the conditions of enough evaluation "
                "regions.")
    if v == br.NOT_QUANTIFIED:
        return ("The recovery test could not put an interval on the fitted expectation for "
                "this metric.")
    if v == br.NOT_EVALUATED:
        n = int((got or {}).get("n_cells") or 0)
        return (f"Only {n} evaluation region{'' if n == 1 else 's'} could test a fitted "
                f"expectation for this metric, fewer than the {MIN_CELLS} the recovery test "
                f"needs.")
    return "No fitted expectation was tested for this metric."


def _transportable(validation: Optional[dict], metric: str, basis: str,
                   target_l3: str) -> tuple[bool, str]:
    """Pre-registration II's transportability condition, as frozen for this target.

    A basis that passed where it was tested is recommended for a target only
    when the target sits inside the range the passing cells spanned on the
    basis's own domain measure. A target the evaluation never placed is refused:
    the rung admits on a positive finding, never on the absence of one.
    """
    got = _verdict(validation, metric, basis) or {}
    tr = got.get("transport") or {}
    inside = (tr.get("targets") or {}).get(str(target_l3))
    measure = tr.get("measure") or "its domain measure"
    if inside is True:
        return True, ""
    if inside is False:
        bound = tr.get("bound")
        side = "at least" if tr.get("better") == "min" else "at most"
        return False, (f"It passed only where {measure} was {side} {bound}, and this ecoregion "
                       f"lies outside that range, so it was never tested in conditions like "
                       f"these.")
    return False, "Transportability to this ecoregion was never evaluated, so it is not assumed."


# --------------------------------------------------------------------------- #
# the rungs
# --------------------------------------------------------------------------- #
def try_national(metric: str, *, frame: pd.DataFrame, values: pd.Series, target_l3: str,
                 validation: Optional[dict] = None,
                 min_donors: int = bt.MIN_DONORS) -> dict:
    """REF-08. Comparable least-disturbed donors from outside the hierarchy.

    Admits only when the donor pool clears the DATA-05 floor **and** the metric
    demonstrated that it transfers: round one and round two both asked whether a
    borrowed donor pool recovers a withheld reference answer, and a rung that
    ignored the answer would be the thing the reviewer refused.
    """
    out: dict[str, Any] = {"rung": "REF-08", "basis": curve_basis.NATIONAL,
                           "admitted": False, "why": "", "n_donors": 0,
                           "decision": None, "values": None}
    target = frame[frame["l3"].astype(str) == str(target_l3)]
    donors = bt.national_reference(frame, exclude_l3=target_l3)
    rows, vals = bt.envelope_donors(metric, target, donors, values)
    out["n_donors"] = int(len(vals))
    if len(vals) < min_donors:
        out["why"] = f"{_donors(len(vals))}, below the floor of {min_donors}."
        return out
    if not _validated(validation, metric, NATIONAL_BASIS):
        out["why"] = _national_refusal(len(vals), _verdict(validation, metric, NATIONAL_BASIS))
        return out
    ok, why = _transportable(validation, metric, NATIONAL_BASIS, target_l3)
    if not ok:
        out["why"] = f"{_donors(len(vals))}, and borrowed donors validated for it elsewhere. {why}"
        return out
    d = rp.PoolDecision(
        metric=metric, status=rp.STATUS_NATIONAL, level=None,
        region_code="national", region_name="National least-disturbed pool",
        family=(rp.family_profile(metric) or {}).get("family"),
        n_pool=int(len(donors)), n_comparable=int(len(rows)), n_usable=int(len(vals)),
        n_local=0, n_huc12=int(rows["huc12"].nunique()) if "huc12" in rows else 0,
        disposition="adequate" if len(vals) >= 20 else "exploratory",
        supported_level=None, transfer_risk=rp.RISK_MODERATE,
        transfer_note=(f"{len(vals)} comparable least-disturbed stations from the national pool, "
                       f"none of them inside this ecoregion or its parents. They were matched to "
                       f"this ecoregion's streams on the metric family's natural covariates."),
        station_ids=tuple(rows["station_key"].astype(str)) if "station_key" in rows else (),
        levels_tried=[], basis=curve_basis.NATIONAL)
    out.update({"admitted": True, "decision": d, "values": vals.reset_index(drop=True),
                "why": f"{len(vals)} comparable national donors, transfer validated."})
    return out


def try_modeled(metric: str, *, frame: pd.DataFrame, values: pd.Series, target_l3: str,
                validation: Optional[dict] = None, region_name: Optional[str] = None,
                seed: int = 11, fit: bool = True) -> dict:
    """REF-09. A fitted expectation, for a metric that was validated for one."""
    out: dict[str, Any] = {"rung": "REF-09", "basis": curve_basis.MODELED,
                           "admitted": False, "why": "", "decision": None,
                           "values": None, "population": None}
    if not _validated(validation, metric, MODELED_BASIS):
        out["why"] = _modeled_refusal(_verdict(validation, metric, MODELED_BASIS))
        return out
    ok, why = _transportable(validation, metric, MODELED_BASIS, target_l3)
    if not ok:
        out["why"] = f"The fitted expectation validated for this metric elsewhere. {why}"
        return out
    if not fit:
        out.update({"admitted": True, "why": "Validated for this metric.",
                    "decision": rp.PoolDecision(
                        metric=metric, status=rp.STATUS_MODELED, level=None,
                        region_code=str(target_l3), region_name=region_name,
                        family=(rp.family_profile(metric) or {}).get("family"),
                        n_pool=0, n_comparable=0, n_usable=0, n_local=0, n_huc12=0,
                        disposition="exploratory", transfer_risk=rp.RISK_NONE,
                        transfer_note="", basis=curve_basis.MODELED)})
        return out
    pop = mr.modeled_population(metric, frame=frame, values=values, target_l3=target_l3,
                               spec=mr.SPEC, seed=seed)
    out["population"] = pop
    if pop.get("values") is None or not len(pop["values"]) or not pop.get("anchors"):
        out["why"] = "The model could not be fitted or produced no usable expectation."
        return out
    d = mr.pool_decision(metric, pop, family=(rp.family_profile(metric) or {}).get("family"),
                         region_name=region_name)
    out.update({"admitted": True, "decision": d, "values": pop["values"],
                "why": (f"Validated for this metric; fitted on {pop['n_train']} stations with "
                        f"this ecoregion's own level added.")})
    return out


def try_published(metric: str, *, frame: pd.DataFrame, target_l3: str,
                  region_name: Optional[str] = None) -> dict:
    """REF-10. A published criterion, admitted on fitness rather than agreement."""
    out: dict[str, Any] = {"rung": "REF-10", "basis": curve_basis.PUBLISHED,
                           "admitted": False, "why": "", "condition": None, "decision": None,
                           "points": None, "fitness": None}
    target = frame[frame["l3"].astype(str) == str(target_l3)]
    got = pb.fitness(metric, frame=target, target_l3=str(target_l3))
    out["fitness"] = got
    if not got["admissible"]:
        # the failed condition rides as its own token; the sentence is for a reader
        failed = [(k, v["why"]) for k, v in got["conditions"].items() if not v["pass"]]
        out["condition"], out["why"] = (failed[0] if failed else
                                        (None, "The fitness test refused this benchmark."))
        return out
    pts = pb.curve_points(metric, got["region"])
    if not pts:
        out["why"] = f"No curve could be built from the criterion for region {got['region']}."
        return out
    d = pb.pool_decision(metric, got["region"], region_code=str(target_l3),
                         region_name=region_name,
                         family=(rp.family_profile(metric) or {}).get("family"))
    out.update({"admitted": True, "decision": d, "points": pts,
                "why": f"All five fitness conditions met for NARS-9 region {got['region']}."})
    return out


# --------------------------------------------------------------------------- #
# the ladder
# --------------------------------------------------------------------------- #
RUNGS = ("REF-08", "REF-09", "REF-10")


def resolve(metrics: Iterable[str], *, frame: pd.DataFrame, values_wide: pd.DataFrame,
            target_l3: str, metric_config: dict, validation: Optional[dict] = None,
            region_name: Optional[str] = None, seed: int = 11,
            enabled: Iterable[str] = RUNGS, fit: bool = True) -> dict:
    """Walk the ladder for every metric the ecoregion hierarchy could not support.

    Returns ``decisions`` and ``curve_rows`` for what admitted, and ``attempts``
    for everything tried, admitted or not, so a withheld metric can say which
    rungs refused it and why.

    ``fit=False`` answers which rung WOULD admit without paying for the fit or
    the curve. The reference census uses it, because the census a reviewer reads
    before a build and the build itself must never disagree about which metrics
    are scored, and refitting every model for every region of a national census
    would cost more than the census is worth.
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
            if rung == "REF-08":
                got = try_national(mk, frame=frame, values=series, target_l3=target_l3,
                                   validation=validation)
            elif rung == "REF-09":
                got = try_modeled(mk, frame=frame, values=series, target_l3=target_l3,
                                  validation=validation, region_name=region_name, seed=seed,
                                  fit=fit)
            else:
                got = try_published(mk, frame=frame, target_l3=target_l3,
                                    region_name=region_name)
            attempt = {"metric": mk, "rung": got["rung"], "admitted": got["admitted"],
                       "basis": got["basis"], "why": got["why"]}
            if got.get("condition"):
                attempt["condition"] = got["condition"]
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
                             "why": "the rung admitted but the engine built no curve"})
            continue
        decisions[mk] = got["decision"]
        curve_rows[mk] = row

    return {"decisions": decisions, "curve_rows": curve_rows,
            "populations": populations, "attempts": attempts}


def _row_from_values(metric: str, values: Any, metric_config: dict) -> Optional[dict]:
    """A curve row built by the shipping engine from a basis's own population."""
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
    """The ladder's own record: one row per metric per rung tried."""
    rows = list(attempts)
    cols = ["metric", "rung", "basis", "admitted", "why"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).reindex(columns=cols)


# --------------------------------------------------------------------------- #
# the evidence a rung is gated on
# --------------------------------------------------------------------------- #
def validation_path():
    from .paths import CONFIG_DIR
    return CONFIG_DIR / "basis_validation.yaml"


def load_validation(path=None) -> dict:
    """The committed verdicts that admit rungs REF-08 and REF-09.

    Generated from a pre-registered basis run by
    ``scripts/build_basis_validation.py`` and committed, so a build reads a
    fixed record rather than re-deciding what counts as validated. Returns
    ``{metric: {basis: record}}``; an absent file means no rung is admitted,
    which is the safe direction.
    """
    from .config import read_yaml
    p = validation_path() if path is None else path
    if not p.exists():
        return {}
    doc = read_yaml(p) or {}
    return {str(mk): dict(v or {}) for mk, v in (doc.get("metrics") or {}).items()}


def validation_provenance(path=None) -> dict:
    """What the committed evidence says about itself, for the inputs digest."""
    from .config import read_yaml
    p = validation_path() if path is None else path
    if not p.exists():
        return {}
    doc = read_yaml(p) or {}
    keep = ("version", "generated", "preregistration", "preregistration_sha256", "source")
    return {k: doc[k] for k in keep if k in doc}
