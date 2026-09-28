"""The six acceptance criteria of methodology 0.14 (ACC-01 to ACC-06).

One rule set for every region and every source, read from
``methodology_config.yaml`` ``acceptance``:

  ACC-01  sample adequacy: at least the exploratory floor of independent
          stations, or of distinct donors for a matched pool
  ACC-02  sampling compatibility: only cycles whose definition and method match
          (applied where values are selected, ``nrsa_dataset.latest_values``)
  ACC-03  ecological applicability: the comparability and faunal rules, and for
          national and modeled sources their documented coverage limits
  ACC-04  scoring stability: no single station moves a quartile by more than the
          configured share of the interquartile range, and none flips the build
          (the scale-free drop-one test, CURVE-04b)
  ACC-05  classification error: the source's class calls agree with reference as
          well as reference agrees with itself in at least two thirds of
          evaluation regions (Pre-registration II's accuracy criterion)
  ACC-06  directional bias: no systematic promotion across evaluation regions

Criteria 1 to 4 are computed for the target when a curve is built. Criteria 5
and 6 are properties of a source for a metric, measured by the recovery test and
frozen in ``config/basis_validation.yaml``; where a metric has fewer evaluation
regions than the minimum, its family's pooled evidence stands in. Exact anchor
recovery (containment, C1) is carried as a diagnostic and never gates. The local
reference is the definition of reference and needs no recovery evidence.

Methodology 0.16 (owner decisions D11 and D13, 2026-09-28) adds two things here.
The curve checks (:func:`curve_checks`): a source option whose curve would be
the engine's degenerate fallback, or whose curve the discrimination check reads
as inverted, fails acceptance with the reason stated, so the ladder moves on and
no fallback ramp is ever published. And the flagged-transfer rung (REF-16): when
no option passes, a second pass takes an option that passes every criterion but
the recovery evidence, records the verdict it was refused with
(:func:`validation_record`) and states it as a limitation
(:func:`transfer_limitation`); ``pool_acceptor`` therefore returns the verdict of
every criterion, not only the first refusal.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import pandas as pd

from . import methodology

#: the key a source option's evidence is filed under in basis_validation.yaml
OPTION_BASIS = {
    "regional_l3": "2r_l3", "regional_l2": "2r_l2", "regional_nars9": "2r_nars9",
    "regional_l1": "2r_l1", "3c_matched": "3c_matched", "3a_envelope": "3a_envelope",
    "modeled": "1B_mixed_local",
}
#: the reader-facing name of each option, for a refusal sentence
OPTION_WORDS = {
    "local": "the local reference", "regional_l3": "this ecoregion under the regional screen",
    "regional_l2": "the Level II pool", "regional_nars9": "the NARS-9 pool",
    "regional_l1": "the Level I pool", "3c_matched": "matched national donors",
    "3a_envelope": "national donors inside the comparability envelope",
    "modeled": "the modeled expectation",
}
#: a pool option's label with a capital, for the start of a sentence
def option_label(option: str) -> str:
    w = OPTION_WORDS.get(option, option)
    return w[:1].upper() + w[1:]
FAMILIES_KEY = "_families"


def settings() -> dict:
    a = methodology.threshold("acceptance", {}) or {}
    samp = a.get("sample_adequacy") or {}
    stab = a.get("scoring_stability") or {}
    cls = a.get("classification_error") or {}
    bias = a.get("directional_bias") or {}
    return {
        "exploratory": int(samp.get("exploratory", 10)),
        "adequate": int(samp.get("adequate", 20)),
        "max_shift_iqr": float(stab.get("max_quartile_shift_iqr", 0.20)),
        "no_decision_flip": bool(stab.get("no_decision_flip", True)),
        "min_cells": int(cls.get("min_cells", 4)),
        "min_share": float(cls.get("min_share", 2.0 / 3.0)),
        "max_net_optimism": float(bias.get("max_net_optimism", 0.05)),
        "evidence_fallback": str(a.get("evidence_fallback") or "family"),
    }


# --------------------------------------------------------------------------- #
# ACC-01 and ACC-04, per target
# --------------------------------------------------------------------------- #
def sample_ok(n: int, st: Optional[dict] = None) -> tuple[bool, str]:
    st = st or settings()
    if int(n) >= st["exploratory"]:
        return True, ""
    return False, f"{int(n)} independent stations, below the floor of {st['exploratory']}"


def stability(values: Any, entry: dict, st: Optional[dict] = None) -> tuple[bool, str, dict]:
    """ACC-04 on a pool's values: the scale-free drop-one test."""
    from . import curve_stability
    st = st or settings()
    rec = curve_stability.influence_check(values, entry)
    if not rec.get("evaluable"):
        return False, "too few stations to test whether one station decides the curve", rec
    if st["no_decision_flip"] and rec.get("decision_flip"):
        return False, "removing one station changes whether a valid curve can be built", rec
    shift = rec.get("max_param_change_iqr")
    if shift is not None and float(shift) > st["max_shift_iqr"]:
        # two decimals unless they would read as the limit itself
        digits = 2 if round(float(shift), 2) > st["max_shift_iqr"] else 3
        return False, (f"one station moves a quartile by {float(shift):.{digits}f} of the "
                       f"interquartile range, more than {st['max_shift_iqr']:.2f}"), rec
    return True, "", rec


