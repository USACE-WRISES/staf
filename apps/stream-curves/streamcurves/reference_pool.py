"""Per-metric reference pools, with comparable borrowing from parent ecoregions.

Rules REF-05, REF-06 and REF-07 (methodology 0.12, owner decision 2026-09-19).

A curve is fitted on least-disturbed stations only (the reference screen,
``reference_screen.py``). Where a Level III ecoregion holds too few of them, the
pool widens to its Level II parent and then its Level I parent, holding the
screen fixed and admitting only stations whose natural setting is comparable
to the target region's own streams. The narrowest level that supports the
metric is used, decided separately for every metric, because usable sample size
and applicability differ by metric. Where no level supports a metric the answer
is ``insufficient``: no curve is built and nothing is scored.

Reference quality is never relaxed to reach a sample size. The region's own
best-available stations appear only as a labeled comparison (REF-07).

Pure: data frames in, data frames and records out. No network, no file writes.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from . import methodology
from . import nrsa_dataset
from . import reference_screen as rscreen
from .config import read_yaml
from .paths import CONFIG_DIR

TRANSFER_CONFIG_PATH = CONFIG_DIR / "reference_transfer.yaml"

LEVELS = ("l3", "l2", "l1")
LEVEL_LABELS = {"l3": "Level III", "l2": "Level II", "l1": "Level I"}
STATUS_LOCAL = "local"
STATUS_INSUFFICIENT = "insufficient"
STATUS_BY_LEVEL = {"l3": STATUS_LOCAL, "l2": "borrowed_l2", "l1": "borrowed_l1"}

RISK_NONE, RISK_LOW, RISK_MODERATE, RISK_HIGH = "none", "low", "moderate", "high"
RISK_UNASSESSED = "unassessed"
#: risks that send a borrowed curve to mandatory review (REF-05)
REVIEW_RISKS = (RISK_MODERATE, RISK_HIGH, RISK_UNASSESSED)
# coarseness order for the transfer-risk comparison
_LEVEL_RANK = {"l3": 0, "l2": 1, "l1": 2, "national": 3}

LEDGER_COLUMNS = ["metric", "station_key", "level", "in_pool", "reason", "value",
                  "source_cycle", "l3", "l2", "l1"]


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def load_transfer_config(path: Optional[str] = None) -> dict:
    return read_yaml(Path(path) if path else TRANSFER_CONFIG_PATH) or {}


def transfer_config_sha256() -> Optional[str]:
    p = TRANSFER_CONFIG_PATH
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def floors() -> dict:
    """The governed sample floors and envelope settings."""
    return {
        "adequate": int(methodology.threshold("data_rules.min_n_unstratified", 20)),
        "exploratory": int(methodology.threshold("data_rules.exploratory_n_unstratified", 10)),
        "quantiles": tuple(methodology.threshold("reference_pool.envelope_quantiles",
                                                 [0.025, 0.975])),
        "min_self_coverage": float(methodology.threshold("reference_pool.min_self_coverage",
                                                         0.80)),
        "stratum_min_n": int(methodology.threshold("data_rules.min_n_stratum", 15)),
    }


def family_of(metric_key: str, cfg: Optional[dict] = None) -> Optional[str]:
    cfg = cfg if cfg is not None else load_transfer_config()
    return (cfg.get("metric_family") or {}).get(str(metric_key))


def family_profile(metric_key: str, cfg: Optional[dict] = None) -> Optional[dict]:
    """The comparability profile of a metric, or None when the metric has no
    family entry (it may not borrow: a local pool or no curve)."""
    cfg = cfg if cfg is not None else load_transfer_config()
    fam = family_of(metric_key, cfg)
    if not fam:
        return None
    prof = dict((cfg.get("families") or {}).get(fam) or {})
    prof["family"] = fam
    prof["covariates"] = list(prof.get("covariates") or [])
    prof["lithology"] = bool(prof.get("lithology"))
    return prof


# --------------------------------------------------------------------------- #
# the national frame
# --------------------------------------------------------------------------- #
def national_frame(*, dataset_id: str = nrsa_dataset.MULTI_CYCLE_DATASET_ID,
                   cycles=None, max_stream_order: Optional[int] = None,
                   protocols=None, keep_stations: Optional[dict] = None,
                   screen: Optional[pd.DataFrame] = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every in-frame station of the archive, joined to its screen record.

    The frame is the run's own (rule DATA-10: stream order, then the sampling
    protocol) plus EASI's non-canal condition. Returns ``(frame, ledger)``; the
    ledger names every station the frame kept out.
    """
    panel, ledger = nrsa_dataset.resolve_site_panel(
        None, dataset=dataset_id,
        cycles=tuple(cycles) if cycles else nrsa_dataset.CYCLES_NEWEST_FIRST,
        max_stream_order=max_stream_order, protocols=protocols,
        keep_stations=keep_stations)
    table = rscreen.load_station_screen() if screen is None else screen
    keep_cols = [c for c in table.columns if c not in ("comid",)]
    frame = panel.merge(table[keep_cols], on="station_key", how="left",
                        suffixes=("", "_screen"))
    canal = frame["fcode_class"].astype(object) == "canal"
    extra = [{"station_key": k, "cycle": "", "missing": "",
              "reason": "the reach is a canal or ditch, outside the reference frame"}
             for k in frame.loc[canal, "station_key"]]
    if extra:
        ledger = pd.concat([ledger, pd.DataFrame(extra)], ignore_index=True)
    frame = frame.loc[~canal].reset_index(drop=True)
    frame.attrs["frame_overrides"] = panel.attrs.get("frame_overrides") or []
    return frame, ledger


