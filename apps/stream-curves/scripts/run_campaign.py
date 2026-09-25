"""The national campaign runner (campaign Round 3): a frozen manifest, the stage-many
line it runs, an index of what came out, the promotion policy's verdict per region, the
batch summary the owner reads, the confirmation the operator writes by hand, a comparison
against the published versions, and a package of the record.

    plan            freeze a campaign root: manifest.json (identity, code, inputs, run,
                    every region with its expected inputs digest, in stage order),
                    commands.md, STATE.md and the two policy files copied beside them;
                    never runs anything and refuses a dirty tree or an existing root
    run             recompute every recorded value against the live tree, refuse on any
                    difference, then run the stage-many line (the same command resumes)
    index           index.json and index.csv, one row per region, from artifacts only
    eligibility     every promotion-policy gate per region, from artifacts only, never
                    refitting; eligibility.json
    batch-summary   promotion_batch_<id>.md and .json for the owner: the eligible table,
                    the exceptions with their open items, the promote commands with
                    placeholders; never writes a confirmation
    confirm         validate a hand-written promotion_batch_<id>.confirmation.json and
                    print the promote commands with the confirming maintainer and date
    compare         each staged region against its published version (compare/<id>.json,
                    the owner-decision diff where the published record carries owner inputs)
    package         a zip of the record with package.json inside

Usage (from apps/stream-curves of the worktree the campaign describes, the shared venv):
    python -B scripts/run_campaign.py plan --root D:/Data/staf-campaign-2026-09/national/x \
        --purpose fast --worktree D:/Code/Work/staf-campaign-gate \
        --census D:/Data/staf-campaign-2026-09/baseline/census/reference_support_census.csv \
        --n-boot 200 --workers 6 --maintainer "Rehearsal (not an owner decision)" --refit all
    python -B scripts/run_campaign.py run --root D:/Data/staf-campaign-2026-09/national/x --dry-run

The words GM, confirmedBy and approvedBy are never written by this script; a confirmation
is validated, never filled.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_region_batch as rrb  # noqa: E402 (sibling script; sanitizes the EASI switches)
from streamcurves import campaign as camp  # noqa: E402
from streamcurves import carry_forward as cf  # noqa: E402
from streamcurves import code_identity  # noqa: E402
from streamcurves import compare as cmp  # noqa: E402
from streamcurves import decisions as dec  # noqa: E402
from streamcurves import methodology  # noqa: E402
from streamcurves import paths  # noqa: E402
from streamcurves import region_build as rb  # noqa: E402
from streamcurves import regional_agent as ra  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DEFAULT_CROSSWALK = _APP_ROOT / "data" / "ecoregion_code_crosswalk.csv"
DEFAULT_STATION_SCREEN = _APP_ROOT / "data" / "nrsa" / "station_screen.parquet"
BATCH_SCRIPT = _SCRIPTS / "run_region_batch.py"
REFUSED = 2


def tree_fingerprint(app_root: Path) -> str:
    """The code fingerprint of the app at ``app_root`` (``code_identity.fingerprint``,
    equal to ``run_region_batch.code_fingerprint()`` on the same tree)."""
    return code_identity.fingerprint(app_root)


def _refuse(*lines: str) -> int:
    for line in lines:
        print(f"[campaign] REFUSED: {line}" if line is lines[0] else f"[campaign]   {line}")
    return REFUSED


def _app_root_of(worktree: Path) -> Path:
    return Path(worktree) / "apps" / "stream-curves"


def _config_root_problem(config_root: Optional[str]) -> Optional[str]:
    """The configuration is read at import: a plan or a run under another root has to be
    started with STREAMCURVES_CONFIG_ROOT set, and a plan without --config-root cannot run
    under one."""
    wanted = Path(config_root).expanduser().resolve() if config_root else None
    current = Path(paths.CONFIG_DIR).resolve()
    if wanted is None and paths.CONFIG_ROOT_OVERRIDDEN:
        return (f"this process runs under STREAMCURVES_CONFIG_ROOT={current}; pass "
                f"--config-root {current} for an experimental arm, or unset it")
    if wanted is not None and current != wanted:
        return (f"--config-root {wanted} needs STREAMCURVES_CONFIG_ROOT={wanted} in this process's "
                "environment (the configuration is read at import)")
    return None


def run_environment(base: dict, manifest: dict) -> dict:
    """The environment a campaign's stage-many runs under: the configuration root only
    when the manifest names one, never an inherited library root (a campaign reads the
    canonical library of its worktree)."""
    env = dict(base)
    env.pop("STREAMCURVES_CONFIG_ROOT", None)
    env.pop("STAF_LIBRARY_ROOT", None)
    cfg = (manifest.get("run") or {}).get("configRoot")
    if cfg:
        env["STREAMCURVES_CONFIG_ROOT"] = str(cfg)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _stage_many_namespace(tokens: list[str]) -> argparse.Namespace:
    return rrb.build_parser().parse_args(list(tokens))


def _stage_name(many: argparse.Namespace, code: str) -> Optional[str]:
    """The name a stage-many records for a code: an explicit ``--name``, else the NRSA
    site table's (None when the code has no candidate sites)."""
    names = rrb._parse_kv(getattr(many, "name", None) or [], "--name")
    return names.get(code) or ra.region_name_for(code)


