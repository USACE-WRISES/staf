"""The DEEP development evidence package: deterministic, verifiable, installable."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from streamcurves import deep_evidence as de
from streamcurves import evidence_store as evs
from streamcurves import library as lib
from streamcurves import provenance as pv
from streamcurves import session_io as sio

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
VERSION = "interior-plateau/v6"


@pytest.fixture(scope="module")
def session():
    vdir = LIBRARY / VERSION
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{VERSION} is not in this checkout")
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    fields["bundle"] = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    doc = json.loads((vdir / lib.PROVENANCE_FILE).read_text(encoding="utf-8"))
    return fields, doc


@pytest.fixture(scope="module")
def written(session, tmp_path_factory):
    fields, doc = session
    out = tmp_path_factory.mktemp("pkgs")
    ref = de.write_from_fields(fields, out, provenance=doc)
    return out, ref


def test_identity_helpers():
    assert de.package_id("71") == "deep-dev-l3-71"
    assert de.package_id("8.3") == "deep-dev-l3-8.3"
    assert de.package_version(assessment_id="interior-plateau", version=6) == "interior-plateau-v6"
    assert de.package_version(inputs_digest="sha256:ca0b44d0977063af1265") == "ca0b44d0977063"[:12]
    assert de.package_version() == "unversioned"


def test_a_session_package_opens_verifies_and_installs(written, session):
    out, ref = written
    fields, doc = session
    assert ref["packageId"] == "deep-dev-l3-71" and ref["roles"] == ["development"]
    assert ref["reproducibility"] == de.REVIEWABLE
    assert ref["version"] == doc["manifest"]["inputsDigest"].split(":", 1)[-1][:12]
    assert ref["packageDigest"].startswith("sha256:") and ref["dataDigest"].startswith("sha256:")
    assert ref["dependsOn"][0]["packageId"] == "nrsa-archive"
    assert ref["dependsOn"][0]["dataDigest"] == doc["manifest"]["inputs"]["nrsa_dataset"]["manifestDigest"]
    folder = out / ref["packageId"]
    got = evs.verify_folder(folder)
    assert got["ok"] and got["packageDigest"] == ref["packageDigest"]
    names = {p.name for p in (folder / "data").iterdir()}
    assert names == {"stations.csv", "values.csv", "pools.json", "curves.json", "ladder.json",
                     "candidates.json", "ledger.json", "decisions.json"}
    manifest = got["manifest"]
    assert manifest["unavailable"] and "pool_ledger" in manifest["unavailable"][0]["item"]
    assert manifest["recipe"]["code"]["fingerprint"] and manifest["recipe"]["engine"]["sha256"]
    assert "producer" in manifest and manifest["producer"]["tool"] == de.TOOL
    # no clock and no absolute path anywhere in the package
    text = (folder / evs.MANIFEST).read_text(encoding="utf-8")
    assert "D:\\\\" not in text and "D:/" not in text and "exportedAt" not in text
    # the archive installs into a store and reads as ready
    zpath = out / ref["archive"]["name"]
    assert zpath.stat().st_size == ref["archive"]["bytes"] == len(zpath.read_bytes())
    assert evs.sha_file(zpath) == ref["archive"]["sha256"]
    store = out / "store"
    installed = evs.install_zip(zpath, root=store)
    assert evs.ready(ref, root=store) == installed
    assert evs.matches(evs.check(installed), ref)
    # and the index beside the archives names it the way a host does
    index = evs.read_index(str(out))
    assert index[ref["packageId"]]["zip"] == ref["archive"]["name"]
    assert index[ref["packageId"]]["packageDigest"] == ref["packageDigest"]


def test_a_package_written_twice_is_byte_identical(written, session):
    out, ref = written
    fields, doc = session
    first = (out / ref["archive"]["name"]).read_bytes()
    manifest_first = (out / ref["packageId"] / evs.MANIFEST).read_bytes()
    again = de.write_from_fields(fields, out, provenance=doc)
    assert again["packageDigest"] == ref["packageDigest"] and again["archive"] == ref["archive"]
    assert (out / again["archive"]["name"]).read_bytes() == first
    assert (out / ref["packageId"] / evs.MANIFEST).read_bytes() == manifest_first


def test_the_data_files_say_what_the_session_holds(written, session):
    out, ref = written
    fields, doc = session
    folder = out / ref["packageId"] / "data"
    stations = pd.read_csv(folder / "stations.csv")
    assert list(stations.columns) == list(de.STATION_COLUMNS)
    assert len(stations) == len(fields["easi_screening_sites"])
    assert set(stations["screen_outcome"]) <= {"retained", "excluded", "not_evaluable"}
    values = pd.read_csv(folder / "values.csv")
    assert list(values.columns) == list(de.VALUE_COLUMNS) and len(values)
    assert set(values["station_key"]) <= set(stations["station_key"])
    curves = json.loads((folder / "curves.json").read_text(encoding="utf-8"))
    build = fields["reference_build"]
    assert set(build["carriedMetrics"]) <= set(curves)
    for mk, c in curves.items():
        assert c["strata"] and all(len(s["points"]) >= 2 for s in c["strata"]), mk
        assert c["basisDigest"].startswith("sha256:")
    ledger = json.loads((folder / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["schema"] == pv.LEDGER_SCHEMA
    assert pv.ledger_rows_for_bundle(fields["bundle"], ledger)["equal"]
    candidates = json.loads((folder / "candidates.json").read_text(encoding="utf-8"))
    assert candidates["schema"] == 1 and candidates["rows"] and candidates["counts"]["selected"]
    decisions = json.loads((folder / "decisions.json").read_text(encoding="utf-8"))
    assert set(decisions) >= {"ownerDecisions", "answers", "standingDecisions", "approvals", "gaps",
                              "enabledPolicyIds", "reviewDecisions"}
    pools = json.loads((folder / "pools.json").read_text(encoding="utf-8"))
    assert set(pools) >= set(build["carriedMetrics"])


def _ledger_frame(support: dict, station_keys: list, code: str) -> pd.DataFrame:
    rows = []
    for mk, sup in support.items():
        option = pv._pool_option(sup) or "local"
        for i, key in enumerate(station_keys):
            rows.append({"metric": mk, "station_key": key, "level": sup.get("level") or "l3",
                         "in_pool": True, "reason": "", "value": float(i + 1),
                         "source_cycle": "2324", "l3": code, "l2": "8.3", "l1": "8",
                         "option": option, "screen": "strict"})
    return pd.DataFrame(rows, columns=list(de.LEDGER_COLUMNS))


def test_a_result_package_with_a_pool_ledger_is_refittable(session, tmp_path):
    """The stage path: a result with its pool ledger writes pool_ledger.csv, names every
    refitted curve's stations, and reads as refittable."""
    fields, doc = session
    build = fields["reference_build"]
    support = pv._support_from_build(build)
    region = fields["region_of_applicability"]
    sites = fields["easi_screening_sites"]
    keys = [str(r["site_id"]) for r in sites[:12]]
    result = {**fields, "region": region, "reference_support": support,
              "reference_pool_ledger": _ledger_frame(support, keys, str(region["code"])),
              "screening_tables": {"easi_screening_sites": sites},
              "run_seed": doc["manifest"]["diagnostics"]["runSeed"],
              "curve_decisions": fields.get("owner_curve_decisions") or [],
              "standing_decisions": doc["manifest"].get("standingDecisions")}
    ref = de.write_package(result, doc, tmp_path, version="interior-plateau-v6")
    assert ref["version"] == "interior-plateau-v6" and ref["reproducibility"] == de.REFITTABLE
    folder = tmp_path / ref["packageId"]
    assert evs.verify_folder(folder)["ok"]
    ledger_csv = pd.read_csv(folder / "data" / "pool_ledger.csv")
    assert list(ledger_csv.columns) == list(de.LEDGER_COLUMNS)
    ledger = json.loads((folder / "data" / "ledger.json").read_text(encoding="utf-8"))
    refitted = [r for r in ledger["rows"] if r["disposition"] == pv.REFITTED]
    assert refitted
    for r in refitted:
        assert r["engine"]["executed"] and r["engine"]["seed"] is not None
        assert r["pool"]["stationIds"] and r["pool"]["evidence"] == "reference_pool_ledger"
        assert r["pool"]["cyclesUsed"] == ["2324"]
    pools = json.loads((folder / "data" / "pools.json").read_text(encoding="utf-8"))
    assert all(p["stationIds"] for p in pools.values())
    values = pd.read_csv(folder / "data" / "values.csv")
    assert set(values["station_key"]) == set(keys)
    manifest = evs.read_manifest(folder)
    assert manifest["checks"]["ledgerSelectedEqualsBundle"] is True
    assert manifest["checks"]["refittedRowsWithStationEvidence"]["n"] == len(refitted)
    # written again: the same package
    again = de.write_package(result, doc, tmp_path, version="interior-plateau-v6")
    assert again["packageDigest"] == ref["packageDigest"] and again["archive"] == ref["archive"]