# --------------------------------------------------------------------------- #
# ACC-05 and ACC-06, from the recovery evidence
# --------------------------------------------------------------------------- #
def _judge(rec: dict, st: dict) -> tuple[Optional[bool], str]:
    acc = (rec or {}).get("acceptance") or {}
    n = int(acc.get("n_cells") or rec.get("n_cells") or 0)
    if n < st["min_cells"]:
        return None, f"{n} evaluation regions, fewer than {st['min_cells']}"
    if acc.get("accepted") is True:
        return True, ""
    reasons = []
    if acc.get("classification_error") is False:
        reasons.append("the source's class calls did not agree with reference often enough")
    if acc.get("directional_bias") is False:
        reasons.append("the source promoted sites systematically")
    if acc.get("coverage") is False:
        reasons.append("the source's data did not reach the conditions of enough "
                       "evaluation regions")
    return False, " and ".join(reasons) or "it did not pass the recovery test"


def evidence_record(metric: str, option: str, validation: Optional[dict], *,
                    family: Optional[str] = None,
                    st: Optional[dict] = None) -> tuple[Optional[dict], str]:
    """The record that judges ``option`` for ``metric``, and whether it is the
    metric's own (``"metric"``) or its family's (``"family"``). ``(None, "")``
    when neither covers enough evaluation regions."""
    st = st or settings()
    basis = OPTION_BASIS.get(option, option)
    table = validation or {}
    own = (table.get(metric) or {}).get(basis)
    if own and _judge(own, st)[0] is not None:
        return own, "metric"
    if st["evidence_fallback"] == "family" and family:
        fam = ((table.get(FAMILIES_KEY) or {}).get(family) or {}).get(basis)
        if fam and _judge(fam, st)[0] is not None:
            return fam, "family"
    return None, ""


def evidence(metric: str, option: str, validation: Optional[dict], *,
             family: Optional[str] = None, st: Optional[dict] = None) -> tuple[bool, str]:
    """ACC-05 and ACC-06 for one metric and source option.

    The metric's own evidence decides when it covers enough evaluation regions;
    otherwise its family's pooled evidence does. No evidence at all is a refusal:
    a source is used on a positive finding, never on the absence of one.
    """
    st = st or settings()
    basis = OPTION_BASIS.get(option, option)
    table = validation or {}
    own = (table.get(metric) or {}).get(basis)
    if own:
        ok, why = _judge(own, st)
        if ok is not None:
            return ok, ("" if ok else f"In the recovery test {why}.")
    if st["evidence_fallback"] == "family" and family:
        fam = ((table.get(FAMILIES_KEY) or {}).get(family) or {}).get(basis)
        if fam:
            ok, why = _judge(fam, st)
            if ok is not None:
                return ok, ("" if ok else
                            f"In the recovery test for its metric family {why}.")
    return False, "The recovery test holds no evidence for this source and metric."


#: how each national option's domain measure reads in a refusal
_TRANSPORT_WORDS = {
    "distance_median": ("The matched donors lie a median distance of {m:.2f} from this "
                        "ecoregion's streams on their natural setting, further than in any "
                        "region where matched donors passed the recovery test (at most "
                        "{b:.2f})."),
    "n_fit": ("Only {m:.0f} donors fall inside this ecoregion's comparability envelope, fewer "
              "than in any region where envelope donors passed the recovery test (at least "
              "{b:.0f})."),
}


