"""Select final curves: the Compare section (this version against another) and the
``candidate=<key>`` deep link (campaign Round 1, WP-E)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

from streamcurves import candidates as C
from streamcurves import compare as cmpv
from streamcurves import library as lib
from views import final_selection as fs

VIEWS = Path(__file__).resolve().parents[1] / "views"
NS = __import__("shiny._namespaces", fromlist=["ResolvedId"]).ResolvedId("summary")
FID = "water-soil-quality"


def _html(tag) -> str:
    return str(tag).replace("&apos;", "'").replace("&quot;", '"')


def _cand(key: str, metric: str) -> dict:
    return {"candidateKey": key, "identity": {"sourceKind": "fitted", "subject": {"id": metric}},
            "label": metric, "basisDigest": "b", "buildStatus": "built",
            "eligibility": {"status": "eligible", "reasons": [], "checks": []},
            "tile": {"metric": metric, "display_name": metric, "units": None,
                     "strata": [{"label": None, "points": [(0.0, 0.0), (1.0, 1.0)]}],
                     "reference_range": (None, None), "domain": None, "badge": "fitted"}}


def _row(key: str, fid: str, status: str) -> dict:
    return {"candidateKey": key, "functionId": fid, "status": status, "unresolved": "", "needsReview": False,
            "decision": {"candidateKey": key, "functionId": fid, "decision": "selected", "rule": "SELECT-04",
                         "reason": "why", "decidedBy": C.AUTOMATED, "who": None, "when": None,
                         "basisDigest": None, "decisionRef": None}}


def _register() -> dict:
    cands = [_cand("k1", "chem_COND"), _cand("k2", "chem_PH"), _cand("k3", "phab_SINU")]
    return {"candidates": cands,
            "rows": [_row("k1", FID, C.SELECTED), _row("k2", FID, C.ELIGIBLE), _row("k3", "channel-floodplain-dynamics", C.SELECTED)],
            "functions": [
                {"functionId": FID, "functionName": "Water and soil quality", "discipline": "Physicochemistry",
                 "selected": [_row("k1", FID, C.SELECTED)], "alternatives": [_row("k2", FID, C.ELIGIBLE)],
                 "gap": None, "unassessed": False, "unresolved": 0, "waiting": [], "stale": []},
                {"functionId": "channel-floodplain-dynamics", "functionName": "Channel and floodplain dynamics",
                 "discipline": "Geomorphology", "selected": [_row("k3", "channel-floodplain-dynamics", C.SELECTED)],
                 "alternatives": [], "gap": None, "unassessed": False, "unresolved": 0, "waiting": [], "stale": []}]}


# --------------------------------------------------------------------------- #
# the deep link
# --------------------------------------------------------------------------- #
def test_the_link_names_a_candidate_and_the_row_is_found_by_its_functions():
    assert fs.parse_candidate_link("?candidate=sqt%3Amn%3Ax&other=1") == "sqt:mn:x"
    assert fs.parse_candidate_link("candidate=k2") == "k2"
    assert fs.parse_candidate_link("?other=1") is None and fs.parse_candidate_link("") is None
    assert fs.parse_candidate_link(None) is None and fs.parse_candidate_link("?candidate=") is None
    reg = _register()
    assert fs.candidate_functions(reg, "k2") == [FID]
    assert fs.candidate_functions(reg, "k3") == ["channel-floodplain-dynamics"]
    assert fs.candidate_functions(reg, "nope") == [] and fs.candidate_functions(reg, None) == []


def test_the_linked_row_is_marked_its_function_opened_and_every_id_stays_namespaced():
    reg = _register()
    html = _html(fs.functions_ui(reg, ns=NS, linked_key="k2"))
    assert 'id="summary-fs_cand_k2"' in html and "is-linked" in html
    # its function is open, the other closed (the open attribute, not the ontoggle's "open")
    open_details = [t for t in re.findall(r'<details[^>]*>', html) if ' open=""' in t]
    assert len(open_details) == 1 and 'summary-fs_water_soil_quality' in open_details[0]
    plain = _html(fs.functions_ui(reg, ns=NS))
    assert "is-linked" not in plain and 'id="summary-fs_cand_k2"' in plain
    ids = [i.split("summary-", 1)[-1].removesuffix("-label") for i in re.findall(r'id="(summary-[^"]+)"', html)]
    assert ids and all("-" not in i and " " not in i for i in ids)
    assert fs.linked_row_id("sqt:mn:x") == "fs_cand_sqt_mn_x"


def test_the_server_reads_the_page_url_and_lands_on_the_row():
    src = (VIEWS / "final_selection.py").read_text(encoding="utf-8")
    link = src[src.index("def _read_deep_link():"):src.index("# ── Compare: A (the session's origin) against B")]
    assert "session.clientdata.url_search()" in link and "parse_candidate_link(search)" in link
    assert "candidate_functions(register(), key)" in link
    assert 'state.nav_request.set("curves")' in link and "state.workspace_section_request.set(SECTION)" in link
    assert '"scrollToElement", {"id": ns(linked_row_id(key))}' in link
    assert "linked_key=highlight()" in src


# --------------------------------------------------------------------------- #
# the Compare section
# --------------------------------------------------------------------------- #
def test_version_a_is_the_sessions_origin_and_b_a_gallery_version_or_a_folder(tmp_path):
    assert fs.origin_version_dir(None) is None and fs.origin_version_dir({"kind": "scratch"}) is None
    assert fs.origin_version_dir({"kind": "staged", "staged_path": str(tmp_path)}) == tmp_path
    assert fs.origin_version_dir({"kind": "run", "run_dir": str(tmp_path)}) == tmp_path
    got = fs.origin_version_dir({"kind": "library", "library_id": "interior-plateau", "version": 6})
    assert got == lib.version_dir("interior-plateau", 6)
    assert fs.origin_label({"kind": "library", "library_id": "interior-plateau", "version": 6}) == "interior-plateau v6"
    assert fs.origin_label({"kind": "run", "run_dir": str(tmp_path / "l3-71")}) == "run l3-71"
    assert fs.origin_label(None) == "this session's origin"
    # the gallery's list, without the download-only versions
    entries = [SimpleNamespace(id="interior-plateau", name="Interior Plateau", versions=[
        SimpleNamespace(version=6, status_label="Preliminary", download_only=False),
        SimpleNamespace(version=5, status_label="Draft", download_only=True)])]
    assert fs.compare_choices(entries) == {"interior-plateau@v6": "Interior Plateau v6 (Preliminary)"}
    assert fs.compare_target("interior-plateau@v6", "") == lib.version_dir("interior-plateau", 6)
    assert fs.compare_target("", f'"{tmp_path}"') == tmp_path
    assert fs.compare_target("", "") is None and fs.compare_target("junk", "") is None


def test_the_chooser_and_the_report_render_with_namespaced_ids(tmp_path):
    html = _html(fs.compare_chooser_ui({"a@v1": "A v1 (Draft)"}, ns=NS, a_label="interior-plateau v6",
                                       selected="a@v1", path_value="x"))
    for i in ("summary-fs_cmp_b", "summary-fs_cmp_path", "summary-fs_cmp_run"):
        assert i in html
    assert 'value="a@v1" selected' in html and "interior-plateau v6" in html
    no_a = _html(fs.compare_chooser_ui({}, ns=NS, a_label="x", can_compare=False))
    assert "no version A" in no_a and "disabled" in no_a
    rep = _report(tmp_path)
    html = _html(fs.compare_report_ui(rep, ns=NS, a_label="A v1", b_label="B v2", export_id=NS("fs_cmp_csv")))
    for head in ("Curves", "Candidate register", "Rebuild ledger", "Reviewer decisions",
                 "Owner decisions in A, as B decided them", "Digests", "Export CSV"):
        assert head in html, head
    assert "spring-chem-ph" in html and "largest delta" in html
    assert "Differs" in html and "is-excluded" in html and "replays" in html
    assert "band 0.39" in html and "IQR)" in html
    assert chr(8212) not in html
    csv_text = fs.compare_csv(rep)
    assert csv_text.startswith("section,subject,field,a,b,note\n")


def _report(tmp_path) -> dict:
    pts_a = [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 1.0}]
    pts_b = [{"x": 0.0, "y": 0.0}, {"x": 12.0, "y": 1.0}]

    def metric(mid, pts):
        return {"metricId": mid, "curve": {"points": pts}, "referenceRange": [2.0, 6.0]}

    def write(folder, bundle, doc):
        folder.mkdir(parents=True)
        bundle = dict(bundle, contentDigest=lib.content_digest(bundle))
        (folder / cmpv.BUNDLE_FILE).write_text(json.dumps(bundle), encoding="utf-8")
        (folder / cmpv.PROVENANCE_FILE).write_text(json.dumps(doc), encoding="utf-8")
        return folder

    a = write(tmp_path / "a" / "v1",
              {"assessmentId": "t", "metricsByFunction": [{"functionId": FID, "metrics": [metric("spring-chem-ph", pts_a)]}]},
              {"records": [{"rule_id": "CURVE-07", "subject": "chem_PH", "reviewer_action": "accept", "reviewer": "GM",
                            "computed": {"curve_status": "degenerate", "reviewer_decision": "reviewer_finalized"}}]})
    b = write(tmp_path / "b" / "v2",
              {"assessmentId": "t", "metricsByFunction": [{"functionId": FID, "metrics": [metric("spring-chem-ph", pts_b)]}]},
              {"records": [{"rule_id": "CURVE-07", "subject": "chem_PH",
                            "computed": {"curve_status": "degenerate", "reviewer_decision": "pending"}}]})
    return cmpv.full_report(a, b, with_registers=True, with_ledger=True, with_digests=True, with_owner_decisions=True)


def test_the_section_sits_in_the_shell_and_compares_off_the_event_loop():
    src = (VIEWS / "final_selection.py").read_text(encoding="utf-8")
    assert 'ui.output_ui(ns("fs_versions"))' in src
    run = src[src.index("def _compare_versions():"):src.index("def fs_cmp_csv():")]
    assert "asyncio.to_thread" in run and "cmpv.full_report" in run
    assert "with_registers=True, with_ledger=True" in run and "with_owner_decisions=True" in run
    assert "origin_version_dir(origin)" in run and "compare_target(choice, path_text)" in run
    assert 'filename=lambda: "version-comparison.csv"' in src


@pytest.mark.skipif(not (lib.version_dir("interior-plateau", 6) / cmpv.BUNDLE_FILE).is_file(),
                    reason="interior-plateau v6 is not in this checkout")
def test_the_gallery_list_offers_the_versions_on_this_computer():
    from streamcurves import gallery
    choices = fs.compare_choices(gallery.entries_from_library())
    assert "interior-plateau@v6" in choices
    assert choices["interior-plateau@v6"].startswith("Interior Plateau") and " v6 (" in choices["interior-plateau@v6"]
