"""Borrowing least-disturbed streams from elsewhere, three ways (Pre-registration II).

Round one moved a region's reference condition across the country by taking the
raw quartiles of whichever national least-disturbed streams passed a comparability
envelope. That left the reviewer's question untested: whether a borrowed condition
transfers reliably to the region it is borrowed for. Two things can make it
transfer better, and this module builds both beside the round-one basis:

* **Adjustment.** Streams elsewhere differ in natural setting even when they pass
  the envelope. :func:`adjusted_model` fits the reference expectation as a function
  of natural setting on every national least-disturbed stream, and the anchors come
  from predicting it for the target's own streams (basis 3b).
* **Distance.** An envelope is pass or fail, and it leaves a region like the
  Eastern Corn Belt Plains with no chemistry donors at all. :func:`gower_matches`
  ranks every national least-disturbed stream by its distance from each of the
  target's streams and borrows the nearest, at a distance that is reported rather
  than hidden (basis 3c).

:func:`envelope_donors` is round one's basis (3a), kept for the comparison, and
:func:`envelope_failures` says which envelope condition keeps donors out, each
condition counted on its own. ``reference_pool.comparable_mask`` records only the
first failing condition, and it checks lithology last, so its reasons understate
lithology.

:func:`nrsa_tp_points` builds the published NRSA Table 7-1 phosphorus criterion
for a NARS-9 region as a curve, by the same construction the fixed criteria use
(basis 4), and :func:`michigan_tp_points` reads the Michigan SQT layer DEEP already
carries for the Eastern Corn Belt Plains, for the cross-check.

Every basis here excludes the target region's own stations from what it borrows
when it is being tested, because a low overlap is not the same as an independent
test. Pure: frames in, records out, except the two readers of published criteria.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import basis_recovery as br
from . import reference_pool as rp

#: Nearest donors taken per target station in 3c. Fixed in Pre-registration II:
#: small enough that the matches stay close, large enough that one odd donor does
#: not stand in for a stream on its own. Not tuned.
GOWER_K = 3
#: The fewest distinct donors a population basis may rest on (DATA-05).
MIN_DONORS = 10
NRSA_METHOD, NRSA_INPUT = "regional-nutrient-condition", "tp"
#: NRSA reports TP in mg/L and StreamCurves' chem_PTL in ug/L.
MG_TO_UG = 1000.0
#: Half a reporting step is how the fixed criteria place an anchor when the better
#: class owns the boundary. Table 7-1 gives TP to 0.1 ug/L.
TP_RESOLUTION_UG = 0.1
MICHIGAN_BUNDLE = (Path(__file__).resolve().parents[2] / "deep" / "data" / "bundles"
                   / "mi-sqt-adapted.deep.json")
MICHIGAN_TP_METRIC = "nutrient-cycling-total-phosphorus"
MICHIGAN_ECBP_LAYER = "Huron Erie Lake Plains & Eastern Corn Belt Plains"


def national_reference(frame: pd.DataFrame, *, exclude_l3: Optional[str] = None) -> pd.DataFrame:
    """Every in-frame station that passes the strict screen, less one region."""
    ref = frame[frame["pass_strict"].astype(bool)]
    if exclude_l3 is not None:
        ref = ref[ref["l3"].astype(str) != str(exclude_l3)]
    return ref


# --------------------------------------------------------------------------- #
# 3a: round one's envelope donors, and what keeps donors out
# --------------------------------------------------------------------------- #
def envelope_donors(metric: str, target_rows: pd.DataFrame, donors_frame: pd.DataFrame,
                    values: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """Round one's Option 3: donors inside the target's comparability envelope for
    the metric's family, with a value for the metric. ``(donor rows, values)``."""
    profile = rp.family_profile(metric)
    empty = (donors_frame.iloc[0:0], pd.Series(dtype=float))
    if not profile:
        return empty
    env = rp.envelope_for(target_rows, profile.get("covariates") or [])
    liths = rp.target_lith_groups(target_rows)
    ok, _ = rp.comparable_mask(donors_frame, env, liths,
                               use_lithology=bool(profile.get("lithology")))
    donors = donors_frame[ok]
    got = pd.to_numeric(values.reindex(donors.index), errors="coerce").dropna()
    return donors.loc[got.index], got