# --------------------------------------------------------------------------- #
# comparability
# --------------------------------------------------------------------------- #
def _transform(values: pd.Series, name: str, cfg: dict) -> pd.Series:
    env = cfg.get("envelope") or {}
    v = pd.to_numeric(values, errors="coerce").astype("float64")
    if name in (env.get("log10") or []):
        floor = float(env.get("slope_floor") or 1e-5) if name == "nhd_slope" else None
        if floor is not None:
            v = v.clip(lower=floor)
        v = np.log10(v.where(v > 0))
    return v


def envelope_for(target_frame: pd.DataFrame, covariates: Iterable[str], *,
                 quantiles=(0.025, 0.975), cfg: Optional[dict] = None) -> dict:
    """``{covariate: (low, high, n)}`` on the comparison scale (log10 where the
    config says so), from the target region's own in-frame stations."""
    cfg = cfg if cfg is not None else load_transfer_config()
    out: dict[str, tuple[float, float, int]] = {}
    for name in covariates:
        if name not in target_frame.columns:
            out[name] = (float("nan"), float("nan"), 0)
            continue
        v = _transform(target_frame[name], name, cfg).dropna()
        if len(v) < 5:
            out[name] = (float("nan"), float("nan"), int(len(v)))
            continue
        lo, hi = np.quantile(v, [quantiles[0], quantiles[1]])
        out[name] = (float(lo), float(hi), int(len(v)))
    return out


def target_lith_groups(target_frame: pd.DataFrame, cfg: Optional[dict] = None) -> list[str]:
    """Lithology groups that are the target region's own: dominant at the
    configured share of its in-frame stations. Empty when the table carries no
    lithology, which switches the lithology condition off (and says so)."""
    cfg = cfg if cfg is not None else load_transfer_config()
    if "lith_group" not in target_frame.columns:
        return []
    groups = target_frame["lith_group"].dropna().astype(str)
    if not len(groups):
        return []
    share_min = float((cfg.get("lithology") or {}).get("target_share_min") or 0.10)
    shares = groups.value_counts(normalize=True)
    own = [g for g, s in shares.items() if s >= share_min and g != "mixed"]
    return sorted(own)