def _region_args(many: argparse.Namespace, tokens: list[str], root: Path, code: str, name: str) -> dict:
    ns = rrb.region_stage_namespace(many, code, name, rb.run_folder(root / camp.RUNS_DIR, code), argv=tokens)
    return vars(ns)


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def cmd_plan(a) -> int:
    root = Path(a.root).resolve()
    manifest_path = root / camp.MANIFEST_FILE
    if manifest_path.exists() and not a.force:
        return _refuse(f"{manifest_path} exists: the manifest is the frozen record of a campaign "
                       "(pass --force to plan the same root again)")
    if a.purpose not in camp.PURPOSES:
        return _refuse(f"--purpose {a.purpose!r} is not one of {', '.join(camp.PURPOSES)}")
    worktree = Path(a.worktree).resolve()
    app_root = _app_root_of(worktree)
    if not (app_root / "scripts" / "run_region_batch.py").is_file():
        return _refuse(f"{worktree} is not a STAF checkout (no apps/stream-curves/scripts/run_region_batch.py)")
    problem = camp.maintainer_label_problem(a.maintainer)
    if problem:
        return _refuse(problem)
    problem = _config_root_problem(a.config_root)
    if problem:
        return _refuse(problem)
    dirty = code_identity.git_dirty(worktree)
    if dirty is None:
        return _refuse(f"git cannot say whether {worktree} is clean; a campaign never starts from an unknown tree")
    if dirty:
        return _refuse(f"{worktree} has uncommitted changes; commit or stash them, then plan again")
    commit = code_identity.git_head(worktree)
    fingerprint = tree_fingerprint(app_root)
    running = rrb.code_fingerprint()
    if fingerprint != running:
        return _refuse(f"plan runs from {_APP_ROOT}, whose code fingerprint {camp.short(running)} is not "
                       f"{worktree}'s {camp.short(fingerprint)}; run plan from the worktree it describes")
    promotion = camp.load_promotion_policy()
    problems = camp.validate_promotion_policy(promotion)
    if problems:
        return _refuse("the promotion policy is not usable:", *problems)
    standing = dec.load_policy(None)
    if a.refit not in ra.REFIT_MODES:
        return _refuse(f"--refit {a.refit!r} is not one of {', '.join(ra.REFIT_MODES)}")

    crosswalk = camp.read_crosswalk(a.crosswalk or DEFAULT_CROSSWALK)
    codes = [r["l3"] for r in crosswalk]
    if a.l3:
        unknown = [c for c in a.l3 if str(c) not in codes]
        if unknown:
            return _refuse(f"--l3 names codes not in the crosswalk: {', '.join(unknown)}")
        codes = [str(c) for c in a.l3]
    names = {r["l3"]: r["name"] for r in crosswalk}
    census = camp.read_census(a.census)
    screen = camp.station_screen_summary(a.station_screen or DEFAULT_STATION_SCREEN, codes)
    seconds = camp.read_order_seconds(a.order_from) if a.order_from else None

    rows = []
    for code in codes:
        s = screen.get(code) or {}
        counts = camp.census_counts(census.get(code) or [])
        n_frame, n_strict = int(s.get("nFrame") or 0), int(s.get("nStrict") or 0)
        rows.append({"l3": code, "name": names.get(code), "l2": s.get("l2"), "l1": s.get("l1"),
                     "nars9": s.get("nars9"), "nStationsAll": s.get("nStationsAll"),
                     "nFrame": n_frame, "nStrict": n_strict, **counts,
                     "supportClass": camp.support_class(n_frame, n_strict),
                     "runFolder": f"{camp.RUNS_DIR}/{camp.run_folder_name(code)}"})
    ordered = camp.order_regions(rows, seconds=seconds)
    tokens = camp.stage_many_tokens(root, workers=a.workers, n_boot=a.n_boot, maintainer=a.maintainer,
                                    refit=a.refit, decisions_root=a.decisions_root,
                                    codes=[r["l3"] for r in ordered])
    many = _stage_many_namespace(tokens)
    decisions_root = many.decisions_root
    base_inputs = None
    stage_lines: dict[str, list[str]] = {}
    for r in ordered:
        code = r["l3"]
        r["stageName"] = _stage_name(many, code)
        r["decisionFiles"] = camp.decision_file_shas(decisions_root, code)
        if not r["stageName"]:
            r["inputsDigest"] = None
            r["carriedFrom"] = None
            continue
        args = _region_args(many, tokens, root, code, r["stageName"])
        if base_inputs is None:
            base_inputs = rrb.region_inputs(args)
            base_inputs.pop("reviewerFiles", None)
        reviewer = ra.reviewer_input_files(rrb.decision_paths(args),
                                           rrb.decisions_root_of(argparse.Namespace(**args), Path(args["out"])))
        carried = rrb.carried_for(args, code)
        r["carriedFrom"] = carried
        r["inputsDigest"] = rrb.region_digest(code, r["stageName"], {**base_inputs, "reviewerFiles": reviewer}, carried)
        # the decision files stage-many hands this region, so the single line records the same
        r["decisionFiles"] = camp.decision_file_shas(decisions_root, code)
        stage_lines[code] = camp.stage_tokens(root, r, n_boot=a.n_boot, maintainer=a.maintainer, refit=a.refit,
                                              decisions_root=decisions_root,
                                              decision_files={k: args.get(k) for k in rrb.DECISION_FILE_ATTRS})
    if base_inputs is None:
        base_inputs = {"flags": {k: getattr(many, k, None) for k in rrb._DIGEST_FLAGS},
                       "methodology": methodology.config_fingerprints(),
                       "policy": {"version": dec.policy_version(standing), "sha256": standing["meta"]["sha256"]},
                       "code": running}
    run = {"workers": int(a.workers), "nBoot": int(a.n_boot), "maintainer": a.maintainer, "refit": a.refit,
           "decisionsRoot": str(decisions_root) if decisions_root else None}
    if a.config_root:
        run["configRoot"] = str(Path(a.config_root).expanduser().resolve())
    sources = {"census": {"path": str(Path(a.census).resolve()), "sha256": camp.sha256_file(a.census)},
               "crosswalk": {"path": str(Path(a.crosswalk or DEFAULT_CROSSWALK).resolve()),
                             "sha256": camp.sha256_file(a.crosswalk or DEFAULT_CROSSWALK)},
               "stationScreen": {"path": str(Path(a.station_screen or DEFAULT_STATION_SCREEN).resolve()),
                                 "sha256": camp.sha256_file(a.station_screen or DEFAULT_STATION_SCREEN)},
               "orderFrom": ({"path": str(Path(a.order_from).resolve()), "sha256": camp.sha256_file(a.order_from)}
                             if a.order_from else None),
               "order": "fast-pass seconds descending" if a.order_from else "frame size descending"}
    manifest = camp.build_manifest(
        purpose=a.purpose, worktree=str(worktree), commit=commit, git_dirty=dirty,
        code_fingerprint=fingerprint, inputs=base_inputs, promotion_policy=promotion, run=run,
        gate_report=a.gate_report, sources=sources,
        commands={"stageMany": tokens, "stage": stage_lines,
                  "index": ["index", "--root", str(root)], "eligibility": ["eligibility", "--root", str(root)]},
        regions=ordered)
    root.mkdir(parents=True, exist_ok=True)
    (root / camp.RUNS_DIR).mkdir(exist_ok=True)
    camp.write_json(manifest_path, manifest)
    camp.write_text(root / camp.COMMANDS_FILE,
                    camp.commands_markdown(manifest, python=sys.executable, app_root=str(app_root), root=str(root)))
    camp.write_text(root / camp.STATE_FILE, camp.state_markdown(manifest, root=str(root), python=sys.executable))
    for src, name in ((promotion["meta"]["path"], camp.PROMOTION_POLICY_FILE),
                      (standing["meta"]["path"], camp.STANDING_POLICY_FILE)):
        (root / name).write_bytes(Path(src).read_bytes())
    ident = manifest["identity"]
    n_digest = sum(1 for r in ordered if r.get("inputsDigest"))
    print(f"[campaign] planned {ident['campaignId']} at commit {commit} ({len(ordered)} regions, "
          f"{n_digest} with an expected inputs digest, {len(ordered) - n_digest} without candidate sites)")
    print(f"[campaign] manifest -> {manifest_path}")
    print(f"[campaign] commands -> {root / camp.COMMANDS_FILE}")
    if a.gate_report and not Path(a.gate_report).is_file():
        print(f"[campaign] note: the gate report {a.gate_report} is not on disk yet; the equivalence gate reads it later")
    return 0


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def _template_region(manifest: dict) -> Optional[dict]:
    return next((r for r in manifest.get("regions") or [] if r.get("stageName")), None)


