"""The governing methodology, loaded from config rather than retyped in code.

``config/methodology/`` holds the machine-readable half of the methodology: the
thresholds every rule tests against, and the rule catalog that records each
rule's threshold and implementation status.

Both files used to live only under ``notes/``, which is neither tracked by git
nor shipped in the app payload. That made the methodology unciteable: hashing it
into a run record fingerprinted something nobody could retrieve, and any in-app
surface reading rule statuses would break once deployed. The prose stays in
``notes/``; the machine-readable half lives here, versioned with the code that
reads it.

Pure: file reads and dict lookups, no network, no global state.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import read_json, read_yaml
from .paths import CONFIG_DIR

logger = logging.getLogger("streamcurves")

METHODOLOGY_DIR = CONFIG_DIR / "methodology"
CONFIG_PATH = METHODOLOGY_DIR / "methodology_config.yaml"
RULE_CATALOG_PATH = METHODOLOGY_DIR / "rule_catalog.json"


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Cached: the config is read on hot paths (overlap thresholds, per-curve
    checks). Edits to the file require a process restart, which matches how
    every other config in the app behaves."""
    return read_yaml(CONFIG_PATH) or {}


@lru_cache(maxsize=1)
def load_rule_catalog() -> dict:
    return read_json(RULE_CATALOG_PATH) or {}


def methodology_version() -> str | None:
    """The version both files must agree on."""
    return ((load_rule_catalog().get("meta") or {}).get("methodology_version")
            or (load_config().get("meta") or {}).get("methodology_version"))


@lru_cache(maxsize=1)
def _rules_by_id() -> dict[str, dict]:
    return {r["id"]: r for r in (load_rule_catalog().get("rules") or []) if r.get("id")}


def rule(rule_id: str) -> dict:
    """One catalog rule. Raises on an unknown id so a typo cannot silently
    produce a provenance record for a rule that does not exist."""
    rules = _rules_by_id()
    if rule_id not in rules:
        raise KeyError(f"Unknown methodology rule '{rule_id}'.")
    return rules[rule_id]


def rule_ids() -> list[str]:
    return sorted(_rules_by_id())


def threshold(path: str, default: Any = None) -> Any:
    """A config value by dotted path, e.g. ``"data_rules.min_n_unstratified"``."""
    node: Any = load_config()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            if default is not None:
                return default
            raise KeyError(f"Unknown methodology threshold '{path}'.")
        node = node[part]
    return node


def missingness_disposition(missing_fraction: Any) -> str:
    """DATA-01/02/03 band for a variable's missing-data fraction.

    ``"auto"`` (eligible for automation), ``"caution"`` (analyze with caution,
    targeted review), ``"review"`` (do not auto-recommend), or ``"unknown"``.
    """
    try:
        f = float(missing_fraction)
    except (TypeError, ValueError):
        return "unknown"
    if f != f:  # NaN
        return "unknown"
    if f <= float(threshold("data_rules.max_missingness_auto")):
        return "auto"
    if f <= float(threshold("data_rules.max_missingness_review")):
        return "caution"
    return "review"


