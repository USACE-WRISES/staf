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

from . import acceptance
from . import curve_basis
from . import methodology
from . import nrsa_dataset
from . import reference_screen as rscreen
from .config import read_yaml
from .paths import CONFIG_DIR

TRANSFER_CONFIG_PATH = CONFIG_DIR / "reference_transfer.yaml"

LEVELS = ("l3", "l2", "l1")
LEVEL_LABELS = {"l3": "Level III", "l2": "Level II", "l1": "Level I", "nars9": "NARS-9 region"}
STATUS_LOCAL = "local"
STATUS_INSUFFICIENT = "insufficient"
STATUS_BY_LEVEL = {"l3": STATUS_LOCAL, "l2": "borrowed_l2", "l1": "borrowed_l1"}
#: v0.14 (REF-11): a pool admitted under the regional screen. Level III under
#: that screen is still the region's own streams, so it reads as local.
STATUS_LOCAL_RELAXED = "local_relaxed"
STATUS_BY_OPTION = {("l3", "strict"): STATUS_LOCAL, ("l3", "regional"): STATUS_LOCAL_RELAXED,
                    ("l2", "regional"): "borrowed_l2", ("nars9", "regional"): "borrowed_nars9",
                    ("l1", "regional"): "borrowed_l1",
                    # campaign Round 2 (B2): the wider pools under the strict screen
                    # when the regional screen is off
                    ("l2", "strict"): "borrowed_l2", ("nars9", "strict"): "borrowed_nars9",
                    ("l1", "strict"): "borrowed_l1"}
SCREEN_STRICT = "strict"
SCREEN_REGIONAL = "regional"
#: statuses for the rungs above the ecoregion hierarchy (REF-08/09/10). They are
#: deliberately NOT "local": a modelled expectation and a published criterion
#: rest on no station of the target ecoregion, and a count of local pools that
#: included them would be the exact false claim this ladder exists to remove.
STATUS_NATIONAL = "national"
STATUS_MODELED = "modeled"
STATUS_PUBLISHED = "published"
LADDER_STATUSES = (STATUS_NATIONAL, STATUS_MODELED, STATUS_PUBLISHED)

RISK_NONE, RISK_LOW, RISK_MODERATE, RISK_HIGH = "none", "low", "moderate", "high"
RISK_UNASSESSED = "unassessed"
#: methodology 0.16 (REF-16): a pool taken under the flagged-transfer rung, whose
#: transfer the recovery test did not confirm (or never covered). The verdict rides
#: on the decision as a limitation, never as a gate (owner decision D11).
RISK_UNVALIDATED = "unvalidated"
RISKS = (RISK_NONE, RISK_LOW, RISK_MODERATE, RISK_HIGH, RISK_UNASSESSED, RISK_UNVALIDATED)
#: the words a reader sees for each risk, wherever a risk is spelled out
RISK_WORDS = {RISK_NONE: "none", RISK_LOW: "low", RISK_MODERATE: "moderate", RISK_HIGH: "high",
              RISK_UNASSESSED: "not yet assessed",
              RISK_UNVALIDATED: "unvalidated (transfer not confirmed by the recovery test)"}
#: risks that send a borrowed curve to mandatory review (REF-05)
REVIEW_RISKS = (RISK_MODERATE, RISK_HIGH, RISK_UNASSESSED)
#: the rule a flagged transfer is recorded under
RULE_FLAGGED = "REF-16"
# coarseness order for the transfer-risk comparison
_LEVEL_RANK = {"l3": 0, "l2": 1, "nars9": 2, "l1": 2, "national": 3}