def live_values(manifest: dict, root: Path) -> dict:
    """Everything ``run`` checks against the manifest, computed from the live tree."""
    worktree = Path(manifest["identity"]["worktree"])
    app_root = _app_root_of(worktree)
    tokens = list((manifest.get("commands") or {}).get("stageMany") or [])
    many = _stage_many_namespace(tokens)
    live: dict = {"code": tree_fingerprint(app_root), "runningCode": rrb.code_fingerprint(),
                  "gitDirty": code_identity.git_dirty(worktree), "commit": code_identity.git_head(worktree)}
    template = _template_region(manifest)
    if template is not None:
        args = _region_args(many, tokens, root, str(template["l3"]), str(template["stageName"]))
        inputs = rrb.region_inputs(args)
        inputs.pop("reviewerFiles", None)
        live["inputs"] = inputs
    live["promotionPolicy"] = camp.load_promotion_policy()["meta"]["sha256"]
    live["copies"] = {name: camp.sha256_file(root / name)
                      for name in (camp.PROMOTION_POLICY_FILE, camp.STANDING_POLICY_FILE)}
    live["decisionFiles"] = {str(r["l3"]): camp.decision_file_shas(many.decisions_root, str(r["l3"]))
                             for r in manifest.get("regions") or []}
    commands = root / camp.COMMANDS_FILE
    line = camp.render_command(tokens)
    if commands.is_file():
        live["stageManyLine"] = line if line in commands.read_text(encoding="utf-8") else "(not in commands.md)"
    else:
        live["stageManyLine"] = "(commands.md is missing)"
    return live