def comparable_mask(candidates: pd.DataFrame, envelope: dict, lith_groups: list[str], *,
                    use_lithology: bool, cfg: Optional[dict] = None
                    ) -> tuple[pd.Series, pd.Series]:
    """``(comparable, reason)`` for candidate stations against a target envelope."""
    cfg = cfg if cfg is not None else load_transfer_config()
    ok = pd.Series(True, index=candidates.index)
    reason = pd.Series("", index=candidates.index, dtype=object)

    def _fail(mask: pd.Series, text: str) -> None:
        new = mask & (reason == "")
        reason[new] = text
        ok[mask] = False

    for name, (lo, hi, n) in envelope.items():
        if not n or lo != lo or hi != hi:
            continue                      # the target cannot define this covariate
        v = _transform(candidates[name], name, cfg) if name in candidates.columns \
            else pd.Series(np.nan, index=candidates.index)
        _fail(v.isna(), f"no_value:{name}")
        _fail(v.notna() & ((v < lo) | (v > hi)), f"outside_envelope:{name}")
    if use_lithology and lith_groups and "lith_group" in candidates.columns:
        g = candidates["lith_group"].astype(object)
        _fail(g.isna(), "no_value:lith_group")
        allowed = set(lith_groups) | {"mixed"}
        _fail(g.notna() & ~g.isin(allowed), "lithology")
    return ok.astype(bool), reason


def self_coverage(target_frame: pd.DataFrame, envelope: dict, lith_groups: list[str], *,
                  use_lithology: bool, cfg: Optional[dict] = None) -> float:
    """The share of the target's own stations its envelope admits. A low share
    means the profile is too tight to describe even the region it is built from."""
    if not len(target_frame):
        return float("nan")
    ok, _ = comparable_mask(target_frame, envelope, lith_groups,
                            use_lithology=use_lithology, cfg=cfg)
    return float(ok.mean())


def transfer_risk(level_used: Optional[str], supported_level: Optional[str]) -> str:
    """How far past the scale a metric's variance supports the pool widened.

    A local pool carries none. A borrowed pool is low risk when the national
    scale analysis found the metric varies no more at Level III than at the
    level used, moderate one step past that, high two steps past it, and
    unassessed when the registry holds no decision for the metric.
    """
    if level_used in (None, "l3"):
        return RISK_NONE
    if not supported_level or supported_level not in _LEVEL_RANK:
        return RISK_UNASSESSED
    gap = _LEVEL_RANK[level_used] - _LEVEL_RANK[supported_level]
    if gap <= 0:
        return RISK_LOW
    return RISK_MODERATE if gap == 1 else RISK_HIGH


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PoolDecision:
    metric: str
    status: str                       # local | borrowed_l2 | borrowed_l1 | insufficient
    level: Optional[str]
    region_code: Optional[str]
    region_name: Optional[str]
    family: Optional[str]
    n_pool: int                       # strict, in-frame stations at the level used
    n_comparable: int                 # of those, comparable to the target
    n_usable: int                     # of those, with a value for this metric
    n_local: int                      # usable stations inside the target Level III
    n_huc12: int
    disposition: str                  # adequate | exploratory | insufficient
    covariates: list = field(default_factory=list)
    envelope: dict = field(default_factory=dict)
    lith_groups: list = field(default_factory=list)
    self_coverage: Optional[float] = None
    supported_level: Optional[str] = None
    transfer_risk: str = RISK_NONE
    transfer_note: str = ""
    station_ids: tuple = ()
    levels_tried: list = field(default_factory=list)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["station_ids"] = list(self.station_ids)
        return out


def _level_name(frame: pd.DataFrame, level: str, code: Any) -> Optional[str]:
    col = f"{level}_name"
    if col not in frame.columns:
        return None
    names = frame.loc[frame[level].astype(str) == str(code), col].dropna()
    return str(names.iloc[0]) if len(names) else None


