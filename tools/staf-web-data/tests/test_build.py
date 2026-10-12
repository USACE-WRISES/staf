"""A whole build on a small layout (webdata/build.py build): every step runs, and what it stores reads back."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webdata import build, deep_regions, points  # noqa: E402


def test_build(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    n = 3000
    rng = np.random.default_rng(4)
    pq.write_table(pa.table({"nhdplusid": np.arange(n, dtype=np.int64) + 10 ** 13, "hydroseq": rng.integers(1, 10 ** 13, n),
                             "dn_step": rng.integers(-5, 5, n).astype(np.int32), "up_step": rng.integers(-5, 5, n).astype(np.int32),
                             "totdasqkm": rng.uniform(0, 100, n), "xmin": rng.uniform(-80, -70, n)}),
                   bundle / "lines2_0101.parquet")
    (bundle / "manifest.json").write_text(json.dumps({"format": 2, "built": "x", "sources": {"nlcd": "2021"},
                                                      "vpus": {"0101": {"lines": "lines2_0101.parquet"}}}), encoding="utf-8")
    library = tmp_path / "library"
    library.mkdir()
    big = {"regions": [{"name": f"region {i}", "curves": list(range(50))} for i in range(40)]}
    (library / "library.json").write_text(json.dumps(big), encoding="utf-8")
    (library / "small.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build, "bundle_files", lambda b, r=None: [("manifest.json", bundle / "manifest.json", "core", None),
                                                                ("lines2_0101.parquet", bundle / "lines2_0101.parquet", "region", "0101")])
    monkeypatch.setattr(build, "xs_files", lambda x, r=None: ([], {}))
    monkeypatch.setattr(build, "engine_files", lambda: [])
    own = tmp_path / "own"
    own.mkdir()
    states = json.dumps({"type": "FeatureCollection", "features": [{"properties": {"state": "NH"}}] * 50}).encode("utf-8")
    (own / "us_states.geojson.gz").write_bytes(gzip.compress(states))
    monkeypatch.setattr(build, "OWN", own)
    monkeypatch.setattr(points, "write", lambda tables, cache, log=print: [])

    def regions(lib, dst):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_text(json.dumps({"features": [{"id": i} for i in range(200)]}), encoding="utf-8")
        return Path(dst)
    monkeypatch.setattr(deep_regions, "write", regions)
    out = tmp_path / "site"
    manifest = build.build(out, bundle=bundle, library=library, log=lambda *a: None)
    files = manifest["files"]
    # the lines file with a page index, its topology on its own; a parquet file's footer size recorded
    lines = files["bundle/lines2_0101.parquet"]
    assert pq.ParquetFile(out / lines["path"]).metadata.row_group(0).column(0).has_offset_index
    tail = (out / lines["path"]).read_bytes()[-8:]
    assert lines["footer"] == int.from_bytes(tail[:4], "little") + 8 and tail[4:] == b"PAR1"
    assert pq.read_table(out / files["topo/hr_0101.parquet"]["path"]).num_rows == n
    # JSON read whole: stored gzipped, keeping the content's size and sha256; a small one as it is
    lib = files["library/library.json"]
    raw = (library / "library.json").read_bytes()
    assert lib["path"].endswith(".json.gz") and lib["encoding"] == "gzip"
    assert gzip.decompress((out / lib["path"]).read_bytes()) == raw
    assert lib["raw_bytes"] == len(raw) and lib["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert lib["bytes"] == (out / lib["path"]).stat().st_size < len(raw)
    assert lib["sha256"] == hashlib.sha256((out / lib["path"]).read_bytes()).hexdigest()
    assert "encoding" not in files["library/small.json"] and files["library/small.json"]["path"].endswith(".json")
    assert files["library/deep-regions.json"]["encoding"] == "gzip"
    # the app's index: the stored paths, the unzipped sizes, the library's content sha256
    index = json.loads(gzip.decompress((out / build.INDEX).read_bytes()))
    assert index["files"]["library/library.json"] == [lib["path"], lib["bytes"], lib["raw_sha256"]]
    assert index["gzip"]["library/library.json"] == len(raw)
    assert "library/small.json" not in index["gzip"]
    assert index["bundle_sources"] == {"nlcd": "2021"}
    assert index["bundle"] == {"built": "x", "format": 2, "regions": 1}
    assert index["footers"]["bundle/lines2_0101.parquet"] == lines["footer"]
    assert "library/library.json" not in index["footers"]
    assert json.loads((out / build.MANIFEST).read_text(encoding="utf-8")) == manifest
    # the static app's own copies (DEEP's state polygons): unzipped content described, stored gzipped again
    st = files["engine/us_states.geojson"]
    assert st["encoding"] == "gzip" and st["raw_bytes"] == len(states)
    assert gzip.decompress((out / st["path"]).read_bytes()) == states
    assert manifest["sources"]["own"] == {"files": ["us_states.geojson"]}
    # a second build stores the same files
    again = build.build(out, bundle=bundle, library=library, log=lambda *a: None)
    assert dict((k, e["path"]) for k, e in again["files"].items()) == dict((k, e["path"]) for k, e in files.items())