def cmd_run(a) -> int:
    root = Path(a.root).resolve()
    try:
        manifest = camp.read_manifest(root)
    except ValueError as exc:
        return _refuse(str(exc))
    cfg = (manifest.get("run") or {}).get("configRoot")
    problem = _config_root_problem(cfg)
    if problem:
        return _refuse(problem)
    live = live_values(manifest, root)
    drift = camp.manifest_drift(manifest, live)
    if drift:
        return _refuse("the live tree differs from the frozen record:", *drift)
    app_root = _app_root_of(Path(manifest["identity"]["worktree"]))
    tokens = list((manifest.get("commands") or {}).get("stageMany") or [])
    argv = [sys.executable, "-B", str(app_root / "scripts" / "run_region_batch.py"), *tokens]
    ident = manifest["identity"]
    print(f"[campaign] {ident['campaignId']}: every recorded value matches the live tree at {ident['commit']}")
    print(f"[campaign] {camp.render_command(argv)}")
    if a.dry_run:
        print("[campaign] dry run: nothing started")
        return 0
    env = run_environment(os.environ, manifest)
    print(f"[campaign] starting stage-many from {app_root}"
          + (f" under STREAMCURVES_CONFIG_ROOT={env['STREAMCURVES_CONFIG_ROOT']}" if env.get("STREAMCURVES_CONFIG_ROOT") else ""),
          flush=True)
    proc = subprocess.run(argv, cwd=str(app_root), env=env)
    print(f"[campaign] stage-many exited {proc.returncode}; run index next")
    return int(proc.returncode)


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
def cmd_index(a) -> int:
    root = Path(a.root).resolve()
    try:
        manifest = camp.read_manifest(root)
    except ValueError as exc:
        return _refuse(str(exc))
    eligibility = camp.read_json(root / camp.ELIGIBILITY_FILE)
    eligibility = eligibility if isinstance(eligibility, dict) else None
    maintainer = str((manifest.get("run") or {}).get("maintainer") or camp.REHEARSAL_LABEL)
    rows = camp.index_rows(root, manifest, eligibility=eligibility,
                           promote_command=lambda run_dir: rrb.promote_command(run_dir, maintainer))
    runs_root = root / camp.RUNS_DIR
    batch_sha = camp.sha256_file(runs_root / "batch_summary.json")
    for row in rows:
        if row["state"] == "no-data":
            camp.write_json(runs_root / camp.run_folder_name(row["l3"]) / camp.REGION_STATE_FILE,
                            camp.region_state_document(row, batch_summary_sha=batch_sha))
    inputs = {"manifest.json": camp.sha256_file(root / camp.MANIFEST_FILE),
              "runs/batch_summary.json": batch_sha,
              "runs/.campaign/summary.json": camp.sha256_file(runs_root / ".campaign" / "summary.json"),
              "runs/.campaign/index.jsonl": camp.sha256_file(runs_root / ".campaign" / "index.jsonl"),
              "eligibility.json": camp.sha256_file(root / camp.ELIGIBILITY_FILE)}
    doc = camp.index_document(manifest, rows, inputs=inputs)
    camp.write_json(root / camp.INDEX_JSON, doc)
    camp.write_text(root / camp.INDEX_CSV, camp.index_csv_text(rows))
    counts = ", ".join(f"{k} {v}" for k, v in doc["counts"].items())
    print(f"[campaign] index: {len(rows)} regions ({counts}) -> {root / camp.INDEX_JSON}")
    return 0


