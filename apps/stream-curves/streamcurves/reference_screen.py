"""The reference screen: a fixed desktop pressure screen, never a score.

Rule REF-04 (methodology 0.12, owner decision 2026-09-19). A station is a
least-disturbed reference site when its watershed passes every condition of
EASI's ``least-disturbed-v1`` screen: impervious cover, agriculture (crop plus
hay), road density, degree of regulation, mapped dams, permitted discharges and
mines, inside the wadeable, non-canal frame. It is the screen EASI's own
regional reference curves were built with, so the two tiers define reference
the same way.

Why not the EASI condition index, which gated reference sites before: the
index changes whenever EASI's criteria do (eight of twenty functions were
re-rated on 2026-09-15 under an unchanged preset), it reads the same landscape
variables that later receive curves, and it costs a live run per site. The
screen is a fixed set of published landscape summaries by COMID. It needs no
service at run time, because ``scripts/nrsa/build_station_screen.py`` evaluates
it once for every station of the NRSA archive and commits the table.

Never a percentile rule: in a converted region a "least-disturbed fifth" would
make converted watersheds the reference.

Pure apart from :func:`load_station_screen` (one cached parquet read).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import methodology
from .paths import DATA_DIR

SCREEN_ID = "least-disturbed-v1"
TIERS = ("strict", "relaxed")

NRSA_DIR = DATA_DIR / "nrsa"
STATION_SCREEN_PATH = NRSA_DIR / "station_screen.parquet"
STATION_SCREEN_META_PATH = NRSA_DIR / "station_screen.meta.json"
VENDORED_CURVES_PATH = (Path(__file__).resolve().parent / "_vendor" / "easi" / "data"
                        / "reference-curves.json")

#: EASI's national builder prefixes the StreamCat candidates it caches with
#: ``sc__``. The station table uses StreamCat's own names.
VARIABLE_ALIASES = {"sc__nabd_densws": "nabd_densws", "sc__npdesdensws": "npdesdensws"}

#: Plain words for a fail reason, in the order a reader expects them.
VARIABLE_LABELS = {
    "pctimp2019ws": "watershed impervious cover (%)",
    "agriculture_ws": "watershed agriculture, crop plus hay (%)",
    "rddensws": "watershed road density (km/km2)",
    "dor": "degree of regulation (%)",
    "nabd_densws": "mapped dams (per km2)",
    "npdesdensws": "permitted discharges (per km2)",
    "mines_ws": "mines (per km2)",
}

_OPS = {
    "<=": lambda v, t: v <= t,
    "<": lambda v, t: v < t,
    "==": lambda v, t: v == t,
    ">=": lambda v, t: v >= t,
    ">": lambda v, t: v > t,
    "!=": lambda v, t: v != t,
}

# EASI's stratifier breakpoints (easi/geo.py slope_class, builder strata.py).
SLOPE_BREAKS = (0.005, 0.02)                 # m/m: the 0.5 % and 2 % classes
SLOPE_LABELS = ("lt_0.5", "0.5_to_2", "ge_2")
DA_BREAKS = (10.0, 100.0)                    # km2
DA_LABELS = ("le_10", "10_to_100", "gt_100")


# --------------------------------------------------------------------------- #
# the rules: governed in methodology_config, mirrored from the vendored EASI
# --------------------------------------------------------------------------- #
def _canonical(name: str) -> str:
    return VARIABLE_ALIASES.get(str(name), str(name))


def _normalize_rules(block: Optional[dict]) -> dict[str, tuple[str, float]]:
    out: dict[str, tuple[str, float]] = {}
    for name, rule in (block or {}).items():
        op, value = rule[0], rule[1]
        out[_canonical(name)] = (str(op), float(value))
    return out


@lru_cache(maxsize=1)
def vendored_screen() -> dict:
    """The screen block EASI's frozen curve file records
    (``reference-curves.json`` -> ``provenance.screen``), names normalized."""
    doc = json.loads(VENDORED_CURVES_PATH.read_text(encoding="utf-8"))
    screen = ((doc.get("provenance") or {}).get("screen")) or {}
    return {
        "id": screen.get("id"),
        "strict": _normalize_rules(screen.get("strict")),
        "relaxed": _normalize_rules(screen.get("relaxed")),
        "frame": {k: [v[0], v[1]] for k, v in (screen.get("frame") or {}).items()},
        "roadDensityCap": screen.get("roadDensityCap"),
    }


def governed_screen() -> dict:
    """The ``reference_screen`` block of the methodology config, normalized the
    same way, so the two can be compared value for value."""
    block = methodology.threshold("reference_screen")
    return {
        "id": block.get("id"),
        "strict": _normalize_rules(block.get("strict")),
        "relaxed": _normalize_rules(block.get("relaxed")),
        "frame": {k: [v[0], v[1]] for k, v in (block.get("frame") or {}).items()},
        "roadDensityCap": block.get("road_density_cap"),
    }


def screen_drift() -> list[str]:
    """Differences between the governed screen and the one the vendored EASI
    built its reference curves with. Empty means none.

    The config MIRRORS the EASI screen (the ``easi_presets`` idiom): the point
    of rule REF-04 is that both tiers define reference identically, so an edit
    to one side alone must be loud.
    """
    problems: list[str] = []
    try:
        want, have = vendored_screen(), governed_screen()
    except Exception as exc:  # noqa: BLE001 - an unreadable side is itself the finding
        return [f"reference_screen: cannot compare the screen ({exc})"]
    if have.get("id") != want.get("id"):
        problems.append(f"reference_screen.id is {have.get('id')!r} but the vendored EASI "
                        f"screen is {want.get('id')!r}")
    for tier in TIERS:
        if have[tier] != want[tier]:
            for name in sorted(set(have[tier]) | set(want[tier])):
                if have[tier].get(name) != want[tier].get(name):
                    problems.append(
                        f"reference_screen.{tier}.{name} is {have[tier].get(name)} but the "
                        f"vendored EASI screen has {want[tier].get(name)}")
    if have.get("frame") != want.get("frame"):
        problems.append(f"reference_screen.frame is {have.get('frame')} but the vendored "
                        f"EASI screen has {want.get('frame')}")
    if have.get("roadDensityCap") != want.get("roadDensityCap"):
        problems.append(f"reference_screen.road_density_cap is {have.get('roadDensityCap')} "
                        f"but the vendored EASI screen has {want.get('roadDensityCap')}")
    return problems


def rules(tier: str = "strict") -> dict[str, tuple[str, float]]:
    """``{variable: (operator, value)}`` for one tier of the governed screen."""
    if tier not in TIERS:
        raise ValueError(f"unknown screen tier {tier!r}; known: {', '.join(TIERS)}")
    return dict(governed_screen()[tier])


def screen_label(tier: str = "strict") -> str:
    return f"{SCREEN_ID} ({tier}), wadeable non-canal frame"


def resolve_reference_method(choice: Optional[str], dataset_id: str) -> str:
    """The reference method a command-line build uses.

    No choice means the pressure screen on the pooled NRSA archive (the
    station table is keyed by its stations) and the legacy ECI gate on the
    legacy data set, which is what reproduces a published version. Asking for
    the pressure screen on the legacy data set is an error, never a silent
    switch.
    """
    from . import nrsa_dataset, run_state
    pooled = dataset_id == nrsa_dataset.MULTI_CYCLE_DATASET_ID
    if not choice:
        return (run_state.REFERENCE_METHOD_PRESSURE if pooled and station_screen_available()
                else run_state.REFERENCE_METHOD_EASI)
    if choice not in run_state.REFERENCE_METHODS:
        raise ValueError(f"unknown reference method {choice!r}; known: "
                         f"{', '.join(run_state.REFERENCE_METHODS)}")
    if choice == run_state.REFERENCE_METHOD_PRESSURE and not pooled:
        raise ValueError(
            "the pressure-screen reference method reads the pooled NRSA archive "
            f"({nrsa_dataset.MULTI_CYCLE_DATASET_ID}); pass --reference-method easi-eci "
            f"with --nrsa-dataset {dataset_id}")
    return choice


# --------------------------------------------------------------------------- #
# derived variables and classes (EASI's formulas, tools/easi-national values.py)
# --------------------------------------------------------------------------- #
def _num(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[name], errors="coerce").astype("float64")


def derive_screen_variables(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``agriculture_ws``, ``dor`` and ``mines_ws`` from their StreamCat parts.

    A missing part makes the derived value missing, never a smaller number:
    agriculture is crop plus hay, the degree of regulation is normalized dam
    storage over annual runoff as a percent (undefined where runoff is not
    positive), and mines are mines plus coal mines.
    """
    out = df.copy()
    out["agriculture_ws"] = _num(out, "pctcrop2019ws") + _num(out, "pcthay2019ws")
    storage, runoff = _num(out, "damnrmstorws"), _num(out, "runoffws")
    safe = runoff.where(runoff > 0)
    out["dor"] = 100.0 * storage / (1000.0 * safe)
    out["mines_ws"] = _num(out, "minedensws") + _num(out, "coalminedensws")
    return out