def envelope_failures(metric: str, target_rows: pd.DataFrame,
                      donors_frame: pd.DataFrame) -> dict:
    """How many donors each envelope condition excludes, each counted on its own.

    For every covariate of the metric's family, and for lithology where the family
    uses it: the donors that fail that condition at all, and the donors that fail
    it and nothing else, so a condition that alone blocks the transfer is visible.
    """
    profile = rp.family_profile(metric) or {}
    cfg = rp.load_transfer_config()
    covs = list(profile.get("covariates") or [])
    env = rp.envelope_for(target_rows, covs)
    fails: dict[str, pd.Series] = {}
    for name in covs:
        lo, hi, n = env[name]
        if not n or lo != lo or hi != hi:
            continue
        v = rp._transform(donors_frame[name], name, cfg) if name in donors_frame.columns \
            else pd.Series(np.nan, index=donors_frame.index)
        fails[name] = v.isna() | (v < lo) | (v > hi)
    liths = rp.target_lith_groups(target_rows)
    if profile.get("lithology") and liths and "lith_group" in donors_frame.columns:
        g = donors_frame["lith_group"].astype(object)
        fails["lithology"] = g.isna() | ~g.isin(set(liths) | {"mixed"})
    out: dict[str, Any] = {"n_donors": int(len(donors_frame)), "family": rp.family_of(metric),
                           "lith_groups": liths}
    any_fail = pd.Series(False, index=donors_frame.index)
    for name, mask in fails.items():
        others = pd.Series(False, index=donors_frame.index)
        for other, m in fails.items():
            if other != name:
                others |= m
        out[f"fail_{name}"] = int(mask.sum())
        out[f"only_{name}"] = int((mask & ~others).sum())
        any_fail |= mask
    out["pass_all"] = int((~any_fail).sum())
    for name in covs:
        lo, hi, _ = env.get(name, (np.nan, np.nan, 0))
        out[f"env_{name}"] = None if lo != lo else [round(lo, 4), round(hi, 4)]
    return out


def envelope_position(target_rows: pd.DataFrame, donors_frame: pd.DataFrame,
                      covariate: str) -> dict:
    """Is a target's envelope on one covariate narrow, or shifted?

    Narrow means the target spans a thin slice of what donors span, excluding donors
    that are otherwise alike; shifted means the target sits in a tail of the donor
    distribution, so the donors are genuinely unlike it. The width ratio compares
    the target's 2.5 to 97.5 percent span with the donors' on the comparison scale,
    and the percentiles place the target's median and extremes in the donors'.
    """
    cfg = rp.load_transfer_config()
    t = rp._transform(target_rows[covariate], covariate, cfg).dropna()
    d = rp._transform(donors_frame[covariate], covariate, cfg).dropna()
    if len(t) < 5 or len(d) < 5:
        return {}
    t_lo, t_hi = np.quantile(t, [0.025, 0.975])
    d_lo, d_hi = np.quantile(d, [0.025, 0.975])

    def pct(x: float) -> float:
        return round(float((d <= x).mean()), 4)

    return {"covariate": covariate,
            "width_ratio": round(float((t_hi - t_lo) / (d_hi - d_lo)), 4) if d_hi > d_lo else None,
            "target_median_pct": pct(float(t.median())), "target_lo_pct": pct(float(t_lo)),
            "target_hi_pct": pct(float(t_hi))}


# --------------------------------------------------------------------------- #
# 3b: the reference expectation, adjusted for natural setting
# --------------------------------------------------------------------------- #
def adjusted_model(donors_frame: pd.DataFrame, values: pd.Series, natural: list[str], *,
                   kind: str, offset: float) -> Optional[dict]:
    """The reference expectation fitted on least-disturbed streams as a linear
    function of natural setting, with no pressure terms.

    Exactly round two's ``linear`` stressor-response specification fitted on
    reference streams only, so 3b and ``1B_linear`` differ in one thing: whether
    disturbed streams enter the fit.
    """
    y = pd.to_numeric(values.reindex(donors_frame.index), errors="coerce")
    keep = y.notna().to_numpy()
    rows = donors_frame[keep]
    return br.fit_model(rows, y[keep].to_numpy(), natural, spec="linear", kind=kind,
                        offset=offset, pressures=False, groups=rows["l3"].astype(str))


