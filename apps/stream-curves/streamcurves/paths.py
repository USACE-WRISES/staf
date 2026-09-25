"""Repo-relative paths.

All disk locations derive from this module so the whole app folder can be
relocated (e.g. into a future staf-app monorepo) without touching code.

``STREAMCURVES_CONFIG_ROOT`` (campaign Round 1) points a FRESH process at another
config folder, so an experimental methodology configuration can run beside the
app's own without a second worktree. It is read once, at import: the app and the
batch runners load their configuration through this module's constants, so a
value set after import changes nothing. A run under another root records it as
``experimental.configRoot`` in its manifest (regional_agent.experimental_block),
and the canonical library refuses such a manifest.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CONFIG_ROOT_ENV = "STREAMCURVES_CONFIG_ROOT"


def _config_dir() -> Path:
    raw = os.environ.get(_CONFIG_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return ROOT / "config"


CONFIG_DIR = _config_dir()
APP_CONFIG_DIR = ROOT / "config"
CONFIG_ROOT_OVERRIDDEN = CONFIG_DIR != APP_CONFIG_DIR
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = DATA_DIR / "templates"
WWW_DIR = ROOT / "www"
