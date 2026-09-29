"""The campaign index inside the Build step (views/region_builder.py): the table over
``region_build.campaign_rows`` with its filters, the promote column read from the packet's
own eligibility, and the two loose ends WP-C left (the old "build" page is gone, the start
page's link lands on stage 3's Build substep)."""
from __future__ import annotations

import json
from pathlib import Path

from streamcurves import curve_basis
from streamcurves import run_state as rs
from views import region_builder as view

APP = Path(__file__).resolve().parents[1]
NS = __import__("shiny._namespaces", fromlist=["ResolvedId"]).ResolvedId("region_builder")


def _html(tag) -> str:
    return str(tag).replace("&apos;", "'").replace("&quot;", '"')


def _packet(code: str, name: str, *, curves, functions, sources=(), carried=(), fixed=(), promotion=None,
            open_items=0, staged=True, path=None):
    return {"region": {"code": code, "name": name},
            "curves": [{"metric": m, "function": f} for m, f in curves],
            "portfolio": [{"function": fname, "function_id": fid, "compact_metrics": mets}
                          for fid, fname, mets in functions],
            "coverage": {"coveredFunctionIds": [fid for fid, _, _ in functions]},
            "reference": {"hierarchy": {"sources": [{"metric": m, "basis": b} for m, b in sources],
                                        "carried": list(carried)},
                          "fixed": [{"metric": m} for m in fixed]},
            "decisions_applied": [{"rule_id": "CURVE-06"}],
            "open_items": [{"item_id": f"X:{i}"} for i in range(open_items)], "hard_stops": [],
            "staged": {"version": 2, "path": str(path)} if staged else None,
            **({"promotion": promotion} if promotion is not None else {})}


def _region(root: Path, code: str, packet: dict) -> Path:
    d = root / f"l3-{code}"
    d.mkdir(parents=True)
    (d / "review_packet.json").write_text(json.dumps(packet), encoding="utf-8")
    return d


def _runs(tmp_path) -> Path:
    root = tmp_path / "runs"
    staged = tmp_path / "staged" / "v2"
    staged.mkdir(parents=True)
    _region(root, "71", _packet(
        "71", "Interior Plateau", curves=[("phab_SINU", "Geomorphology: Channel and floodplain dynamics")],
        functions=[("channel-floodplain-dynamics", "Channel and floodplain dynamics", ["phab_SINU"]),
                   ("water-soil-quality", "Water and soil quality", ["chem_PTL"])],
        sources=[("chem_PTL", curve_basis.PUBLISHED)], carried=["chem_COND"], fixed=["pctimp2019ws"],
        promotion={"eligible": False, "blockers": ["register incomplete", "2 open items"]}, open_items=2,
        path=staged))
    _region(root, "58", _packet(
        "58", "Northeastern Highlands", curves=[("bent_EPT_NTAX", "Biology: Community dynamics")],
        functions=[("community-dynamics", "Community dynamics", ["bent_EPT_NTAX"])],
        promotion={"eligible": True, "blockers": []}, path=staged))
    (root / "l3-58" / "evidence.json").write_text(json.dumps({"packageId": "deep-dev-l3-58", "packageDigest": "sha256:x"}),
                                                  encoding="utf-8")
    _region(root, "9", _packet("9", "Thin Region", curves=[], functions=[], staged=False, open_items=1))
    return root


# --------------------------------------------------------------------------- #
# the rows and the filters
# --------------------------------------------------------------------------- #
def test_the_index_reads_what_each_packet_adds(tmp_path):
    rows = {r["region"]: r for r in view.index_rows(_runs(tmp_path))}
    assert list(rows) == ["9", "58", "71"]
    ip = rows["71"]
    assert ip["functions"] == {"channel-floodplain-dynamics": "Channel and floodplain dynamics",
                               "water-soil-quality": "Water and soil quality"}
    assert ip["metrics"] == ["chem_COND", "chem_PTL", "pctimp2019ws", "phab_SINU"]
    assert ip["source_types"] == ["carried", "fitted", "fixed", curve_basis.PUBLISHED]
    assert ip["promotion"] == {"eligible": False, "blockers": ["register incomplete", "2 open items"]}
    assert view.promote_words(ip) == "not yet: register incomplete; 2 open items"
    assert view.promote_words(rows["58"]) == "eligible" and rows["58"]["evidence"] == "ready"
    # a packet without the block falls back to what the index derives
    thin = rows["9"]
    assert thin["promotion"] is None and view.promote_words(thin) == "not yet" and thin["source_types"] == []
    assert view.promote_words({"promote_eligible": True}) == "eligible"
    assert view.promote_words({"promotion": {"eligible": False, "blockers": []}}) == "not yet"
    assert view.packet_index_detail(None) == {"functions": {}, "metrics": [], "source_types": [], "promotion": None}
    assert view.index_rows(tmp_path / "missing") == []