def evaluate(df: pd.DataFrame, tier: str = "strict") -> tuple[pd.Series, pd.Series]:
    """``(passes, fail_reasons)`` for one tier.

    A station passes only when EVERY rule holds. A variable with no value fails
    its rule: a watershed that cannot be shown to be low-pressure is not a
    reference watershed. ``fail_reasons`` joins the failing variables with
    ``;`` and writes ``missing:<variable>`` for an absent value.
    """
    rule_set = rules(tier)
    passes = pd.Series(True, index=df.index)
    reasons = pd.Series("", index=df.index, dtype=object)
    for name, (op, value) in rule_set.items():
        vals = _num(df, name)
        missing = vals.isna()
        ok = _OPS[op](vals, value) & ~missing
        passes &= ok
        tag = pd.Series("", index=df.index, dtype=object)
        tag[missing] = f"missing:{name}"
        tag[~missing & ~ok] = name
        add = tag != ""
        reasons[add] = np.where(reasons[add] == "", tag[add], reasons[add] + ";" + tag[add])
    return passes.astype(bool), reasons


def describe_failures(row: Any, tier: str = "strict") -> str:
    """One station's fail reasons in plain words with the values, for a table."""
    rule_set = rules(tier)
    parts = []
    for name, (op, value) in rule_set.items():
        raw = row.get(name) if hasattr(row, "get") else None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = float("nan")
        label = VARIABLE_LABELS.get(name, name)
        if v != v:
            parts.append(f"{label} has no value")
        elif not _OPS[op](v, value):
            parts.append(f"{label} {v:g} is not {op} {value:g}")
    return "; ".join(parts)


