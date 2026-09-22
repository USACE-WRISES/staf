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


def pool_acceptor(metric: str, entry: dict, validation: Optional[dict], *,
                  family: Optional[str] = None) -> Callable[[str, pd.Series], tuple[bool, str]]:
    """The ``accept`` callable ``reference_pool.choose_pool`` takes: stability for
    every pool, and the recovery evidence for every pool but the local one."""
    st = settings()

    def accept(option: str, values: pd.Series) -> tuple[bool, str]:
        ok, why, _ = stability(values, entry, st)
        if not ok:
            return False, f"The pool is not stable, since {why}."
        if option == "local":
            return True, ""
        return evidence(metric, option, validation, family=family, st=st)

    return accept


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