def test_every_filter_narrows_the_rows(tmp_path):
    rows = view.index_rows(_runs(tmp_path))
    codes = lambda kept: [r["region"] for r in kept]  # noqa: E731
    assert codes(view.filter_index_rows(rows)) == ["9", "58", "71"]
    assert codes(view.filter_index_rows(rows, region="71")) == ["71"]
    assert codes(view.filter_index_rows(rows, region="highlands")) == ["58"]
    assert codes(view.filter_index_rows(rows, function="water-soil-quality")) == ["71"]
    assert codes(view.filter_index_rows(rows, metric="bent_EPT_NTAX")) == ["58"]
    assert codes(view.filter_index_rows(rows, source_type=curve_basis.PUBLISHED)) == ["71"]
    assert codes(view.filter_index_rows(rows, source_type="fitted")) == ["58", "71"]
    assert codes(view.filter_index_rows(rows, evidence="ready")) == ["58"]
    assert codes(view.filter_index_rows(rows, evidence="missing")) == ["9", "71"]
    assert codes(view.filter_index_rows(rows, open_items="none")) == ["58"]
    assert codes(view.filter_index_rows(rows, open_items="some")) == ["9", "71"]
    assert codes(view.filter_index_rows(rows, function="community-dynamics", open_items="some")) == []
    choices = view.index_choices(rows)
    assert list(choices["region"]) == ["9", "58", "71"] and choices["region"]["71"] == "71  Interior Plateau"
    assert choices["function"]["community-dynamics"] == "Community dynamics"
    assert choices["source_type"]["fitted"] == "Built here" and choices["source_type"]["carried"] == "Carried forward"
    assert choices["source_type"][curve_basis.PUBLISHED] == curve_basis.label_for(curve_basis.PUBLISHED)
    assert "bent_EPT_NTAX" in choices["metric"]


def test_the_card_renders_the_six_filters_and_the_open_action(tmp_path):
    rows = view.index_rows(_runs(tmp_path))
    filters = _html(view.campaign_filters_ui(view.index_choices(rows), ns=NS, values={"ci_open": "some"}))
    for name, label in view.INDEX_FILTERS:
        assert f'id="region_builder-{name}"' in filters and label in filters
    assert 'value="some" selected' in filters
    table = _html(view.campaign_table_ui(rows, ns=NS, total=len(rows)))
    assert "3 of 3 regions" in table and table.count(">Open<") == 3
    assert "region_builder-open_region" in table and "not yet: register incomplete; 2 open items" in table
    assert "eligible" in table and "Interior Plateau" in table
    kept = view.filter_index_rows(rows, region="nowhere")
    assert "No region matches these filters (3 in this folder)." in _html(view.campaign_table_ui(kept, ns=NS, total=3))
    for text in (filters, table):
        assert chr(8212) not in text
    src = (APP / "views" / "region_builder.py").read_text(encoding="utf-8")
    body = src[src.index("def campaign_index():"):src.index("def campaign_table():")]
    assert "campaign_rows(out_root())" in body and 'ui.output_ui(ns("campaign_table"))' in body
    table_body = src[src.index("def campaign_table():"):]
    assert "filter_index_rows(" in table_body and "_open_staged_now(land=True)" in src


# --------------------------------------------------------------------------- #
# the two loose ends
# --------------------------------------------------------------------------- #
def test_the_old_build_page_is_gone_and_the_start_page_lands_on_the_build_substep():
    app = (APP / "app.py").read_text(encoding="utf-8")
    assert 'region_builder_server("build"' not in app and '"Region Builder"' not in app
    assert "from views.region_builder import" not in app
    assert "build" not in rs.TOOL_KEYS
    project = (APP / "views" / "project.py").read_text(encoding="utf-8")
    start = project[project.index("def _start_runs():"):project.index("def _start_easi_import():")]
    assert '_open_tool("build")' not in start
    assert '_request_nav("data", wizard_step=rs.ECOREGION_BUILD_SUBSTEPS[0][0])' in start
    assert rs.ECOREGION_BUILD_SUBSTEPS[0] == (4, "Build")
    # the builder is mounted once, inside the stage-3 Build step
    import_map = (APP / "views" / "import_map.py").read_text(encoding="utf-8")
    assert 'region_builder_server(\n        "region_builder", state' in import_map


def test_a_download_only_entry_offline_says_a_download_is_needed():
    from views import project
    assert project.DOWNLOAD_NEEDED_OFFLINE.startswith("Download needed: this version is not on this computer yet")
    src = (APP / "views" / "project.py").read_text(encoding="utf-8")
    opener = src[src.index("async def _gallery_open("):src.index("# ── New project")]
    assert "DOWNLOAD_NEEDED_OFFLINE if v.download_only else" in opener
    detail = src[src.index("def gal_detail():"):src.index("def _gal_version():")]
    assert "if v.download_only and not v.assets.get(\"pack\")" in detail
    assert chr(8212) not in project.DOWNLOAD_NEEDED_OFFLINE