def _transfer_note(decision_level: str, region_name: Optional[str], region_code: Any,
                   n_usable: int, n_local: int, covariates: list, lithology: bool,
                   risk: str, supported: Optional[str]) -> str:
    where = f"{LEVEL_LABELS[decision_level]} {region_code}" + (
        f" ({region_name})" if region_name else "")
    matched = ", ".join(_COVARIATE_WORDS.get(c, c) for c in covariates)
    if lithology:
        matched += (", " if matched else "") + "surficial lithology"
    text = (f"{n_usable} least-disturbed stations from {where}, {n_local} of them inside this "
            f"ecoregion. Borrowed stations were matched to this ecoregion's streams on {matched}.")
    if risk == RISK_LOW:
        text += (" The national scale analysis found this metric varies no more between Level III "
                 "ecoregions than at the level borrowed from, so the transfer risk is low.")
    elif risk in (RISK_MODERATE, RISK_HIGH):
        text += (f" The national scale analysis supports this metric at "
                 f"{LEVEL_LABELS.get(supported or '', 'a finer level')}, finer than the level "
                 f"borrowed from, so the transfer risk is {risk} and the expectation may not "
                 "represent local conditions.")
    elif risk == RISK_UNASSESSED:
        text += " The national scale analysis holds no decision for this metric, so the transfer risk is unassessed."
    return text


_COVARIATE_WORDS = {
    "drainage_area_sqkm": "drainage area", "nhd_slope": "channel slope",
    "elevws": "elevation", "precip8110ws": "precipitation", "tmean8110ws": "air temperature",
    "runoffws": "runoff", "bfiws": "base flow index", "hydrlcondws": "hydraulic conductivity",
    "kffactws": "soil erodibility",
}