def transport_ok(metric: str, option: str, validation: Optional[dict], *,
                 family: Optional[str] = None, measure: Optional[float] = None,
                 st: Optional[dict] = None) -> tuple[bool, str]:
    """ACC-03 for a national option: the target must sit inside the range of the
    source's own domain measure that its passing evaluation regions spanned. A
    source that passed only where donors were close, or plentiful, has not been
    shown to work where they are not."""
    rec, _ = evidence_record(metric, option, validation, family=family, st=st)
    tr = (rec or {}).get("transport") or {}
    bound, better = tr.get("bound"), tr.get("better")
    if bound is None:
        return True, ""
    if measure is None or measure != measure:
        return False, ("This ecoregion could not be placed on the conditions the recovery "
                       "test covered, so the source is not assumed to apply.")
    m, b = float(measure), float(bound)
    outside = (m < b - 1e-9) if better == "min" else (m > b + 1e-9)
    if not outside:
        return True, ""
    text = _TRANSPORT_WORDS.get(str(tr.get("measure")))
    return False, (text.format(m=m, b=b) if text else
                   "This ecoregion lies outside the conditions the recovery test covered.")


def validation_record(metric: str, option: str, validation: Optional[dict], *,
                      family: Optional[str] = None, st: Optional[dict] = None) -> dict:
    """The recovery verdict a source option was judged by (ACC-05 and ACC-06), as
    the flagged-transfer rung records it on a decision (``transfer_validation``,
    REF-16): the evidence key (``basis``), whose record decided (``evidence``:
    ``metric``, ``family`` or ``none``), the verdict word, ``a1_share``,
    ``net_opt`` and ``n_cells`` of that record, the thresholds the test requires,
    whether it accepted, and the words the first pass refused it with."""
    st = st or settings()
    basis = OPTION_BASIS.get(option, option)
    table = validation or {}
    rec, source = None, "none"
    own = (table.get(metric) or {}).get(basis)
    if own:
        rec, source = own, "metric"
        if _judge(own, st)[0] is None and st["evidence_fallback"] == "family" and family:
            fam = ((table.get(FAMILIES_KEY) or {}).get(family) or {}).get(basis)
            if fam:
                rec, source = fam, "family"
    elif st["evidence_fallback"] == "family" and family:
        fam = ((table.get(FAMILIES_KEY) or {}).get(family) or {}).get(basis)
        if fam:
            rec, source = fam, "family"
    ok, why = evidence(metric, option, validation, family=family, st=st)
    rec = rec or {}
    acc = rec.get("acceptance") or {}
    out = {"basis": basis, "evidence": source, "accepted": bool(ok), "why": why,
           "min_cells": int(st["min_cells"]), "min_share": round(float(st["min_share"]), 4),
           "max_net_optimism": float(st["max_net_optimism"])}
    for key in ("verdict", "a1_share", "net_opt", "n_cells", "c1_share"):
        value = rec.get(key)
        if value is None:
            value = acc.get(key)
        out[key] = _plain(value)
    return out


