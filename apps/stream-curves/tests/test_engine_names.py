"""Both watershed engines are named once, from the vendored vocabulary, and the
tokens the digests and bundles carry never change."""
from __future__ import annotations

import re
from pathlib import Path

from streamcurves import engine_names, metric_picker, science_report, site_engine_source
from streamcurves._vendor.site_engine import naming

ROOT = Path(__file__).resolve().parents[1]


def test_display_names_come_from_the_vendored_vocabulary():
    assert engine_names.SITE_ENGINE == naming.DISPLAY_NAMES[naming.SITE_ENGINE_TOKEN] == "STAF site engine"
    assert engine_names.STREAMCAT == naming.DISPLAY_NAMES[naming.STREAMCAT_TOKEN] == "StreamCat lookup engine"
    assert engine_names.site_engine_label("0.2.0") == "STAF site engine v0.2.0"
    assert engine_names.predictor_source_display(None) == "StreamCat lookup engine"
    assert engine_names.predictor_source_display("streamcat") == "StreamCat lookup engine"
    assert engine_names.predictor_source_display("site-engine v0.2.0") == (
        "STAF site engine (site-engine v0.2.0), exact-watershed values")


def test_tokens_are_unchanged():
    assert naming.SITE_ENGINE_TOKEN == "site-engine" and naming.STREAMCAT_TOKEN == "streamcat"
    assert site_engine_source.predictor_source_of([]) == "streamcat"
    assert site_engine_source.engine_identity()["id"] == "site-engine"
    assert metric_picker._SRC_DISPLAY["streamcat"] == engine_names.STREAMCAT
    assert metric_picker._SRC_DISPLAY["site_engine"] == engine_names.SITE_ENGINE
    cli = (ROOT / "scripts" / "run_region_batch.py").read_text(encoding="utf-8")
    assert cli.count('choices=("streamcat", "site-engine")') == 2
    rb = (ROOT / "views" / "region_builder.py").read_text(encoding="utf-8")
    assert '"streamcat":' in rb and '"site-engine":' in rb


def test_wizard_source_filter_keys_match_the_picker_labels():
    src = (ROOT / "views" / "import_map.py").read_text(encoding="utf-8")
    assert '"StreamCat": "StreamCat"' not in src and '"Site engine":' not in src
    assert '_SRC_DISPLAY["streamcat"]' in src and '_SRC_DISPLAY["site_engine"]' in src
    assert "EPA StreamCAT" not in src


def test_science_report_names_the_engine():
    html = science_report.render_report if hasattr(science_report, "render_report") else None
    assert html is None or callable(html)
    assert "engine_names.predictor_source_display" in (
        ROOT / "streamcurves" / "science_report.py").read_text(encoding="utf-8")


def test_changed_copy_has_no_em_dash():
    for rel in ("streamcurves/engine_names.py", "views/region_builder.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for m in re.finditer(r"engine_names\.[A-Z_]+|STAF site engine[^\n\"]*", text):
            assert "—" not in m.group(0)
    assert "—" not in engine_names.SITE_ENGINE_COST
