"""The DEEP guide's metric reference (docs/walkthroughs/deep/index.md) is generated
from the regional assessments by scripts/build_guide_reference.py (2026-10-04): the
detail the Assessment page leaves out lives there. The page must be current."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_guide_reference.py"


def _module():
    spec = importlib.util.spec_from_file_location("build_guide_reference", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_guide_reference_is_current():
    mod = _module()
    if not Path(mod.PAGE).exists():
        pytest.skip("the docs site is not in this checkout")
    assert mod.main(["--check"]) == 0, "run apps/deep/scripts/build_guide_reference.py"


def test_the_guide_reference_covers_every_discipline_and_names_no_engine():
    section, n = _module().generated()
    assert n >= 30
    for disc in ("Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"):
        assert f"### {disc}\n" in section, disc
    assert section.count("How to measure.") == n
    for word in ("STAF site engine", "HR reach watershed", chr(0x2014), chr(0x2013)):
        assert word not in section, word