def slope_class(slope: Any) -> Optional[str]:
    """EASI's channel-slope class of a slope in m/m (0.5 % and 2 % breaks, a
    boundary value belongs to the steeper class)."""
    try:
        v = float(slope)
    except (TypeError, ValueError):
        return None
    if v != v or v < 0:
        return None
    if v < SLOPE_BREAKS[0]:
        return SLOPE_LABELS[0]
    if v < SLOPE_BREAKS[1]:
        return SLOPE_LABELS[1]
    return SLOPE_LABELS[2]


def da_class(area_sqkm: Any) -> Optional[str]:
    """Drainage-area class (10 and 100 km2, a boundary value belongs to the
    smaller class)."""
    try:
        v = float(area_sqkm)
    except (TypeError, ValueError):
        return None
    if v != v or v <= 0:
        return None
    if v <= DA_BREAKS[0]:
        return DA_LABELS[0]
    if v <= DA_BREAKS[1]:
        return DA_LABELS[1]
    return DA_LABELS[2]


# --------------------------------------------------------------------------- #
# the committed station table
# --------------------------------------------------------------------------- #
def station_screen_available(path: Optional[Path] = None) -> bool:
    return Path(path or STATION_SCREEN_PATH).exists()


@lru_cache(maxsize=2)
def _load_station_screen(path_str: str) -> pd.DataFrame:
    return pd.read_parquet(path_str)