def choose_pool(metric: str, values: pd.Series, frame: pd.DataFrame, target_l3: str, *,
                profile: Optional[dict], scale_entry: Optional[dict] = None,
                excluded: Optional[dict] = None, cfg: Optional[dict] = None,
                settings: Optional[dict] = None) -> tuple[PoolDecision, pd.DataFrame]:
    """The narrowest level that supports ``metric`` for ``target_l3``.

    ``values`` is indexed by station key (the metric's latest non-null value).
    ``frame`` is :func:`national_frame`. Returns the decision and the ledger
    rows for this metric (one per station per level tried).
    """
    cfg = cfg if cfg is not None else load_transfer_config()
    st = settings or floors()
    excluded = {str(k): str(v) for k, v in (excluded or {}).items()}
    target_l3 = str(target_l3)
    target = frame[frame["l3"].astype(str) == target_l3]
    codes = {"l3": target_l3}
    for level in ("l2", "l1"):
        got = target[level].dropna().astype(str)
        codes[level] = got.mode().iat[0] if len(got) else None

    covariates = list((profile or {}).get("covariates") or [])
    use_lith = bool((profile or {}).get("lithology"))
    envelope = envelope_for(target, covariates, quantiles=st["quantiles"], cfg=cfg)
    lith_groups = target_lith_groups(target, cfg) if use_lith else []
    coverage = self_coverage(target, envelope, lith_groups, use_lithology=use_lith, cfg=cfg)
    supported = (scale_entry or {}).get("supported_level")

    vals = pd.to_numeric(values, errors="coerce")
    rows: list[pd.DataFrame] = []
    tried: list[dict] = []
    chosen: Optional[dict] = None
    exploratory: Optional[dict] = None

    for level in LEVELS:
        code = codes.get(level)
        if code is None:
            continue
        if level != "l3" and profile is None:
            break                       # no family entry: this metric may not borrow
        members = frame[frame[level].astype(str) == str(code)].copy()
        keys = members["station_key"].astype(str)
        is_local = members["l3"].astype(str) == target_l3
        strict = members["pass_strict"].astype(bool)
        comparable, why = comparable_mask(members, envelope, lith_groups,
                                          use_lithology=use_lith, cfg=cfg)
        comparable = comparable | is_local            # the region's own are always admitted
        owner_out = keys.isin(set(excluded))
        value = keys.map(vals)
        has_value = value.notna()

        reason = pd.Series("", index=members.index, dtype=object)
        reason[~strict] = "failed_screen:" + members.loc[~strict, "fail_strict"].astype(str)
        step = strict & owner_out
        reason[step] = "excluded_by_owner:" + keys[step].map(excluded).astype(str)
        step = strict & ~owner_out & ~comparable
        reason[step] = why[step]
        step = strict & ~owner_out & comparable & ~has_value
        reason[step] = "no_value"
        in_pool = strict & ~owner_out & comparable & has_value

        n_pool = int((strict & ~owner_out).sum())
        n_comp = int((strict & ~owner_out & comparable).sum())
        n_use = int(in_pool.sum())
        info = {"level": level, "region_code": str(code), "n_pool": n_pool,
                "n_comparable": n_comp, "n_usable": n_use,
                "n_local": int((in_pool & is_local).sum())}
        tried.append(info)
        ledger = pd.DataFrame({
            "metric": metric, "station_key": keys.values, "level": level,
            "in_pool": in_pool.values, "reason": reason.values, "value": value.values,
            "source_cycle": members.get("source_cycle", pd.Series(None, index=members.index)).values,
            "l3": members["l3"].values, "l2": members["l2"].values, "l1": members["l1"].values})
        rows.append(ledger)
        pick = {**info, "ids": tuple(keys[in_pool]),
                "n_huc12": int(_cluster_ids(members[in_pool]).nunique())}
        if n_use >= st["adequate"]:
            chosen = pick
            break
        if exploratory is None and n_use >= st["exploratory"]:
            exploratory = pick

    ledger_all = (pd.concat(rows, ignore_index=True) if rows
                  else pd.DataFrame(columns=LEDGER_COLUMNS))
    final = chosen or exploratory
    if final is None:
        # Report the best attempt, and the widest level on a tie: it is the last
        # thing that was tried, so its counts say why the ladder ran out.
        best = (max(reversed(tried), key=lambda t: t["n_usable"]) if tried else {})
        decision = PoolDecision(
            metric=metric, status=STATUS_INSUFFICIENT, level=None, region_code=None,
            region_name=None, family=(profile or {}).get("family"),
            n_pool=int(best.get("n_pool") or 0), n_comparable=int(best.get("n_comparable") or 0),
            n_usable=int(best.get("n_usable") or 0), n_local=int(best.get("n_local") or 0),
            n_huc12=0, disposition="insufficient", covariates=covariates,
            envelope=_envelope_record(envelope), lith_groups=lith_groups,
            self_coverage=_round(coverage), supported_level=supported,
            transfer_risk=RISK_NONE,
            transfer_note=("No level of the ecoregion hierarchy holds enough comparable "
                           "least-disturbed stations with a value for this metric. "
                           "Insufficient reference support: no curve is built."),
            station_ids=(), levels_tried=tried)
        ledger_all = ledger_all.assign(in_pool=False) if len(ledger_all) else ledger_all
        return decision, ledger_all

    level = final["level"]
    risk = transfer_risk(level, supported)
    name = _level_name(frame, level, final["region_code"])
    note = ("" if level == "l3" else _transfer_note(
        level, name, final["region_code"], final["n_usable"], final["n_local"],
        covariates, use_lith and bool(lith_groups), risk, supported))
    decision = PoolDecision(
        metric=metric, status=STATUS_BY_LEVEL[level], level=level,
        region_code=final["region_code"], region_name=name,
        family=(profile or {}).get("family"), n_pool=final["n_pool"],
        n_comparable=final["n_comparable"], n_usable=final["n_usable"],
        n_local=final["n_local"], n_huc12=final["n_huc12"],
        disposition="adequate" if final["n_usable"] >= st["adequate"] else "exploratory",
        covariates=covariates, envelope=_envelope_record(envelope), lith_groups=lith_groups,
        self_coverage=_round(coverage), supported_level=supported, transfer_risk=risk,
        transfer_note=note, station_ids=final["ids"], levels_tried=tried)
    # only the level used is the pool; the wider levels were never needed
    ledger_all.loc[ledger_all["level"] != level, "in_pool"] = False
    return decision, ledger_all