# --------------------------------------------------------------------------- #
# eligibility
# --------------------------------------------------------------------------- #
def decisions_file_for(run_dir: Path) -> Path:
    """The curve decisions file a run's record names (``run_region_batch.decisions_file_of``
    over the staged provenance's recorded command), else the run folder's own."""
    vdir = camp.staged_version_dir(run_dir)
    man = ((camp.read_json(vdir / camp.PROVENANCE_FILE) or {}).get("manifest") if vdir else None) or {}
    argv = list((man.get("agent") or {}).get("argv") or [])
    return rrb.decisions_file_of(Path(run_dir), argv, man)


def _regions_of(manifest: dict, wanted) -> list[dict]:
    regions = list(manifest.get("regions") or [])
    if not wanted:
        return regions
    known = {str(r.get("l3")) for r in regions}
    unknown = [str(c) for c in wanted if str(c) not in known]
    if unknown:
        raise ValueError(f"--l3 names regions the manifest does not list: {', '.join(unknown)}")
    keep = {str(c) for c in wanted}
    return [r for r in regions if str(r.get("l3")) in keep]


def cmd_eligibility(a) -> int:
    root = Path(a.root).resolve()
    try:
        manifest = camp.read_manifest(root)
        regions = _regions_of(manifest, a.l3)
    except ValueError as exc:
        return _refuse(str(exc))
    policy = camp.load_promotion_policy()
    problems = camp.validate_promotion_policy(policy)
    if problems:
        return _refuse("the promotion policy is not usable:", *problems)
    recorded = ((manifest.get("inputs") or {}).get("promotionPolicy") or {}).get("sha256")
    if recorded != policy["meta"]["sha256"]:
        return _refuse(f"the promotion policy changed since plan (recorded {camp.short(recorded)}, now "
                       f"{camp.short(policy['meta']['sha256'])}); plan again or restore the file")
    out: dict[str, dict] = {}
    for r in regions:
        code = str(r["l3"])
        run_dir = root / camp.RUNS_DIR / camp.run_folder_name(code)
        expect = camp.expectation_from_manifest(manifest, code) or {}
        res = camp.evaluate_gates(run_dir, expect=expect, decisions_file=decisions_file_for(run_dir),
                                  policy=policy, gate_report=manifest.get("gateReport"))
        out[code] = res
        if res["eligible"]:
            print(f"[eligibility] L3-{code} {r.get('name')}: eligible")
        else:
            print(f"[eligibility] L3-{code} {r.get('name')}: not eligible ({len(res['blockers'])} blocker(s)): "
                  + " | ".join(res["blockers"]))
    doc = {"schema": camp.ELIGIBILITY_SCHEMA, "generatedAt": camp.now_iso(),
           "policy": camp.promotion_policy_record(policy),
           "campaignId": manifest["identity"]["campaignId"],
           "inputs": {"manifest.json": camp.sha256_file(root / camp.MANIFEST_FILE),
                      "promotion_policy.yaml": policy["meta"]["sha256"],
                      "gateReport": camp.sha256_file(manifest.get("gateReport")) if manifest.get("gateReport") else None},
           "regions": out}
    if a.l3:
        # a narrowed run keeps the other regions' earlier verdicts
        earlier = camp.read_json(root / camp.ELIGIBILITY_FILE)
        if isinstance(earlier, dict) and earlier.get("campaignId") == doc["campaignId"]:
            merged = dict(earlier.get("regions") or {})
            merged.update(out)
            doc["regions"] = merged
    camp.write_json(root / camp.ELIGIBILITY_FILE, doc)
    n_ok = sum(1 for v in out.values() if v["eligible"])
    print(f"[eligibility] {n_ok} of {len(out)} eligible under policy {policy['meta'].get('version')} "
          f"-> {root / camp.ELIGIBILITY_FILE}")
    return 0


