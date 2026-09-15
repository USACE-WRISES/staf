"""Stored evidence normalization belongs to the national method digest."""
from __future__ import annotations

from pathlib import Path

import pytest

from easi import national


@pytest.mark.parametrize("relative", ["datasources/fabric.py", "national/records.py"])
def test_method_version_hashes_evidence_normalization_sources(tmp_path, monkeypatch, relative):
    assert relative in national._METHOD_SOURCES
    source = Path(national.__file__).resolve().parent.parent
    package = tmp_path / "easi"
    hashed = [source / name for name in national._METHOD_SOURCES]
    hashed += sorted((source / "metrics").glob("*.py"))
    for path in hashed:
        target = package / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    monkeypatch.setattr(national, "__file__", str(package / "national" / "__init__.py"))
    national.method_version.cache_clear()
    try:
        before = national.method_version()
        path = package / relative
        path.write_bytes(path.read_bytes() + b"\n# isolated normalization revision\n")
        national.method_version.cache_clear()
        assert national.method_version() != before
    finally:
        national.method_version.cache_clear()
