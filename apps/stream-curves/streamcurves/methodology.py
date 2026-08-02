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


def load_config() -> dict:
    return read_yaml(CONFIG_PATH) or {}


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
