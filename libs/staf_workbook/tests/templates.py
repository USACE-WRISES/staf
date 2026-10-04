"""The three apps' real calculator templates (tests skip where the apps are not checked out)."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EASI = REPO / "apps" / "easi" / "www" / "calculator" / "EASI_Calculator_1.1.xlsx"
SFARI = REPO / "apps" / "sfari" / "data" / "calculator" / "SFARI_Calculator_Draft_2026-06-29.xlsx"
DEEP_DIR = REPO / "apps" / "deep" / "www" / "calculators"
DEEP = DEEP_DIR / "blue-ridge@v1.xlsx"


def all_templates() -> list:
    out = [p for p in (EASI, SFARI) if p.is_file()]
    if DEEP_DIR.is_dir():
        out += sorted(p for p in DEEP_DIR.glob("*@v*.xlsx"))
    return out


def main_templates() -> list:
    return [p for p in (EASI, SFARI, DEEP) if p.is_file()]
