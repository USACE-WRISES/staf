"""Campaign Round 1 (WP-B): stage-many hands each region its own decision files
(--decisions-root, H2), their shas ride the manifest and the region digest, promote
refuses a decision file that changed after the stage, "your choice stands" is
enforced before the evidence pass (owner_curves.published_vs_standing), and an
experimental manifest never reaches the canonical library (D4a).

Every check here is offline and cheap: the evidence pass is never run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import methodology
from streamcurves import owner_curves as oc
from streamcurves import region_build as rb
from streamcurves import regional_agent as ra
from streamcurves import session_io as sio

APP = Path(__file__).resolve().parents[1]
SCRIPT = APP / "scripts" / "run_region_batch.py"
LABEL = "Rehearsal (not an owner decision)"
WHY = "A reason long enough to be recorded here."


def _batch():
    scripts = str(SCRIPT.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("run_region_batch_stage_decisions", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _decision(metric, action, **kw):
    return oc.new_decision(metric, action, rationale=kw.pop("rationale", WHY),
                           recorded_by=kw.pop("recorded_by", "GM"), **kw)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _region_files(root: Path, code: str) -> dict[str, Path]:
    """A region's four decision files under ``<root>/l3-<code>/``."""
    run_dir = rb.run_folder(root, code)
    run_dir.mkdir(parents=True, exist_ok=True)
    oc.save(run_dir, _decision("chem_TURB", oc.REMOVE))
    rb.save_answer(run_dir, {"rule_id": "CURVE-07", "subject": "phab_SINU", "action": "accept",
                             "rationale": WHY, "reviewer": "GM", "date": "2026-09-25"})
    rb.save_gap(run_dir, {"functionId": "fn-x", "reason": "no-suitable-metric",
                          "justification": WHY, "recordedBy": "GM"})
    rb.save_candidate_register(run_dir, {"schema": 1, "considered": [], "decisions": []})
    return {"curve_decisions": run_dir / oc.DECISIONS_FILE,
            "reviewer_decisions": run_dir / rb.OWNER_DECISIONS_FILE,
            "region_coverage_exceptions": run_dir / rb.COVERAGE_EXCEPTIONS_FILE,
            "candidate_register": run_dir / rb.CANDIDATE_REGISTER_FILE}


def _many(mod, **kw) -> argparse.Namespace:
    base = {k: None for k in mod._STAGE_MANY_FLAGS}
    base.update(enable_policy=[], approve_portfolio=[], maintainer=LABEL)
    base.update(kw)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- #
# stage-many hands each region its own files
# --------------------------------------------------------------------------- #
def test_stage_many_hands_each_region_its_own_decision_files(tmp_path):
    mod = _batch()
    root = tmp_path / "decisions"
    files = _region_files(root, "55")
    a = _many(mod, decisions_root=str(root), refit="all")
    ns = mod.region_stage_namespace(a, "55", "Eastern Corn Belt Plains", tmp_path / "out" / "l3-55",
                                    argv=["stage-many", "--l3", "55"])
    for attr, path in files.items():
        assert getattr(ns, attr) == str(path), attr
    assert ns.decisions_root == str(root) and ns.refit == "all"
    # a region with no record under the root reads nothing (and, under a full
    # refit, nothing is seeded from a published version)
    other = mod.region_stage_namespace(a, "65", "Northern Lakes and Forests",
                                       tmp_path / "out" / "l3-65", argv=["stage-many"])
    assert all(getattr(other, attr) is None for attr in files)
    assert not rb.run_folder(root, "65").exists()
    # the shas join the region's inputs, keyed relative to the root, so a changed
    # answer re-stages the region and a moved root does not
    inputs = mod.region_inputs(vars(ns))
    assert inputs["reviewerFiles"] == {f"l3-55/{p.name}": _sha(p) for p in files.values()}
    assert "decisions_root" not in inputs["flags"] and inputs["flags"]["refit"] == "all"
    d0 = mod.region_digest("55", "E", inputs, None)
    files["reviewer_decisions"].write_text("[]\n", encoding="utf-8")
    assert mod.region_digest("55", "E", mod.region_inputs(vars(ns)), None) != d0
    # the stage-many flags carry the root, the mode and the policy for the namespace
    assert {"decisions_root", "refit", "value_policy"} <= set(mod._STAGE_MANY_FLAGS)


