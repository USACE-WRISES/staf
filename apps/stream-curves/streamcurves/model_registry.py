"""The modeled-reference registry: rule REF-13 (methodology 0.14).

A modeled reference is used only from an approved specification in
``config/model_registry.yaml``, and only where the target sits inside the
limits that specification was validated over. This module reads the registry,
decides applicability for a target from the target's own data, and runs the
specification through the same code the recovery test scored
(``modeled_reference``), so a build never fits a model the registry does not
name and never extrapolates past where one was tested.

Applicability is computed, never listed. For every target it checks:

  - the target has its own stations to estimate its level (DATA-05 floor);
  - the natural setting of its streams is covered by the low-disturbance
    training stations at least as well as in the weakest cell the model passed;
  - on every pressure, its least-disturbed training station is no further from
    the national reference point than in any cell the model passed.

Candidate entries (a specification that passed a pre-registered test but that
the owner has not approved) are reported and never run.

Pure: frames in, records out. No network, no file writes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from . import basis_recovery as br
from . import modeled_reference as mr

APPROVED = "approved"
CANDIDATE = "candidate"
REGISTRY_FILE = "model_registry.yaml"

#: the pressure names a refusal is written in (user-visible: plain words)
PRESSURE_WORDS = {
    "pctimp2019ws": ("impervious cover", "percentage points"),
    "agriculture_ws": ("agricultural cover", "percentage points"),
    "rddensws": ("road density", "km per square km"),
    "dor": ("dam storage", "percent of mean flow"),
    "nabd_densws": ("dam density", "dams per square km"),
    "npdesdensws": ("permitted discharge density", "per square km"),
    "mines_ws": ("mining", "mines"),
}


def registry_path() -> Path:
    from .paths import CONFIG_DIR
    return CONFIG_DIR / REGISTRY_FILE


@lru_cache(maxsize=4)
def _load(path_text: str) -> dict:
    from .config import read_yaml
    p = Path(path_text)
    doc = (read_yaml(p) or {}) if p.exists() else {}
    return {"version": doc.get("version"),
            "procedures": dict(doc.get("procedures") or {}),
            "requirements": dict(doc.get("requirements") or {}),
            "entries": [dict(e) for e in doc.get("entries") or []]}


def load(path: Optional[Path] = None) -> dict:
    """The registry. An absent file is an empty registry: nothing is modeled."""
    return _load(str(path or registry_path()))


def registry_sha256(path: Optional[Path] = None) -> Optional[str]:
    import hashlib
    p = Path(path or registry_path())
    return ("sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()) if p.exists() else None


def entry_for(metric: str, registry: Optional[dict] = None) -> Optional[dict]:
    """The metric's entry: an approved one when there is one, else a candidate."""
    reg = registry if registry is not None else load()
    mine = [e for e in reg["entries"] if str(e.get("metric")) == metric]
    approved = [e for e in mine if e.get("status") == APPROVED]
    return (approved or mine or [None])[0]


def procedure_of(entry: dict, registry: Optional[dict] = None) -> dict:
    reg = registry if registry is not None else load()
    return dict(reg["procedures"].get(str(entry.get("procedure"))) or {})


def _fmt(x: float) -> str:
    return f"{float(x):.2f}"


def applicability(entry: dict, *, frame: pd.DataFrame, values: pd.Series, target_l3: str,
                  registry: Optional[dict] = None) -> dict:
    """Whether the target lies inside the entry's validated limits.

    ``values`` is the metric's value per station, positional with ``frame``.
    Returns ``{"ok", "why", "coverage", "gaps", "n_local"}``; ``why`` is a
    sentence a DEEP reader can act on when ``ok`` is False.
    """
    reg = registry if registry is not None else load()
    proc = procedure_of(entry, reg)
    natural = list(proc.get("natural_covariates") or mr.NATURAL)
    limits = entry.get("limits") or {}
    need = int((reg.get("requirements") or {}).get("min_target_stations") or 10)
    code = str(target_l3)
    l3 = frame["l3"].astype(str)
    region = frame[l3 == code]
    local = region[~region["pass_strict"].astype(bool)]
    n_local = int(values.reindex(local.index).notna().sum())
    out: dict[str, Any] = {"ok": False, "why": "", "coverage": None, "gaps": {},
                           "n_local": n_local}
    if n_local < need:
        out["why"] = (f"Only {n_local} of this ecoregion's own streams carry the metric, fewer "
                      f"than the {need} a modeled reference needs to estimate the ecoregion's "
                      f"own level.")
        return out
    train = mr.training_frame(frame, code)
    train = train[values.reindex(train.index).notna().to_numpy()]
    cov = br.extrapolation_share(train, region, natural)
    out["coverage"] = None if cov is None else round(float(cov), 4)
    floor = limits.get("min_coverage_share")
    if cov is None or (floor is not None and float(cov) < float(floor) - 1e-9):
        share = "an unknown share" if cov is None else f"{float(cov):.0%}"
        out["why"] = (f"The low-disturbance training streams cover the natural setting of "
                      f"{share} of this ecoregion's streams, less than the "
                      f"{float(floor or 0):.0%} of the weakest region the model was validated "
                      f"in, so the prediction would extrapolate past where it was tested.")
        return out
    gaps = br.disturbance_gap(local, br.reference_pressure_vector(frame))
    out["gaps"] = {k.replace("gap_", ""): (None if v is None else round(float(v), 4))
                   for k, v in gaps.items()}
    for pressure, lim in (limits.get("max_disturbance_gap") or {}).items():
        got = out["gaps"].get(pressure)
        if got is None or lim is None:
            continue
        if float(got) > float(lim) + 1e-9:
            words, units = PRESSURE_WORDS.get(pressure, (pressure, ""))
            out["why"] = (f"This ecoregion's least-disturbed stream sits {_fmt(got)} {units} of "
                          f"{words} above the national reference point, and the model was "
                          f"validated only up to {_fmt(lim)}, so the prediction would "
                          f"extrapolate past where it was tested.").replace("  ", " ")
            return out
    out["ok"] = True
    return out


def run(entry: dict, *, frame: pd.DataFrame, values: pd.Series, target_l3: str,
        registry: Optional[dict] = None, seed: Optional[int] = None) -> dict:
    """Run an approved entry for one target: the modeled population and its
    refit interval, through the recovery test's own code."""
    reg = registry if registry is not None else load()
    proc = procedure_of(entry, reg)
    spec = str(entry.get("procedure") or mr.SPEC)
    seed = int(seed if seed is not None else proc.get("seed") or 11)
    metric = str(entry["metric"])
    pop = mr.modeled_population(metric, frame=frame, values=values, target_l3=target_l3,
                                spec=spec, seed=seed,
                                n_resid=int(proc.get("residual_draws") or mr.N_RESID))
    interval = None
    if pop.get("anchors"):
        interval = mr.anchor_interval(metric, frame=frame, values=values, target_l3=target_l3,
                                      spec=spec, seed=seed,
                                      n_boot=int(proc.get("bootstrap_refits") or mr.N_BOOT))
    return {"population": pop, "interval": interval}


def candidates(registry: Optional[dict] = None) -> list[dict]:
    """Entries that wait for the owner, for the report of what needs a person."""
    reg = registry if registry is not None else load()
    return [e for e in reg["entries"] if e.get("status") == CANDIDATE]