def test_reference_reads_a_manifest_a_folder_and_an_archive(written):
    out, ref = written
    folder = out / ref["packageId"]
    from_folder = evs.reference(folder)
    from_zip = evs.reference(out / ref["archive"]["name"])
    from_doc = evs.reference(evs.read_manifest(folder), archive=ref["archive"])
    assert from_folder == from_zip == {k: v for k, v in from_doc.items() if k != "archive"}
    assert from_doc == ref
    for key in ("packageId", "version", "dataDigest", "packageDigest", "title", "roles",
                "reproducibility", "bytes", "coverage", "dependsOn"):
        assert key in from_folder


def test_the_public_base_per_kind_and_the_override(monkeypatch):
    monkeypatch.delenv(evs.ENV_BASE_URL, raising=False)
    assert evs.public_base("easi").endswith("/easi-evidence/")
    assert evs.public_base("deep").endswith("/deep-evidence/")
    assert evs.public_base() == evs.public_base("easi")
    from views import easi_page
    assert easi_page.PUBLIC_EVIDENCE_BASE == evs.public_base("easi")
    monkeypatch.setenv(evs.ENV_BASE_URL, "D:/somewhere/evidence")
    assert evs.public_base("deep") == "D:/somewhere/evidence" == evs.public_base("easi")
    with pytest.raises(ValueError):
        monkeypatch.delenv(evs.ENV_BASE_URL)
        evs.public_base("sqt")


