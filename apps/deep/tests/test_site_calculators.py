"""The STAF site's Apply STAF page offers DEEP's calculators, one per Level III ecoregion, from
docs/_data/deep_calculators.json, which scripts/build_site_calculators.py writes and every bake of
DEEP's own folders refreshes (owner, 2026-10-08), and which must say what the library says. The
site opens DEEP inside the STAF app, and the app's STAF_LINKS mirror the site's."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
SCRIPT = APP / "scripts" / "build_site_calculators.py"
BAKE = APP / "scripts" / "bake_library_into_deep.py"
APPS_YML = REPO / "docs" / "_data" / "apps.yml"
SRC = (APP / "app.py").read_text(encoding="utf-8")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def site():
    if not APPS_YML.is_file():
        pytest.skip("no STAF site in this checkout")
    return _load(SCRIPT, "build_site_calculators")


def _entry(tool: str) -> dict:
    for block in re.split(r"(?m)^- (?=id: )", APPS_YML.read_text(encoding="utf-8")):
        fields = dict(re.findall(r"(?m)^\s*(\w+): *(.*?) *$", block))
        if fields.get("id") == tool:
            return fields
    return {}


def test_the_site_list_is_current(site):
    assert site.main(["--check"]) == 0, "run py apps/deep/scripts/build_site_calculators.py"


def test_the_list_is_every_shown_ecoregion_with_its_calculator(site):
    items = json.loads(site.OUT.read_text(encoding="utf-8"))["calculators"]
    assert len(items) >= 85
    assert all(i["kind"] == "ecoregion" for i in items)       # the page says one per Level III ecoregion
    assert not any(i["id"].endswith("-sqt-adapted") for i in items)
    assert all((REPO / i["file"]).is_file() and i["file"].endswith(f"/{i['id']}.xlsx") for i in items)
    assert set(i["status"] for i in items) <= {"Draft", "Preliminary", "Final"}   # owner, 2026-10-08
    assert len(set(i["region"] for i in items)) == len(items)
    assert [i["region"].casefold() for i in items] == sorted(i["region"].casefold() for i in items)


def test_the_site_list_matches_the_library(site):
    """Each entry is the version and status the library says DEEP opens by default (owner,
    2026-10-08: the site follows the library); the deep-site-list workflow runs the same check on
    every push that touches the library, DEEP's data or the list."""
    if site.library_defaults() is None:
        pytest.skip("no apps/library in this checkout")
    assert site.library_mismatches() == [], "rebake DEEP and commit the list with the library"
    items = json.loads(site.OUT.read_text(encoding="utf-8"))["calculators"]
    edited = [dict(items[0], status="Final")] + items[2:] + [dict(items[0], id="no-such-region")]
    found = "\n".join(site.library_mismatches(edited))
    assert f"{items[0]['id']}: the site list says" in found        # another status
    assert f"{items[1]['id']}: the library shows" in found         # left out
    assert "no-such-region: the site list offers" in found         # not in the library


def test_only_a_bake_of_deeps_own_folders_rewrites_the_list(tmp_path):
    bake = _load(BAKE, "bake_for_the_site_list")
    assert bake.refresh_site_list(tmp_path / "data", None) is None
    assert bake.refresh_site_list(bake.DEFAULT_OUT, tmp_path / "www") is None
    assert "refresh_site_list(args.out, args.www)" in BAKE.read_text(encoding="utf-8")


def test_the_site_names_this_list_and_opens_deep_in_staf(site):
    entry = _entry("deep")
    assert entry.get("calculator_list") == site.OUT.stem
    assert entry.get("url", "").startswith("https://gtmenichino-staf.") and entry["url"].endswith("/?tool=deep")
    block = SRC.split("STAF_LINKS = {", 1)[1].split("}", 1)[0]
    for tool in ("easi", "sfari", "deep"):
        assert re.search(rf'"{tool}":\s*"([^"]+)"', block).group(1) == _entry(tool)["url"]
