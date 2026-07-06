"""Repo-relative paths.

All disk locations derive from this module so the whole app folder can be
relocated (e.g. into a future staf-app monorepo) without touching code.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = DATA_DIR / "templates"
WWW_DIR = ROOT / "www"
