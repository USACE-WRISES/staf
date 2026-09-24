"""Evidence packages: identity from the data, a deterministic archive, verification."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pyarrow as pa

from builder import evidence_export as ee


def _package(tmp_path, *, producer_time="2026-09-23T00:00:00Z"):
    pkg = ee.Package(tmp_path, "test-pkg")
    pkg.parquet(pa.table({"comid": [1, 2, 3], "v": [0.5, 1.5, None]}), "values.parquet")
    pkg.json({"a": 1}, "notes.json")
    return pkg.finish(title="t", roles=["development"], reproducibility="refittable",
                      producer={"created": producer_time})


def test_the_identity_is_the_data_and_the_description_not_who_exported_it(tmp_path):
    first = _package(tmp_path / "a")
    again = _package(tmp_path / "b", producer_time="2026-09-24T12:00:00Z")
    assert first["dataDigest"] == again["dataDigest"]
    assert first["packageDigest"] == again["packageDigest"]
    assert first["zip"] == again["zip"]
    got = ee.verify(tmp_path / "a" / "test-pkg")
    assert got["damaged"] == [] and got["dataDigestOk"] and got["packageDigest"] == first["packageDigest"]


def test_the_archive_is_deterministic_and_a_damaged_file_is_found(tmp_path):
    first = _package(tmp_path / "a")
    blob = (tmp_path / "a" / first["zip"]).read_bytes()
    second = _package(tmp_path / "a")
    assert (tmp_path / "a" / second["zip"]).read_bytes() == blob
    doc = json.loads((tmp_path / "a" / "test-pkg" / "evidence.json").read_text(encoding="utf-8"))
    assert doc["files"]["data/values.parquet"]["rows"] == 3
    (tmp_path / "a" / "test-pkg" / "data" / "notes.json").write_text("{}", encoding="utf-8")
    assert ee.verify(tmp_path / "a" / "test-pkg")["damaged"] == ["data/notes.json"]


def test_two_exports_at_other_times_are_the_same_bytes(tmp_path):
    first = _package(tmp_path / "a")
    again = _package(tmp_path / "b", producer_time="2026-09-24T12:00:00Z")
    assert (tmp_path / "a" / first["zip"]).read_bytes() == (tmp_path / "b" / again["zip"]).read_bytes()
    assert first["exportedAt"] != again["exportedAt"]
    doc = json.loads((tmp_path / "a" / "test-pkg" / "evidence.json").read_text(encoding="utf-8"))
    assert "producer" not in doc or "created" not in doc["producer"]


def test_a_dependency_must_resolve_in_the_export(tmp_path):
    pkg = ee.Package(tmp_path, "needs-other")
    pkg.json({"a": 1}, "notes.json")
    pkg.finish(title="t", roles=["development"], reproducibility="reviewable",
               dependsOn=[{"packageId": "test-pkg", "dataDigest": "sha256:" + "0" * 64}])
    assert ee.check_dependencies(tmp_path) == ["needs-other needs test-pkg sha256:000000000000"]
    real = _package(tmp_path)
    pkg = ee.Package(tmp_path, "needs-other")
    pkg.json({"a": 1}, "notes.json")
    pkg.finish(title="t", roles=["development"], reproducibility="reviewable",
               dependsOn=[{"packageId": "test-pkg", "dataDigest": real["dataDigest"]}])
    assert ee.check_dependencies(tmp_path) == []


# --------------------------------------------------------------------------- #
# review B, round 2: the exporter's identity (N6), the itemized gaps (M7), the recipe (N9)
# --------------------------------------------------------------------------- #
def _finish(folder, producer):
    pkg = ee.Package(folder, "test-pkg")
    pkg.json({"a": 1}, "notes.json")
    return pkg.finish(title="t", roles=["development"], reproducibility="reviewable", producer=producer)


def test_the_index_says_which_exporter_ran_and_keeps_a_clean_trees_false(tmp_path):
    clean = _finish(tmp_path / "a", {"created": "2026-09-24T00:00:00Z", "commit": "abc", "tool": "x",
                                     "exporterSha256": "f" * 64, "exporterDirty": False})
    assert clean["exporterDirty"] is False and clean["exporterSha256"] == "f" * 64
    assert clean["exporterCommit"] == "abc"
    doc = json.loads((tmp_path / "a" / "test-pkg" / "evidence.json").read_text(encoding="utf-8"))
    assert doc["producer"] == {"tool": "x"}                     # the rest went to the index
    dirty = _finish(tmp_path / "b", {"created": "2026-09-25T00:00:00Z", "commit": "def", "tool": "x",
                                     "exporterSha256": "e" * 64, "exporterDirty": True})
    assert dirty["exporterDirty"] is True and dirty["packageDigest"] == clean["packageDigest"]
    unknown = _finish(tmp_path / "c", {"tool": "x", "exporterDirty": None})
    assert "exporterDirty" not in unknown                        # git could not say: not claimed
    ident = ee.exporter_identity()
    assert ident["exporterSha256"] == ee.sha_file(Path(ee.__file__))
    assert ident["exporterDirty"] in (True, False, None)


def test_both_value_packages_itemize_what_the_snapshot_cannot_show():
    gaps = ee.values_gaps({"er_median": {"source": "values.er_median"},
                           "woody_wsrp100": {"source": "landscape.woody_wsrp100"}}, "the reach values")
    assert "er_median" in gaps[0]["item"] and "woody" not in gaps[0]["item"]
    assert "values.parquet" in gaps[0]["why"] and "receipts" in gaps[0]["why"]
    assert gaps[1]["item"] == "the vintages of the StreamCat, NLCD and EROM records behind the reach values"
    only = ee.values_gaps({"woody_wsrp100": {"source": "landscape.woody_wsrp100"}}, "the member values")
    assert [g["item"] for g in only] == [
        "the vintages of the StreamCat, NLCD and EROM records behind the member values"]
    for export in (ee.export_members, ee.export_universe_values):
        assert "values_gaps(dictionary" in inspect.getsource(export)


def test_no_timing_rides_in_a_manifest():
    """A check's duration in the manifest gave every export of the universe another package
    digest (18.7 s, then 17.2 s): the exports record results only, and the index records when."""
    for export in (ee.export_universe, ee.export_members, ee.export_fits, ee.export_universe_values,
                   ee.export_eval_refs, ee.export_operational):
        src = inspect.getsource(export)
        assert '"seconds"' not in src and "perf_counter() - t0, 1)}" not in src, export.__name__


def test_the_recorded_recipe_code_is_what_the_refit_computes_and_stays_out_of_data():
    from streamcurves.easi_method import refit
    got = ee.recipe_code()
    assert got == refit.recipe_code() and set(got) == {"fit_recipe.py", "refit.py"}
    raw = (ee.STREAM_CURVES_APP / "streamcurves" / "easi_method" / "refit.py").read_bytes()
    assert got["refit.py"] == hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    # the fits package also writes engine_block() into data/recipe.json: the code hashes must
    # never ride there, or they would move that package's data digest
    assert "code" not in ee.engine_block()
    for export in (ee.export_members, ee.export_fits):
        assert '"code": recipe_code()' in inspect.getsource(export)
