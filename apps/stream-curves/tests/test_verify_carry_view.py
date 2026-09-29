"""``verify`` re-stages a version against what its stage could carry forward from.

A re-stage that read the canonical library would find the version under test
itself there (the latest published) and carry from it, stamping ``carriedForward``
blocks the record never had; the campaign's Round A replay (2026-09-25) showed
six of seven latest versions come back with a different content digest for
that reason alone, every curve identical. ``carry_view`` cuts a library view
to the versions before the one the record carried from, and
``resolve_decision_paths`` finds decision files a record names relative to
another checkout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
if str(APP / "scripts") not in sys.path:
    sys.path.insert(0, str(APP / "scripts"))

import run_region_batch as rrb  # noqa: E402
from streamcurves import carry_forward as cf  # noqa: E402


def _fake_library(root: Path, code: str = "55", versions=(1, 2, 3)) -> Path:
    lib = root / "library"
    adir = lib / "assessments" / "fake-region"
    adir.mkdir(parents=True)
    man = {"assessmentId": "fake-region", "assessmentType": "deep", "latestVersion": max(versions),
           "region": {"kind": "ecoregion", "code": code},
           "versions": [{"version": v, "contentDigest": f"sha256:{v:064d}"} for v in versions]}
    (adir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (adir / "status.json").write_text("{}", encoding="utf-8")
    for v in versions:
        (adir / f"v{v}").mkdir()
        (adir / f"v{v}" / "assessment.deep.json").write_text(json.dumps({"v": v}), encoding="utf-8")
    return lib


def test_a_canonical_version_carries_from_the_versions_before_it(tmp_path):
    lib = _fake_library(tmp_path)
    vdir = lib / "assessments" / "fake-region" / "v3"
    view = rrb.carry_view(vdir, {"code": "55"}, {}, tmp_path / "root", library=lib)
    assert view["assessmentId"] == "fake-region" and view["cutoff"] == 3
    assert view["latestVersion"] == 2
    root = Path(view["root"])
    assert cf.find_published("55", root=root) == ("fake-region", 2)
    assert sorted(p.name for p in (root / "assessments" / "fake-region").iterdir()) == [
        "manifest.json", "status.json", "v1", "v2"]
    man = json.loads((root / "assessments" / "fake-region" / "manifest.json").read_text(encoding="utf-8"))
    assert [v["version"] for v in man["versions"]] == [1, 2]
    # the version under test itself is never in the view
    assert not (root / "assessments" / "fake-region" / "v3").exists()


def test_a_first_version_carries_from_nothing(tmp_path):
    lib = _fake_library(tmp_path)
    vdir = lib / "assessments" / "fake-region" / "v1"
    view = rrb.carry_view(vdir, {"code": "55"}, {}, tmp_path / "root", library=lib)
    assert view["latestVersion"] == 0 and view["cutoff"] == 1
    assert cf.find_published("55", root=Path(view["root"])) is None
    # and the stage's carry argument then carries nothing: an empty prepared carry
    assert rrb.carry_argument("missing", "55", Path(view["root"])) == {}
    assert rrb.published_bundle("55", root=Path(view["root"])) is None
    assert rrb.carried_from("55", root=Path(view["root"])) is None


def test_a_recorded_carry_from_version_wins_over_the_folder(tmp_path):
    lib = _fake_library(tmp_path)
    # a staged candidate outside the canonical library that recorded carrying from v1
    vdir = tmp_path / "staged" / "library" / "assessments" / "fake-region" / "v9"
    vdir.mkdir(parents=True)
    manifest = {"inputs": {"reference": {"carryForward": {"mode": "published_curves", "fromVersion": 1}}}}
    view = rrb.carry_view(vdir, {"code": "55"}, manifest, tmp_path / "root", library=lib)
    assert view["cutoff"] == 2 and view["latestVersion"] == 1
    # a staged candidate with no record carries from everything the library holds
    view = rrb.carry_view(vdir, {"code": "55"}, {}, tmp_path / "root2", library=lib)
    assert view["cutoff"] == 4 and view["latestVersion"] == 3


def test_a_full_refit_and_an_unpublished_region_need_no_view(tmp_path):
    lib = _fake_library(tmp_path)
    vdir = lib / "assessments" / "fake-region" / "v3"
    assert rrb.carry_view(vdir, {"code": "55"}, {"inputs": {"refit": {"mode": "all"}}},
                          tmp_path / "root", library=lib) is None
    view = rrb.carry_view(vdir, {"code": "71"}, {}, tmp_path / "root", library=lib)
    assert view["assessmentId"] is None and view["latestVersion"] == 0
    assert cf.find_published("71", root=Path(view["root"])) is None
    # no view: the canonical library, as every stage reads it
    assert rrb.carry_argument("missing", "55", None) is True
    assert rrb.carry_argument("all", "55", Path(view["root"])) is False


def test_recorded_relative_decision_paths_resolve_under_the_decisions_root(tmp_path):
    runs = tmp_path / "runs"
    (runs / "l3-55").mkdir(parents=True)
    answers = runs / "l3-55" / "owner_decisions.json"
    answers.write_text("{}", encoding="utf-8")
    parsed = argparse.Namespace(
        curve_decisions=None, reviewer_decisions="notes/DEEP_Working/analysis/runs/l3-55/owner_decisions.json",
        coverage_exceptions="D:/absolute/exceptions.json", region_coverage_exceptions=None,
        candidate_register="notes/DEEP_Working/analysis/runs/l3-55/missing.json")
    moved = rrb.resolve_decision_paths(parsed, str(runs))
    assert moved == {"reviewer_decisions": str(answers)}
    assert parsed.reviewer_decisions == str(answers)
    # an absolute path and a file that exists nowhere stay as recorded
    assert parsed.coverage_exceptions == "D:/absolute/exceptions.json"
    assert parsed.candidate_register == "notes/DEEP_Working/analysis/runs/l3-55/missing.json"


def test_verify_parser_takes_a_decisions_root():
    ns = rrb.build_parser().parse_args(["verify", "--version-dir", "x", "--decisions-root", "r"])
    assert ns.decisions_root == "r" and ns.fn is rrb.cmd_verify
    ns = rrb.build_parser().parse_args(["verify", "--version-dir", "x"])
    assert ns.decisions_root is None


@pytest.mark.parametrize("mode", ["missing", "all"])
def test_carried_for_reads_the_view_root(tmp_path, mode):
    lib = _fake_library(tmp_path)
    view = rrb.carry_view(lib / "assessments" / "fake-region" / "v3", {"code": "55"}, {},
                          tmp_path / "root", library=lib)
    got = rrb.carried_for({"refit": mode, "carry_root": view["root"]}, "55")
    if mode == "all":
        assert got is None
    else:
        assert got == {"assessmentId": "fake-region", "version": 2, "contentDigest": f"sha256:{2:064d}"}