def test_the_decisions_payload_carries_no_clock():
    """Two builds of identical inputs stamp their policy answers with different clocks; the
    package's identity is its content, so the stamps stay out of decisions.json (the stage
    gate of 2026-09-25 saw Southeastern Plains' package digest move for that reason alone)."""
    def doc(stamp):
        records = [dict(rule_id="CURVE-04", subject="chem_PTL", reviewer="standing-policy:x",
                        reviewer_action="accept", reviewer_rationale="Accepted under the policy.",
                        reviewed_at=stamp, reviewer_decision_class="accept_with_flag",
                        reviewer_rationale_origin="standing_policy:1.2")]
        queue = dict(items=[dict(item_id="CURVE-04:chem_PTL", status="resolved", reviewer="standing-policy:x",
                                 reviewer_action="accept", reviewer_rationale="Accepted.", reviewed_at=stamp)])
        review = dict(chem_PTL=dict(status="review", decision="accept", decision_note="ok",
                                    decided_by="GM", decided_at=stamp))
        return de.decisions_doc(records=records, review_queue=queue, curve_review=review)
    a = doc("2026-09-25T12:56:31+00:00")
    b = doc("2026-09-25T13:19:53+00:00")
    assert a == b
    text = json.dumps(a)
    assert "reviewed_at" not in text and "decided_at" not in text and "2026-09-25T" not in text
    assert a["answers"][0]["reviewer_action"] == "accept"
    assert a["reviewQueueResolved"][0]["item_id"] == "CURVE-04:chem_PTL"
    assert a["reviewDecisions"]["chem_PTL"]["decided_by"] == "GM"
