"""The station-level biological indices table (scripts/build_bio_indices.py): one row
per station from the newest cycle that published an index, matched through the
archive's station tables, classes recoded as EPA prints them, nothing filled, and a
manifest that names its sources and what a cycle does not publish."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

APP = Path(__file__).resolve().parents[1]
SCRIPTS = APP / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_bio_indices as bb  # noqa: E402


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)] + [",".join("" if v is None else str(v) for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")


@pytest.fixture
def raw(tmp_path) -> Path:
    root = tmp_path / "raw"
    _write(root / "1314/nrsa1314_allcond_05312019_0.csv",
           ["SITE_ID", "VISIT_NO", "BENT_MMI_COND", "MMI_BENT", "OE_SCORE", "OE_COND", "FISH_MMI_COND", "MMI_FISH"],
           [["A1", 1, "Good", 70.5, 0.95, "O/E>=0.9", "Fair", 55.0]])
    _write(root / "1314/nrsa1314_bentmmi_04232019.csv",
           ["PUBLICATION_DATE", "SITE_ID", "VISIT_NO", "INDEX_VISIT", "MMI_BENT", "OE_SCORE"],
           [["x", "A1", 1, "Y", 70.5, 0.95], ["x", "A1", 2, "N", 10.0, 0.10], ["x", "A2", 1, "Y", 33.0, 0.61]])
    _write(root / "1314/nrsa1314_fishmmi_04232019.csv", ["SITE_ID", "VISIT_NO", "MMI_FISH"],
           [["A1", 1, 55.0], ["A2", 1, 41.0]])
    _write(root / "1819/nrsa-1819-benthic-macroinvertebrate-mmi-data.csv",
           ["SITE_ID", "VISIT_NO", "MMI_BENT", "BENT_MMI_COND"],
           [["B1", 1, 66.0, "Good"], ["B2", 1, 12.0, "Poor"], ["B2", 2, 14.0, "Poor"]])
    _write(root / "1819/nrsa-1819-fish-mmi-data.csv", ["SITE_ID", "VISIT_NO", "FISH_MMI_COND", "MMI_FISH"],
           [["B1", 1, "Fair", 50.0]])
    _write(root / "1819/nrsa1819_data_for_populationestimates.csv", ["SITE_ID", "VISIT_NO", "OE_COND"],
           [["B1", 1, "O/E<0.8"], ["B2", 1, "Not Assessed"]])
    _write(root / "2324/nrsa2324_benthicmmi.csv", ["SITE_ID", "VISIT_NO", "MMI_BENT", "BENT_MMI_COND"],
           [["C1", 1, 60.0, "Not Assessed"], ["ZZ", 1, 50.0, "Good"]])
    _write(root / "2324/nrsa2324_fishmmi.csv", ["SITE_ID", "VISIT_NO", "FISH_MMI_COND", "MMI_FISH"],
           [["C1", 1, "Good", 80.0]])
    return root


@pytest.fixture
def archive(tmp_path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    visits = pd.DataFrame({"cycle": ["1314", "1314", "1314", "1819", "1819", "2324"],
                           "site_id": ["A1", "A1", "A2", "B1", "B2", "C1"],
                           "visit_no": ["1", "2", "1", "1", "1", "1"],
                           "station_key": ["ST1", "ST1", "ST2", "ST1", "ST3", "ST1"]})
    visits.to_parquet(root / "site_visits.parquet", index=False)
    stations = pd.DataFrame({"station_key": ["ST1", "ST2", "ST3"], "us_l3code": ["55", "55", "71"],
                             "ag_eco9": ["TPL", "TPL", "SAP"], "huc8": ["05120101", "05120102", "05130101"],
                             "cycles_sampled": ["1314,1819,2324", "1314", "1819"]})
    stations.to_parquet(root / "stations.parquet", index=False)
    return root


def _sources():
    return bb.load_sources(bb.DEFAULT_SOURCES)


def test_recode_class_reads_labels_as_epa_prints_them():
    s = _sources()
    assert bb.recode_class("oe", "O/E>=0.9", s) == "Good"
    assert bb.recode_class("oe", "O/E<0.9", s) == "Fair"
    assert bb.recode_class("oe", "O/E<0.8", s) == "Poor"
    assert bb.recode_class("oe", "OE<0.5", s) == "Poor"
    assert bb.recode_class("oe", "Not Assessed", s) is None
    assert bb.recode_class("benthic_mmi", "poor", s) == "Poor"
    assert bb.recode_class("fish_mmi", "Insufficient Sampling", s) is None
    assert bb.recode_class("fish_mmi", None, s) is None


def test_build_takes_the_newest_index_and_never_fills(raw, archive):
    table, manifest = bb.build(raw=raw, archive=archive, sources=_sources(), lock=None)
    assert list(table.columns) == bb.COLUMNS
    by = table.set_index("station_key")
    assert list(by.index) == ["ST1", "ST2", "ST3"]
    st1 = by.loc["ST1"]
    assert st1["benthic_mmi_cycle"] == "2324" and st1["mmi_bent"] == 60.0
    assert pd.isna(st1["benthic_mmi_class"]), "2023-24 printed Not Assessed; the 2018-19 class is never filled in"
    assert st1["oe_cycle"] == "1819" and st1["oe_class"] == "Poor" and pd.isna(st1["oe_score"]), "2018-19 has no O/E score"
    assert st1["fish_mmi_cycle"] == "2324" and st1["fish_mmi_class"] == "Good" and st1["mmi_fish"] == 80.0
    st2 = by.loc["ST2"]
    assert st2["benthic_mmi_cycle"] == "1314" and st2["mmi_bent"] == 33.0 and pd.isna(st2["benthic_mmi_class"])
    assert st2["oe_cycle"] == "1314" and st2["oe_score"] == 0.61 and pd.isna(st2["oe_class"])
    assert st2["mmi_fish"] == 41.0 and pd.isna(st2["fish_mmi_class"])
    st3 = by.loc["ST3"]
    assert st3["benthic_mmi_cycle"] == "1819" and st3["benthic_mmi_class"] == "Poor" and st3["mmi_bent"] == 12.0
    assert pd.isna(st3["oe_cycle"]) and pd.isna(st3["fish_mmi_cycle"])
    assert table["chlorophyll_class"].isna().all()
    assert st1["us_l3code"] == "55" and st3["ag_eco9"] == "SAP"
    # the 1314 visit-2 row never entered: ST1's 2013-14 benthic score would otherwise be 10
    c1314 = manifest["cycles"]["1314"]
    assert c1314["index_visits"] == 2 and c1314["matched_stations"] == 2 and c1314["unmatched_site_ids"] == []
    assert c1314["benthic_mmi"] == {"with_score": 2, "with_class": 1, "score_published": True, "class_published": True}
    assert manifest["cycles"]["2324"]["unmatched_site_ids"] == ["ZZ"]
    assert manifest["cycles"]["1819"]["oe"]["score_published"] is False
    assert manifest["indices"]["benthic_mmi"]["by_newest_cycle"] == {"1314": 1, "1819": 1, "2324": 1}
    assert manifest["indices"]["chlorophyll"]["stations"] == 0
    statements = " ".join(s["statement"] for s in manifest["not_published"])
    assert "chlorophyll" in statements and "2018-19 publishes the class only" in statements
    assert all(len(s["sha256"]) == 64 and s["lock_match"] is None for s in manifest["sources"])
    assert manifest["stations"] == 3


def test_lock_drift_is_refused_unless_allowed(raw, archive):
    path = "1314/nrsa1314_allcond_05312019_0.csv"
    lock = {"files": {"1314/KEY_VARIABLES/data": {"sha256": "sha256:" + "0" * 64, "file": Path(path).name}}}
    with pytest.raises(SystemExit):
        bb.build(raw=raw, archive=archive, sources=_sources(), lock=lock)
    table, manifest = bb.build(raw=raw, archive=archive, sources=_sources(), lock=lock, allow_drift=True)
    assert manifest["lock_drift"] == [path]
    rec = next(s for s in manifest["sources"] if s["path"] == path)
    assert rec["lock_match"] is False and rec["lock_sha256"] == "0" * 64


def test_committed_sources_table_names_locked_files():
    sources = _sources()
    lock_path = bb.DEFAULT_ARCHIVE / bb.LOCK_FILE
    if not lock_path.is_file():
        pytest.skip("no sources.lock.json in this checkout")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))["files"]
    for cycle, block in sources["cycles"].items():
        for f in block["files"]:
            assert f["lock_key"] in lock, f["lock_key"]
            assert lock[f["lock_key"]]["file"] == Path(f["path"]).name
            assert str(f["path"]).startswith(str(cycle) + "/")
            for index, cols in f["provides"].items():
                spec = sources["indices"][index]
                assert set(cols) <= {spec["score_column"], spec["class_column"]}, (f["path"], index)
    assert sources["indices"]["chlorophyll"]["not_published"]
    text = bb.DEFAULT_SOURCES.read_text(encoding="utf-8")
    assert chr(0x2014) not in text
    yaml.safe_load(text)


def test_main_refuses_a_root_under_apps(tmp_path, raw, archive):
    with pytest.raises(SystemExit):
        bb.main(["--out", str(APP / "data" / "scratch"), "--raw", str(raw), "--archive", str(archive)])
