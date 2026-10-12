"""The app's index holds what the app reads of the manifest (webdata/build.py app_index)."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webdata import build  # noqa: E402


def test_app_index():
    manifest = {"format": 1, "built": "2026-10-11T00:00:00+00:00", "sources": {"x": {}}, "regions": ["0101"],
                "files": {"bundle/lines2_0101.parquet": {"path": "bundle/0101/lines2_0101.abc.parquet", "bytes": 10,
                                                         "sha256": "a" * 64, "kind": "region", "region": "0101"},
                          "library/m.zip": {"path": "library/m.def.zip", "bytes": 5, "sha256": "b" * 64, "kind": "library"},
                          "library/c.json": {"path": "library/c.123.json.gz", "bytes": 7, "sha256": "d" * 64, "kind": "library",
                                             "encoding": "gzip", "raw_bytes": 40, "raw_sha256": "e" * 64}}}
    raw = build.app_index(manifest)
    assert raw == build.app_index(manifest)                  # the same bytes every time (no gzip timestamp)
    index = json.loads(gzip.decompress(raw))
    assert index == {"format": 1, "built": manifest["built"], "regions": ["0101"],
                     "files": {"bundle/lines2_0101.parquet": ["bundle/0101/lines2_0101.abc.parquet", 10],
                               "library/m.zip": ["library/m.def.zip", 5, "b" * 64],
                               "library/c.json": ["library/c.123.json.gz", 7, "e" * 64]},
                     "gzip": {"library/c.json": 40}, "footers": {}, "datasets": {}, "bundle_sources": {}}


def test_dataset_regions():
    m = {"format": 2, "vpus": {"0101": {"vpu": "0101", "package": {"name": "x.zip"}, "bounds": [-70.4, 45.7, -66.5, 48.1],
                                        "id_range": [1, 2], "hydroseq_range": [3, 4], "grid": {"crs": 5070, "cell": 10.0},
                                        "vpuid": "0101", "lines": {"file": "lines2_0101.parquet", "bytes": 4, "sha256": "c"},
                                        "catchments": {"file": "cats2_0101.parquet"}, "arcs": {"file": "arcs2_0101.parquet"},
                                        "steps": {"file": "steps2_0101.bin"}}}}
    assert build.dataset_regions(m) == {"0101": {
        "bounds": [-70.4, 45.7, -66.5, 48.1], "grid": {"crs": 5070, "cell": 10.0}, "lines": "lines2_0101.parquet",
        "catchments": "cats2_0101.parquet", "arcs": "arcs2_0101.parquet", "steps": "steps2_0101.bin", "id_range": [1, 2],
        "hydroseq_range": [3, 4], "vpuid": "0101", "v2attrs": None}}
