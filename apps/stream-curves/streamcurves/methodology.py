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


CARRY_FORWARD_CHOICES = ("published_curves", "none")


def carry_forward_default() -> str:
    """``reference_hierarchy.carry_forward``: what a build does with the curves
    its ecoregion's latest published version scores when the caller does not
    say. ``published_curves`` carries them forward unchanged unless their data
    were found defective (methodology 0.14, ``carry_forward.prepare``); ``none``
    rebuilds every curve. The batch runner's ``--refit`` default reads this."""
    value = str(threshold("reference_hierarchy.carry_forward")).strip()
    if value not in CARRY_FORWARD_CHOICES:
        raise ValueError(
            f"reference_hierarchy.carry_forward is {value!r}; expected one of "
            f"{', '.join(CARRY_FORWARD_CHOICES)}.")
    return value


# --------------------------------------------------------------------------- #
# Campaign Round 2 knobs (2026-09-25). Every default is today's behavior. A
# knob joins a run's inputs digest only when it differs from its default
# (provenance.build_run_manifest, ``reference.knobs``, absence semantics), so
# every published version still replays. The knobs GOVERN and are not mirror
# checked: a campaign config root (STREAMCURVES_CONFIG_ROOT) moves them.
# --------------------------------------------------------------------------- #
LADDER_RULE_FIRST_PASS = "first_pass"
LADDER_RULE_NARROWEST_ADEQUATE = "narrowest_adequate"
LADDER_RULES = (LADDER_RULE_FIRST_PASS, LADDER_RULE_NARROWEST_ADEQUATE)
SECOND_METRIC_RANK = "rank"
SECOND_METRIC_INDEPENDENT = "independent_and_discriminating"
SECOND_METRIC_RULES = (SECOND_METRIC_RANK, SECOND_METRIC_INDEPENDENT)
ZERO_INFLATED_TWO_PART = "two_part"
ZERO_INFLATED_WITHHOLD = "withhold"
ZERO_INFLATED_HANDLINGS = (ZERO_INFLATED_TWO_PART, ZERO_INFLATED_WITHHOLD)
#: the monotone IQR ladders' tail endpoints in IQR units, the engine's own
#: (curves.MONOTONE_TAIL_OFFSETS_IQR restates them; a test pins the two equal).
#: iqr-seed-3 (methodology 0.15, candidate C3b adopted): 0.5, 1.5 and 2.5; the
#: iqr-seed-2 values 0.3, 4/3 and 7/3 are a non-default setting from here on
MONOTONE_TAIL_OFFSETS_IQR = (0.5, 1.5, 2.5)

#: config path -> the default, which is today's behavior
KNOB_DEFAULTS: dict[str, Any] = {
    "reference_pool.ladder_rule": LADDER_RULE_FIRST_PASS,
    "reference_hierarchy.regional_screen.enabled": True,
    "metric_portfolio.fill_to": 2,
    "metric_portfolio.second_metric_rule": SECOND_METRIC_RANK,
    "metric_portfolio.second_metric_max_abs_spearman": 0.65,
    "metric_portfolio.second_metric_min_auc": 0.55,
    "curve12.gate": False,
    "curve12.min_auc": 0.55,
    "curve10.tail_offsets_iqr": list(MONOTONE_TAIL_OFFSETS_IQR),
    "curve10.zero_inflated_share": None,
    "curve10.zero_inflated_handling": None,
}
_MISSING = object()


def knob(path: str) -> Any:
    """A Round 2 knob's raw config value, or its default when the key is absent
    (a config root copied from an older config still runs as today)."""
    if path not in KNOB_DEFAULTS:
        raise KeyError(f"Unknown Round 2 knob '{path}'.")
    value = threshold(path, _MISSING)
    return KNOB_DEFAULTS[path] if value is _MISSING else value


def ladder_rule() -> str:
    """``reference_pool.ladder_rule`` (candidate B1): which passing pool option
    ``reference_pool.choose_pool`` uses."""
    value = str(knob("reference_pool.ladder_rule") or "").strip()
    if value not in LADDER_RULES:
        raise ValueError(f"reference_pool.ladder_rule is {value!r}; expected one of "
                         f"{', '.join(LADDER_RULES)}.")
    return value


def regional_screen_enabled() -> bool:
    """``reference_hierarchy.regional_screen.enabled`` (candidate B2)."""
    return bool(knob("reference_hierarchy.regional_screen.enabled"))


def portfolio_settings() -> dict:
    """SELECT-04's knobs (candidate C1): ``fill_to``, ``second_metric_rule`` and
    the two limits of the independent-and-discriminating rule."""
    rule = str(knob("metric_portfolio.second_metric_rule") or "").strip()
    if rule not in SECOND_METRIC_RULES:
        raise ValueError(f"metric_portfolio.second_metric_rule is {rule!r}; expected one of "
                         f"{', '.join(SECOND_METRIC_RULES)}.")
    return {"fill_to": int(knob("metric_portfolio.fill_to") or 2),
            "second_metric_rule": rule,
            "max_abs_spearman": float(knob("metric_portfolio.second_metric_max_abs_spearman")),
            "min_auc": float(knob("metric_portfolio.second_metric_min_auc"))}