# --------------------------------------------------------------------------- #
# batch-summary and confirm
# --------------------------------------------------------------------------- #
def cmd_batch_summary(a) -> int:
    root = Path(a.root).resolve()
    try:
        manifest = camp.read_manifest(root)
        regions = _regions_of(manifest, a.l3)
    except ValueError as exc:
        return _refuse(str(exc))
    index_doc = camp.read_json(root / camp.INDEX_JSON)
    eligibility_doc = camp.read_json(root / camp.ELIGIBILITY_FILE)
    if not isinstance(index_doc, dict) or not isinstance(eligibility_doc, dict):
        return _refuse("index.json and eligibility.json are needed first (run index and eligibility)")
    batch_id = str(a.batch).strip()
    if not batch_id or any(ch in batch_id for ch in "/\\ "):
        return _refuse("--batch needs a short id with no spaces or slashes")
    inputs = {"manifest.json": camp.sha256_file(root / camp.MANIFEST_FILE),
              "index.json": camp.sha256_file(root / camp.INDEX_JSON),
              "eligibility.json": camp.sha256_file(root / camp.ELIGIBILITY_FILE)}
    doc = camp.batch_summary_document(
        batch_id=batch_id, manifest=manifest, index_doc=index_doc, eligibility_doc=eligibility_doc,
        codes=[str(r["l3"]) for r in regions], python=sys.executable,
        script=str(_app_root_of(Path(manifest["identity"]["worktree"])) / "scripts" / "run_region_batch.py"),
        inputs=inputs, facts=camp.region_batch_facts)
    jp = camp.write_json(root / f"promotion_batch_{batch_id}.json", doc)
    mp = camp.write_text(root / f"promotion_batch_{batch_id}.md", camp.batch_summary_markdown(doc))
    print(f"[batch] {len(doc['eligible'])} eligible, {len(doc['exceptions'])} exception(s) of "
          f"{len(doc['regions'])} region(s) -> {mp} (and {jp.name}); no confirmation is written")
    return 0


def cmd_confirm(a) -> int:
    root = Path(a.root).resolve()
    try:
        manifest = camp.read_manifest(root)
    except ValueError as exc:
        return _refuse(str(exc))
    batch_id = str(a.batch).strip()
    batch = camp.read_json(root / f"promotion_batch_{batch_id}.json")
    if not isinstance(batch, dict):
        return _refuse(f"no batch summary promotion_batch_{batch_id}.json in {root}; run batch-summary first")
    confirmation = camp.read_json(a.confirmation)
    if confirmation is None:
        return _refuse(f"{a.confirmation} is not readable JSON")
    problems = camp.validate_confirmation(confirmation, batch=batch, manifest=manifest)
    if problems:
        return _refuse(f"the confirmation {a.confirmation} does not authorize batch {batch_id}:", *problems)
    script = str(_app_root_of(Path(manifest["identity"]["worktree"])) / "scripts" / "run_region_batch.py")
    commands = camp.confirmed_promote_commands(batch, confirmation, python=sys.executable, script=script)
    print(f"[confirm] batch {batch_id} of {manifest['identity']['campaignId']}: {len(commands)} region(s) "
          f"confirmed by {confirmation.get('confirmedBy')} on {confirmation.get('confirmedAt')}; run in this "
          "order from a checkout at the campaign's commit with STAF_LIBRARY_PUBLISH=1:")
    for line in commands:
        print(line)
    return 0


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #
def _owner_diff_markdown(code: str, aid: str, items: list[dict], counts: dict) -> str:
    lines = [f"# Owner decisions of {aid} against the campaign's staged version (L3-{code})", "",
             f"{counts.get('agree', 0)} agree, {counts.get('differ', 0)} differ, "
             f"{counts.get('not_applicable', 0)} not applicable. The published version is the record; the "
             "staged version was built without its owner decisions as inputs unless the manifest says so.", ""]
    lines += camp._md_table(["rule", "subject", "recorded", "re-derived", "verdict", "detail"],
                            [[it.get("rule"), it.get("subject"),
                              json.dumps(it.get("recorded") or {}, sort_keys=True, default=str),
                              json.dumps(it.get("rederived") or {}, sort_keys=True, default=str),
                              cmp.VERDICT_WORDS.get(it.get("verdict"), it.get("verdict")), it.get("detail")]
                             for it in items])
    anchors = [(it, an) for it in items for an in it.get("anchors") or []]
    if anchors:
        lines += ["", "## Band edges that moved", ""]
        lines += camp._md_table(["rule", "subject", "band", "published", "staged", "shift"],
                                [[it.get("rule"), it.get("subject"), an.get("band"), an.get("a"), an.get("b"),
                                  f"{an['deltaIqr']:+.2f} IQR" if an.get("deltaIqr") is not None else ""]
                                 for it, an in anchors])
    lines.append("")
    return "\n".join(lines)