# --------------------------------------------------------------------------- #
# 3c: the nearest comparable streams, by Gower distance
# --------------------------------------------------------------------------- #
def gower_matches(metric: str, target_rows: pd.DataFrame, donors_frame: pd.DataFrame,
                  values: pd.Series, *, k: int = GOWER_K) -> pd.DataFrame:
    """For each target station, its ``k`` nearest donors with a value, with
    replacement across target stations.

    Gower distance on the metric family's covariates: each continuous covariate on
    the envelope's comparison scale, divided by its range over the donors, and
    ``lith_group`` as one categorical term (0 when equal, 1 when not) where the
    family uses lithology; the mean over the terms. A station missing any term is
    not matched. Ties go to the donor that comes first, so a rerun matches the
    same streams.
    """
    cols = ["target_ix", "target_huc12", "donor_ix", "donor_l3", "distance", "value"]
    profile = rp.family_profile(metric)
    if not profile:
        return pd.DataFrame(columns=cols)
    cfg = rp.load_transfer_config()
    covs = [c for c in (profile.get("covariates") or [])
            if c in target_rows.columns and c in donors_frame.columns]
    use_lith = bool(profile.get("lithology")) and "lith_group" in donors_frame.columns \
        and "lith_group" in target_rows.columns
    y = pd.to_numeric(values.reindex(donors_frame.index), errors="coerce")
    donors = donors_frame[y.notna().to_numpy()]
    if not covs or not len(donors):
        return pd.DataFrame(columns=cols)
    dmat = np.column_stack([rp._transform(donors[c], c, cfg).to_numpy(dtype=float) for c in covs])
    tmat = np.column_stack([rp._transform(target_rows[c], c, cfg).to_numpy(dtype=float)
                            for c in covs])
    span = np.nanmax(dmat, axis=0) - np.nanmin(dmat, axis=0)
    span = np.where(span > 0, span, 1.0)
    d_ok = ~np.isnan(dmat).any(axis=1)
    t_ok = ~np.isnan(tmat).any(axis=1)
    if use_lith:
        d_lith = donors["lith_group"].astype(object).to_numpy()
        t_lith = target_rows["lith_group"].astype(object).to_numpy()
        d_ok &= pd.notna(d_lith)
        t_ok &= pd.notna(t_lith)
    donors, dmat = donors[d_ok], dmat[d_ok]
    if use_lith:
        d_lith = d_lith[d_ok]
    dvals = y.reindex(donors.index).to_numpy(dtype=float)
    d_l3 = donors["l3"].astype(str).to_numpy()
    n_terms = len(covs) + (1 if use_lith else 0)
    out = []
    t_index = target_rows.index.to_numpy()
    t_huc = (target_rows["huc12"].astype(object).to_numpy() if "huc12" in target_rows.columns
             else np.full(len(target_rows), None, dtype=object))
    for i in np.flatnonzero(t_ok):
        dist = (np.abs(dmat - tmat[i]) / span).sum(axis=1)
        if use_lith:
            dist = dist + (d_lith != t_lith[i]).astype(float)
        dist = dist / n_terms
        for j in np.argsort(dist, kind="stable")[:int(k)]:
            out.append({"target_ix": t_index[i], "target_huc12": t_huc[i],
                        "donor_ix": donors.index[j], "donor_l3": d_l3[j],
                        "distance": round(float(dist[j]), 5), "value": float(dvals[j])})
    return pd.DataFrame(out, columns=cols)


