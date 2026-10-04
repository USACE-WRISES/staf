"""The lean values index (``hrbuild.leanpack.write_index``): a lean run over the regions still missing
keeps the regions an earlier run listed (the national build encodes in more than one run)."""
import json

from hrbuild.leanpack import lean, write_index


def _region(n):
    return {"exact_bytes": 10 * n, "lean_bytes": 4 * n, "ratio": 0.4, "files": {}}


def test_a_later_run_keeps_the_earlier_regions(tmp_path):
    for v in ("0101", "0102", "1801"):
        (tmp_path / f"lc2_{v}.parquet").write_bytes(b"lean")
    write_index(tmp_path, {"encoding": lean.ENCODING, "regions": {"0101": _region(1), "0102": _region(2)}})
    index = write_index(tmp_path, {"encoding": lean.ENCODING, "regions": {"1801": _region(3)}})
    assert list(index["regions"]) == ["0101", "0102", "1801"]
    assert index["exact_bytes"] == 60 and index["lean_bytes"] == 24 and index["ratio"] == 0.4
    assert json.loads((tmp_path / "values.json").read_text(encoding="utf-8")) == index


def test_regions_without_lean_files_or_of_another_encoding_drop_out(tmp_path):
    (tmp_path / "lc2_0102.parquet").write_bytes(b"lean")
    (tmp_path / "values.json").write_text(json.dumps({"encoding": lean.ENCODING, "regions": {"0101": _region(1)}}),
                                          encoding="utf-8")
    index = write_index(tmp_path, {"encoding": lean.ENCODING, "regions": {"0102": _region(2)}})
    assert list(index["regions"]) == ["0102"]                    # 0101's lean files are gone
    (tmp_path / "values.json").write_text(json.dumps({"encoding": "lean-0", "regions": {"0102": _region(9)}}),
                                          encoding="utf-8")
    index = write_index(tmp_path, {"encoding": lean.ENCODING, "regions": {}})
    assert index["regions"] == {}                                # an older encoding's entries are not kept