def _cluster_ids(members: pd.DataFrame) -> pd.Series:
    """HUC12 where known, else HUC8: the bootstrap's cluster unit."""
    h12 = members.get("huc12")
    h8 = members.get("huc8")
    if h12 is None:
        return h8.astype(object) if h8 is not None else pd.Series(dtype=object)
    return h12.astype(object).where(h12.notna(), h8.astype(object) if h8 is not None else None)


def _round(x: Any, digits: int = 3) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else round(v, digits)


def _envelope_record(envelope: dict) -> dict:
    return {k: {"low": _round(lo, 5), "high": _round(hi, 5), "n": int(n)}
            for k, (lo, hi, n) in envelope.items()}


# --------------------------------------------------------------------------- #
# the local best-available comparison (REF-07)
# --------------------------------------------------------------------------- #
def local_comparison_stations(target_frame: pd.DataFrame, cfg: Optional[dict] = None
                              ) -> tuple[pd.DataFrame, str]:
    """The target's own best-available stations and how they were chosen:
    those passing the relaxed screen, or, where too few do, the lowest-pressure
    quarter of the region's stations by mean percentile rank of the screen's
    pressure variables."""
    cfg = cfg if cfg is not None else load_transfer_config()
    lc = cfg.get("local_comparison") or {}
    min_n = int(lc.get("min_n") or 10)
    relaxed = target_frame[target_frame["pass_relaxed"].astype(bool)]
    if len(relaxed) >= min_n:
        return relaxed, "stations of this ecoregion that pass the relaxed pressure screen"
    names = [v for v in (lc.get("pressure_variables") or []) if v in target_frame.columns]
    if not names or not len(target_frame):
        return target_frame.iloc[0:0], "no local comparison"
    ranks = pd.concat([pd.to_numeric(target_frame[v], errors="coerce").rank(pct=True)
                       for v in names], axis=1)
    score = ranks.mean(axis=1, skipna=True)
    k = min(len(target_frame), max(min_n, int(math.ceil(len(target_frame) / 4.0))))
    picked = target_frame.loc[score.sort_values(kind="stable").index[:k]]
    return picked, ("the quarter of this ecoregion's stations with the lowest landscape "
                    "pressure (too few pass the relaxed screen)")


def local_comparison(metric: str, values: pd.Series, target_frame: pd.DataFrame,
                     cfg: Optional[dict] = None) -> Optional[dict]:
    cfg = cfg if cfg is not None else load_transfer_config()
    stations, definition = local_comparison_stations(target_frame, cfg)
    if not len(stations):
        return None
    v = pd.to_numeric(stations["station_key"].astype(str).map(values), errors="coerce").dropna()
    if len(v) < 5:
        return None
    q25, q50, q75 = np.quantile(v, [0.25, 0.50, 0.75])
    return {"label": (cfg.get("local_comparison") or {}).get(
                "label", "Local best-available comparison (not reference)"),
            "definition": definition, "n": int(len(v)),
            "q25": float(q25), "q50": float(q50), "q75": float(q75)}