def load_station_screen(path: Optional[Path] = None) -> pd.DataFrame:
    """One row per NRSA station: geography, natural setting, the screen
    variables, and the strict and relaxed flags. Cached; treat as read-only."""
    p = Path(path or STATION_SCREEN_PATH)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} is not built; run scripts/nrsa/build_station_screen.py")
    return _load_station_screen(str(p))


def clear_cache() -> None:
    _load_station_screen.cache_clear()
    vendored_screen.cache_clear()


def file_sha256(path: Path) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def station_screen_identity(path: Optional[Path] = None) -> dict:
    """What a run records about the table it read: the file's own hash (never
    the hash the meta file claims), plus the build facts from the meta file."""
    p = Path(path or STATION_SCREEN_PATH)
    meta_path = p.with_name(p.stem + ".meta.json")
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - an unreadable meta file is reported, not fatal
            meta = {}
    return {
        "path": f"data/nrsa/{p.name}",
        "sha256": file_sha256(p),
        "rows": meta.get("rows"),
        "screenId": meta.get("screenId") or SCREEN_ID,
        "easiMethodVersion": meta.get("easiMethodVersion"),
        "source": meta.get("source"),
        "builtAt": meta.get("builtAt"),
    }


# --------------------------------------------------------------------------- #
# the screening tables the rest of the app already reads
# --------------------------------------------------------------------------- #
METHOD = "pressure_screen"


def to_screening_tables(panel: pd.DataFrame, screen: Optional[pd.DataFrame] = None, *,
                        tier: str = "strict") -> dict:
    """The three screening tables, in the shape the EASI screen produced, from
    the station table. ``panel`` needs ``site_id`` (the station key), ``lat``,
    ``lon``; everything downstream (retained ids, reviewer overrides, site
    exclusions, the session) reads these tables unchanged.

    A station the table does not hold is ``not_evaluable``, never retained.
    """
    table = load_station_screen() if screen is None else screen
    by_key = table.set_index("station_key")
    flag = "pass_strict" if tier == "strict" else "pass_relaxed"
    rows: list[dict] = []
    for rec in panel.itertuples(index=False):
        sid = str(getattr(rec, "site_id"))
        base = {"site_id": sid, "lat": getattr(rec, "lat", None),
                "lon": getattr(rec, "lon", None)}
        if sid not in by_key.index:
            rows.append({**base, "state": "failed", "comid": getattr(rec, "comid", None),
                         "eci": None, "condition": None,
                         "auto_decision": "not_evaluable", "final_decision": "not_evaluable",
                         "issue_code": "no_screen_record",
                         "issue": "the station screen table holds no record for this station",
                         "reason": "no screen record", "comid_source": "archive"})
            continue
        s = by_key.loc[sid]
        if isinstance(s, pd.DataFrame):          # defensive: the key is unique by contract
            s = s.iloc[0]
        evaluable = bool(s.get("screen_evaluable", True))
        canal = str(s.get("fcode_class") or "") == "canal"
        passed = bool(s.get(flag, False)) and not canal
        decision = "retained" if passed else ("excluded" if evaluable or canal
                                              else "not_evaluable")
        reason = ("passes every condition of the screen" if passed
                  else "the reach is a canal or ditch, outside the reference frame" if canal
                  else describe_failures(s, tier))
        rows.append({**base, "state": "succeeded",
                     "comid": (int(s["comid"]) if pd.notna(s.get("comid")) else None),
                     "eci": (float(s["eci_raw"]) if "eci_raw" in s.index
                             and pd.notna(s.get("eci_raw")) else None),
                     "condition": None,
                     "auto_decision": decision, "final_decision": decision,
                     "issue_code": "", "issue": "",
                     "reason": reason, "comid_source": "archive"})
    criteria = {
        "criteria": {"screen": SCREEN_ID, "tier": tier, "rules": {
            k: [op, v] for k, (op, v) in rules(tier).items()}},
        "method": METHOD,
        "config": {"frame": governed_screen().get("frame")},
        "diagnostics": {"n_sites": len(rows)},
        "station_screen": station_screen_identity(),
    }
    return {"easi_screening_sites": rows, "easi_screening_metrics": [],
            "easi_screening_criteria": criteria}