def matched_summary(matches: pd.DataFrame) -> dict:
    """What a 3c pool rests on: its anchors, distinct donors, and how far away."""
    if matches is None or not len(matches):
        return {"anchors": None, "n_distinct": 0, "distance_median": None, "distance_max": None}
    return {"anchors": br.anchors_of(matches["value"]),
            "n_distinct": int(matches["donor_ix"].nunique()),
            "n_donor_l3": int(matches["donor_l3"].nunique()),
            "distance_median": round(float(matches["distance"].median()), 4),
            "distance_max": round(float(matches["distance"].max()), 4)}


# --------------------------------------------------------------------------- #
# 4: published criteria
# --------------------------------------------------------------------------- #
def _catalog() -> dict:
    from . import fixed_criteria as fc
    return fc._vendored_catalog()


def nrsa_tp_bands(nars9: str) -> Optional[tuple[float, float]]:
    """NRSA Table 7-1 total phosphorus (good/fair, fair/poor) for a NARS-9 region,
    in ug/L, from the vendored EASI catalog."""
    from . import fixed_criteria as fc
    method = fc._method(_catalog(), NRSA_METHOD)
    tp = next((i for i in method.get("inputs") or [] if i.get("key") == NRSA_INPUT), None)
    pair = ((tp or {}).get("regionalBands") or {}).get(str(nars9))
    if not pair:
        return None
    return float(pair[0]) * MG_TO_UG, float(pair[1]) * MG_TO_UG


def nrsa_tp_points(nars9: str) -> Optional[list[dict]]:
    """The Table 7-1 criterion for one NARS-9 region as a DEEP curve.

    Built exactly as ``fixed_criteria`` builds a published criterion: the bands
    EASI applies (Good at or below good/fair, Poor at or above fair/poor), anchored
    at index 0.69 and 0.39 with the half-step shift where the better class owns the
    boundary, 1.0 at zero, and the Fair segment extended to zero.
    """
    from . import fixed_criteria as fc
    from ._vendor.easi import screening_methods as sm
    method = fc._method(_catalog(), NRSA_METHOD)
    tp = next((i for i in method.get("inputs") or [] if i.get("key") == NRSA_INPUT), None)
    bands = sm.regional_bands(tp, str(nars9)) if tp else None
    if not bands:
        return None
    scaled = [{**b, "min": None if b.get("min") is None else float(b["min"]) * MG_TO_UG,
               "max": None if b.get("max") is None else float(b["max"]) * MG_TO_UG}
              for b in bands]
    entry = {**fc._anchors(scaled, TP_RESOLUTION_UG), "domain": [0.0, None]}
    pts = fc._points(entry, {"Good": 0.85, "Fair": 0.545, "Poor": 0.195})
    return [{"x": float(x), "y": float(y)} for x, y in pts]


def michigan_tp_points(path: Optional[Path] = None,
                       layer: str = MICHIGAN_ECBP_LAYER) -> Optional[list[dict]]:
    """The Michigan SQT total phosphorus curve for the Huron Erie Lake Plains and
    Eastern Corn Belt Plains layer, as DEEP carries it (ug/L)."""
    p = Path(path) if path else MICHIGAN_BUNDLE
    if not p.exists():
        return None
    bundle = json.loads(p.read_text(encoding="utf-8"))
    for fn in bundle.get("metricsByFunction") or []:
        for m in fn.get("metrics") or []:
            if m.get("metricId") != MICHIGAN_TP_METRIC:
                continue
            for lyr in m.get("curveLayers") or []:
                if str(lyr.get("stratum") or "").startswith(layer):
                    return [{"x": float(q["x"]), "y": float(q["y"])}
                            for q in lyr.get("points") or []]
    return None


def majority_nars9(rows: pd.DataFrame) -> tuple[Optional[str], float]:
    """A region's NARS-9 region and the share of its stations in it. Six Level III
    regions straddle two; a curve per cell takes the majority and reports the
    share."""
    if "nars9" not in rows.columns:
        return None, 0.0
    s = rows["nars9"].dropna().astype(str)
    if not len(s):
        return None, 0.0
    counts = s.value_counts()
    return str(counts.index[0]), round(float(counts.iloc[0] / len(s)), 4)