#: ``screen`` is the screen the option was tried under; ``admitted_by`` says which
#: screen admitted each station (strict, regional, or empty when it failed both)
LEDGER_COLUMNS = ["metric", "station_key", "level", "in_pool", "reason", "value",
                  "source_cycle", "l3", "l2", "l1", "option", "screen", "admitted_by"]


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
    family entry (it may not borrow: a local pool or no curve).

    A top-level ``search_order`` in the transfer config (campaign Round 2
    candidate B3; absent by default) replaces every family's own order, and the
    profile says so (``search_order_source``). The config's hash is already in
    the inputs digest, so the override adds no digest key of its own.
    """
    cfg = cfg if cfg is not None else load_transfer_config()
    fam = family_of(metric_key, cfg)
    if not fam:
        return None
    prof = dict((cfg.get("families") or {}).get(fam) or {})
    prof["family"] = fam
    prof["covariates"] = list(prof.get("covariates") or [])
    prof["lithology"] = bool(prof.get("lithology"))
    global_order = cfg.get("search_order")
    if global_order:
        prof["search_order"] = [str(o) for o in global_order]
        prof["search_order_source"] = "global"
    else:
        prof["search_order_source"] = "family"
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
    #: which rung of the basis ladder produced this pool (REF-08/09/10). Every
    #: pool this module chooses is a station pool, so it is always the regional
    #: rung; the wider rungs are set by their own modules.
    basis: str = curve_basis.REGIONAL
    #: v0.14: the screen the pool was admitted under (strict or the regional
    #: screen), and that screen's own record (its agriculture limit and source)
    screen: str = SCREEN_STRICT
    screen_detail: dict = field(default_factory=dict)
    #: v0.14: every source option tried before this one, with why it refused
    options_tried: list = field(default_factory=list)
    #: v0.16 (REF-16): the recovery verdict a flagged transfer was refused with in
    #: the first pass (acceptance.validation_record), and the confidence cap the
    #: curve carries; empty and None on every other decision
    transfer_validation: dict = field(default_factory=dict)
    confidence_cap: Optional[int] = None

    @property
    def flagged(self) -> bool:
        """A pool taken under the flagged-transfer rung (REF-16)."""
        return self.transfer_risk == RISK_UNVALIDATED

    def to_dict(self) -> dict:
        out = asdict(self)
        out["station_ids"] = list(self.station_ids)
        out["basis_label"] = curve_basis.label_for(self.basis)
        return out


def _level_name(frame: pd.DataFrame, level: str, code: Any) -> Optional[str]:
    col = f"{level}_name"
    if col not in frame.columns:
        return None
    names = frame.loc[frame[level].astype(str) == str(code), col].dropna()
    return str(names.iloc[0]) if len(names) else None


def _transfer_note(decision_level: str, region_name: Optional[str], region_code: Any,
                   n_usable: int, n_local: int, covariates: list, lithology: bool,
                   risk: str, supported: Optional[str], *,
                   screen_detail: Optional[dict] = None, fauna: Optional[list] = None) -> str:
    where = f"{LEVEL_LABELS[decision_level]} {region_code}" + (
        f" ({region_name})" if region_name else "")
    matched = ", ".join(_COVARIATE_WORDS.get(c, c) for c in covariates)
    if lithology:
        matched += (", " if matched else "") + "surficial lithology"
    inside = (f"{n_local} of them inside this ecoregion" if n_local
              else "none of them inside this ecoregion")
    if screen_detail:
        limit = screen_detail.get("agriculture_limit")
        basis = (f"the 90th percentile at EPA's NRSA reference sites in NARS-9 region "
                 f"{screen_detail.get('nars9')}" if screen_detail.get("agriculture_rule")
                 == "epa_reference_range" else "the screen's floor")
        kind = (f"stations passing the regional least-disturbed screen (watershed agriculture "
                f"up to {limit:g} percent, {basis}, with the relaxed tier's other limits)")
    else:
        kind = "least-disturbed stations"
    if decision_level == "l3":
        text = f"{n_usable} {kind} of this ecoregion."
    else:
        text = (f"{n_usable} {kind} from {where}, {inside}. Borrowed stations were matched to "
                f"this ecoregion's streams on {matched}.")
    if fauna:
        text += " Every station drains to this ecoregion's faunal province."
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


def _flagged_note(where_text: str, validation: Optional[dict], cap: Optional[int],
                  disposition: str, st: dict) -> str:
    """The transfer note of a pool taken under the flagged-transfer rung (REF-16):
    where the stations come from (``where_text``, the ordinary transfer note with
    no risk sentence), that the recovery test did not confirm the source and with
    what numbers against what the test requires, and that the curve carries a
    confidence cap. Plain words, no rule code a DEEP reader has to look up."""
    v = validation or {}
    why = str(v.get("why") or "").strip()
    if not why:
        why = "The recovery test holds no evidence for this source and metric."
    if not why.endswith("."):
        why += "."
    numbers = []
    a1, n, net = v.get("a1_share"), v.get("n_cells"), v.get("net_opt")
    min_cells = int(v.get("min_cells") or st.get("min_cells") or 4)
    max_net = float(v.get("max_net_optimism") or 0.05)
    if a1 is not None and a1 == a1:
        cells = f" of {int(n)} evaluation regions" if n is not None else " of evaluation regions"
        numbers.append(f"class agreement {float(a1):.2f}{cells}, where two thirds over at least "
                       f"{min_cells} regions is required")
    if net is not None and net == net:
        numbers.append(f"net optimism {float(net):+.2f}, where at most {max_net:.2f} is allowed")
    found = (" The test found " + "; ".join(numbers) + ".") if numbers else ""
    floor_words = ("at least the adequate floor of usable comparable stations" if disposition == "adequate"
                   else "at least the exploratory floor of usable comparable stations")
    cap_words = (f"its confidence is capped at {int(cap)}" if cap is not None
                 else "its confidence is capped")
    return (f"{where_text} Flagged transfer: the recovery test did not confirm this source for this "
            f"ecoregion. {why}{found} No source passed the validated acceptance criteria for this "
            f"metric here, so the pool is used under the flagged-transfer rung as the narrowest "
            f"comparable pool with {floor_words} that passes the sample, stability and "
            f"comparability checks. The curve carries transfer risk unvalidated and {cap_words}; "
            "read the condition band with that limitation in mind.")


_COVARIATE_WORDS = {
    "drainage_area_sqkm": "drainage area", "nhd_slope": "channel slope",
    "elevws": "elevation", "precip8110ws": "precipitation", "tmean8110ws": "air temperature",
    "runoffws": "runoff", "bfiws": "base flow index", "hydrlcondws": "hydraulic conductivity",
    "kffactws": "soil erodibility",
}


# --------------------------------------------------------------------------- #
# v0.14 (REF-11): the regional screen, the widened envelope, the faunal rule
# --------------------------------------------------------------------------- #
def hierarchy_settings() -> dict:
    """The governed numbers of the reference hierarchy (methodology_config.yaml)."""
    rs = methodology.threshold("reference_hierarchy.regional_screen", {}) or {}
    return {
        "agriculture_quantile": float(rs.get("agriculture_quantile", 0.90)),
        "agriculture_floor": float(rs.get("agriculture_floor", 25.0)),
        "min_epa_reference_sites": int(rs.get("min_epa_reference_sites", 5)),
        "screen_id": str(rs.get("id") or "least-disturbed-regional-v1"),
        "widen": float(methodology.threshold("reference_hierarchy.envelope_widen_fraction", 0.0)),
        # campaign Round 2 (B2): false keeps every regional pool to the strict screen
        "enabled": bool(rs.get("enabled", True)),
    }


def _withheld_codes(withhold_l3) -> set[str]:
    if withhold_l3 is None:
        return set()
    if isinstance(withhold_l3, str):
        return {withhold_l3.strip()} if withhold_l3.strip() else set()
    return {str(c).strip() for c in withhold_l3 if str(c).strip()}


def epa_agriculture_limit(screen_table: pd.DataFrame, nars9: Optional[str],
                          hs: Optional[dict] = None, *, withhold_l3=None) -> dict:
    """The regional screen's agriculture limit for one NARS-9 region.

    The quantile of watershed agriculture at EPA's own NRSA reference sites
    (``rt_nrsa == "R"``) in the region, never below the floor, because EPA's
    regional designation is the documented statement of what least disturbed
    means there. A region with too few EPA reference sites uses the floor.

    ``withhold_l3`` (a code or codes): stations of those Level III ecoregions
    leave the calibration, so a recovery test that withholds a region never lets
    that region's own EPA sites set the limit it is scored under (the campaign's
    fold rule). Recorded as ``withheld_l3`` only when given.
    """
    hs = hs or hierarchy_settings()
    out = {"nars9": nars9, "floor": hs["agriculture_floor"], "quantile": hs["agriculture_quantile"],
           "n_epa_reference": 0, "epa_quantile_value": None, "limit": hs["agriculture_floor"],
           "rule": "floor"}
    held = _withheld_codes(withhold_l3)
    if held:
        out["withheld_l3"] = sorted(held)
        if "l3" in screen_table.columns:
            screen_table = screen_table[~screen_table["l3"].astype(str).isin(held)]
    if not nars9 or "rt_nrsa" not in screen_table.columns:
        return out
    ref = screen_table[(screen_table["nars9"].astype(str) == str(nars9))
                       & (screen_table["rt_nrsa"].astype(str) == "R")]
    ag = pd.to_numeric(ref.get("agriculture_ws"), errors="coerce").dropna()
    out["n_epa_reference"] = int(len(ag))
    if len(ag) < hs["min_epa_reference_sites"]:
        return out
    q = float(np.quantile(ag, hs["agriculture_quantile"]))
    out["epa_quantile_value"] = round(q, 2)
    out["limit"] = round(max(q, hs["agriculture_floor"]), 2)
    out["rule"] = "epa_reference_range" if q > hs["agriculture_floor"] else "floor"
    return out


def regional_screen_mask(frame: pd.DataFrame, agriculture_limit: float) -> pd.Series:
    """The regional screen: the relaxed tier of the reference screen with its
    agriculture limit replaced by the region's. A station missing a variable
    fails that rule, as the screen's own tiers do."""
    base = rscreen.rules("relaxed")
    ok = pd.Series(True, index=frame.index)
    for var, (op, limit) in base.items():
        lim = float(agriculture_limit) if var == "agriculture_ws" else float(limit)
        v = pd.to_numeric(frame.get(var), errors="coerce") if var in frame.columns \
            else pd.Series(np.nan, index=frame.index)
        if op == "<=":
            ok &= v.notna() & (v <= lim)
        elif op == "<":
            ok &= v.notna() & (v < lim)
        elif op == "==":
            ok &= v.notna() & (v == lim)
        elif op == ">=":
            ok &= v.notna() & (v >= lim)
        else:
            raise ValueError(f"unknown screen operator {op!r} for {var}")
    return ok