def test_the_folder_is_the_region_builders_and_legacy_folders_rename(tmp_path):
    mod = _batch()
    out_root = tmp_path / "many"
    legacy = out_root / "l3-55-eastern-corn-belt-plains"
    (legacy / "library").mkdir(parents=True)
    packet = {"staged": {"path": str(legacy / "library" / "v1"), "root": str(legacy / "library")},
              "promote_command": f"python x promote --out {legacy}"}
    (legacy / "review_packet.json").write_text(json.dumps(packet), encoding="utf-8")
    (legacy / "promote_command.txt").write_text(f"python x promote --out {legacy}\n", encoding="utf-8")
    (out_root / "l3-71-interior-plateau").mkdir()
    (out_root / "l3-71").mkdir()                      # the target exists: left alone
    done = mod.rename_legacy_folders(out_root)
    assert [(a.name, b.name) for a, b in done] == [("l3-55-eastern-corn-belt-plains", "l3-55")]
    assert rb.run_folder(out_root, "55").is_dir() and not legacy.exists()
    moved = json.loads((out_root / "l3-55" / "review_packet.json").read_text(encoding="utf-8"))
    assert moved["staged"]["path"] == str(out_root / "l3-55" / "library" / "v1")
    assert str(out_root / "l3-55") in (out_root / "l3-55" / "promote_command.txt").read_text(encoding="utf-8")
    assert (out_root / "l3-71-interior-plateau").is_dir()
    # a stage-many with the flag and no region renames and stops
    ns = _many(mod, l3=[], name=[], out_root=str(out_root), rename_legacy_folders=True,
               workers=1, isolated=False)
    assert mod.cmd_stage_many(ns) == 0
    assert mod.cmd_stage_many(_many(mod, l3=[], name=[], out_root=str(out_root), workers=1,
                                    isolated=False)) == 2


# --------------------------------------------------------------------------- #
# promote refuses a decision file that moved after the stage
# --------------------------------------------------------------------------- #
def test_promote_refuses_changed_answer_files(tmp_path):
    mod = _batch()
    root = tmp_path / "decisions"
    files = _region_files(root, "55")
    manifest = {"region": {"kind": "ecoregion", "code": "55"},
                "reviewerInputs": {"decisionsRoot": str(root),
                                   "files": {f"l3-55/{p.name}": _sha(p) for p in files.values()}}}
    assert mod.reviewer_inputs_drift(manifest, tmp_path / "run") == []
    files["reviewer_decisions"].write_text("[]\n", encoding="utf-8")
    files["candidate_register"].unlink()
    assert mod.reviewer_inputs_drift(manifest, tmp_path / "run") == [
        "l3-55/candidate_register.json: missing", "l3-55/owner_decisions.json: changed"]
    # without a recorded root the files are read beside the run folder's parent
    assert mod.reviewer_inputs_drift({"reviewerInputs": {"files": {"x.json": "sha256:0"}}},
                                     tmp_path / "run") == ["x.json: missing"]
    # and the stage-many region's curve decisions are found under the root
    argv = ["stage-many", "--l3", "55", "--out-root", str(tmp_path / "many")]
    assert mod._decisions_file(tmp_path / "many" / "l3-55", argv, manifest) == files["curve_decisions"]
    assert mod._decisions_file(tmp_path / "many" / "l3-55",
                               argv + ["--decisions-root", str(tmp_path / "elsewhere")],
                               manifest) == tmp_path / "elsewhere" / "l3-55" / oc.DECISIONS_FILE
    assert mod._decisions_file(tmp_path / "run", ["stage", "--curve-decisions", str(files["curve_decisions"])],
                               manifest) == files["curve_decisions"]
    assert mod._decisions_file(tmp_path / "run", ["stage"], {}) == tmp_path / "run" / oc.DECISIONS_FILE


