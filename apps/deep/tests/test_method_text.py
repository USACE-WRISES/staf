"""Get Forms reads plainly (2026-10-04): the method text in its concise wording, the
Source column naming the data rather than the engine, and a table without the
scored-against line under every metric.

The concise wording is applied at display, keyed by the published text, so no
bundle or digest changes; each concise text keeps every number of the original."""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from deep import assessments, field_form, method_text, report

ROOT = Path(__file__).resolve().parents[1]
#: the engine sentence older bundles appended; the concise text names the data instead
_BOILER = re.compile(r"Values recomputed by the STAF site engine v[0-9.]+ over the HR reach "
                     r"watershed \([^)]*\)\.")


def _numbers(text: str) -> set:
    t = _BOILER.sub("", text)
    t = re.sub(r"v[0-9]+(\.[0-9]+)+", "", t)
    return set(re.findall(r"[0-9]+(?:\.[0-9]+)?", t))


def _displayed_texts() -> set:
    """Every method text DEEP can show: the one :func:`field_form.method_text` picks
    for each metric of every bundle and library version."""
    files = glob.glob(str(ROOT / "data" / "bundles" / "*.deep.json"))
    files += glob.glob(str(ROOT.parent / "library" / "assessments" / "**" / "*.json"),
                       recursive=True)
    out = set()
    for f in files:
        try:
            b = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(b, dict):
            continue
        for fn in b.get("metricsByFunction") or []:
            for m in fn.get("metrics") or []:
                name = str(m.get("metricName") or "")
                for key in ("methodContext", "howToMeasure", "metricStatement"):
                    t = " ".join(str(m.get(key) or "").split())
                    if t and t != name:
                        out.add(t)
                        break
    return out


def test_each_concise_text_is_shorter_and_keeps_every_number():
    assert method_text.CONCISE
    for old, new in method_text.CONCISE.items():
        assert len(new) < len(old), old[:60]
        assert _numbers(old) <= _numbers(new), (old[:60], _numbers(old) - _numbers(new))
        assert chr(0x2014) not in new and chr(0x2013) not in new, new[:60]
        assert "STAF site engine" not in new and "HR reach watershed" not in new, new[:60]
        assert new == " ".join(new.split()) and old == " ".join(old.split())


def test_every_long_text_deep_shows_has_a_concise_wording():
    texts = _displayed_texts()
    assert texts
    missing = [t[:80] for t in texts if len(t) > 160 and t not in method_text.CONCISE]
    assert not missing, missing
    stale = [k[:80] for k in method_text.CONCISE if k not in texts]
    assert not stale, stale


def test_method_text_applies_the_concise_wording():
    m = {"metricName": "Impervious surface",
         "methodContext": "Percent of the watershed in impervious surface. DEEP computes it "
                          "for the delineated watershed. Nothing is measured in the field."}
    assert field_form.method_text(m) == ("Percent of the delineated watershed in impervious "
                                         "surface, computed by DEEP. No field measurement.")
    assert field_form.method_text({"metricName": "x", "methodContext": "Count riffles."}) \
        == "Count riffles."
    assert method_text.concise("  a   b ") == "a b"


def test_the_source_column_names_the_data_not_the_engine():
    lab = report.source_label
    assert lab({"origin": "desktop", "basis": "site-engine",
                "source": "STAF site engine v0.5.0 impervious (HR reach watershed, NLCD 2021)"}) \
        == "NLCD 2021"
    assert lab({"origin": "desktop", "basis": "site-engine",
                "source": "STAF site engine v0.5.0 road density (HR reach watershed, "
                          "TIGERweb roads)"}) == "TIGERweb roads"
    assert lab({"origin": "desktop", "engine": True,
                "source": "STAF site engine v0.2.0 (HR reach watershed)"}) == "Computed by DEEP"
    assert lab({"origin": "desktop", "basis": "streamcat",
                "source": "StreamCat lookup engine pctwdwet2019 plus pcthbwet2019 "
                          "(watershed)"}) == "EPA StreamCat"
    assert lab({"origin": "desktop", "basis": "streamcat",
                "source": "StreamCat lookup engine rddens (watershed), describes the nearest "
                          "StreamCat reach Mink Brook (COMID 1), 446 ft downstream"}) \
        == "EPA StreamCat, nearest reach"
    assert lab({"origin": "desktop", "basis": "nlcd",
                "source": "NLCD 2021 impervious (watershed)"}) == "NLCD 2021"
    assert lab({"origin": "desktop", "basis": "3dep", "source": "3DEP DEM cross-sections"}) \
        == "3DEP elevation (modeled)"
    assert lab({"origin": "desktop", "basis": "nid", "source": "USACE National Inventory of Dams"}) \
        == "National Inventory of Dams"
    assert lab({"origin": "field", "basis": "streamcat", "source": "x"}) == ""
    assert lab(None) == ""


def test_the_get_forms_rows_use_the_short_source_and_the_concise_method():
    la = assessments.load_predefined("northeastern-highlands")
    measured = {"spring-pctimp2019ws": {
        "value": 1.0, "na": False, "note": "", "origin": "desktop", "engine": True,
        "basis": "site-engine",
        "source": "STAF site engine v0.5.0 impervious (HR reach watershed, NLCD 2021)"}}
    row = next(r for r in report.metric_rows(la, measured)
               if r["metricId"] == "spring-pctimp2019ws")
    assert row["source"] == "NLCD 2021" and row["status"] == report.STATUS_AVAILABLE
    assert row["method"].endswith("computed by DEEP. No field measurement.")
    # the CSV keeps the full record
    csv_txt = report.build_csv({}, la, measured, {"subIndices": {}})
    assert "STAF site engine v0.5.0 impervious (HR reach watershed, NLCD 2021)" in csv_txt


def test_the_dialog_table_is_plain():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    table = src[src.index("def ff_table():"):src.index("def _ff_preview_route(")]
    assert 'ui.tags.th("F/D"' in table and "white-space:nowrap" in table
    assert "scored_against" not in table and "ff-src-sub" not in table
