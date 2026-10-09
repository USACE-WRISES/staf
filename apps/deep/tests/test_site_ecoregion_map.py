"""The Apply STAF page's "Find your ecoregion" map (owner, 2026-10-08): docs/assets/data/
ecoregions-l3-map.json, drawn from DEEP's own Level III outlines by
scripts/build_site_ecoregion_map.py. It must be current, cover every ecoregion the site offers a
DEEP calculator for, stay small enough to load the moment the dialog opens, and color its regions
in DEEP's status palette."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
SCRIPT = APP / "scripts" / "build_site_ecoregion_map.py"
MAP = REPO / "docs" / "assets" / "data" / "ecoregions-l3-map.json"
CALCULATORS = REPO / "docs" / "_data" / "deep_calculators.json"
CSS = REPO / "docs" / "assets" / "css" / "custom.css"
JS = REPO / "docs" / "assets" / "js" / "apply-staf.js"


@pytest.fixture(scope="module")
def site():
    if not MAP.parent.is_dir():
        pytest.skip("no STAF site in this checkout")
    spec = importlib.util.spec_from_file_location("build_site_ecoregion_map", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_map_is_built_from_the_current_outlines_and_script(site):
    """The recorded input digests, not a rebuild (that reads the 1:500,000 states for ~25 s);
    `build_site_ecoregion_map.py --check` does the full comparison by hand."""
    recorded = json.loads(MAP.read_text(encoding="utf-8"))["inputs"]
    assert recorded == site.input_digests(), "run py apps/deep/scripts/build_site_ecoregion_map.py"


def test_the_map_has_deeps_ecoregions_and_every_calculator(site):
    doc = json.loads(MAP.read_text(encoding="utf-8"))
    codes = [r["code"] for r in doc["regions"]]
    source = json.loads((APP / "data" / "ecoregions_l3.geojson").read_text(encoding="utf-8"))
    assert codes == sorted({str(f["properties"]["US_L3CODE"]) for f in source["features"]}, key=int)
    assert len(codes) == 85 and all(r["d"].startswith("M") and r["name"] for r in doc["regions"])
    offered = {str(c["code"]) for c in json.loads(CALCULATORS.read_text(encoding="utf-8"))["calculators"]}
    assert offered <= set(codes), "a calculator the map cannot select"
    assert doc["states"].startswith("M") and doc["width"] == 1000 and doc["height"] > 0


def test_the_map_stays_small():
    assert MAP.stat().st_size < 250_000


def _deep_palette() -> dict:
    """DEEP's status colors, ``{label: (line, fill)}``, read as text from www/coverage.js, which
    test_region_status.py holds equal to app.py's REGION_STATUS_STYLE (no Shiny import here)."""
    src = (APP / "www" / "coverage.js").read_text(encoding="utf-8")
    block = src.split("var STATUS = {", 1)[1].split("};", 1)[0]
    rows = re.findall(r'(\w+): \{ label: "([^"]+)", line: "(#[0-9a-f]{6})", fill: "(#[0-9a-f]{6})"', block)
    return dict((label, (line, fill)) for _key, label, line, fill in rows)


def _rule(css: str, selector: str) -> str:
    return css.split(selector + " {", 1)[1].split("}", 1)[0]


def test_the_map_colors_its_regions_in_deeps_palette():
    """The site's map shows each region in its calculator's status color, as DEEP's map does
    (owner, 2026-10-08), and its key draws DEEP's legend swatch: the line color around the fill."""
    if not CSS.is_file():
        pytest.skip("no STAF site in this checkout")
    css = CSS.read_text(encoding="utf-8")
    palette = _deep_palette()
    assert list(palette) == ["Draft", "Preliminary", "Final"]
    for label, (line, fill) in palette.items():
        cls = "is-" + label.lower()
        assert f"fill: {fill};" in _rule(css, f".eco-map-regions path.{cls}"), label
        swatch = _rule(css, f".eco-map-swatch.{cls}")
        rgb = ", ".join(str(int(fill[i:i + 2], 16)) for i in (1, 3, 5))
        assert f"border-color: {line};" in swatch and f"rgba({rgb}, " in swatch, label
    script = JS.read_text(encoding="utf-8")
    assert "{ Draft: 'is-draft', Preliminary: 'is-preliminary', Final: 'is-final' }" in script
