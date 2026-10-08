"""The STAF site (docs/_data/apps.yml) opens EASI inside the STAF app and offers its blank calculator
for download, linked raw from main (owner, 2026-10-08). A renamed calculator or a moved app must
not leave the site with a dead link, and the app's STAF_LINKS mirror the site's."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from easi import calculator

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
APPS_YML = REPO / "docs" / "_data" / "apps.yml"
SRC = (APP / "app.py").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(not APPS_YML.is_file(), reason="no STAF site in this checkout")


def _entry(tool: str) -> dict:
    for block in re.split(r"(?m)^- (?=id: )", APPS_YML.read_text(encoding="utf-8")):
        fields = dict(re.findall(r"(?m)^\s*(\w+): *(.*?) *$", block))
        if fields.get("id") == tool:
            return fields
    return {}


def _staf_link(tool: str) -> str:
    block = SRC.split("STAF_LINKS = {", 1)[1].split("}", 1)[0]
    return re.search(rf'"{tool}":\s*"([^"]+)"', block).group(1)


def test_the_site_offers_the_calculator_the_app_serves():
    entry = _entry("easi")
    assert (REPO / entry["calculator"]).resolve() == calculator.TEMPLATE_PATH.resolve(), \
        "the calculator was renamed: update calculator and calculator_label in docs/_data/apps.yml"
    assert f"version {calculator.TEMPLATE_VERSION}" in entry["calculator_label"]


def test_the_site_opens_easi_inside_staf():
    entry = _entry("easi")
    assert entry["url"].startswith("https://gtmenichino-staf.") and entry["url"].endswith("/?tool=easi")
    assert _staf_link("easi") == entry["url"]
    assert all(_staf_link(tool) == _entry(tool)["url"] for tool in ("sfari", "deep"))
