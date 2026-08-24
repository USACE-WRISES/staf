"""Readable names for metric keys.

The bundled NRSA catalog's ``label`` column is the bare mnemonic, so both paths
that seed ``metric_config`` produced a code where a name belongs: the import
wizard used the raw column name (``phab_XEMBED``) and the headless agent used the
mnemonic (``XEMBED``). This module resolves a real name instead, from
``data/nrsa/metric_dictionary.csv`` (built by
``scripts/nrsa/build_metric_dictionary.py``).

Two ways in:

* ``display_name_for`` / ``short_name_for`` / ``description_for`` / ``units_for``
  look a single key up.
* ``resolve_metric_config`` fills a whole ``metric_config`` in one pass, and only
  where the stored value is blank or is the code itself. A name a person typed is
  never overwritten.

Resolution happens at render time as well as at build time, so a session saved
before this existed, or a published assessment that must never be edited, still
shows readable names.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Mapping, Optional

import pandas as pd

from .paths import DATA_DIR

DICTIONARY_PATH = DATA_DIR / "nrsa" / "metric_dictionary.csv"

DICTIONARY_COLUMNS = [
    "metric_key", "display_name", "short_name", "description", "units", "category",
    "epa_short_name", "source_cycle", "name_origin",
]

# StreamCat serves one variable at two scales; the session key carries the scale
# suffix while the catalog and the dictionary hold the base code.
_SCALE_SUFFIXES = ("ws", "cat")


def _blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


@lru_cache(maxsize=1)
def load_dictionary(path: Optional[str] = None) -> pd.DataFrame:
    """The metric dictionary, or an empty frame when it has not been built."""
    target = DICTIONARY_PATH if path is None else path
    try:
        frame = pd.read_csv(target, dtype=str, keep_default_na=False, na_values=[""])
    except Exception:
        return pd.DataFrame({c: pd.Series([], dtype=object) for c in DICTIONARY_COLUMNS})
    for col in DICTIONARY_COLUMNS:
        if col not in frame.columns:
            frame[col] = None
    return frame


@lru_cache(maxsize=1)
def _index() -> dict[str, dict[str, Any]]:
    frame = load_dictionary()
    out: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        key = str(row.get("metric_key") or "").strip()
        if key:
            out.setdefault(key, row)
    return out


def clear_cache() -> None:
    """Drop the cached dictionary (tests and the build script rebuild it)."""
    load_dictionary.cache_clear()
    _index.cache_clear()


def _candidates(metric_key: str):
    key = str(metric_key or "").strip()
    if not key:
        return
    yield key
    lowered = key.lower()
    for suffix in _SCALE_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            yield key[: -len(suffix)]
    # a bare mnemonic stored where a namespaced key belongs
    if "_" not in key:
        for prefix in ("phab", "chem", "bent", "fish", "land"):
            yield f"{prefix}_{key}"


def entry_for(metric_key: str) -> dict[str, Any]:
    index = _index()
    for candidate in _candidates(metric_key):
        found = index.get(candidate)
        if found:
            return found
    return {}


def _field(metric_key: str, field: str, default: Optional[str] = None) -> Optional[str]:
    value = entry_for(metric_key).get(field)
    return default if _blank(value) else str(value).strip()


def display_name_for(metric_key: str, default: Optional[str] = None) -> Optional[str]:
    """The readable name, e.g. ``phab_XEMBED`` -> 'Embeddedness'."""
    return _field(metric_key, "display_name", default)


def short_name_for(metric_key: str, default: Optional[str] = None) -> Optional[str]:
    """A compact name for tile headers and table cells; falls back to the name."""
    return _field(metric_key, "short_name", None) or display_name_for(metric_key, default)


def description_for(metric_key: str, default: Optional[str] = None) -> Optional[str]:
    """One or two sentences on what the metric measures, for tooltips."""
    return _field(metric_key, "description", default)


def units_for(metric_key: str, default: Optional[str] = None) -> Optional[str]:
    return _field(metric_key, "units", default)


def category_for(metric_key: str, default: Optional[str] = None) -> Optional[str]:
    """The NRSA catalog category, e.g. 'Physical habitat' or 'Landscape'."""
    return _field(metric_key, "category", default)


def is_placeholder_name(name: Any, metric_key: str) -> bool:
    """True when a stored display name is really just the code.

    Both seeding paths wrote a code: the wizard the full column name, the
    headless agent the mnemonic after the prefix. Either is safe to replace; a
    name someone typed is not.
    """
    if _blank(name):
        return True
    text = str(name).strip()
    key = str(metric_key or "").strip()
    if text == key:
        return True
    if "_" in key and text == key.split("_", 1)[1]:
        return True
    # the wizard also sanitizes column names, so compare loosely
    return re.sub(r"[^0-9a-z]", "", text.lower()) == re.sub(r"[^0-9a-z]", "", key.lower())


def resolve_metric_config(metric_config: Optional[Mapping[str, Any]]) -> dict[str, dict]:
    """Fill placeholder ``display_name`` / ``units`` across a whole config.

    Returns a new dict; the input is not modified. Entries that already carry a
    real name keep it, so this is safe to run on every render.
    """
    if not metric_config:
        return {}
    out: dict[str, dict] = {}
    for key, entry in metric_config.items():
        cfg = dict(entry or {})
        if is_placeholder_name(cfg.get("display_name"), key):
            resolved = display_name_for(key)
            if resolved:
                cfg["display_name"] = resolved
        if _blank(cfg.get("units")):
            resolved_units = units_for(key)
            if resolved_units:
                cfg["units"] = resolved_units
        if _blank(cfg.get("description")):
            resolved_desc = description_for(key)
            if resolved_desc:
                cfg["description"] = resolved_desc
        out[key] = cfg
    return out
