"""A reference expectation for an ecoregion that holds no reference stream.

Rule REF-09 (methodology 0.13, owner decision 2026-09-21).

The Eastern Corn Belt Plains has no least-disturbed stream. Its cleanest station
carries 30.7 percent agricultural watershed cover, against a national reference
median of zero, so no percentile of its own observations estimates reference
condition and no comparable donor pool exists for its chemistry. What can still
be estimated is the *response* to landscape pressure, nationally, together with
the ecoregion's own level, which its 50 disturbed stations do measure.

That is what this module builds: a stressor-response model fitted across the
national frame with an ecoregion random intercept, evaluated at the national
reference pressure vector, with the target's own intercept added back. The
specification is `mixed_local` from the round-two harness, and it may only be
used for a metric that reached **validated** under the pre-registration, which
`validated_for` enforces.

The curve itself is not special-cased. `curves.build_reference_curve` is
population-agnostic, so a basis is only ever a fitting population; this module
produces the population (the model's pooled predictive draws for the target's
own streams) and the ordinary curve builder does the rest. A modelled curve and
a station-pool curve therefore share every line of curve geometry, and differ
only in where their values came from.

Pure: frames in, frames and records out. No network, no file writes.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from . import basis_recovery as br
from . import curve_basis
from . import reference_pool as rp

#: the natural-setting covariates the validated specification was fitted on.
#: Fixed here because a curve built on a different set is not the thing that
#: was validated.
NATURAL = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]
#: the one specification that reached validated under Pre-registration II
SPEC = "mixed_local"
#: residual draws per target station, as pre-registered
N_RESID = 200
#: bootstrap replicates for the anchor interval
N_BOOT = 20


def training_frame(frame: pd.DataFrame, target_l3: str) -> pd.DataFrame:
    """Every station the model may learn from for ``target_l3``.

    Every other ecoregion contributes all of its stations. The target
    contributes only its **non-reference** stations, which is what estimates its
    level without letting its own reference answer leak into the prediction it
    is meant to stand in for.

    This is the harness's rule with the held-out evaluation regions put back.
    The hold-out existed to measure the specification; once it has been
    measured, withholding half the country from the fit would only make the
    published curve worse than the one that was validated.
    """
    l3 = frame["l3"].astype(str)
    code = str(target_l3)
    local = frame[l3 == code]
    return pd.concat([frame[l3 != code], local[~local["pass_strict"].astype(bool)]])


def validated_for(validation: Optional[dict], metric: str) -> bool:
    """Whether the pre-registered evidence admits ``metric`` on this rung.

    ``validation`` is the verdict table of a basis run, keyed by metric. Only a
    **validated** verdict admits. Promising is documented and held, exactly as
    Pre-registration II states, so a promising metric returns False here and
    falls through to the next rung.
    """
    entry = (validation or {}).get(metric)
    if not isinstance(entry, dict):
        return False
    return str(entry.get("verdict") or "").strip().lower() == "validated"


def modeled_population(metric: str, *, frame: pd.DataFrame, values: pd.Series,
                       target_l3: str, target_vector: Optional[dict] = None,
                       spec: str = SPEC, seed: int = 11,
                       n_resid: int = N_RESID) -> dict:
    """The reference population a fitted model implies for ``target_l3``.

    ``values`` is the metric's value per station, positional with ``frame``.
    Returns the pooled predictive sample together with everything a reader needs
    to judge it: the fit size, the target's own intercept, and how far the
    prediction point sits from the region's cleanest stream on every pressure.

    The sample is each target station's fitted value plus ``n_resid`` draws from
    the training residuals centred within their own ecoregion, back-transformed
    and pooled. It describes streams rather than conditional means, which is
    what a reference quartile has to describe.
    """
    code = str(target_l3)
    l3 = frame["l3"].astype(str)
    region = frame[l3 == code]
    tvec = target_vector if target_vector is not None else br.reference_pressure_vector(frame)
    kind = br.transform_for(metric)
    offset = br.log_offset(values)

    train = training_frame(frame, code)
    y = values.reindex(train.index)
    keep = y.notna().to_numpy()
    train = train[keep]
    model = br.fit_model(train, y[keep].to_numpy(), NATURAL, spec=spec, kind=kind,
                         offset=offset, groups=train["l3"].astype(str))

    out: dict[str, Any] = {
        "metric": metric, "l3": code, "spec": spec, "transform": kind,
        "n_train": 0, "n_local_train": int((l3 == code).sum() and len(
            region[~region["pass_strict"].astype(bool)])),
        "n_target": int(len(region)), "blup": None, "values": None, "anchors": None,
        "interval": None, "extrapolation_ok": None, "gap": {},
    }
    if model is None or not len(region):
        return out

    rng = np.random.default_rng(br.cell_seed(metric, code, spec, "production", seed=seed))
    p = br.predict_model(model, region, tvec, group=code)
    out["n_train"] = int(model["n_train"])
    out["blup"] = p["blup"]
    if not p["n"]:
        return out

    resid = model.get("resid")
    if resid is None:
        sample = br.from_scale(p["mid"], kind)
    else:
        draws = rng.choice(np.asarray(resid, dtype=float), size=int(n_resid), replace=True)
        sample = br.from_scale(np.add.outer(p["mid"], draws).ravel(), kind)

    out["values"] = pd.Series(np.asarray(sample, dtype=float)).dropna().reset_index(drop=True)
    out["anchors"] = br.anchors_of(out["values"])
    out["extrapolation_ok"] = br.extrapolation_share(train, region, NATURAL)
    out["gap"] = br.disturbance_gap(region, tvec)
    return out


def anchor_interval(metric: str, *, frame: pd.DataFrame, values: pd.Series, target_l3: str,
                    target_vector: Optional[dict] = None, spec: str = SPEC, seed: int = 11,
                    n_boot: int = N_BOOT) -> Optional[dict]:
    """A cluster bootstrap on the modelled anchors, refitting inside the loop.

    CURVE-06 asks every candidate to state an interval. A modelled basis states
    one by refitting on resampled HUC12 clusters of its own training data, so
    the interval carries the fit's uncertainty and not only the residual draw's.
    """
    code = str(target_l3)
    l3 = frame["l3"].astype(str)
    region = frame[l3 == code]
    tvec = target_vector if target_vector is not None else br.reference_pressure_vector(frame)
    kind = br.transform_for(metric)
    offset = br.log_offset(values)
    train = training_frame(frame, code)
    y = values.reindex(train.index)
    keep = y.notna().to_numpy()
    train, y = train[keep], y[keep]
    if not len(train) or not len(region):
        return None
    draw_rng = np.random.default_rng(br.cell_seed(metric, code, spec, "boot-draws", seed=seed))

    def refit(pos):
        rows = train.iloc[pos]
        m = br.fit_model(rows, y.iloc[pos].to_numpy(), NATURAL, spec=spec, kind=kind,
                         offset=offset, groups=rows["l3"].astype(str))
        return br.model_anchors(m, region, tvec, group=code, rng=draw_rng,
                                n_resid=N_RESID)["anchors"]

    return br.cluster_bootstrap(
        refit, train["huc12"], n_boot=n_boot,
        rng=np.random.default_rng(br.cell_seed(metric, code, spec, "boot", seed=seed)))


def pool_decision(metric: str, population: dict, *, family: Optional[str] = None,
                  region_name: Optional[str] = None,
                  supported_level: Optional[str] = None) -> rp.PoolDecision:
    """The modelled basis, stated in the same shape a station pool states itself.

    Counts that mean stations are reported as the stations that were actually
    used: ``n_pool`` is the fit, ``n_local`` the target's own stations that
    estimated its level. ``n_usable`` is deliberately the fit size and not the
    size of the synthetic sample, which is an artefact of the residual draw and
    would overstate the evidence by a factor of 200.
    """
    n_train = int(population.get("n_train") or 0)
    n_local = int(population.get("n_local_train") or 0)
    gap = population.get("gap") or {}
    ag = gap.get("gap_agriculture_ws")
    reach = (f" This ecoregion's least agricultural stream carries {ag:.1f} percentage points "
             f"more agricultural cover than that point, so the curve is an extrapolation."
             if ag else "")
    blup = population.get("blup")
    level = ("" if blup is None else
             f" This ecoregion's own level was estimated from {n_local:,} of its stations.")
    note = (f"No stream in this ecoregion is clean enough to observe reference condition. The "
            f"response to landscape pressure was fitted across {n_train:,} stations nationally "
            f"and evaluated at the median pressure of the national reference streams."
            f"{level}{reach}")
    return rp.PoolDecision(
        metric=metric, status=rp.STATUS_MODELED, level=None,
        region_code=str(population.get("l3") or ""), region_name=region_name,
        family=family, n_pool=n_train, n_comparable=n_train, n_usable=n_train,
        n_local=n_local, n_huc12=0,
        disposition="exploratory",
        supported_level=supported_level, transfer_risk=rp.RISK_NONE,
        transfer_note=note, station_ids=(), levels_tried=[],
        basis=curve_basis.MODELED)