def curve12_gate() -> dict:
    """The CURVE-12 gate (candidate C2): ``{"gate": bool, "min_auc": float}``."""
    return {"gate": bool(knob("curve12.gate")), "min_auc": float(knob("curve12.min_auc"))}


def parse_offset(value: Any) -> float:
    """An IQR offset from the config: a number, or a fraction written as a
    string such as ``"4/3"`` (YAML reads an unquoted 4/3 as a string too). An
    integer fraction keeps its numerator and denominator (``curves.IqrOffset``, a
    float) so the seed multiplies then divides, as the iqr-seed-2 literals did."""
    if isinstance(value, str) and "/" in value:
        num, den = (part.strip() for part in value.split("/", 1))
        if num.lstrip("-").isdigit() and den.isdigit():
            from .curves import IqrOffset
            return IqrOffset(int(num), int(den))
        return float(num) / float(den)
    return float(value)


def seed_geometry() -> dict:
    """The CURVE-10 knobs (candidates C3a and C3b) as the curve engine reads them.

    ``tail_offsets_iqr`` is the ``(near, mid, far)`` tuple, ``custom_tail_offsets``
    says whether it differs from the engine's own (iqr-seed-3: 0.5, 1.5, 2.5;
    a different triple joins the inputs digest through :func:`round2_knobs`),
    ``zero_inflated_share`` is the threshold or None (off) and
    ``zero_inflated_handling`` is ``two_part``, ``withhold`` or None.
    """
    raw = knob("curve10.tail_offsets_iqr")
    raw = MONOTONE_TAIL_OFFSETS_IQR if raw is None else raw
    try:
        offsets = tuple(parse_offset(v) for v in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"curve10.tail_offsets_iqr must be three IQR offsets, got {raw!r}") from exc
    if len(offsets) != 3 or not (0.0 < offsets[0] <= offsets[1] <= offsets[2]):
        raise ValueError("curve10.tail_offsets_iqr must be three positive, non-decreasing "
                         f"IQR offsets [near, mid, far]; got {list(offsets)!r}.")
    share = knob("curve10.zero_inflated_share")
    share = None if share is None else float(share)
    if share is not None and not (0.0 <= share < 1.0):
        raise ValueError(f"curve10.zero_inflated_share must lie in [0, 1); got {share!r}.")
    handling = knob("curve10.zero_inflated_handling")
    handling = None if handling in (None, "") else str(handling).strip()
    if handling is not None and handling not in ZERO_INFLATED_HANDLINGS:
        raise ValueError(f"curve10.zero_inflated_handling is {handling!r}; expected one of "
                         f"{', '.join(ZERO_INFLATED_HANDLINGS)} or null.")
    if share is not None and handling is None:
        raise ValueError("curve10.zero_inflated_share is set but curve10.zero_inflated_handling "
                         "is null; say two_part or withhold.")
    return {"tail_offsets_iqr": offsets,
            "custom_tail_offsets": offsets != tuple(MONOTONE_TAIL_OFFSETS_IQR),
            "zero_inflated_share": share,
            "zero_inflated_handling": handling if share is not None else None}


