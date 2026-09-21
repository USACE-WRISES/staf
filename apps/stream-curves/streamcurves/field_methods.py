"""How each metric is measured, for the bundle's ``methodContext``.

``config/field_methods.yaml`` holds a few plain sentences per metric: where on
the reach, how, and what the value is. A regional assessment's curves are built
from NRSA data, so a field value is comparable with its curve only when it is
measured the NRSA way, and the text restates that protocol from EPA's field
operations manual (the sources are listed in the file, each checked before use).

The exporter writes the text into every metric entry, DEEP prints it on the field
worksheet, in the Field Forms dialog and in the metric's hover card, and a metric
without an entry simply carries none.

Pure: one YAML read, cached.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Optional

from .config import read_yaml
from .paths import CONFIG_DIR

FIELD_METHODS_PATH = CONFIG_DIR / "field_methods.yaml"
#: What the DEEP worksheet prints of a method note before it cuts at a sentence.
WORKSHEET_LIMIT = 320


@lru_cache(maxsize=1)
def load() -> dict:
    return read_yaml(FIELD_METHODS_PATH) or {}


def clear_cache() -> None:
    load.cache_clear()


def sha256() -> Optional[str]:
    p = FIELD_METHODS_PATH
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _candidates(metric_key: str) -> list[str]:
    """The key as given, then without the StreamCat watershed suffix, so a
    column named ``bfiws`` finds an entry written as either."""
    key = str(metric_key)
    out = [key]
    if key.endswith("ws"):
        out.append(key[:-2])
    return out


def entry_for(metric_key: str) -> Optional[dict]:
    metrics = load().get("metrics") or {}
    for key in _candidates(metric_key):
        if key in metrics:
            return metrics[key]
    return None


def method_context(metric_key: str) -> str:
    """The metric's method text on one line, or ``""`` when it has no entry."""
    entry = entry_for(metric_key)
    if not entry:
        return ""
    return " ".join(str(entry.get("method") or "").split())


def where(metric_key: str) -> str:
    entry = entry_for(metric_key) or {}
    return str(entry.get("where") or "")


def source_of(metric_key: str) -> Optional[dict]:
    entry = entry_for(metric_key) or {}
    return (load().get("sources") or {}).get(str(entry.get("source") or ""))


def reach_layout() -> str:
    return " ".join(str(load().get("reach_layout") or "").split())


def annotations(metric_keys) -> dict[str, dict]:
    """``{metric: {"methodContext": text}}`` for the metrics that have an entry,
    in the shape the exporter's ``metricAnnotations`` take."""
    out: dict[str, dict] = {}
    for mk in metric_keys:
        text = method_context(mk)
        if text:
            out[str(mk)] = {"methodContext": text}
    return out