def national_spans(frame: pd.DataFrame, covariates: Iterable[str],
                   cfg: Optional[dict] = None) -> dict:
    """Each covariate's national 2.5 to 97.5 percent span on the comparison scale."""
    cfg = cfg if cfg is not None else load_transfer_config()
    out = {}
    for name in covariates:
        if name not in frame.columns:
            continue
        v = _transform(frame[name], name, cfg).dropna()
        if len(v) >= 20:
            lo, hi = np.quantile(v, [0.025, 0.975])
            out[name] = float(hi - lo)
    return out


def widen_envelope(envelope: dict, spans: dict, fraction: float) -> dict:
    """The target's envelope widened on each side by ``fraction`` of the national
    span, so a compact region does not shut out comparable streams."""
    if not fraction:
        return envelope
    out = {}
    for name, (lo, hi, n) in envelope.items():
        pad = fraction * float(spans.get(name) or 0.0)
        out[name] = (lo - pad, hi + pad, n) if (n and lo == lo and hi == hi) else (lo, hi, n)
    return out


def _huc2(rows: pd.DataFrame) -> pd.Series:
    return rows.get("huc12", pd.Series("", index=rows.index)).astype(str).str[:2]


def fauna_groups_of(rows: pd.DataFrame, cfg: Optional[dict] = None) -> list[str]:
    """The faunal provinces a set of stations drains to: those holding at least
    the configured share of them."""
    cfg = cfg if cfg is not None else load_transfer_config()
    spec = cfg.get("fauna_groups") or {}
    by_huc2 = {h: g for g, codes in (spec.get("groups") or {}).items() for h in codes}
    groups = _huc2(rows).map(by_huc2).dropna()
    if not len(groups):
        return []
    share = groups.value_counts(normalize=True)
    floor = float(spec.get("target_share_min") or 0.10)
    return sorted(g for g, s in share.items() if s >= floor)


