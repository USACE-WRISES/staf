"""Config registry loaders — port of the config block in app/global.R (142-148)
plus cached readers for the YAML/JSON registries under config/.

At startup the app is in a no-data state: metric/strat/predictor/factor-recode
configs begin as empty dicts and are populated from an uploaded workbook (or a
restored session). Only output_registry.yaml is loaded eagerly, matching
global.R.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_DIR


@lru_cache(maxsize=None)
def _read_yaml(path: str) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_yaml(path: str | Path) -> Any:
    return _read_yaml(str(path))


def read_json(path: str | Path) -> Any:
    return _read_json(str(path))


# --------------------------------------------------------------------------- #
# Registry readers (cached; keyed structures mirror the R named lists).
# --------------------------------------------------------------------------- #


def output_registry() -> dict:
    return read_yaml(CONFIG_DIR / "output_registry.yaml") or {}


def oh_parameter_map_raw() -> dict:
    return read_yaml(CONFIG_DIR / "oh_parameter_map.yaml") or {}


def metric_registry() -> dict:
    return read_yaml(CONFIG_DIR / "metric_registry.yaml") or {}


def stratification_registry() -> dict:
    return read_yaml(CONFIG_DIR / "stratification_registry.yaml") or {}


def predictor_registry() -> dict:
    return read_yaml(CONFIG_DIR / "predictor_registry.yaml") or {}


def factor_recode_registry() -> dict:
    return read_yaml(CONFIG_DIR / "factor_recode_registry.yaml") or {}


def metric_map_raw() -> dict:
    return read_yaml(CONFIG_DIR / "metric_map.yaml") or {}


def staf_functions_raw() -> Any:
    return read_json(CONFIG_DIR / "staf_functions.json")


def staf_metric_library_raw() -> Any:
    return read_json(CONFIG_DIR / "staf_metric_library.json")


def startup_configs() -> dict[str, dict]:
    """The app-start config state (global.R:144-148): everything empty except
    output_config."""
    return {
        "metric_config": {},
        "strat_config": {},
        "predictor_config": {},
        "factor_recode_config": {},
        "output_config": output_registry(),
    }
