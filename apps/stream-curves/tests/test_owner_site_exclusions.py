"""Owner site exclusions on a batch stage (2026-09-02).

A site the STAF site engine cannot value (the 8,096 km2 NRS18_NH_10004 basin
came back without land cover) is the owner's decision to drop, never a silent
NaN. ``--exclude-site SITE_ID=REASON`` marks the screening row, leaves the
retained pool, and rides the manifest, the digest, and the packet.
"""
from __future__ import annotations

from pathlib import Path

from streamcurves import provenance as pv
from streamcurves import regional_agent as ra
from streamcurves import review_packet as rp

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _screening():
    rows = [{"site_id": s, "state": "succeeded", "final_decision": "retained"} for s in "ABC"]
    rows.append({"site_id": "D", "state": "succeeded", "final_decision": "excluded",
                 "reason": "screened out"})
    tables = {"easi_screening_sites": rows}
    return {"tables": tables, "sites": rows, "retained_ids": ["A", "B", "C"],
            "counts": {"n_screened": 4, "n_retained": 3, "n_excluded": 1, "n_unresolved": 0}}


def test_owner_exclusion_marks_the_row_and_recomputes_the_pool():
    screening = _screening()
    retained = {"A", "B", "C"}
    recs = ra.apply_owner_site_exclusions(screening, retained,
                                          {"B": "engine cannot value it", "Z": "not here"})
    assert retained == {"A", "C"}
    assert screening["retained_ids"] == ["A", "C"]
    assert screening["counts"]["n_retained"] == 2 and screening["counts"]["n_excluded"] == 2
    row = next(r for r in screening["sites"] if r["site_id"] == "B")
    assert row["final_decision"] == "excluded" and row["reviewer"] == "owner"
    assert row["reason"] == "engine cannot value it"
    assert recs == [
        {"site_id": "B", "reason": "engine cannot value it", "source": "owner", "was_retained": True},
        {"site_id": "Z", "reason": "not here", "source": "owner", "was_retained": False}]
    assert ra.apply_owner_site_exclusions(screening, retained, None) == []


def test_run_evidence_records_an_owner_exclusion_offline():
    cands, _ = ra.select_candidates_detailed("55", dataset="legacy-1819")
    victim = str(cands["site_id"].iloc[0])
    plain = ra.run_evidence("55", "Eastern Corn Belt Plains", do_screen=False,
                            use_streamcat=False, diagnostics_enabled=False,
                            nrsa_dataset_id="legacy-1819")
    ev = ra.run_evidence("55", "Eastern Corn Belt Plains", do_screen=False,
                         use_streamcat=False, diagnostics_enabled=False,
                         nrsa_dataset_id="legacy-1819",
                         exclude_sites={victim: "engine cannot value this basin"})
    assert ev["n_retained"] == plain["n_retained"] - 1
    assert victim not in ev["retained_ids"]
    assert ev["screening_counts"]["n_retained"] == ev["n_retained"]
    assert ev["screening_counts"]["n_owner_excluded"] == 1
    assert ev["owner_site_exclusions"] == [{"site_id": victim, "reason": "engine cannot value this basin",
                                            "source": "owner", "was_retained": True}]
    result = ra.assemble(ev)
    assert result["owner_site_exclusions"] == ev["owner_site_exclusions"]
    assert victim not in result["retained_site_ids"]
    manifest = pv.build_run_manifest(result, argv=[])
    assert manifest["inputs"]["easi"]["owner_site_exclusions"][0]["site_id"] == victim
    assert pv.digest_payload_from_manifest(manifest)["owner_site_exclusions"] == [victim]
    plain_manifest = pv.build_run_manifest(ra.assemble(plain), argv=[])
    assert "owner_site_exclusions" not in pv.digest_payload_from_manifest(plain_manifest)
    assert plain_manifest["inputsDigest"] != manifest["inputsDigest"]


def test_packet_names_owner_exclusions():
    p = {"owner_site_exclusions": [{"site_id": "NRS18_NH_10004",
                                    "reason": "an 8,096 km2 basin the engine cannot value",
                                    "source": "owner", "was_retained": True}]}
    text = "\n".join(rp.owner_exclusion_lines(p))
    assert "Owner exclusion:" in text and "NRS18_NH_10004" in text
    assert "left the retained pool, an 8,096 km2 basin" in text
    assert rp.owner_exclusion_lines({}) == []
    source = (Path(__file__).resolve().parents[1] / "streamcurves" / "review_packet.py").read_text(encoding="utf-8")
    assert "lines += owner_exclusion_lines(p)" in source


def test_the_flag_reaches_run_evidence():
    text = (_SCRIPTS / "run_region_batch.py").read_text(encoding="utf-8")
    assert 'add_argument("--exclude-site"' in text
    start = text.index("ra.run_evidence(")
    assert "exclude_sites=_parse_kv(a.exclude_site" in text[start:start + 1100]
    ns = text.index("argparse.Namespace(")
    assert "exclude_site=[]" in text[ns:ns + 1000]