# --------------------------------------------------------------------------- #
# "your choice stands": a published owner decision needs a standing one
# --------------------------------------------------------------------------- #
def _bundle_with_owner_decisions(*metrics: str) -> dict:
    from streamcurves.deep_export import deep_slug
    return {"metricsByFunction": [
        {"functionId": "fn-a", "metrics": [
            {"metricId": "spring-" + deep_slug(m), "metricName": m,
             "ownerDecision": {"id": f"cd-{m}", "kind": "refused", "recordedBy": "GM"}}
            for m in metrics] + [{"metricId": "spring-fitted", "metricName": "fitted"}]}]}


def test_published_vs_standing_names_the_published_choices_without_a_record(tmp_path):
    bundle = _bundle_with_owner_decisions("chem_TURB", "bent_EPT_NTAX")
    assert oc.published_vs_standing(bundle, None) == ["spring-bent-ept-ntax", "spring-chem-turb"]
    assert oc.published_vs_standing(bundle, []) == ["spring-bent-ept-ntax", "spring-chem-turb"]
    run_dir = tmp_path / "l3-55"
    oc.save(run_dir, _decision("chem_TURB", oc.REMOVE))
    assert oc.published_vs_standing(bundle, run_dir / oc.DECISIONS_FILE) == ["spring-bent-ept-ntax"]
    oc.save(run_dir, _decision("bent_EPT_NTAX", oc.UNMAP, functions=["fn-a"]))
    assert oc.published_vs_standing(bundle, oc.load(run_dir)) == []
    assert oc.published_vs_standing({"metricsByFunction": []}, None) == []
    assert oc.published_vs_standing(None, None) == []


class _Stop(Exception):
    pass


def _stage_ns(mod, tmp_path, *extra):
    return mod.build_parser().parse_args(
        ["stage", "--l3", "55", "--name", "Eastern Corn Belt Plains", "--out", str(tmp_path / "run"),
         "--nrsa-dataset", "legacy-1819", "--no-screen", "--no-streamcat", "--n-boot", "20",
         "--maintainer", LABEL, *extra])


def test_cmd_stage_refuses_a_published_owner_decision_with_no_standing_one(tmp_path, monkeypatch):
    mod = _batch()
    seen = {}

    def stop(*args, **kw):
        seen.update(kw)
        raise _Stop()

    monkeypatch.setattr(mod, "published_bundle", lambda code, root=None: _bundle_with_owner_decisions("chem_TURB"))
    monkeypatch.setattr(mod.ra, "run_evidence", stop)
    # no file: refused before the expensive pass runs
    assert mod.cmd_stage(_stage_ns(mod, tmp_path, "--refit", "missing")) == 2
    assert not seen
    # the region's record holds the decision: the stage goes on (into the pass)
    run_dir = tmp_path / "l3-55"
    oc.save(run_dir, _decision("chem_TURB", oc.REMOVE))
    with pytest.raises(_Stop):
        mod.cmd_stage(_stage_ns(mod, tmp_path, "--refit", "missing",
                                "--curve-decisions", str(run_dir / oc.DECISIONS_FILE)))
    assert seen["hold"] == ["chem_TURB"] and seen["carry"] is True
    # a full refit reads no published version, so nothing is checked against one
    seen.clear()
    with pytest.raises(_Stop):
        mod.cmd_stage(_stage_ns(mod, tmp_path, "--refit", "all"))
    assert seen["carry"] is False


