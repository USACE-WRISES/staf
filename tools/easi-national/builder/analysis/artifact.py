"""Export the approved EASI reference curves from the completed analysis.

This reads the frozen registry and evidence; it never runs an analysis step
or writes into the data root. The only fit needed by the export is the
unsplit national entrenchment fallback, using the existing national panel.

    python -m builder.analysis.artifact --out ../../apps/easi/data/reference-curves.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from builder import STREAM_CURVES_APP
from builder.analysis import curves, panels, screens, stats
from builder.paths import DataRoot


SET_SPECS = {
    "corridor-woody": ("woody_wsrp100", "l2"),
    "corridor-natural": ("natural_wsrp100", "l2"),
    "flow-variability": ("q_cv_monthly", "l2"),
    "entrenchment": ("er_median", "slope_class"),
}
SLOPE_CLASSES = frozenset(("lt_0.5", "0.5_to_2", "ge_2"))
ENGINE_PATH = STREAM_CURVES_APP / "streamcurves" / "curves.py"


def _sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _rounded(value):
    """Canonical JSON values, including six-place finite floats."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Reference artifact contains a non-finite number")
        rounded = round(value, 6)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value


def _curve(row: dict) -> dict:
    points = row.get("points")
    if points is None:
        points = json.loads(row.get("points_json") or "[]")
    if len(points) < 2:
        raise ValueError(f"Usable curve has no point list: {row.get('quantity')}")
    numeric = {key: float(row[key]) for key in ("q25", "q50", "q75", "x39", "x69")}
    return _rounded({
        "points": [[float(x), float(y)] for x, y in points],
        "n": int(row["n"]),
        "nMembers": int(row["n_members"]),
        **numeric,
        "status": row["status"],
        "panelTier": row["panel_tier"],
        "screen": row["screen"],
    })


def _key(row: dict, stratifier: str) -> str | None:
    level, stratum, split = row["level"], row["stratum"], row.get("split") or ""
    if level == "national" and stratum == "national:national":
        if not split:
            return "national"
        if stratifier == "slope_class" and split in SLOPE_CLASSES:
            return split
    if stratifier == "l2" and level == "l2" and not split and stratum.startswith("l2:"):
        return stratum.split(":", 1)[1]
    return None


def _national_entrenchment(root: DataRoot) -> tuple[dict, dict]:
    """Pool the stored national panel, including members with no slope."""
    member_path, panel_path = panels.members_path(root), panels.panels_path(root)
    filters = [("level", "=", "national"), ("stratum", "=", "national:national")]
    panel_rows = pq.read_table(panel_path, filters=filters).to_pylist()
    if len(panel_rows) != 1:
        raise ValueError("An unsplit entrenchment fallback requires one stored national panel")
    panel = panel_rows[0]
    member_ids = pq.read_table(member_path, columns=["comid"], filters=filters)["comid"].to_pylist()
    if not member_ids or len(set(member_ids)) != len(member_ids):
        raise ValueError("The stored national panel must contain distinct COMIDs")
    comids = np.asarray(sorted(member_ids), dtype=np.int64)
    quantity = curves.QUANTITIES["er_median"]
    values = curves._quantity_values(root, quantity, comids)
    if values is None:
        raise ValueError("The stored values table has no er_median evidence")
    finite = np.isfinite(values)
    if int(finite.sum()) < curves.SPLIT_FLOOR:
        raise ValueError("The national entrenchment fallback has fewer than 30 finite values")
    fit = curves.fit_curve(values[finite], quantity, "national:national")
    pressure = curves._pressure(root, comids)
    row = {
        "quantity": quantity.key, "level": "national", "stratum": "national:national", "split": "",
        "higher_is_better": quantity.higher_is_better,
        "panel_tier": panel["panel_tier"], "screen": panel["screen"],
        "n_members": len(member_ids), **fit,
        "rho_pressure": stats.spearman(values, pressure),
    }
    row["usable"], reason = curves.usable(quantity, row, panel["panel_tier"])
    if not row["usable"]:
        raise ValueError(f"No usable national entrenchment fallback: {reason}")
    # Hash the exact pooled observations, before output rounding. Nonmembers
    # and missing ER values cannot affect this fit or its evidence digest.
    observations = [[int(comid), float(value)] for comid, value in zip(comids[finite], values[finite])]
    provenance = {
        "method": "pooled-existing-national-panel",
        "panelMembersSha256": _sha256(member_path),
        "referencePanelsSha256": _sha256(panel_path),
        "observationsSha256": hashlib.sha256(
            json.dumps(observations, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest(),
    }
    return row, provenance


def build_artifact(root: DataRoot) -> dict:
    """Build the four approved sets without changing any source file."""
    registry_path = curves.registry_path(root)
    registry = pq.read_table(registry_path).to_pylist()
    values_meta = json.loads((root.analysis / "values_meta.json").read_text(encoding="utf-8"))
    manifest = json.loads((root.staging / "manifest.json").read_text(encoding="utf-8"))
    stamp = registry_path.stat()
    provenance = {
        "datasetVintage": manifest["vintage"],
        "valuesMethodVersion": values_meta["method_version"],
        "analysisVersion": values_meta["analysis_version"],
        "valuesBuiltAt": values_meta["built_at"],
        "registry": {
            "sha256": _sha256(registry_path), "mtimeNs": stamp.st_mtime_ns,
            "mtimeUtc": datetime.fromtimestamp(stamp.st_mtime, timezone.utc).isoformat(),
        },
        "curveEngineSha256": _sha256(ENGINE_PATH),
        "screen": {
            "id": "least-disturbed-v1", "roadDensityCap": screens.ROAD_DENSITY_CAP,
            "strict": screens.STRICT, "relaxed": screens.RELAXED, "frame": screens.FRAME_RULES,
        },
        "panelFloors": {
            "complete": panels.FLOOR_COMPLETE, "exploratory": panels.FLOOR_EXPLORATORY,
            "split": curves.SPLIT_FLOOR,
        },
    }
    sets = {}
    for set_id, (quantity, stratifier) in SET_SPECS.items():
        selected = {}
        higher = curves.QUANTITIES[quantity].higher_is_better
        for row in registry:
            if row["quantity"] != quantity or not row.get("usable"):
                continue
            key = _key(row, stratifier)
            if key is None:
                continue
            if key in selected:
                raise ValueError(f"Duplicate curve in {set_id}: {key}")
            if row["higher_is_better"] != higher:
                raise ValueError(f"Wrong curve direction in {set_id}: {key}")
            selected[key] = _curve(row)
        if set_id == "entrenchment" and "national" not in selected:
            fallback, source = _national_entrenchment(root)
            selected["national"] = _curve(fallback)
            provenance["entrenchmentNationalFallback"] = source
        if "national" not in selected:
            raise ValueError(f"Reference curve set {set_id} requires a usable national curve")
        sets[set_id] = {
            "quantity": quantity, "stratifier": stratifier,
            "higherIsBetter": higher, "curves": selected,
        }
    return _rounded({"schemaVersion": 1, "provenance": provenance, "sets": sets})


def write_artifact(root: DataRoot, out: Path) -> Path:
    """Write deterministic UTF-8/LF JSON to the explicitly requested path."""
    artifact = build_artifact(root)
    encoded = (json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encoded)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Existing national data root (default: EASI_NATIONAL_ROOT)")
    parser.add_argument("--out", type=Path, required=True, help="Reference-curves JSON to write")
    args = parser.parse_args(argv)
    root = DataRoot(args.root) if args.root is not None else DataRoot.default()
    out = write_artifact(root, args.out)
    print(f"Wrote reference curves to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