def fauna_mask(candidates: pd.DataFrame, groups: list[str],
               cfg: Optional[dict] = None) -> pd.Series:
    """Stations draining to one of ``groups``. Empty groups admit everything, so
    a target the table cannot place is not silently emptied."""
    if not groups:
        return pd.Series(True, index=candidates.index)
    cfg = cfg if cfg is not None else load_transfer_config()
    by_huc2 = {h: g for g, codes in ((cfg.get("fauna_groups") or {}).get("groups") or {}).items()
               for h in codes}
    return _huc2(candidates).map(by_huc2).isin(set(groups))


def search_order(profile: Optional[dict]) -> list[str]:
    """The regional pool options of a metric family, in its fixed order."""
    order = list((profile or {}).get("search_order") or ["l3", "l2", "l1"])
    return [o for o in order if o in ("l3", "l2", "nars9", "l1")]


def choose_pool(metric: str, values: pd.Series, frame: pd.DataFrame, target_l3: str, *,
                profile: Optional[dict], scale_entry: Optional[dict] = None,
                excluded: Optional[dict] = None, cfg: Optional[dict] = None,
                settings: Optional[dict] = None, accept=None,
                withhold: Optional[Iterable[str]] = None,
                only_option: Optional[str] = None,
                ladder_rule: Optional[str] = None,
                withhold_l3=None,
                curve_check=None,
                flagged: Optional[dict] = None) -> tuple[PoolDecision, pd.DataFrame]:
    """The station pool that passes acceptance for ``metric`` (REF-11).

    The options are the local reference (Level III under the strict screen) and
    then the regional least-disturbed pools, Level III, Level II, NARS-9 and
    Level I in the metric family's fixed ``search_order``, each under the
    target's regional screen. A pool must hold at least the exploratory floor of
    independent stations (ACC-01) and pass ``accept`` (the stability and evidence
    criteria, ACC-04 to ACC-06), a callable ``(option, pool_values) -> (ok,
    why)`` or ``(ok, why, detail)`` (``acceptance.pool_acceptor``: ``detail``
    carries every criterion's verdict); the local reference needs no recovery
    evidence and is passed as option ``"local"``. ``withhold`` removes stations
    from every pool, which is how a recovery test keeps a region's own reference
    out of what it scores.

    Methodology 0.16 (owner decisions D11 and D13, 2026-09-28). ``curve_check``,
    a callable ``(option, pool_values, context) -> (ok, why, record)``
    (``acceptance.curve_checker``), refuses an option whose curve would be the
    engine's degenerate fallback or would read as inverted against the option's
    own in-frame pressured stations (``context["pressure_values"]``); a refused
    option is recorded with the reason and the ladder moves on, so no fallback
    ramp is ever published. ``flagged`` (``reference_hierarchy.flagged_transfer``;
    None reads the config) governs the flagged-transfer rung, REF-16: when no
    option passes, a second pass over the options that failed the recovery
    evidence ALONE (ACC-05/06; the sample, stability, comparability and curve
    checks all passed) takes the narrowest with usable n at the adequate floor
    (``prefer_adequate``), else the narrowest with at least ``min_usable``; the
    decision carries transfer risk ``unvalidated``, the verdict it was refused
    with (``transfer_validation``), the ``confidence_cap`` and a note that says
    so. The caller keeps a validated source of any kind ahead of a flagged one
    (``pressure_evidence.run_evidence`` tries the national, modeled and published
    sources for a flagged metric first).

    Which passing option is used is ``ladder_rule`` (campaign Round 2 candidate
    B1; None reads ``reference_pool.ladder_rule``): ``first_pass``, the first
    option that passes, is methodology 0.14; ``narrowest_adequate`` walks every
    option and uses the narrowest that passes with usable n at the adequate floor,
    else the narrowest exploratory option that passes. With the regional screen
    off (``reference_hierarchy.regional_screen.enabled: false``, candidate B2)
    every wider pool admits strict-screen stations only, the Level III regional
    option is the local reference itself and is not tried again, and the ledger's
    ``admitted_by`` column says which screen admitted each station.
    ``withhold_l3`` keeps those ecoregions' stations out of the EPA agriculture
    calibration of the regional screen (the campaign's fold rule).

    ``values`` is indexed by station key (the metric's latest compatible value).
    ``frame`` is :func:`national_frame`. Returns the decision and the ledger
    rows for this metric (one per station per option tried).
    """
    cfg = cfg if cfg is not None else load_transfer_config()
    st = settings or floors()
    hs = hierarchy_settings()
    fl = dict(flagged) if flagged is not None else acceptance.flagged_settings()
    rule = str(ladder_rule or methodology.ladder_rule())
    if rule not in methodology.LADDER_RULES:
        raise ValueError(f"unknown ladder rule {rule!r}; expected one of "
                         f"{', '.join(methodology.LADDER_RULES)}")
    regional_on = bool(hs.get("enabled", True))
    excluded = {str(k): str(v) for k, v in (excluded or {}).items()}
    withheld = {str(k) for k in (withhold or ())}
    target_l3 = str(target_l3)
    target = frame[frame["l3"].astype(str) == target_l3]
    codes = {"l3": target_l3}
    for level in ("l2", "l1", "nars9"):
        got = target[level].dropna().astype(str) if level in target.columns else pd.Series(dtype=str)
        codes[level] = got.mode().iat[0] if len(got) else None

    covariates = list((profile or {}).get("covariates") or [])
    use_lith = bool((profile or {}).get("lithology"))
    raw_envelope = envelope_for(target, covariates, quantiles=st["quantiles"], cfg=cfg)
    envelope = widen_envelope(raw_envelope, national_spans(frame, covariates, cfg), hs["widen"])
    lith_groups = target_lith_groups(target, cfg) if use_lith else []
    coverage = self_coverage(target, envelope, lith_groups, use_lithology=use_lith, cfg=cfg)
    supported = (scale_entry or {}).get("supported_level")
    fauna = fauna_groups_of(target, cfg) if (profile or {}).get("fauna") else []
    if regional_on:
        # EPA designates reference among all its sites, boatable ones included, so the
        # calibration reads the whole station table rather than the wadeable frame
        ag = epa_agriculture_limit(rscreen.load_station_screen(), codes.get("nars9"), hs,
                                   withhold_l3=withhold_l3)
        regional_ok = regional_screen_mask(frame, ag["limit"])
        screen_detail = {"id": hs["screen_id"], "agriculture_limit": ag["limit"],
                         "agriculture_rule": ag["rule"], "nars9": ag["nars9"],
                         "n_epa_reference": ag["n_epa_reference"],
                         "epa_quantile_value": ag["epa_quantile_value"],
                         "base_tier": "relaxed"}
        if ag.get("withheld_l3"):
            screen_detail["calibration_withheld_l3"] = list(ag["withheld_l3"])
    else:
        # candidate B2: the relaxed tier and the EPA agriculture limit never apply
        regional_ok = pd.Series(False, index=frame.index)
        screen_detail = {"id": hs["screen_id"], "enabled": False, "base_tier": "strict"}

    options = [("local", "l3", SCREEN_STRICT)]
    if profile is not None:
        for g in search_order(profile):
            if regional_on:
                options.append((f"regional_{g}", g, SCREEN_REGIONAL))
            elif g != "l3":
                options.append((f"regional_{g}", g, SCREEN_STRICT))
    if only_option is not None:
        # one option on its own, as a recovery test scores each separately
        options = [o for o in options if o[0] == only_option]

    vals = pd.to_numeric(values, errors="coerce")
    rows: list[pd.DataFrame] = []
    tried: list[dict] = []
    if not regional_on and profile is not None and "l3" in search_order(profile) \
            and only_option in (None, "regional_l3"):
        tried.append({"option": "regional_l3", "level": "l3", "screen": SCREEN_STRICT,
                      "why": "the regional screen is off, so this ecoregion's option is "
                             "the local reference"})
    chosen: Optional[dict] = None
    #: narrowest_adequate: the narrowest exploratory option that passed, used
    #: only when no option is adequate
    fallback: Optional[dict] = None
    #: REF-16: the options refused by the recovery evidence alone, in search
    #: order, each with the verdict it was refused with
    flaggable: list[dict] = []
    for option, level, screen in options:
        code = codes.get(level)
        if code is None:
            tried.append({"option": option, "level": level, "screen": screen,
                          "why": "the target has no region at this level"})
            continue
        members = frame[frame[level].astype(str) == str(code)].copy()
        keys = members["station_key"].astype(str)
        is_local = members["l3"].astype(str) == target_l3
        # a station the strict screen passes passes every looser screen, so the
        # regional pools admit the strict stations and the regional ones
        strict_ok = members["pass_strict"].astype(bool)
        regional_here = regional_ok.reindex(members.index).fillna(False).astype(bool)
        passes = strict_ok if screen == SCREEN_STRICT else strict_ok | regional_here
        admitted_by = pd.Series("", index=members.index, dtype=object)
        admitted_by[strict_ok] = SCREEN_STRICT
        if screen == SCREEN_REGIONAL:
            admitted_by[~strict_ok & regional_here] = SCREEN_REGIONAL
        comparable, why = comparable_mask(members, envelope, lith_groups,
                                          use_lithology=use_lith, cfg=cfg)
        if fauna:
            fm = fauna_mask(members, fauna, cfg)
            why = why.where(fm | (why != ""), "fauna")
            comparable = comparable & fm
        comparable = comparable | is_local            # the region's own are always admitted
        owner_out = keys.isin(set(excluded))
        held = keys.isin(withheld)
        value = keys.map(vals)
        has_value = value.notna()

        reason = pd.Series("", index=members.index, dtype=object)
        failed = ~passes
        reason[failed] = ("failed_screen:" + members.loc[failed, "fail_strict"].astype(str)
                          if screen == SCREEN_STRICT and "fail_strict" in members.columns
                          else "failed_regional_screen")
        step = passes & held
        reason[step] = "withheld_for_test"
        step = passes & ~held & owner_out
        reason[step] = "excluded_by_owner:" + keys[step].map(excluded).astype(str)
        step = passes & ~held & ~owner_out & ~comparable
        reason[step] = why[step]
        step = passes & ~held & ~owner_out & comparable & ~has_value
        reason[step] = "no_value"
        in_pool = passes & ~held & ~owner_out & comparable & has_value

        n_pool = int((passes & ~held & ~owner_out).sum())
        n_comp = int((passes & ~held & ~owner_out & comparable).sum())
        n_use = int(in_pool.sum())
        info = {"option": option, "level": level, "screen": screen, "region_code": str(code),
                "n_pool": n_pool, "n_comparable": n_comp, "n_usable": n_use,
                "n_local": int((in_pool & is_local).sum())}
        ledger = pd.DataFrame({
            "metric": metric, "station_key": keys.values, "level": level,
            "in_pool": in_pool.values, "reason": reason.values, "value": value.values,
            "source_cycle": members.get("source_cycle", pd.Series(None, index=members.index)).values,
            "l3": members["l3"].values, "l2": members["l2"].values, "l1": members["l1"].values,
            "option": option, "screen": screen, "admitted_by": admitted_by.values})
        rows.append(ledger)
        if n_use < st["exploratory"]:
            tried.append({**info, "why": f"{n_use} usable stations, below the floor of "
                                         f"{st['exploratory']}"})
            continue
        pool_values = pd.Series(value[in_pool].to_numpy(dtype="float64"),
                                index=keys[in_pool].to_numpy())
        accepted, why_not, detail = True, "", None
        if accept is not None:
            got = accept("local" if option == "local" else option, pool_values)
            accepted, why_not = bool(got[0]), str(got[1] or "")
            detail = got[2] if len(got) > 2 and isinstance(got[2], dict) else None
        # REF-16: an option refused by the recovery evidence alone is a candidate
        # for the flagged rung, so it faces the curve checks like an accepted one
        evidence_only = (not accepted) and acceptance.evidence_only_refusal(detail)
        checks = list((detail or {}).get("checks") or [])
        curve_rec: dict = {}
        if curve_check is not None and (accepted or evidence_only):
            # D13: the option's own in-frame pressured stations, for the inverted check
            pressure_values = None
            if "pass_relaxed" in members.columns:
                evaluable = (members["screen_evaluable"].astype(bool)
                             if "screen_evaluable" in members.columns
                             else pd.Series(True, index=members.index))
                pressured = evaluable & ~members["pass_relaxed"].astype(bool) & value.notna()
                pressure_values = pd.Series(value[pressured].to_numpy(dtype="float64"),
                                            index=keys[pressured].to_numpy())
            curve_ok, curve_why, curve_rec = curve_check(
                option, pool_values, {"level": level, "region_code": str(code), "option": option,
                                      "pressure_values": pressure_values})
            curve_rec = dict(curve_rec or {})
            checks = checks + [{"check": acceptance.CURVE_CHECK, "pass": bool(curve_ok),
                                "why": "" if curve_ok else str(curve_why)}]
            if not curve_ok:
                tried.append({**info, "why": str(curve_why), "refused_by": "curve",
                              "checks": checks, "curve": curve_rec})
                continue
        if not accepted:
            entry = {**info, "why": why_not}
            if checks:
                entry["checks"] = checks
            if evidence_only and fl.get("enabled") and n_use >= int(fl.get("min_usable") or 0):
                entry["flag_eligible"] = True
                flaggable.append({**info, "ids": tuple(keys[in_pool]),
                                  "n_huc12": int(_cluster_ids(members[in_pool]).nunique()),
                                  "validation": dict((detail or {}).get("validation") or {}),
                                  "curve": curve_rec, "tried_index": len(tried)})
            tried.append(entry)
            continue
        candidate = {**info, "ids": tuple(keys[in_pool]),
                     "n_huc12": int(_cluster_ids(members[in_pool]).nunique())}
        record = {**info, "why": "accepted"}
        if checks:
            record["checks"] = checks
        if curve_rec:
            record["curve"] = curve_rec
        if rule == methodology.LADDER_RULE_FIRST_PASS or n_use >= st["adequate"]:
            chosen = candidate
            tried.append(record)
            break
        # narrowest_adequate: an exploratory pool that passes is kept in reserve
        # while a wider adequate pool is looked for
        tried.append({**record, "why": f"passes acceptance with {n_use} usable stations, "
                                       f"exploratory; a wider adequate pool (at least "
                                       f"{st['adequate']}) is looked for first"})
        if fallback is None:
            fallback = candidate
    if chosen is None and fallback is not None:
        chosen = fallback
        for x in tried:
            if x.get("option") == fallback["option"] and "n_usable" in x:
                x["why"] = ("accepted: the narrowest exploratory pool that passes, since no "
                            "wider pool is adequate")
    # --- REF-16, the flagged-transfer rung: the second pass ---
    # No option passed the validated acceptance. Of the options the recovery
    # evidence alone refused, the narrowest holding the adequate floor of usable
    # comparable stations is taken (prefer_adequate), else the narrowest at all;
    # the verdict rides on the decision and the curve is flagged, never a gap.
    flagged_pick: Optional[dict] = None
    if chosen is None and flaggable and fl.get("enabled"):
        adequate = [c for c in flaggable if c["n_usable"] >= st["adequate"]]
        flagged_pick = adequate[0] if (fl.get("prefer_adequate", True) and adequate) else flaggable[0]
        chosen = flagged_pick
        mark = tried[flagged_pick["tried_index"]]
        mark["accepted"] = True
        mark["flagged"] = True
        mark["why"] = ("accepted under the flagged-transfer rung (REF-16): no option passed the "
                       "validated acceptance, and this is the narrowest comparable pool with "
                       f"{flagged_pick['n_usable']} usable stations that passes every check but "
                       "the recovery evidence, which refused it: " + str(mark.get("why") or ""))

    ledger_all = (pd.concat(rows, ignore_index=True) if rows
                  else pd.DataFrame(columns=LEDGER_COLUMNS))
    levels_tried = [{k: v for k, v in x.items() if k in ("level", "region_code", "n_pool",
                                                            "n_comparable", "n_usable", "n_local")}
                    for x in tried if "n_pool" in x]
    if chosen is None:
        # Report the best attempt, and the widest option on a tie: it is the last
        # thing that was tried, so its counts say why the hierarchy ran out.
        counted = [x for x in tried if "n_usable" in x]
        best = (max(reversed(counted), key=lambda x: x["n_usable"]) if counted else {})
        decision = PoolDecision(
            metric=metric, status=STATUS_INSUFFICIENT, level=None, region_code=None,
            region_name=None, family=(profile or {}).get("family"),
            n_pool=int(best.get("n_pool") or 0), n_comparable=int(best.get("n_comparable") or 0),
            n_usable=int(best.get("n_usable") or 0), n_local=int(best.get("n_local") or 0),
            n_huc12=0, disposition="insufficient", covariates=covariates,
            envelope=_envelope_record(envelope), lith_groups=lith_groups,
            self_coverage=_round(coverage), supported_level=supported,
            transfer_risk=RISK_NONE,
            transfer_note=("No station pool, local or regional, holds enough comparable "
                           "least-disturbed stations with a value for this metric and passes "
                           "acceptance. Insufficient reference support from stations."),
            station_ids=(), levels_tried=levels_tried, screen_detail=screen_detail,
            options_tried=tried)
        ledger_all = ledger_all.assign(in_pool=False) if len(ledger_all) else ledger_all
        return decision, ledger_all

    level = chosen["level"]
    screen = chosen["screen"]
    risk = transfer_risk(level, supported)
    name = _level_name(frame, level, chosen["region_code"])
    disposition = "adequate" if chosen["n_usable"] >= st["adequate"] else "exploratory"
    validation: dict = {}
    cap: Optional[int] = None
    if flagged_pick is not None:
        risk = RISK_UNVALIDATED
        validation = dict(flagged_pick.get("validation") or {})
        cap = fl.get("confidence_cap")
        where = _transfer_note(
            level, name, chosen["region_code"], chosen["n_usable"], chosen["n_local"],
            covariates, use_lith and bool(lith_groups), RISK_UNVALIDATED, supported,
            screen_detail=screen_detail if screen == SCREEN_REGIONAL else None, fauna=fauna)
        note = _flagged_note(where, validation, cap, disposition, acceptance.settings())
    else:
        note = ("" if chosen["option"] == "local" else _transfer_note(
            level, name, chosen["region_code"], chosen["n_usable"], chosen["n_local"],
            covariates, use_lith and bool(lith_groups), risk, supported,
            screen_detail=screen_detail if screen == SCREEN_REGIONAL else None, fauna=fauna))
    decision = PoolDecision(
        metric=metric, status=STATUS_BY_OPTION[(level, screen)], level=level,
        region_code=chosen["region_code"], region_name=name,
        family=(profile or {}).get("family"), n_pool=chosen["n_pool"],
        n_comparable=chosen["n_comparable"], n_usable=chosen["n_usable"],
        n_local=chosen["n_local"], n_huc12=chosen["n_huc12"],
        disposition=disposition,
        covariates=covariates, envelope=_envelope_record(envelope), lith_groups=lith_groups,
        self_coverage=_round(coverage), supported_level=supported, transfer_risk=risk,
        transfer_note=note, station_ids=chosen["ids"], levels_tried=levels_tried,
        screen=screen, screen_detail=screen_detail if screen == SCREEN_REGIONAL else {},
        options_tried=tried, transfer_validation=validation, confidence_cap=cap)
    # only the option used is the pool; the others were tried or never needed
    ledger_all.loc[ledger_all["option"] != chosen["option"], "in_pool"] = False
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
                excluded: Optional[dict] = None, cfg: Optional[dict] = None,
                accept_for=None, curve_check_for=None,
                flagged: Optional[dict] = None) -> dict:
    """Pools for every metric of one region.

    ``values_wide`` is keyed by ``site_id`` (the station key), one column per
    metric. Returns ``decisions`` (metric -> PoolDecision), ``ledger``,
    ``local_comparison`` (metric -> dict) and ``data``: one row per station that
    is in ANY metric's pool, each metric column holding a value only where the
    station is in that metric's pool. Masking is what lets the curve builder,
    the diagnostics and a reopened session all reproduce a per-metric pool from
    a single data frame.

    ``accept_for`` (metric -> the ``accept`` callable of :func:`choose_pool`)
    applies the acceptance criteria of methodology 0.14 to every pool option;
    without it a pool is taken on the sample floor alone. ``curve_check_for``
    (metric -> the ``curve_check`` callable) applies the curve checks of
    methodology 0.16 (D13), and ``flagged`` governs the flagged-transfer rung
    (REF-16), both as :func:`choose_pool` describes.
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
            scale_entry=reg.get(mk), excluded=excluded, cfg=cfg, settings=settings,
            accept=accept_for(mk) if accept_for is not None else None,
            curve_check=curve_check_for(mk) if curve_check_for is not None else None,
            flagged=flagged)
        decisions[mk] = decision
        ledgers.append(ledger)
        comp = local_comparison(mk, series, target, cfg)
        if comp:
            comparisons[mk] = comp

    ledger = (pd.concat(ledgers, ignore_index=True) if ledgers
              else pd.DataFrame(columns=LEDGER_COLUMNS))
    return {"decisions": decisions, "ledger": ledger, "local_comparison": comparisons,
            "data": pool_data(decisions, values_wide, frame),
            "target_n_frame": int(len(target)),
            "target_n_strict": int(target["pass_strict"].astype(bool).sum()),
            "target_n_relaxed": int(target["pass_relaxed"].astype(bool).sum())}


def pool_data(decisions: dict, values_wide: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """The masked pooled frame of a set of station-pool decisions: one row per
    station in ANY pool, each metric column holding a value only inside that
    metric's pool. A decision with no pool (insufficient, or a source after the
    station pools) contributes no column and no station. Recomputed by the build
    after the sources after the station pools have decided, so a flagged pool a
    validated source replaced leaves the frame and the run seed (REF-16)."""
    wide = values_wide.set_index(values_wide["site_id"].astype(str)) \
        if "site_id" in values_wide.columns else values_wide
    pools = {mk: d for mk, d in decisions.items()
             if d.status != STATUS_INSUFFICIENT and d.status not in LADDER_STATUSES
             and curve_basis.resolve(d.basis) == curve_basis.REGIONAL}
    union = sorted({sid for d in pools.values() for sid in d.station_ids})
    base = frame[frame["station_key"].astype(str).isin(union)].copy()
    base["site_id"] = base["station_key"].astype(str)
    base = base.sort_values("site_id").reset_index(drop=True)
    for mk, d in pools.items():
        if mk not in wide.columns:
            continue
        members = set(d.station_ids)
        col = base["site_id"].map(pd.to_numeric(wide[mk], errors="coerce"))
        base[mk] = col.where(base["site_id"].isin(members))
    return base


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
            "supported_level": d.supported_level, "transfer_risk": d.transfer_risk,
            "transfer_validation": validation_words(d.transfer_validation),
            "confidence_cap": d.confidence_cap})
    return pd.DataFrame(rows)


def validation_words(validation: Optional[dict]) -> str:
    """A flagged transfer's recovery verdict as one table cell: the evidence key,
    the verdict, class agreement, net optimism and the cell count (REF-16);
    empty for a decision that carries none."""
    v = validation or {}
    if not v:
        return ""
    parts = [str(v.get("basis") or ""), str(v.get("verdict") or "not evaluated")]
    a1, net, n = v.get("a1_share"), v.get("net_opt"), v.get("n_cells")
    if a1 is not None:
        parts.append(f"a1_share {float(a1):.4f}")
    if net is not None:
        parts.append(f"net_opt {float(net):+.4f}")
    if n is not None:
        parts.append(f"n_cells {int(n)}")
    if v.get("evidence"):
        parts.append(f"evidence {v['evidence']}")
    return "; ".join(p for p in parts if p)


def regional_screen_label(detail: dict) -> str:
    """The regional screen in words, with the agriculture limit it used."""
    lim = detail.get("agriculture_limit")
    how = ("the 90th percentile at EPA's reference sites of the NARS-9 region"
           if detail.get("agriculture_rule") == "epa_reference_range" else "the 25 percent floor")
    return (f"{detail.get('id') or 'least-disturbed-regional-v1'} (relaxed tier, watershed "
            f"agriculture at most {float(lim):g} percent, {how})" if lim is not None
            else str(detail.get("id") or "least-disturbed-regional-v1"))


def reference_support_record(d, *, screen_tier: str = "strict") -> dict:
    """The per-metric ``referenceSupport`` block of a published bundle.

    ``d`` is a :class:`PoolDecision` or its ``to_dict()`` form (what the
    evidence of a run carries)."""
    if isinstance(d, PoolDecision):
        d = d.to_dict()
    level = d.get("level")
    basis = curve_basis.resolve(d.get("basis"))
    detail = d.get("screen_detail") or {}
    regional = d.get("screen") == SCREEN_REGIONAL
    screen = (regional_screen_label(detail) if regional
              else rscreen.screen_label(screen_tier))
    flagged = str(d.get("transfer_risk") or "") == RISK_UNVALIDATED
    return {
        "status": d.get("status"), "level": level,
        "levelLabel": LEVEL_LABELS.get(level or "", ""),
        "regionCode": d.get("region_code"), "regionName": d.get("region_name"),
        "screen": screen,
        **({"screenId": detail.get("id"),
            "agricultureLimit": detail.get("agriculture_limit"),
            "agricultureRule": detail.get("agriculture_rule")} if regional else {}),
        "nPool": d.get("n_pool"), "nComparable": d.get("n_comparable"),
        "nUsable": d.get("n_usable"), "nLocal": d.get("n_local"),
        "nHuc12": d.get("n_huc12"), "disposition": d.get("disposition"),
        "family": d.get("family"),
        "covariates": list(d.get("covariates") or [])
                      + (["lith_group"] if d.get("lith_groups") else []),
        "selfCoverage": d.get("self_coverage"), "supportedLevel": d.get("supported_level"),
        "transferRisk": d.get("transfer_risk"), "transferNote": d.get("transfer_note"),
        # REF-16 (methodology 0.16): the verdict a flagged transfer was refused
        # with and the cap it carries; absent on every other record, so no
        # published record changes shape
        **({"transferValidation": dict(d.get("transfer_validation") or {}),
            "confidenceCap": d.get("confidence_cap"),
            "rule": RULE_FLAGGED} if flagged else {}),
        "basis": basis, "basisLabel": curve_basis.label_for(basis),
        "basisStatement": curve_basis.statement_for(basis),
        "basisLimit": curve_basis.limit_for(basis),
    }


def flagged_limitation(d, st: Optional[dict] = None) -> str:
    """The limitation sentence a flagged transfer's curve carries (REF-16), from a
    :class:`PoolDecision`, its ``to_dict`` form or the bundle's ``referenceSupport``
    record; empty for anything not flagged. The same words DEEP and the
    calculator print (``acceptance.transfer_limitation``)."""
    if isinstance(d, PoolDecision):
        d = d.to_dict()
    d = d or {}
    if str(d.get("transfer_risk") or d.get("transferRisk") or "") != RISK_UNVALIDATED:
        return ""
    validation = d.get("transfer_validation") or d.get("transferValidation") or {}
    cap = d.get("confidence_cap") if "confidence_cap" in d else d.get("confidenceCap")
    return acceptance.transfer_limitation(validation, cap, st)
