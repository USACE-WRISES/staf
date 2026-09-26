"""Refit EASI's reference curves from evidence packages.

``easi-dev-members`` holds every reference panel member of every level with the value each
fitted quantity read and its composite pressure; ``fit_recipe`` is the builder's fit code,
vendored verbatim. ``fit_registry`` groups the members into fits exactly as the builder's
curves step does (a geometry quantity only at the national level, by slope class; a split
quantity by its split; otherwise by stratum) and fits each group, so refitting a package
reproduces its curve registry; ``operational_curves`` then assembles the 34 curves EASI scores
with (the NARS-9 fits of the three regional families with their national curves, and the
entrenchment slope classes with the pooled national fallback) in the method file's own shape.

An EASI method's curves are the method's: every fit runs the curve engine under the tail
endpoints the package's recipe records (``recipe_geometry``), never under DEEP's current
default. A recipe written before the endpoints were recorded means the method's own, the
legacy triple 0.3, 4/3 and 7/3 IQR (iqr-seed-2), which is what every such fit ran under.

Nothing here reads a developer path: only package folders.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from . import fit_recipe as fr

REGIONAL_SETS = {"corridor-natural": "natural_wsrp100", "corridor-woody": "woody_wsrp100",
                 "flow-variability": "q_cv_monthly"}
ENTRENCHMENT = ("entrenchment", "er_median")
SLOPE_CLASSES = ("lt_0.5", "0.5_to_2", "ge_2")
LEVELS = ("l3", "l2", "l1", "nars9", "national")


def load_members(package_dir: Path):
    """``(members, values, panels)`` pandas frames from an ``easi-dev-members`` folder.
    ``members.attrs["fit_geometry"]`` carries the tail endpoints the package's fits ran
    under (:func:`package_geometry`), which every fit from these frames then uses."""
    import pyarrow.parquet as pq
    d = Path(package_dir) / "data"
    members = pq.read_table(d / "panel_members.parquet").to_pandas()
    values = pq.read_table(d / "member_values.parquet").to_pandas()
    panels = pq.read_table(d / "reference_panels.parquet").to_pandas()
    members.attrs["fit_geometry"] = package_geometry(package_dir)
    return members, values, panels


#: The refit's own code, recorded by the members and fits packages (SHA-256 of the LF bytes).
RECIPE_CODE = {"fit_recipe.py": "fit recipe code", "refit.py": "refit code"}

#: the tail endpoints each curve method version fitted under (parsed: a fraction keeps its
#: numerator and denominator, ``curves.IqrOffset``), for a recipe that names the version
#: without spelling the endpoints
GEOMETRY_BY_METHOD_VERSION = {fr.CURVE_METHOD_VERSION: fr.seed_tail_offsets(),
                              "iqr-seed-3": (0.5, 1.5, 2.5)}
#: the ``differences`` entry of :func:`recipe_check` when the package's endpoints are not the
#: running engine's default (the refit still fits under the package's)
ENDPOINTS_DIFFERENCE = "curve tail endpoints"


def recipe_geometry(recipe: Optional[dict]) -> dict:
    """The tail endpoints a package's fits ran under, from its ``recipe`` block:
    ``{"tail_offsets_iqr": (near, mid, far), "curve_method_version", "recorded"}``.

    ``recipe.engine.tailOffsetsIqr`` (three IQR offsets as the knob spells them, a fraction
    such as "4/3" kept as a fraction so the fit multiplies then divides) and
    ``recipe.engine.curveMethodVersion`` are what the exporter records; a recipe that names
    only a known version means that version's endpoints. A recipe written before the
    endpoints were recorded (neither field) means the method's own, the legacy triple
    0.3, 4/3 and 7/3 (iqr-seed-2): that is what every such fit ran under. The parsed
    offsets are kept as parsed (``fit_recipe.parse_tail_offsets``), never as plain floats.
    """
    doc = recipe or {}
    engine = doc.get("engine") or {}
    raw = engine.get("tailOffsetsIqr", doc.get("tailOffsetsIqr"))
    version = engine.get("curveMethodVersion") or doc.get("curveMethodVersion")
    if raw is None:
        if version is None:
            return {"tail_offsets_iqr": fr.seed_tail_offsets(),
                    "curve_method_version": fr.CURVE_METHOD_VERSION, "recorded": False}
        if str(version) not in GEOMETRY_BY_METHOD_VERSION:
            raise ValueError(f"the package records curve method version {version!r} without its "
                             "tail endpoints (tailOffsetsIqr), so the refit cannot fit under them")
        return {"tail_offsets_iqr": GEOMETRY_BY_METHOD_VERSION[str(version)],
                "curve_method_version": str(version), "recorded": True}
    try:
        offsets = fr.parse_tail_offsets(raw)
    except ValueError as exc:
        raise ValueError(f"the package records tail endpoints {raw!r}; expected three positive, "
                         "non-decreasing IQR offsets [near, mid, far]") from exc
    return {"tail_offsets_iqr": offsets,
            "curve_method_version": str(version) if version else fr.curve_method_version_for(offsets),
            "recorded": True}


def package_geometry(package_dir: Path) -> dict:
    """:func:`recipe_geometry` of a package folder's manifest (a folder with no manifest
    is taken as written before the endpoints were recorded)."""
    from .. import evidence_store as evs
    folder = Path(package_dir)
    if not (folder / evs.MANIFEST).is_file():
        return recipe_geometry(None)
    return recipe_geometry(evs.read_manifest(folder).get("recipe") or {})


def fit_geometry_of(members, tail_offsets_iqr=None) -> tuple:
    """The tail endpoints a fit from ``members`` runs under: the caller's, else the
    package's (``members.attrs["fit_geometry"]``, set by :func:`load_members`), else
    the method's own; parsed as the engine takes them (a fraction stays a fraction)."""
    if tail_offsets_iqr is not None:
        return fr.parse_tail_offsets(tail_offsets_iqr)
    geometry = getattr(members, "attrs", {}).get("fit_geometry") or {}
    return fr.parse_tail_offsets(geometry.get("tail_offsets_iqr") or fr.SEED_TAIL_OFFSETS_IQR)