def _plain(value):
    """A JSON-plain scalar: numpy numbers to Python, NaN to None."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    if f != f:
        return None
    if isinstance(value, float):
        return f
    return int(f) if f.is_integer() else f


def transfer_limitation(validation: Optional[dict], cap: Optional[int] = None,
                        st: Optional[dict] = None) -> str:
    """The limitation a flagged transfer carries (REF-16), in the words DEEP and
    the calculator print beside the metric: what the recovery test found against
    what it requires, and that the confidence is capped. Plain ASCII."""
    st = st or settings()
    v = validation or {}
    a1, n, net = v.get("a1_share"), v.get("n_cells"), v.get("net_opt")
    min_cells = int(v.get("min_cells") or st["min_cells"])
    max_net = float(v.get("max_net_optimism") or st["max_net_optimism"])
    if a1 is None or a1 != a1:
        found = ("no recovery evidence for this source and metric; class agreement in two "
                 "thirds of evaluation regions required")
    else:
        found = f"class agreement {float(a1):.2f} of evaluation regions; two thirds required"
        if n is not None and int(n) < min_cells:
            found += f", over {int(n)} evaluation regions where {min_cells} are required"
        if net is not None and net == net and float(net) > max_net:
            found += f"; net optimism {float(net):+.2f}, at most {max_net:.2f} allowed"
    tail = f"; confidence capped at {int(cap)}" if cap is not None else "; confidence capped"
    return ("Scored on a reference pool whose transfer to this ecoregion was not confirmed by "
            f"the recovery test ({found}){tail}.")


def pool_acceptor(metric: str, entry: dict, validation: Optional[dict], *,
                  family: Optional[str] = None
                  ) -> Callable[[str, pd.Series], tuple[bool, str, dict]]:
    """The ``accept`` callable ``reference_pool.choose_pool`` takes: stability for
    every pool, and the recovery evidence for every pool but the local one.

    Returns ``(ok, why, detail)``: ``why`` is the first refusal, in words, and
    ``detail`` carries every criterion's verdict (``checks``: ACC-04, then
    ACC-05/06 for a pool that is not the local reference) and the recovery record
    (``validation``, :func:`validation_record`), which the flagged-transfer rung
    reads to take an option refused by the evidence alone (REF-16)."""
    st = settings()

    def accept(option: str, values: pd.Series) -> tuple[bool, str, dict]:
        ok, why, _ = stability(values, entry, st)
        checks = [{"check": "ACC-04", "pass": bool(ok),
                   "why": "" if ok else f"The pool is not stable, since {why}."}]
        detail: dict = {"checks": checks, "validation": None}
        if not ok:
            return False, checks[0]["why"], detail
        if option == "local":
            return True, "", detail
        ev_ok, ev_why = evidence(metric, option, validation, family=family, st=st)
        checks.append({"check": "ACC-05/06", "pass": bool(ev_ok), "why": ev_why})
        detail["validation"] = validation_record(metric, option, validation, family=family, st=st)
        return bool(ev_ok), ("" if ev_ok else ev_why), detail

    return accept


def flagged_settings() -> dict:
    """REF-16's governed settings (``methodology.flagged_transfer``)."""
    return methodology.flagged_transfer()


def evidence_only_refusal(detail: Optional[dict]) -> bool:
    """Whether an acceptor's refusal rests on the recovery evidence alone (ACC-05
    and ACC-06), every other criterion having passed: the condition under which
    the flagged-transfer rung may take the option (REF-16)."""
    checks = list((detail or {}).get("checks") or [])
    if not checks:
        return False
    failed = [c for c in checks if not c.get("pass")]
    return bool(failed) and all(str(c.get("check")) == "ACC-05/06" for c in failed)


# --------------------------------------------------------------------------- #
# the curve checks (methodology 0.16, owner decision D13)
# --------------------------------------------------------------------------- #
#: the engine statuses of a curve that is a fallback or has no usable shape
DEGENERATE_STATUSES = ("degenerate_q25", "degenerate_curve")
#: the check id the refusal is recorded under: it replaces the CURVE-07 review a
#: fallback curve used to be held for
CURVE_CHECK = "CURVE-07"


def curve_checks(metric: str, entry: dict, values: Any, pressure_values: Any = None
                 ) -> tuple[bool, str, dict]:
    """Whether the curve a source option would build is one the methodology
    publishes (owner decision D13, methodology 0.16).

    Refused with the reason stated: a curve the engine can only draw as its
    degenerate fallback (a non-positive lower quartile on a scale that cannot go
    negative, or no interquartile spread), a pool below the engine's hard floor,
    and, where in-frame pressured stations of the option's geography carry the
    metric (``pressure_values``), a curve the discrimination check (CURVE-12)
    reads as inverted. Returns ``(ok, why, record)``; the record carries the
    engine status and the CURVE-12 numbers where the check ran, so the decision
    can state them."""
    from . import curve_stability as cs
    from . import discrimination as dz
    vals = cs._clean(values)
    pts, status = cs._build_points(vals, entry)
    record: dict = {"curve_status": status}
    if status == "degenerate_q25":
        return False, ("The curve on this pool would be the engine's fallback ramp: the pool's "
                       "lower quartile sits at or below zero on a scale that cannot go negative "
                       "(curve status degenerate_q25), and a fallback curve is refused under "
                       "methodology 0.16 (owner decision D13)."), record
    if status == "degenerate_curve" or pts is None:
        return False, ("The curve on this pool has no usable shape (curve status "
                       f"{status or 'unknown'}: the pool's interquartile range is zero or undefined), "
                       "so no valid curve can be built from it and the option is refused "
                       "(owner decision D13)."), record
    if pressure_values is not None:
        pres = cs._clean(pressure_values)
        if len(pres) >= dz.MIN_GROUP:
            disc = dz.curve_discrimination(metric, entry, pts, vals, pres)
            record["curve12"] = {k: disc.get(k) for k in
                                 ("aucRefVsPressure", "nRef", "nPressure", "medianRefIndex",
                                  "medianPressureIndex", "verdict")}
            if disc.get("verdict") == dz.VERDICT_INVERTED:
                auc = disc.get("aucRefVsPressure")
                return False, ("The curve on this pool would rank pressured stations above "
                               "reference stations (discrimination check inverted, area under the "
                               f"curve {float(auc):.2f} over {disc.get('nRef')} reference and "
                               f"{disc.get('nPressure')} pressured stations), so it is refused under "
                               "methodology 0.16 (owner decision D13)."), record
    return True, "", record


