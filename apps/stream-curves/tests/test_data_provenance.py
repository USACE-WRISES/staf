"""The NRSA data bundle carries its provenance record (data/nrsa_provenance.json).

The record names the dataset, when and where it was pulled, and the sha256 of
each bundled file. If a data file changes, this test fails until the provenance
record is regenerated deliberately, so the data can never drift silently away
from its recorded source.
"""

import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
EXPECTED_FILES = {"nrsa_metrics.parquet", "nrsa_sites.csv", "nrsa_metric_catalog.csv"}


def _load():
    return json.loads((DATA / "nrsa_provenance.json").read_text(encoding="utf-8"))


def test_provenance_names_the_dataset():
    doc = _load()
    assert doc["dataset"] == (
        "EPA National Rivers and Streams Assessment 2018-19 (NRSA 2018-19)"
    )
    assert "December 2025" in doc["source_statement"]


def test_provenance_covers_exactly_the_bundled_nrsa_files():
    doc = _load()
    assert {f["file"] for f in doc["files"]} == EXPECTED_FILES


def test_provenance_digests_match_the_data_files():
    doc = _load()
    for rec in doc["files"]:
        p = DATA / rec["file"]
        assert p.is_file(), rec["file"]
        digest = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        assert digest == rec["sha256"], (
            f"{rec['file']} changed without regenerating nrsa_provenance.json"
        )
        assert p.stat().st_size == rec["bytes"]