def cmd_compare(a) -> int:
    root = Path(a.root).resolve()
    try:
        manifest = camp.read_manifest(root)
    except ValueError as exc:
        return _refuse(str(exc))
    library = Path(a.library).resolve() if a.library else ra.CANONICAL_LIBRARY
    out_dir = root / camp.COMPARE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in manifest.get("regions") or []:
        code = str(r["l3"])
        run_dir = root / camp.RUNS_DIR / camp.run_folder_name(code)
        vdir = camp.staged_version_dir(run_dir)
        row = {"l3": code, "name": r.get("name"), "assessmentId": None, "publishedVersion": None,
               "stagedVersion": None, "publishedDigest": None, "stagedDigest": None, "digestEqual": None,
               "curvesIdentical": None, "curvesDiffer": None, "onlyPublished": None, "onlyStaged": None,
               "decisionsSame": None, "decisionsDiffer": None, "ownerAgree": None, "ownerDiffer": None,
               "ownerNotApplicable": None, "report": None, "ownerDiff": None, "note": None}
        if vdir is None:
            row["note"] = "not staged"
            rows.append(row)
            continue
        found = cf.find_published(code, root=library)
        if found is None:
            row["note"] = "no published version"
            rows.append(row)
            continue
        aid, ver = found
        pub = library / "assessments" / aid / f"v{ver}"
        rep = cmp.compare(pub, vdir)
        pub_bundle, pub_doc = cmp.load_version(pub)
        st_bundle, st_doc = cmp.load_version(vdir)
        doc = {"schema": camp.COMPARE_SCHEMA, "campaignId": manifest["identity"]["campaignId"], "l3": code,
               "published": {"assessmentId": aid, "version": ver, "path": str(pub),
                             "contentDigest": pub_bundle.get("contentDigest")},
               "staged": {"path": str(vdir), "contentDigest": st_bundle.get("contentDigest")},
               "inputs": {f"published/{name}": camp.sha256_file(pub / name) for name in (cmp.BUNDLE_FILE, cmp.PROVENANCE_FILE)}
               | {f"staged/{name}": camp.sha256_file(vdir / name) for name in (cmp.BUNDLE_FILE, cmp.PROVENANCE_FILE)},
               "report": rep}
        report_path = camp.write_json(out_dir / f"{aid}.json", doc)
        row.update(assessmentId=aid, publishedVersion=ver, stagedVersion=vdir.name,
                   publishedDigest=pub_bundle.get("contentDigest"), stagedDigest=st_bundle.get("contentDigest"),
                   digestEqual=(pub_bundle.get("contentDigest") == st_bundle.get("contentDigest")),
                   curvesIdentical=len(rep["curves"]["identical"]), curvesDiffer=len(rep["curves"]["differ"]),
                   onlyPublished=len(rep["curves"]["only_a"]), onlyStaged=len(rep["curves"]["only_b"]),
                   decisionsSame=rep["decisions"]["same"], decisionsDiffer=len(rep["decisions"]["differ"]),
                   report=str(report_path))
        diff_fn = getattr(cmp, "differences_from_owner_decisions", None)
        if diff_fn is not None:
            items = diff_fn(pub_doc, st_doc, recorded_bundle=pub_bundle, rederived_bundle=st_bundle)
            if items:
                counts = cmp.owner_decision_counts(items)
                camp.write_json(out_dir / f"{aid}.owner_decision_diff.json",
                                {"schema": camp.COMPARE_SCHEMA, "l3": code, "assessmentId": aid,
                                 "publishedVersion": ver, "items": items, "counts": counts})
                md = camp.write_text(out_dir / f"{aid}.owner_decision_diff.md",
                                     _owner_diff_markdown(code, aid, items, counts))
                row.update(ownerAgree=counts.get("agree", 0), ownerDiffer=counts.get("differ", 0),
                           ownerNotApplicable=counts.get("not_applicable", 0), ownerDiff=str(md))
        rows.append(row)
        print(f"[compare] L3-{code} {aid} v{ver} vs staged: curves {row['curvesIdentical']} identical, "
              f"{row['curvesDiffer']} differ; decisions {row['decisionsSame']} same, {row['decisionsDiffer']} differ"
              + (f"; owner decisions {row['ownerAgree']} agree, {row['ownerDiffer']} differ" if row["ownerDiff"] else ""))
    columns = ["l3", "name", "assessmentId", "publishedVersion", "stagedVersion", "publishedDigest", "stagedDigest",
               "digestEqual", "curvesIdentical", "curvesDiffer", "onlyPublished", "onlyStaged", "decisionsSame",
               "decisionsDiffer", "ownerAgree", "ownerDiffer", "ownerNotApplicable", "report", "ownerDiff", "note"]
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for row in rows:
        w.writerow([camp._cell(row.get(c)) for c in columns])
    camp.write_text(out_dir / "summary.csv", buf.getvalue())
    camp.write_json(out_dir / "summary.json", {"schema": camp.COMPARE_SCHEMA,
                                               "campaignId": manifest["identity"]["campaignId"],
                                               "library": str(library), "rows": rows})
    n = sum(1 for r in rows if r.get("assessmentId"))
    print(f"[compare] {n} region(s) compared against {library} -> {out_dir / 'summary.csv'}")
    return 0