def curve_checker(metric: str, entry: dict) -> Callable[[str, Any, Optional[dict]], tuple[bool, str, dict]]:
    """The ``curve_check`` callable ``reference_pool.choose_pool`` takes:
    ``(option, pool_values, context) -> (ok, why, record)``, ``context`` carrying
    the option's ``pressure_values`` (in-frame stations of the same geography that
    fail the relaxed screen) when the caller has them."""

    def check(option: str, values: Any, context: Optional[dict] = None) -> tuple[bool, str, dict]:
        return curve_checks(metric, entry, values, (context or {}).get("pressure_values"))

    return check


def all_checks(metric: str, option: str, values: Any, entry: dict,
               validation: Optional[dict], *, family: Optional[str] = None,
               measure: Optional[float] = None, st: Optional[dict] = None,
               n: Optional[int] = None) -> list[dict]:
    """Every acceptance criterion a source option faces, each run to its verdict
    rather than stopping at the first refusal: ``[{check, pass, why}]``. For a
    source the owner accepted over the build's refusal (REF-15), so the record
    names every check it fails. ``n``: the independent stations or donors the
    sample floor counts, where ``values`` repeat a donor (matched donors are
    drawn per target stream); the values themselves otherwise."""
    st = st or settings()
    vals = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    out: list[dict] = []
    ok, why = sample_ok(len(vals) if n is None else int(n), st)
    out.append({"check": "ACC-01", "pass": ok, "why": why})
    ok, why, _ = stability(vals, entry, st)
    out.append({"check": "ACC-04", "pass": ok,
                "why": "" if ok else f"The pool is not stable, since {why}."})
    if option != "local":
        ok, why = evidence(metric, option, validation, family=family, st=st)
        out.append({"check": "ACC-05/06", "pass": ok, "why": why})
    # methodology 0.16 (D13): a fallback curve is a refusal, recorded like any
    # other failed check on a source the owner accepted anyway (the build still
    # refuses to publish a fallback ramp: _row_from_values returns no row for it)
    if len(vals) >= 5:
        ok, why, _ = curve_checks(metric, entry, vals)
        out.append({"check": CURVE_CHECK, "pass": ok, "why": why})
    if option in ("3c_matched", "3a_envelope"):
        ok, why = transport_ok(metric, option, validation, family=family, measure=measure, st=st)
        out.append({"check": "ACC-03", "pass": ok, "why": why})
    return out


def acceptance_record(verdict: dict, st: Optional[dict] = None) -> dict:
    """The ``acceptance`` block a verdict row carries in basis_validation.yaml:
    each criterion's result beside the containment diagnostic."""
    st = st or settings()
    n = int(verdict.get("n_cells") or 0)
    a1 = verdict.get("a1_share")
    net = verdict.get("net_opt")
    c3 = verdict.get("c3_share")
    kind_model = verdict.get("kind") == "model"
    out = {
        "n_cells": n,
        # shares are recorded to 4 decimals, so two thirds reads 0.6667; the
        # nearest share below two thirds over 200 cells still reads 0.6650
        "classification_error": None if a1 is None or a1 != a1 else bool(float(a1) >= st["min_share"] - 1e-4),
        "directional_bias": None if net is None or net != net else bool(float(net) <= st["max_net_optimism"]),
        "anchor_recovery_diagnostic": verdict.get("c1_share"),
    }
    if kind_model:
        out["coverage"] = None if c3 is None or c3 != c3 else bool(float(c3) >= st["min_share"] - 1e-4)
    out["accepted"] = bool(n >= st["min_cells"] and out["classification_error"]
                           and out["directional_bias"] and out.get("coverage", True) is not False
                           and str(verdict.get("verdict") or "") not in ("not quantified",
                                                                         "not evaluated"))
    return out
