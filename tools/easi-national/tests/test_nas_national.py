"""NAS national cache: the paged pull with resume, and the chunk stage
answering HUC12s from the cache without a request."""
from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq

from builder import state
from builder.paths import DataRoot
from builder.stages import nas as nas_stage
from builder.stages import nas_national as nn
from builder.units import Chunk


def _record(name, huc12, huc8="02080204", year=2015):
    return {"key": f"{name}-{huc12}-{year}", "speciesID": "1", "scientificName": name, "commonName": "x",
            "group": "Fishes", "state": "Virginia", "huc8": huc8, "huc10": huc12[:10], "huc12": huc12,
            "year": year, "status": "established"}


def test_national_pull_pages_until_the_end_and_resumes(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    pages = {0: {"results": [_record("Cyprinus carpio", "020802040101"), _record("Corbicula fluminea", "020802040101"),
                             _record("Cyprinus carpio", "")], "endOfRecords": False},
             3: {"results": [_record("Micropterus salmoides", "020802040102")], "endOfRecords": True}}
    calls = []

    def fetch(offset, limit):
        calls.append(offset)
        return pages[offset]

    path = nn.run_nas_national(root, progress, control, fetch=fetch, page=3)
    assert calls == [0, 3]
    table = pq.read_table(path)
    assert table.num_rows == 4 and table.column("year").type == pa.int32()
    assert not (root.national / "nas_parts").exists() and len(state.Ledger(root, "nas-national")) == 0

    # resume: the first page already in the ledger is not fetched again
    calls.clear()
    ledger = state.Ledger(root, "nas-national")
    parts = root.national / "nas_parts"
    parts.mkdir()
    nn.common.write_part(parts, "o000000000", {"records": [nn._slim(r) for r in pages[0]["results"]], "end": False})
    ledger.add("o000000000")
    nn.run_nas_national(root, progress, control, fetch=fetch, page=3)
    assert calls == [3]
    taxa = nn.cached_taxa(root)
    assert taxa["020802040101"] == {"taxa": ["Corbicula fluminea", "Cyprinus carpio"], "n": 2}
    assert "" not in taxa


def test_chunk_stage_answers_huc12s_from_the_cache_without_requests(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    rows = [_record("Cyprinus carpio", "020802040101"), _record("Corbicula fluminea", "020802040101"),
            _record("Micropterus salmoides", "020802040102")]
    nn.run_nas_national(root, progress, control, fetch=lambda o, l: {"results": rows, "endOfRecords": True})
    chunk = Chunk(id="huc8-test", kind="huc8", label="t", huc8s=["02080204"])
    chunk.save(root)
    nn.common.write_parquet(pa.table({"huc_12": ["020802040101", "020802040103"]}),
                            root.chunk_raw("huc8-test", "huc12"))

    def never(huc12):
        raise AssertionError("the API must not be asked when the cache exists")

    nas_stage.run_nas(root, chunk, state.UnitStates(root), progress, control, fetch=never)
    out = {r["huc12"]: r for r in pq.read_table(root.chunk_raw("huc8-test", "nas")).to_pylist()}
    assert json.loads(out["020802040101"]["taxa"]) == ["Corbicula fluminea", "Cyprinus carpio"]
    assert out["020802040101"]["n_records"] == 2 and out["020802040103"]["taxa"] == "[]"
    assert out["020802040103"]["n_records"] == 0 and not out["020802040103"]["truncated"]