# --------------------------------------------------------------------------- #
# all metrics of one region
# --------------------------------------------------------------------------- #
def build_pools(metrics: Iterable[str], values_wide: pd.DataFrame, frame: pd.DataFrame,
                target_l3: str, *, scale_registry: Optional[dict] = None,
                excluded: Optional[dict] = None, cfg: Optional[dict] = None) -> dict:
    """Pools for every metric of one region.

    ``values_wide`` is keyed by ``site_id`` (the station key), one column per
    metric. Returns ``decisions`` (metric -> PoolDecision), ``ledger``,
    ``local_comparison`` (metric -> dict) and ``data``: one row per station that
    is in ANY metric's pool, each metric column holding a value only where the
    station is in that metric's pool. Masking is what lets the curve builder,
    the diagnostics and a reopened session all reproduce a per-metric pool from
    a single data frame.
    """
    cfg = cfg if cfg is not None else load_transfer_config()
    settings = floors()
    wide = values_wide.set_index(values_wide["site_id"].astype(str)) \
        if "site_id" in values_wide.columns else values_wide
    target = frame[frame["l3"].astype(str) == str(target_l3)]
    reg = (scale_registry or {}).get("metrics") or {}
    decisions: dict[str, PoolDecision] = {}
    ledgers: list[pd.DataFrame] = []
    comparisons: dict[str, dict] = {}
    for mk in metrics:
        series = wide[mk] if mk in wide.columns else pd.Series(dtype="float64")
        decision, ledger = choose_pool(
            mk, series, frame, target_l3, profile=family_profile(mk, cfg),
            scale_entry=reg.get(mk), excluded=excluded, cfg=cfg, settings=settings)
        decisions[mk] = decision
        ledgers.append(ledger)
        comp = local_comparison(mk, series, target, cfg)
        if comp:
            comparisons[mk] = comp

    union = sorted({sid for d in decisions.values() for sid in d.station_ids})
    base = frame[frame["station_key"].astype(str).isin(union)].copy()
    base["site_id"] = base["station_key"].astype(str)
    base = base.sort_values("site_id").reset_index(drop=True)
    for mk, d in decisions.items():
        if d.status == STATUS_INSUFFICIENT or mk not in wide.columns:
            continue
        members = set(d.station_ids)
        col = base["site_id"].map(pd.to_numeric(wide[mk], errors="coerce"))
        base[mk] = col.where(base["site_id"].isin(members))
    ledger = (pd.concat(ledgers, ignore_index=True) if ledgers
              else pd.DataFrame(columns=LEDGER_COLUMNS))
    return {"decisions": decisions, "ledger": ledger, "local_comparison": comparisons,
            "data": base, "target_n_frame": int(len(target)),
            "target_n_strict": int(target["pass_strict"].astype(bool).sum()),
            "target_n_relaxed": int(target["pass_relaxed"].astype(bool).sum())}


def support_table(decisions: dict[str, PoolDecision]) -> pd.DataFrame:
    """The reference-support table a reviewer reads: one row per metric."""
    rows = []
    for mk, d in decisions.items():
        rows.append({
            "metric": mk, "family": d.family, "status": d.status,
            "level": LEVEL_LABELS.get(d.level or "", ""), "region_code": d.region_code,
            "region_name": d.region_name, "n_pool": d.n_pool, "n_comparable": d.n_comparable,
            "n_usable": d.n_usable, "n_local": d.n_local, "n_huc12": d.n_huc12,
            "disposition": d.disposition, "covariates": ", ".join(d.covariates),
            "lithology": ", ".join(d.lith_groups), "self_coverage": d.self_coverage,
            "supported_level": d.supported_level, "transfer_risk": d.transfer_risk})
    return pd.DataFrame(rows)


def reference_support_record(d, *, screen_tier: str = "strict") -> dict:
    """The per-metric ``referenceSupport`` block of a published bundle.

    ``d`` is a :class:`PoolDecision` or its ``to_dict()`` form (what the
    evidence of a run carries)."""
    if isinstance(d, PoolDecision):
        d = d.to_dict()
    level = d.get("level")
    return {
        "status": d.get("status"), "level": level,
        "levelLabel": LEVEL_LABELS.get(level or "", ""),
        "regionCode": d.get("region_code"), "regionName": d.get("region_name"),
        "screen": rscreen.screen_label(screen_tier),
        "nPool": d.get("n_pool"), "nComparable": d.get("n_comparable"),
        "nUsable": d.get("n_usable"), "nLocal": d.get("n_local"),
        "nHuc12": d.get("n_huc12"), "disposition": d.get("disposition"),
        "family": d.get("family"),
        "covariates": list(d.get("covariates") or [])
                      + (["lith_group"] if d.get("lith_groups") else []),
        "selfCoverage": d.get("self_coverage"), "supportedLevel": d.get("supported_level"),
        "transferRisk": d.get("transfer_risk"), "transferNote": d.get("transfer_note"),
    }