# --------------------------------------------------------------------------- #
# an experimental manifest never reaches the canonical library (D4a)
# --------------------------------------------------------------------------- #
def test_experimental_block_reads_the_config_root_and_the_extension_flag(tmp_path, monkeypatch):
    monkeypatch.delenv(ra.CONFIG_ROOT_ENV, raising=False)
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: False)
    assert ra.experimental_block() is None
    monkeypatch.setenv(ra.CONFIG_ROOT_ENV, str(APP / "config"))      # the app's own root
    assert ra.experimental_block() is None
    monkeypatch.setenv(ra.CONFIG_ROOT_ENV, str(tmp_path))
    block = ra.experimental_block()
    assert block == {"configRoot": str(tmp_path.resolve()), "extensionFlag": False}
    monkeypatch.delenv(ra.CONFIG_ROOT_ENV)
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: True)
    assert ra.experimental_block() == {"configRoot": None, "extensionFlag": True}
    assert lib.experimental_label({"manifest": {"experimental": block}}) == \
        f"configuration root {tmp_path.resolve()}"
    assert lib.experimental_label({"manifest": {"experimental": None}}) is None
    assert "extension flag" in lib.experimental_label(
        {"manifest": {"experimental": {"configRoot": None, "extensionFlag": True}}})


def _fake_staged_run(out: Path, *, experimental) -> None:
    """A run folder with a staged version whose manifest is current on
    methodology and policy, so promote gets as far as the experimental gate."""
    vdir = out / "library" / "assessments" / "eastern-corn-belt-plains" / "v1"
    vdir.mkdir(parents=True)
    policy = dec.load_policy(None)
    region = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}
    manifest = {"region": region, "agent": {"argv": ["stage"]},
                "methodology": methodology.config_fingerprints(),
                "standingDecisions": {"sha256": policy["meta"]["sha256"]},
                "reviewerInputs": {"files": {}, "decisionsRoot": str(out.parent)},
                "experimental": experimental}
    (vdir / lib.PROVENANCE_FILE).write_text(json.dumps(
        {"manifest": manifest, "records": [], "reviewQueue": {"items": [], "counts": {"open": 0}}}),
        encoding="utf-8")
    (vdir / lib.BUNDLE_FILE).write_text(json.dumps(
        {"metricsByFunction": [], "contentDigest": "sha256:x", "functionCoverage": {"exclusions": []}}),
        encoding="utf-8")
    (vdir / lib.SESSION_FILE).write_text(sio.dumps_session(sio.dump_session_fields(
        {"region_of_applicability": region, "owner_curve_decisions": []}, session_name="t")),
        encoding="utf-8")
    (vdir / lib.META_FILE).write_text(json.dumps(
        {"assessmentId": "eastern-corn-belt-plains", "assessmentName": "ECBP", "region": region,
         "author": "policy-candidate " + dec.PENDING_SUFFIX}), encoding="utf-8")
    (out / "review_packet.json").write_text(json.dumps(
        {"region": region, "staged": {"path": str(vdir), "version": 1}}), encoding="utf-8")


def test_promote_refuses_an_experimental_manifest_on_the_canonical_root(tmp_path):
    mod = _batch()
    out = tmp_path / "runs" / "l3-55"
    _fake_staged_run(out, experimental={"configRoot": str(tmp_path / "cfg"), "extensionFlag": False})
    ns = mod.build_parser().parse_args(["promote", "--out", str(out), "--maintainer", "GM",
                                        "--publish-root", str(ra.CANONICAL_LIBRARY)])
    with pytest.raises(SystemExit, match=r"experimental \(configuration root .*cfg"):
        mod.cmd_promote(ns)
    # the same run promotes into an isolated root: the gates after it decide
    ns = mod.build_parser().parse_args(["promote", "--out", str(out), "--maintainer", "GM",
                                        "--publish-root", str(tmp_path / "isolated")])
    try:
        mod.cmd_promote(ns)
    except SystemExit as exc:
        assert "experimental" not in str(exc)
    except Exception:  # noqa: BLE001 - a fabricated bundle fails later gates, which is fine
        pass