# --------------------------------------------------------------------------- #
# Mirror verification (the config's own warning made executable)
#
# Some config blocks MIRROR engine constants rather than governing them: the
# EASI screening presets, the engine's index-band cuts, the curve gate's
# geometry, and the DEEP scoring contract. The engine is the methodology's
# approved implementation for those values, so an edit to the mirror alone
# would make the published methodology describe thresholds the software does
# not apply. This check makes that drift loud instead of silent.
# --------------------------------------------------------------------------- #
def mirror_drift() -> list[str]:
    """Human-readable descriptions of config-vs-engine drift. Empty means clean."""
    problems: list[str] = []
    cfg = load_config()

    # 1. easi_presets must equal the vendored engine's PRESETS.
    try:
        from ._vendor.easi.batch import qualify as _qualify
        mirrored = cfg.get("easi_presets") or {}
        for name, engine_rule in _qualify.PRESETS.items():
            if name not in mirrored:
                problems.append(f"easi_presets is missing the engine preset '{name}'.")
            elif mirrored[name] != engine_rule:
                problems.append(
                    f"easi_presets['{name}'] differs from the engine: "
                    f"config {mirrored[name]!r} vs engine {engine_rule!r}.")
        for name in mirrored:
            if name not in _qualify.PRESETS:
                problems.append(f"easi_presets carries '{name}', unknown to the engine.")
    except Exception as exc:  # noqa: BLE001 - a broken vendor import is itself drift
        problems.append(f"could not load the vendored EASI presets: {exc}")

    # 2. The engine's condition-band cuts behind the presets.
    try:
        from ._vendor.easi import config as _easi_config
        cuts = [b[0] for b in _easi_config.INDEX_BANDS[:2]]
        deep_bands = list(threshold("curve_rules.deep_index_bands"))
        if [float(c) for c in cuts] != [float(b) for b in deep_bands]:
            problems.append(
                f"curve_rules.deep_index_bands {deep_bands} differs from the "
                f"engine's INDEX_BANDS cuts {cuts}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the engine index bands: {exc}")

    # 3. Curve-gate geometry mirrors curves.py (structural to iqr-seed-1).
    try:
        from . import curves as _curves
        low, high = _curves.INDEX_DRAWING_BANDS
        if (float(threshold("curve_rules.index_low_band")) != float(low)
                or float(threshold("curve_rules.index_high_band")) != float(high)):
            problems.append(
                "curve_rules index bands differ from the curve engine's "
                f"{_curves.INDEX_DRAWING_BANDS}.")
        if int(threshold("curve_rules.max_band_crossings")) != int(
                _curves.MAX_BAND_CROSSINGS):
            problems.append(
                "curve_rules.max_band_crossings differs from the curve engine's "
                f"{_curves.MAX_BAND_CROSSINGS}.")
        if int(threshold("data_rules.curve_engine_hard_floor_n")) != int(
                _curves.CURVE_ENGINE_HARD_FLOOR_N):
            problems.append(
                "data_rules.curve_engine_hard_floor_n differs from the curve "
                f"engine's {_curves.CURVE_ENGINE_HARD_FLOOR_N}.")
        approved = [str(f) for f in threshold("curve_rules.approved_families")]
        if _curves.CURVE_FAMILY not in approved:
            problems.append(
                f"the engine's curve family {_curves.CURVE_FAMILY!r} is not in "
                f"curve_rules.approved_families {approved}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the curve-gate geometry: {exc}")

    # 3b. The stratifier screening engine's phase-1 significance cut.
    try:
        from . import screening as _screening
        if float(threshold("stratifier_rules.screening_significance_alpha")) != float(
                _screening.SCREENING_ALPHA):
            problems.append(
                "stratifier_rules.screening_significance_alpha differs from the "
                f"screening engine's {_screening.SCREENING_ALPHA}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the screening significance cut: {exc}")

    # 4. The curve method version mirrors run_state.
    try:
        from . import run_state as _rs
        declared = str(threshold("meta.curve_method_version"))
        if declared != _rs.CURVE_METHOD_VERSION:
            problems.append(
                f"meta.curve_method_version ({declared!r}) differs from the engine's "
                f"{_rs.CURVE_METHOD_VERSION!r}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the curve method version: {exc}")

    # 5. The DEEP scoring contract mirrors deep_export.
    try:
        from . import deep_export as _dx
        contract = _dx.SCORING_CONTRACT_CONSTANTS
        pairs = [
            ("curve_rules.deep_index_bands", list(contract["indexBands"])),
            ("curve_rules.deep_function_score_bands",
             list(contract["functionScoreBands"])),
            ("curve_rules.deep_function_score_max", contract["functionScoreMax"]),
            ("curve_rules.deep_indirect_weight", contract["indirectWeight"]),
        ]
        for path, engine_value in pairs:
            if list_or_value(threshold(path)) != list_or_value(engine_value):
                problems.append(
                    f"{path} ({threshold(path)!r}) differs from the DEEP export "
                    f"contract ({engine_value!r}).")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the DEEP scoring contract: {exc}")

    return problems


def list_or_value(v: Any) -> Any:
    return list(v) if isinstance(v, (list, tuple)) else v


def verify_mirrors(strict: bool = True) -> list[str]:
    """Run :func:`mirror_drift`. In strict mode any drift raises, so a headless
    run cannot proceed under a config that misdescribes the engine. Non-strict
    callers (the interactive app at startup) log and continue."""
    problems = mirror_drift()
    if problems and strict:
        raise RuntimeError(
            "methodology config drift against the engine:\n- " + "\n- ".join(problems))
    for p in problems:
        logger.error("methodology mirror drift: %s", p)
    return problems


def _sha256(path: Path) -> str | None:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def config_fingerprints() -> dict:
    """What a run record cites so another run can be compared against it."""
    return {
        "methodology_version": methodology_version(),
        "config_path": str(CONFIG_PATH.relative_to(CONFIG_DIR.parent)).replace("\\", "/"),
        "config_sha256": _sha256(CONFIG_PATH),
        "rule_catalog_path": str(
            RULE_CATALOG_PATH.relative_to(CONFIG_DIR.parent)).replace("\\", "/"),
        "rule_catalog_sha256": _sha256(RULE_CATALOG_PATH),
    }


def file_fingerprints(paths) -> list[dict]:
    """``[{path, sha256}]`` for arbitrary inputs, relative to the app root."""
    root = CONFIG_DIR.parent
    out = []
    for path in paths:
        path = Path(path)
        try:
            rel = str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(path).replace("\\", "/")
        out.append({"path": rel, "sha256": _sha256(path)})
    return out


def inputs_digest(payload: dict) -> str:
    """A stable digest over a run's declared inputs.

    Two runs with the same digest saw the same configs, the same data files and
    the same request, so they must produce the same assessment. Sorted keys and
    a canonical separator keep it independent of dict ordering.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