# --------------------------------------------------------------------------- #
# package
# --------------------------------------------------------------------------- #
def cmd_package(a) -> int:
    root = Path(a.root).resolve()
    try:
        manifest = camp.read_manifest(root)
    except ValueError as exc:
        return _refuse(str(exc))
    notes = Path(a.notes).resolve() if a.notes else None
    if notes is not None and not notes.is_dir():
        return _refuse(f"--notes {notes} is not a folder")
    zip_path, doc = camp.build_package(root, Path(a.out).resolve(), manifest, notes=notes)
    print(f"[package] {len(doc['members'])} member(s), {len(doc['downloads'])} evidence archive(s) to download "
          f"by digest -> {zip_path} ({camp.short(camp.sha256_file(zip_path))})")
    return 0


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="freeze a campaign root: manifest, commands, state, policy copies")
    p.add_argument("--root", required=True, help="the campaign root (national/<campaign-id> or an arm)")
    p.add_argument("--purpose", required=True, choices=list(camp.PURPOSES))
    p.add_argument("--worktree", required=True, help="the checkout the campaign runs from (this one)")
    p.add_argument("--census", required=True, help="reference_support_census.csv of the census over every code")
    p.add_argument("--crosswalk", default=None, help=f"default {DEFAULT_CROSSWALK}")
    p.add_argument("--station-screen", default=None, help=f"default {DEFAULT_STATION_SCREEN}")
    p.add_argument("--n-boot", type=int, required=True)
    p.add_argument("--workers", type=int, required=True)
    p.add_argument("--maintainer", required=True,
                   help=f"the label every stage records: {camp.REHEARSAL_LABEL!r} or {camp.POLICY_CANDIDATE_LABEL!r}")
    p.add_argument("--refit", required=True, choices=list(ra.REFIT_MODES))
    p.add_argument("--decisions-root", default=None, metavar="FOLDER",
                   help="where each region's decision files are read from (stage-many's default otherwise)")
    p.add_argument("--config-root", default=None, metavar="DIR",
                   help="an experimental configuration root (the process must run under STREAMCURVES_CONFIG_ROOT=DIR)")
    p.add_argument("--gate-report", default=None, help="<gate root>/report.json of the equivalence gate at this commit")
    p.add_argument("--order-from", default=None, metavar="INDEX_CSV",
                   help="order the regions by an earlier campaign's seconds (the fast pass) instead of frame size")
    p.add_argument("--l3", action="append", default=[], metavar="CODE", help="narrow the campaign to these codes")
    p.add_argument("--force", action="store_true", help="plan again into a root that has a manifest")
    p.set_defaults(fn=cmd_plan)

    r = sub.add_parser("run", help="check the live tree against the manifest, then run the stage-many line")
    r.add_argument("--root", required=True)
    r.add_argument("--dry-run", action="store_true", help="print the checks and the line; start nothing")
    r.set_defaults(fn=cmd_run)

    i = sub.add_parser("index", help="index.json and index.csv from the run folders' artifacts")
    i.add_argument("--root", required=True)
    i.set_defaults(fn=cmd_index)

    e = sub.add_parser("eligibility", help="every promotion-policy gate per region, from artifacts only")
    e.add_argument("--root", required=True)
    e.add_argument("--l3", action="append", default=[], metavar="CODE")
    e.set_defaults(fn=cmd_eligibility)

    b = sub.add_parser("batch-summary", help="promotion_batch_<id>.md and .json for the owner")
    b.add_argument("--root", required=True)
    b.add_argument("--batch", required=True, metavar="ID")
    b.add_argument("--l3", action="append", default=[], metavar="CODE", help="the batch's regions, in order")
    b.set_defaults(fn=cmd_batch_summary)

    c = sub.add_parser("confirm", help="validate a hand-written confirmation and print the promote commands")
    c.add_argument("--root", required=True)
    c.add_argument("--batch", required=True, metavar="ID")
    c.add_argument("--confirmation", required=True, metavar="FILE",
                   help="promotion_batch_<id>.confirmation.json, written by hand")
    c.set_defaults(fn=cmd_confirm)

    k = sub.add_parser("compare", help="each staged region against its published version")
    k.add_argument("--root", required=True)
    k.add_argument("--library", default=None, help="the library to compare against (default apps/library)")
    k.set_defaults(fn=cmd_compare)

    z = sub.add_parser("package", help="a zip of the record with package.json inside")
    z.add_argument("--root", required=True)
    z.add_argument("--out", required=True, help="the packages folder")
    z.add_argument("--notes", default=None, help="a notes folder to carry along")
    z.set_defaults(fn=cmd_package)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
