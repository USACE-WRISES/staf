"""The app reads the STAF data bundle from its staf-data-current release by default (2026-10-04):
app.py sets STAF_DATA_SOURCE to auto unless it is set, so an explicit value wins (service asks the
USGS services only); the site engine's own default stays service (StreamCurves, the national builder)."""
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
LINE = 'os.environ.setdefault("STAF_DATA_SOURCE", "auto")'


def test_app_py_defaults_the_data_source_to_auto():
    src = (APP / "app.py").read_text(encoding="utf-8")
    assert src.count(LINE) == 1
    assert src.index(LINE) < src.index("from shiny import")     # beside the other environment defaults