def round2_knobs() -> dict:
    """``{config path: value}`` for every Round 2 knob set away from its default,
    the block a run's manifest records under ``inputs.reference.knobs`` and the
    inputs digest carries. Empty on the shipped config, so no published digest
    gains a key. Values are normalized (an offset list to floats), so a fraction
    written differently is not a different setting."""
    current: dict[str, Any] = {
        "reference_pool.ladder_rule": ladder_rule(),
        "reference_hierarchy.regional_screen.enabled": regional_screen_enabled(),
    }
    ps = portfolio_settings()
    current.update({
        "metric_portfolio.fill_to": ps["fill_to"],
        "metric_portfolio.second_metric_rule": ps["second_metric_rule"],
        "metric_portfolio.second_metric_max_abs_spearman": ps["max_abs_spearman"],
        "metric_portfolio.second_metric_min_auc": ps["min_auc"],
    })
    gate = curve12_gate()
    current.update({"curve12.gate": gate["gate"], "curve12.min_auc": gate["min_auc"]})
    geo = seed_geometry()
    current.update({
        "curve10.tail_offsets_iqr": list(geo["tail_offsets_iqr"]),
        "curve10.zero_inflated_share": geo["zero_inflated_share"],
        "curve10.zero_inflated_handling": geo["zero_inflated_handling"],
    })
    return {k: v for k, v in sorted(current.items()) if v != KNOB_DEFAULTS[k]}


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

    # 4b. The screening method version mirrors run_state.
    try:
        from . import run_state as _rs
        declared = str(threshold("meta.screening_method_version"))
        if declared != _rs.SCREENING_METHOD_VERSION:
            problems.append(
                f"meta.screening_method_version ({declared!r}) differs from the "
                f"engine's {_rs.SCREENING_METHOD_VERSION!r}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the screening method version: {exc}")

    # 4c. The reference screen method version mirrors run_state (v0.12).
    try:
        from . import run_state as _rs
        declared = str(threshold("meta.reference_screen_method_version"))
        if declared != _rs.REFERENCE_SCREEN_METHOD_VERSION:
            problems.append(
                f"meta.reference_screen_method_version ({declared!r}) differs from the "
                f"engine's {_rs.REFERENCE_SCREEN_METHOD_VERSION!r}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the reference screen method version: {exc}")

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

    # 6. The reference screen mirrors the screen the vendored EASI built its
    #    regional reference curves with (rule REF-04, v0.12).
    try:
        from . import reference_screen as _rscreen
        problems += _rscreen.screen_drift()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the reference screen: {exc}")

    # 7. The fixed criteria mirror the vendored EASI scoring catalog (CURVE-11).
    try:
        from . import fixed_criteria as _fixed
        problems += _fixed.criteria_drift()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the fixed criteria: {exc}")

    # 8. The standing-decisions index mirrors the policy file's enabled flags
    #    (config/methodology/standing_decisions.yaml governs; the index is what
    #    the Rules page and the prose cite).
    try:
        from . import decisions as _dec
        policy = _dec.load_policy()
        entries = [e for e in policy.get("entries") or [] if e.get("id")]
        on = sorted(str(e["id"]) for e in entries if e.get("enabled", False))
        off = sorted(str(e["id"]) for e in entries if not e.get("enabled", False))
        index = cfg.get("standing_decisions") or {}
        declared_on = sorted(str(i) for i in index.get("default_enabled") or [])
        declared_off = sorted(str(i) for i in index.get("enable_per_run_only") or [])
        if declared_on != on:
            problems.append(
                f"standing_decisions.default_enabled {declared_on} differs from the "
                f"policy file's enabled entries {on}.")
        if declared_off != off:
            problems.append(
                f"standing_decisions.enable_per_run_only {declared_off} differs from "
                f"the policy file's opt-in entries {off}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the standing-decisions index: {exc}")

    # 9. The lifecycle block mirrors the library's vocabulary (library.py is
    #    the writer; the config describes it to a reader).
    try:
        from . import library as _lib
        life = cfg.get("lifecycle") or {}
        pairs = [
            ("lifecycle.version_statuses", list(life.get("version_statuses") or []),
             list(_lib.VERSION_STATUSES)),
            ("lifecycle.default_status", life.get("default_status"), _lib.DEFAULT_STATUS),
            ("lifecycle.validation_states", list(life.get("validation_states") or []),
             list(_lib.VALIDATION_STATES)),
        ]
        for path, declared, actual in pairs:
            if declared != actual:
                problems.append(
                    f"{path} ({declared!r}) differs from the library's {actual!r}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the lifecycle vocabulary: {exc}")

    # 10. The CURVE-12 verdict cuts mirror discrimination.py (campaign Round 2:
    #     the gate's min_auc is a knob and is not checked here; the cuts the
    #     verdict words rest on are the engine's).
    try:
        from . import discrimination as _dz
        cuts = (cfg.get("curve12") or {}).get("verdict_cuts") or {}
        pairs = [("discriminates", _dz.AUC_DISCRIMINATES), ("weak", _dz.AUC_WEAK),
                 ("inverted", _dz.AUC_INVERTED)]
        for name, engine_value in pairs:
            if name not in cuts:
                problems.append(f"curve12.verdict_cuts is missing '{name}'.")
            elif float(cuts[name]) != float(engine_value):
                problems.append(
                    f"curve12.verdict_cuts.{name} ({cuts[name]!r}) differs from the "
                    f"discrimination engine's {engine_value!r}.")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not compare the CURVE-12 verdict cuts: {exc}")

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


def _relative_config_path(path: Path) -> str:
    """The config file's path relative to the app root, or the absolute path when the
    config root was pointed elsewhere (``STREAMCURVES_CONFIG_ROOT``)."""
    try:
        return str(path.relative_to(CONFIG_DIR.parent)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def config_fingerprints() -> dict:
    """What a run record cites so another run can be compared against it.

    ``config_root`` appears only when the process runs under another config root
    (absence semantics: every record written under the app's own root keeps its keys)."""
    out = {
        "methodology_version": methodology_version(),
        "config_path": _relative_config_path(CONFIG_PATH),
        "config_sha256": _sha256(CONFIG_PATH),
        "rule_catalog_path": _relative_config_path(RULE_CATALOG_PATH),
        "rule_catalog_sha256": _sha256(RULE_CATALOG_PATH),
    }
    try:
        from . import paths as _paths
        if getattr(_paths, "CONFIG_ROOT_OVERRIDDEN", False):
            out["config_root"] = str(_paths.CONFIG_DIR).replace("\\", "/")
    except Exception:  # pragma: no cover - paths always imports
        pass
    return out


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