def _offsets_words(offsets) -> str:
    """``0.3, 4/3 and 7/3`` for the legacy triple, ``0.5, 1.5 and 2.5`` otherwise."""
    def one(v) -> str:
        ratio = getattr(v, "ratio", None)
        if ratio:
            return f"{ratio[0]}/{ratio[1]}"
        x = float(v)                                    # for the words only
        for num, den in ((4, 3), (7, 3), (2, 3), (5, 3), (1, 3)):
            if abs(x - num / den) < 1e-9:
                return f"{num}/{den}"
        return f"{x:g}"
    items = [one(v) for v in offsets or ()]
    return _listed(items, "and") if items else "unknown"


def recipe_code() -> dict:
    """``{file: SHA-256}`` of the code a refit runs besides the curve engine: the vendored fit
    recipe and this module, over their bytes with LF line endings (a CRLF working copy hashes
    the same)."""
    import hashlib
    here = Path(__file__).resolve().parent
    return {name: hashlib.sha256((here / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in RECIPE_CODE}


def recipe_check(package_dir: Path) -> dict:
    """The running curve engine, refit code, fit constants and tail endpoints against those a
    package records (its ``recipe`` block): ``{"same", "differences", "notRecorded", "engine":
    {"recorded", "running"}, "geometry": {...}}``. A refit is expected to be exact only when
    they are the same; a package that records no engine or code hash is never taken to match.

    The tail endpoints are reported like the engine (``geometry``: the package's, recorded or
    assumed legacy, and the running engine's default, with their curve method versions), and
    a difference is listed as :data:`ENDPOINTS_DIFFERENCE`; the refit fits under the package's
    endpoints whatever the running default, so this difference alone leaves it exact."""
    import hashlib
    from .. import evidence_store as evs
    from .. import methodology, run_state
    from ..paths import ROOT
    from . import fit_recipe as fr
    recipe = evs.read_manifest(Path(package_dir)).get("recipe") or {}
    engine = recipe.get("engine") or {}
    raw = (ROOT / "streamcurves" / "curves.py").read_bytes()
    running = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    diffs, missing = [], []
    if not engine.get("sha256_lf"):
        missing.append("the curve engine")
    elif engine["sha256_lf"] != running:
        diffs.append("curve engine")
    package_geometry_ = recipe_geometry(recipe)
    running_offsets = tuple(methodology.seed_geometry()["tail_offsets_iqr"])
    fits_under = tuple(package_geometry_["tail_offsets_iqr"])
    geometry = {"recorded": package_geometry_["recorded"],
                "package": list(fits_under),
                "packageCurveMethodVersion": package_geometry_["curve_method_version"],
                "running": list(running_offsets),
                "runningCurveMethodVersion": str(run_state.CURVE_METHOD_VERSION),
                "refitUnder": list(fits_under)}
    if not fr.same_tail_offsets(fits_under, running_offsets):
        diffs.append(ENDPOINTS_DIFFERENCE)
    recorded_code = recipe.get("code") or {}
    for name, sha_now in recipe_code().items():
        if not recorded_code.get(name):
            missing.append(f"the {RECIPE_CODE[name]}")
        elif recorded_code[name] != sha_now:
            diffs.append(RECIPE_CODE[name])
    here = {"indexBands": list(fr.INDEX_BANDS), "pressureRhoMax": fr.PRESSURE_RHO_MAX,
            "splitFloor": fr.SPLIT_FLOOR,
            "panelFloors": {"complete": fr.FLOOR_COMPLETE, "exploratory": fr.FLOOR_EXPLORATORY},
            "minStratum": fr.MIN_STRATUM, "thinningSeed": fr.SEED,
            "screen": {"strict": fr.STRICT, "relaxed": fr.RELAXED, "frame": fr.FRAME_RULES,
                       "pressureVariables": list(fr.PRESSURE_VARIABLES)}}
    recorded = recipe.get("constants") or {}

    def norm(v):
        return json.loads(json.dumps(v, sort_keys=True, default=list))

    for key, value in here.items():
        if key == "screen":
            for part, val in value.items():
                if part in (recorded.get("screen") or {}) and norm(recorded["screen"][part]) != norm(val):
                    diffs.append(f"screen {part}")
        elif key in recorded and norm(recorded[key]) != norm(value):
            diffs.append(key)
    return {"same": not diffs and not missing, "differences": diffs, "notRecorded": missing,
            "engine": {"recorded": engine.get("sha256_lf"), "running": running},
            "geometry": geometry, "checkedConstants": bool(recorded)}


def _listed(items: list, last: str) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {last} " + items[-1]


def recipe_words(check: dict) -> Optional[str]:
    """A recipe check in words, or None when the refit is expected to be exact. An endpoint
    difference is said on its own: the refit fits under the package's endpoints, so by itself
    it does not make the refit inexact."""
    if not check or check.get("same"):
        return None
    parts = []
    diffs = [d for d in check.get("differences") or [] if d != ENDPOINTS_DIFFERENCE]
    if diffs:
        parts.append(f"the {_listed(diffs, 'and')} here {'differs' if len(diffs) == 1 else 'differ'} "
                     "from what the package records")
    if check.get("notRecorded"):
        parts.append("the package does not record " + _listed(check["notRecorded"], "or"))
    sentences = []
    if parts:
        sentences.append("The refit is not expected to match exactly: " + "; ".join(parts) + ".")
    if ENDPOINTS_DIFFERENCE in (check.get("differences") or []):
        geo = check.get("geometry") or {}
        whose = ("the tail endpoints the package records" if geo.get("recorded") else
                 "the method's own tail endpoints, which a package that records none was fitted under")
        sentences.append(
            f"The refit fits under {whose} ({_offsets_words(geo.get('package'))} IQR, "
            f"{geo.get('packageCurveMethodVersion')}), not the curve engine's current default "
            f"({_offsets_words(geo.get('running'))}, {geo.get('runningCurveMethodVersion')}).")
    return " ".join(sentences) if sentences else None


def regenerate_members(universe_dir: Path):
    """Every level's reference panel members drawn again from an ``easi-dev-universe``
    package: the builder's screens and seeded thinning (``fit_recipe.select_panels``) over the
    universe in its own row order, float32 columns widened as the builder widens them."""
    import pandas as pd
    import pyarrow.parquet as pq
    frame = pq.read_table(Path(universe_dir) / "data" / "universe.parquet").to_pandas()
    for name in frame.columns:
        if str(frame[name].dtype) == "float32":
            frame[name] = frame[name].astype("float64")
    parts = [fr.select_panels(frame, level) for level in fr.LEVELS]
    panels = pd.concat([p for p, _ in parts], ignore_index=True)
    members = pd.concat([m for _, m in parts], ignore_index=True)
    return panels, members


def same_members(regenerated, stored) -> dict:
    """Row-for-row equality of two member tables over the stored table's columns."""
    cols = list(stored.columns)
    missing = [c for c in cols if c not in regenerated.columns]
    if missing or len(regenerated) != len(stored):
        return {"identical": False, "rows": [len(regenerated), len(stored)], "missingColumns": missing}
    a = regenerated[cols].reset_index(drop=True)
    b = stored[cols].reset_index(drop=True)
    differing = [c for c in cols if not a[c].equals(b[c])]
    return {"identical": not differing, "rows": len(stored), "differingColumns": differing}


def _row_values(members, values, column: str) -> np.ndarray:
    """``values[column]`` aligned to the member rows (NaN where a COMID has none)."""
    lookup = values.set_index("comid")[column]
    return lookup.reindex(members["comid"].to_numpy()).to_numpy(dtype=float)


def fit_registry(members, values, panels, *, quantities: Optional[Iterable[str]] = None,
                 levels: Iterable[str] = LEVELS, tail_offsets_iqr=None) -> list[dict]:
    """Every quantity x level x stratum (x split) fit, as the builder's curves step fits it,
    under the tail endpoints of :func:`fit_geometry_of` (the package's unless the caller
    says otherwise)."""
    offsets = fit_geometry_of(members, tail_offsets_iqr)
    tier_of = {(r["level"], r["stratum"]): (r["panel_tier"], r["screen"])
               for r in panels.to_dict("records")}
    pressure = _row_values(members, values, "composite_pressure")
    rows: list[dict] = []
    for q in fr.QUANTITIES.values():
        if quantities is not None and q.key not in set(quantities):
            continue
        if q.key not in values.columns:
            continue
        frame = members.assign(_value=_row_values(members, values, q.key), _pressure=pressure)
        for level in levels:
            if q.geometry and level != "national":
                continue
            sub = frame[frame["level"] == level]
            if q.split and q.split in sub.columns:
                groups = sub.groupby(["stratum", q.split], dropna=True, observed=True)
            elif q.geometry and "slope_class" in sub.columns:
                groups = sub.groupby(["stratum", "slope_class"], dropna=True, observed=True)
            else:
                groups = sub.groupby(["stratum"], dropna=True, observed=True)
            for key, group in groups:
                stratum = key[0] if isinstance(key, tuple) else key
                split = key[1] if isinstance(key, tuple) and len(key) > 1 else ""
                tier, screen = tier_of.get((level, stratum), ("none", "none"))
                vals = group["_value"].to_numpy(dtype=float)
                vals_ok = vals[np.isfinite(vals)]
                row = {"quantity": q.key, "level": level, "stratum": stratum, "split": str(split or ""),
                       "kind": q.kind, "higher_is_better": q.higher_is_better, "panel_tier": tier,
                       "screen": screen, "n_members": int(len(group)), "n": int(vals_ok.size)}
                if vals_ok.size < fr.SPLIT_FLOOR:
                    row.update(status="insufficient_data", usable=False, reason="fewer than 30 values",
                               points=[])
                    rows.append(row)
                    continue
                fit = fr.fit_curve(vals_ok, q, f"{stratum}|{split}" if split else stratum,
                                   tail_offsets_iqr=offsets)
                rho = fr.spearman(group["_value"].to_numpy(dtype=float),
                                  group["_pressure"].to_numpy(dtype=float))
                row.update(fit)
                row["rho_pressure"] = rho
                row["bp_good"] = fit["q25"] if q.higher_is_better else fit["q75"]
                row["bp_poor"] = fit["q05"] if q.higher_is_better else fit["q95"]
                row["usable"], row["reason"] = fr.usable(q, row, tier)
                rows.append(row)
    return rows


def national_entrenchment(members, values, panels, *, tail_offsets_iqr=None) -> dict:
    """The unsplit national entrenchment curve: every national panel member with an
    entrenchment ratio, members without a slope class included (the artifact's fallback),
    under the tail endpoints of :func:`fit_geometry_of`."""
    offsets = fit_geometry_of(members, tail_offsets_iqr)
    q = fr.QUANTITIES["er_median"]
    nat = members[(members["level"] == "national") & (members["stratum"] == "national:national")]
    comids = np.asarray(sorted(nat["comid"].to_numpy()), dtype=np.int64)
    lookup = values.set_index("comid")
    vals = lookup["er_median"].reindex(comids).to_numpy(dtype=float)
    # the builder ranks the pressure over these national members only (not over every
    # member row, as the registry fits do), from the members' own pressure variables
    raw = {v: lookup[f"screen__{v}"].reindex(comids).to_numpy()
           for v in fr.PRESSURE_VARIABLES if f"screen__{v}" in lookup.columns}
    pres, _ = fr.composite_pressure(raw)
    finite = np.isfinite(vals)
    fit = fr.fit_curve(vals[finite], q, "national:national", tail_offsets_iqr=offsets)
    panel = panels[(panels["level"] == "national") & (panels["stratum"] == "national:national")]
    panel = panel.to_dict("records")[0]
    row = {"quantity": q.key, "level": "national", "stratum": "national:national", "split": "",
           "higher_is_better": q.higher_is_better, "panel_tier": panel["panel_tier"],
           "screen": panel["screen"], "n_members": int(len(nat)), **fit,
           "rho_pressure": fr.spearman(vals, pres)}
    row["usable"], row["reason"] = fr.usable(q, row, panel["panel_tier"])
    return row


def operational_curves(rows: list[dict], members, values, panels, *, tail_offsets_iqr=None) -> dict:
    """The 34 curves EASI scores with, ``{set: {curve key: curve}}`` in the method file's
    shape (six-decimal rounding included); the pooled national entrenchment fallback is
    fitted here, under the tail endpoints of :func:`fit_geometry_of`."""
    def usable_rows(quantity, level):
        return [r for r in rows if r["quantity"] == quantity and r["level"] == level and r.get("usable")]

    out: dict[str, dict] = {}
    for set_id, quantity in REGIONAL_SETS.items():
        curves = {}
        for r in usable_rows(quantity, "nars9"):
            if not r.get("split"):
                curves[r["stratum"].split(":", 1)[1]] = fr._curve(r)
        nat = [r for r in usable_rows(quantity, "national") if not r.get("split")]
        if len(nat) == 1:
            curves["national"] = fr._curve(nat[0])
        out[set_id] = curves
    set_id, quantity = ENTRENCHMENT
    curves = {}
    for r in usable_rows(quantity, "national"):
        if r.get("split") in SLOPE_CLASSES and r["stratum"] == "national:national":
            curves[r["split"]] = fr._curve(r)
    fallback = national_entrenchment(members, values, panels, tail_offsets_iqr=tail_offsets_iqr)
    if fallback["usable"]:
        curves["national"] = fr._curve(fallback)
    out[set_id] = curves
    return out


# --------------------------------------------------------------------------- #
# comparisons
# --------------------------------------------------------------------------- #
REGISTRY_FIELDS = ("status", "n", "n_members", "q25", "q50", "q75", "x39", "x69", "usable",
                   "panel_tier", "screen", "rho_pressure", "reason")


def _close(a, b, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        return abs(fa - fb) <= tol
    return a == b


_KEY_FIELDS = {"quantity", "level", "stratum", "split", "points", "points_json"}


def compare_registry(refit: list[dict], stored: list[dict], *, tol: float = 0.0) -> dict:
    """Refit rows against a stored registry: rows matched by (quantity, level, stratum, split),
    every field both carry and every knot compared (exactly unless ``tol``). A stored field the
    refit does not produce is listed in ``notRefit``, never counted as agreeing."""
    key = lambda r: (r["quantity"], r["level"], r["stratum"], r.get("split") or "")  # noqa: E731
    a = {key(r): r for r in refit}
    b = {key(r): r for r in stored}
    differing = []
    not_refit: set = set()
    compared_fields: set = set()
    for k in sorted(set(a) & set(b)):
        x, y = a[k], b[k]
        both = sorted((set(x) & set(y)) - _KEY_FIELDS)
        compared_fields.update(both)
        not_refit.update(set(y) - set(x) - _KEY_FIELDS)
        fields = [f for f in both if not _close(x.get(f), y.get(f), tol)]
        px = x.get("points") or []
        py = y.get("points")
        if py is None:
            py = json.loads(y.get("points_json") or "[]")
        if len(px) != len(py) or any(not (_close(p[0], q[0], tol) and _close(p[1], q[1], tol))
                                     for p, q in zip(px, py)):
            fields.append("points")
        if fields:
            differing.append({"key": list(k), "fields": fields})
    return {"compared": len(set(a) & set(b)), "onlyRefit": sorted(map(list, set(a) - set(b))),
            "onlyStored": sorted(map(list, set(b) - set(a))), "differing": differing,
            "fieldsCompared": sorted(compared_fields), "notRefit": sorted(not_refit),
            "identical": not differing and set(a) == set(b)}


def compare_curves(refit: dict, artifact: dict) -> dict:
    """Refit operational curves against a method file's reference curves, field by field."""
    out = {"curves": 0, "identical": 0, "differing": [], "missing": [], "extra": []}
    for set_id, got_set in (refit or {}).items():
        for key in got_set or {}:
            if key not in (((artifact.get("sets") or {}).get(set_id) or {}).get("curves") or {}):
                out["extra"].append(f"{set_id}/{key}")
    for set_id, s in (artifact.get("sets") or {}).items():
        for key, curve in (s.get("curves") or {}).items():
            out["curves"] += 1
            got = (refit.get(set_id) or {}).get(key)
            if got is None:
                out["missing"].append(f"{set_id}/{key}")
                continue
            if got == curve:
                out["identical"] += 1
                continue
            diffs = {}
            for field in sorted(set(got) | set(curve)):
                a, b = got.get(field), curve.get(field)
                if a == b:
                    continue
                if field == "points" and a and b and len(a) == len(b):
                    diffs[field] = max(max(abs(p[0] - q[0]), abs(p[1] - q[1])) for p, q in zip(a, b))
                elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    diffs[field] = abs(float(a) - float(b))
                else:
                    diffs[field] = [a, b]
            out["differing"].append({"curve": f"{set_id}/{key}", "diffs": diffs})
    out["allIdentical"] = out["identical"] == out["curves"] and not out["missing"] and not out["extra"]
    return out


__all__ = ["load_members", "regenerate_members", "same_members", "fit_registry", "recipe_check",
           "recipe_code", "recipe_words", "recipe_geometry", "package_geometry", "fit_geometry_of",
           "RECIPE_CODE", "GEOMETRY_BY_METHOD_VERSION", "ENDPOINTS_DIFFERENCE",
           "national_entrenchment", "operational_curves", "compare_registry", "compare_curves",
           "REGIONAL_SETS", "ENTRENCHMENT", "SLOPE_CLASSES"]
