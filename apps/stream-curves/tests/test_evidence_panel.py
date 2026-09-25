"""The Evidence panel (views/evidence_panel.py): the packages table with status, Download and
View, the reference a DEEP session names, the stations behind a metric from a package's
pool ledger, and its two homes (the EASI page, the curve source panel)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from shiny import ui

from streamcurves import evidence_store as evs
from streamcurves.easi_method import evidence as ev
from views import evidence_panel as evp

VIEWS = Path(__file__).resolve().parents[1] / "views"
NS = __import__("shiny._namespaces", fromlist=["ResolvedId"]).ResolvedId("source_panel")
LEDGER = ("metric,station_key,level,in_pool,reason,value,source_cycle,l3,l2,l1,option,screen\n"
          "phab_XCMGW,NRS_B,l3,true,,1.5,2324,71,8.3,8,local,strict\n"
          "phab_XCMGW,NRS_A,l3,true,,2.5,1819,71,8.3,8,local,strict\n"
          "phab_XCMGW,NRS_C,l3,false,failed the screen,2.5,2324,71,8.3,8,local,strict\n"
          "chem_PTL,NRS_A,l1,true,,10,1819,71,8.3,8,l1_regional,strict\n")


def _html(tag) -> str:
    return str(tag).replace("&apos;", "'").replace("&quot;", '"')


def _package(folder: Path, *, with_ledger: bool = True) -> tuple[Path, dict]:
    (folder / "data").mkdir(parents=True)
    data = {"data/curves.json": b"{}\n"}
    if with_ledger:
        data["data/pool_ledger.csv"] = LEDGER.encode("utf-8")
    files = {}
    for rel, body in data.items():
        (folder / rel).write_bytes(body)
        files[rel] = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "mediaType": "text/plain"}
    doc = {"schema": evs.SCHEMA, "schemaVersion": evs.SCHEMA_VERSION, "packageId": "deep-dev-l3-71",
           "version": "interior-plateau-v6", "dataDigest": evs.data_digest(files), "files": files,
           "title": "DEEP development evidence, Level III ecoregion 71 (Interior Plateau)",
           "roles": ["development"], "reproducibility": "refittable" if with_ledger else "reviewable",
           "coverage": {"region": {"kind": "ecoregion", "code": "71", "name": "Interior Plateau"},
                        "stations": 3, "curves": 2, "functionsScored": 2},
           "dependsOn": [{"packageId": "nrsa-archive", "version": "multi-cycle-v1", "dataDigest": "sha256:" + "a" * 64}]}
    (folder / evs.MANIFEST).write_text(json.dumps(doc), encoding="utf-8")
    return folder, evs.reference(folder)


@pytest.fixture
def package(tmp_path):
    return _package(tmp_path / "src")


@pytest.fixture
def store(tmp_path, package):
    folder, ref = package
    root = tmp_path / "store"
    evs.install_folder(folder, root=root)
    return root


# --------------------------------------------------------------------------- #
# the reference a DEEP session names
# --------------------------------------------------------------------------- #
def test_the_reference_is_read_from_the_build_the_provenance_or_the_version_folder(tmp_path, package):
    folder, ref = package
    assert evp.evidence_reference_for({"evidence": ref}) == ref
    assert evp.evidence_reference_for({"evidence": [ref]}) == ref
    assert evp.evidence_reference_for(None, {"evidenceReferences": [ref]}) == ref
    vdir = tmp_path / "staged" / "v3"
    vdir.mkdir(parents=True)
    (vdir / "evidence.json").write_text(json.dumps(ref), encoding="utf-8")
    assert evp.evidence_reference_for(None, None, {"kind": "staged", "staged_path": str(vdir)}) == ref
    assert evp.evidence_reference_for(None, None, {"kind": "run", "run_dir": str(vdir)}) == ref
    assert evp.evidence_reference_for(None, None, {"kind": "library", "library_id": "x", "version": "bad"}) is None
    assert evp.evidence_reference_for({"evidence": {"no": "id"}}) is None and evp.evidence_reference_for(None) is None
    # the build's own reference wins, and the same package is listed once
    other = dict(ref, packageDigest="sha256:" + "f" * 64)
    refs = evp.evidence_references({"evidence": ref}, {"evidenceReferences": [ref, other]})
    assert refs == [ref, other]
    # an EASI package is not a DEEP version's evidence
    easi = dict(ref, packageId="easi-dev-members")
    assert evp.evidence_reference_for({"evidence": easi}) is None and evp.kind_of(easi) == "easi"
    assert evp.kind_of(ref) == "deep"


# --------------------------------------------------------------------------- #
# the stations behind a metric
# --------------------------------------------------------------------------- #
def test_the_stations_behind_a_metric_come_from_the_pool_ledger(package, tmp_path):
    folder, _ref = package
    got = evp.stations_for_metric(folder, "phab_XCMGW")
    assert got["available"] and got["total"] == 2 and got["judged"] == 3
    assert [r["station_key"] for r in got["rows"]] == ["NRS_A", "NRS_B"]
    assert got["rows"][0]["option"] == "local" and got["rows"][0]["value"] == "2.5"
    assert evp.stations_for_metric(folder, "chem_PTL", limit=1)["rows"][0]["level"] == "l1"
    assert evp.stations_for_metric(folder, "nothing") == {"rows": [], "total": 0, "judged": 0, "available": True}
    bare, _ = _package(tmp_path / "bare", with_ledger=False)
    assert evp.stations_for_metric(bare, "phab_XCMGW")["available"] is False
    html = _html(evp.stations_table(got, "phab_XCMGW"))
    assert "2 stations in the pool behind phab_XCMGW, of 3 judged" in html and "NRS_A" in html
    assert "no station-level pool ledger" in _html(evp.stations_table({"available": False}, "x"))
    assert "holds no station in the pool" in _html(evp.stations_table({"available": True, "rows": []}, "x"))


# --------------------------------------------------------------------------- #
# the packages table: status, Download, View
# --------------------------------------------------------------------------- #
def test_the_table_offers_download_for_a_missing_package_and_view_for_an_installed_one(package, store, monkeypatch):
    folder, ref = package
    monkeypatch.delenv(evs.ENV_BASE_URL, raising=False)
    missing = _html(evp.packages_table([ref], [], prefix="ev", ns=NS, kind="deep"))
    assert "Not on this computer" in missing and "Download" in missing and "source_panel-ev_download" in missing
    assert "Interior Plateau" in missing and "3 stations, 2 curves, 2 functions scored" in missing
    installed = evs.installed(store)
    assert ev.status(ref, installed) == "installed"
    have = _html(evp.packages_table([ref], installed, prefix="ev", ns=NS, kind="deep"))
    assert "Verified" in have and ">View<" in have and "source_panel-ev_view" in have and "Download" not in have
    # a caller's own control rides in the last cell
    extra = _html(evp.packages_table([ref], installed, prefix="pkg", ns=NS, kind="easi",
                                     extra=lambda i, r: ui.tags.button("Remove", type="button")))
    assert "Remove" in extra and "source_panel-pkg_view" in extra
    assert "No development data packages are named." in _html(evp.packages_table([], [], prefix="ev", ns=NS))
    # a host that cannot be named: no Download, import instead
    assert evp.fetchable("sqt") is False
    none = _html(evp.packages_table([dict(ref, packageId="sqt-x")], [], prefix="ev", ns=NS, kind="sqt"))
    assert "Import its package file." in none and "Download" not in none


def test_the_evidence_section_lists_the_stations_once_the_package_is_here(package, store, monkeypatch):
    folder, ref = package
    monkeypatch.delenv(evs.ENV_BASE_URL, raising=False)
    none = _html(evp.evidence_section(None, [], prefix="ev", ns=NS, metric="phab_XCMGW"))
    assert "records no evidence package" in none
    before = _html(evp.evidence_section(ref, [], prefix="ev", ns=NS, metric="phab_XCMGW"))
    assert "Download the package to list the stations" in before and "Download" in before
    installed = evs.installed(store)
    here = next(Path(r["path"]) for r in installed if r["verified"])
    after = _html(evp.evidence_section(ref, installed, prefix="ev", ns=NS, metric="phab_XCMGW", folder=here,
                                       busy=None))
    assert "Stations behind phab_XCMGW" in after and "NRS_A" in after and "NRS_C" not in after
    busy = _html(evp.evidence_section(ref, installed, prefix="ev", ns=NS, metric="phab_XCMGW",
                                      busy=" Downloading x..."))
    assert "Downloading x" in busy
    for text in (none, before, after):
        assert chr(8212) not in text


def test_the_viewer_reads_a_package_and_previews_its_tables(package):
    folder, ref = package
    doc = evs.read_manifest(folder)
    html = _html(evp.viewer_modal(doc, prefix="ev", ns=NS, tables=["data/pool_ledger.csv"]))
    assert "pool_ledger.csv" in html and "source_panel-ev_table" in html and "source_panel-ev_csv" in html
    assert "Export as CSV" in html and "nrsa-archive" in html and "Interior Plateau" in html
    preview = _html(evp.preview_ui(folder / "data" / "pool_ledger.csv"))
    assert "First 4 rows" in preview and "station_key" in preview
    assert b"".join(evp.csv_chunks(folder / "data" / "pool_ledger.csv")) == LEDGER.encode("utf-8")
    assert evp.covers({"memberRows": 12, "reaches": 3}) == "12 member rows, 3 reaches"
    assert evp.check_lines({"ledgerSelectedEqualsBundle": True,
                            "refittedRowsWithStationEvidence": {"n": 3, "of": 4}}) == [
        "The rebuild ledger's selected curves are exactly the bundle's.",
        "3 of 4 refitted curves name the stations behind them."]
    assert evp.size_text(2_500_000) == "2.5 MB" and evp.size_text(900) == "1 KB"


# --------------------------------------------------------------------------- #
# its two homes
# --------------------------------------------------------------------------- #
def test_the_easi_page_and_the_source_panel_share_the_panel():
    easi = (VIEWS / "easi_page.py").read_text(encoding="utf-8")
    assert "evp.evidence_panel_server(input, output, session, state, prefix=\"pkg\"" in easi
    assert "handlers=()" in easi and "evp.packages_table(" in easi
    # the page keeps its four handlers under their ids, each a thin call into the panel
    assert "_launch(_packages.download(ref))" in easi
    assert 'await _packages.view(ref, preview_output=ui.output_ui(ns("pkg_preview")))' in easi
    assert "return evp.preview_ui(path)" in easi and "yield from evp.csv_chunks(path)" in easi
    assert "def _run_install(path: Path):" in easi and "evs.fetch_reference" not in easi
    assert "covers = evp.covers" in easi and "size_text = evp.size_text" in easi
    panel = (VIEWS / "source_panel.py").read_text(encoding="utf-8")
    assert 'evp.evidence_panel_server(\n        input, output, session, state, prefix="ev", kind="deep"' in panel
    assert "evp.evidence_reference_for(build, provenance, origin)" in panel
    assert 'evidence=ui.output_ui(ns("ev_section"))' in panel and "def ev_section" in panel
    assert "@output(suspend_when_hidden=False)" in panel
    body = panel[panel.index("def panel_body("):panel.index("def build_has_curve(")]
    assert '_section("Evidence", evidence)' in body and "evidence=None" in body
    modal = panel[panel.index("def panel_modal("):panel.index("def entry_for(")]
    assert "evidence=evidence" in modal
