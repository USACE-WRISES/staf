"""Batch mode for a new ecoregion: one unattended run to a staged version plus an
end-review packet, then a zero-recompute promote after the owner's review.

    stage    run the evidence pass once, apply the standing-decision policy to
             the review queue (re-assembling the decision-dependent tail until
             the queue stops changing), publish into the STAGED library root
             under <out>/library as a draft, and write review_packet.md.
    promote  after the end review: confirm the staged decisions under the
             owner's name (with any recorded overrides), verify nothing drifted,
             and publish the same content into the canonical apps/library —
             as a Draft by default (the curves still await an in-app review;
             approve them from the Validate page or pass --status preliminary
             for a packet that was reviewed exhaustively).
    replay   apply the policy to published versions offline and report whether
             it reproduces their recorded decisions.
    stage-many
             stage several Level III codes in sequence with the same flags
             (names from the NRSA site table), one run folder each under
             --out-root, and write batch_summary.md; never promotes.

A stage refuses when the screen left more than --max-unresolved-share of the
candidates unresolved (a service outage shrinks the pool without excluding
anyone on the criteria); --allow-unresolved stages anyway on the record. The
StreamCat join is cached per run (streamcat_cache.json), so a re-stage reads
it back and the evidence pass reproduces offline.

Usage (from the repo root, shared venv):
    .venv/Scripts/python apps/stream-curves/scripts/run_region_batch.py stage \
        --l3 71 --name "Interior Plateau" --out notes/DEEP_Working/analysis/runs/ip-71 \
        --n-boot 1000 --maintainer gtmenichino
    .venv/Scripts/python apps/stream-curves/scripts/run_region_batch.py promote \
        --out notes/DEEP_Working/analysis/runs/ip-71 --maintainer gtmenichino \
        --publish-root apps/library --rebake-deep

A staged version can never reach the canonical library by accident: its
decisions carry the reviewer "standing-policy:<id> (pending owner confirmation)"
and library.publish_version refuses that marker on a canonical publish.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from streamcurves import carry_forward as cf  # noqa: E402
from streamcurves import decisions as dec  # noqa: E402
from streamcurves import library as lib  # noqa: E402
from streamcurves import methodology  # noqa: E402
from streamcurves import nrsa_dataset  # noqa: E402
from streamcurves import provenance as pv  # noqa: E402
from streamcurves import reference_screen as rscreen  # noqa: E402
from streamcurves import regional_agent as ra  # noqa: E402
from streamcurves import review_packet as rp  # noqa: E402
from streamcurves import run_state  # noqa: E402
from streamcurves import session_io as sio  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(o):
    for attr in ("item", "tolist"):
        fn = getattr(o, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                pass
    return str(o)


def _parse_kv(specs, flag, sep="="):
    out = {}
    for spec in specs or []:
        k, _, v = str(spec).partition(sep)
        if not k or not v:
            raise SystemExit(f"{flag} needs KEY{sep}VALUE, got {spec!r}")
        out[k.strip()] = v.strip()
    return out


def _valid_approver(approver: str) -> bool:
    """An approver is a name (one token, e.g. ``gtmenichino``) or a pending
    marker (``owner-draft (pending owner confirmation)``); prose in this slot
    means the NOTE was passed without an approver, which once put a rationale
    into a published meta as the approving person."""
    approver = str(approver or "").strip()
    if not approver:
        return False
    return (" " not in approver) or approver.endswith(dec.PENDING_SUFFIX)


def _parse_approvals(specs):
    out = []
    for spec in specs or []:
        fid, _, rest = str(spec).partition("=")
        approver, _, note = rest.partition(":")
        if not fid or not approver:
            raise SystemExit(f"--approve-portfolio needs FUNCTIONID=APPROVER[:NOTE], got {spec!r}")
        if not _valid_approver(approver):
            raise SystemExit(
                f"--approve-portfolio {fid.strip()}: the approver {approver.strip()!r} reads as "
                f"prose; use FUNCTIONID=APPROVER:NOTE with a name or a pending marker as APPROVER")
        out.append({"functionId": fid.strip(), "approvedBy": approver.strip(),
                    "note": note.strip() or None})
    return out


def _confirm_approvals(meta: dict, *, maintainer: str, date: str) -> list[dict]:
    """Rewrite pending portfolio approvals to the confirming owner and refuse
    any approval that does not resolve to that owner: a canonical version
    carries one approving person, the one who said go."""
    approvals = meta.get("portfolioApprovals") or []
    for ap in approvals:
        if dec.PENDING_SUFFIX in str(ap.get("approvedBy") or ""):
            ap["approvedBy"] = maintainer
            ap["confirmedAt"] = date
    strangers = [str(ap.get("functionId")) for ap in approvals
                 if str(ap.get("approvedBy") or "").strip() != maintainer]
    if strangers:
        raise SystemExit(
            "portfolio approvals not confirmed by the promoting owner on: "
            f"{', '.join(strangers)}. Re-stage with --approve-portfolio FUNCTIONID=APPROVER:NOTE "
            "(a name or a pending marker as APPROVER).")
    return approvals


def _confirm_coverage_exceptions(bundle: dict, session: dict, doc: dict, *,
                                 maintainer: str, date: str) -> int:
    """COV-01: a documented gap the standing decision recorded names its pending
    reviewer; the owner's confirmation at promote puts the owner's name there,
    in the bundle (which DEEP reads), the session and the provenance. The
    function coverage block sits outside the content digest, so the published
    digest still equals the staged one. Returns how many were confirmed."""
    n = 0

    def fix(entries):
        nonlocal n
        for e in entries or []:
            if isinstance(e, dict) and dec.PENDING_SUFFIX in str(e.get("recordedBy") or ""):
                e["recordedBy"] = maintainer
                e["recordedAt"] = date
                e["confirmedBy"] = maintainer
                n += 1

    fix(((bundle.get("functionCoverage") or {}).get("exclusions")))
    fields = session.get("fields") if isinstance(session.get("fields"), dict) else session
    fix(fields.get("function_coverage_exceptions"))
    for key in ("coverage",):
        block = (doc.get("manifest") or {}).get(key)
        if isinstance(block, dict):
            fix(block.get("exclusions"))
    return n


def unresolved_share(counts: dict) -> Optional[float]:
    """The share of screened candidates the screen never resolved (None when
    nothing was screened)."""
    n = int((counts or {}).get("n_screened") or 0)
    if n <= 0:
        return None
    return int((counts or {}).get("n_unresolved") or 0) / n


def unresolved_check(counts: dict, *, max_share: float, allow: bool) -> tuple[Optional[str], Optional[str]]:
    """``("refuse" | "warn" | None, message)``. A screen that left candidates
    unresolved (a service outage, a failed or cancelled assessment) shrinks the
    reference pool without excluding anyone on the criteria; beyond the share
    allowed the stage refuses rather than staging a pool smaller than the
    region's data, unless the owner accepts that on the record."""
    share = unresolved_share(counts)
    if share is None or share <= max_share:
        return None, None
    msg = (f"{counts.get('n_unresolved')} of {counts.get('n_screened')} candidates unresolved by the "
           f"screen ({share:.0%}, above the {max_share:.0%} allowed): the pool is smaller than the "
           "region's data. Re-run when the services are up, or pass --allow-unresolved to stage "
           "anyway on the record.")
    return ("warn" if allow else "refuse"), msg


def _staged_root(out_dir: Path) -> Path:
    root = (out_dir / "library").resolve()
    if root == ra.CANONICAL_LIBRARY:
        raise SystemExit("stage refuses to write the canonical library; its staged root is "
                         "always <out>/library")
    return root


def _latest_staged_version(root: Path, slug: str) -> tuple[int, Path]:
    manifest = root / "assessments" / slug / "manifest.json"
    if not manifest.is_file():
        raise SystemExit(f"no staged assessment at {manifest.parent}")
    m = json.loads(manifest.read_text(encoding="utf-8"))
    v = int(m.get("latestVersion") or 0)
    return v, manifest.parent / f"v{v}"


def promote_command(out_dir: Path, maintainer: str) -> str:
    return (f"{sys.executable} {Path(__file__).resolve()} promote --out {out_dir} "
            f"--maintainer {maintainer} --publish-root apps/library --rebake-deep")


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #
def _frame_max_order(choice: str):
    """The largest stream order the reference panel accepts. ``all`` keeps every
    stream (and adds no digest key); ``wadeable`` reads the governed value, so
    the frame lives in methodology_config, not in the CLI."""
    return nrsa_dataset.governed_frame(choice)[0]


def _frame_protocols(choice: str):
    """The NRSA sampling protocols that stand in when a station's stream order
    cannot be resolved."""
    return nrsa_dataset.governed_frame(choice)[1]


def _engine_config(a) -> Optional[dict]:
    """Engine overrides from the CLI (None when nothing was asked), recorded by
    the site-engine report so the manifest and the packet say what the values
    were computed under."""
    cfg = {}
    if getattr(a, "engine_snap_tolerance_ft", None) is not None:
        cfg["snapTolFt"] = float(a.engine_snap_tolerance_ft)
    if getattr(a, "engine_max_reaches", None) is not None:
        cfg["maxReaches"] = int(a.engine_max_reaches)
    if getattr(a, "engine_max_hops", None) is not None:
        cfg["maxHops"] = int(a.engine_max_hops)
    return cfg or None


def cmd_stage(a) -> int:
    out_dir = Path(a.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    staged_root = _staged_root(out_dir)
    policy = dec.load_policy(a.policy)
    problems = dec.validate_policy(policy)
    if problems:
        print("standing-decision policy is not usable:")
        for p in problems:
            print(f"  - {p}")
        return 1
    enabled = list(a.enable_policy or [])
    dec.enabled_entries(policy, enabled)  # raises on an unknown id
    started = _now()
    # A direct attribute read, like the dataset below: a Namespace that forgets
    # the method must fail loudly, not run the other one.
    try:
        reference_method = rscreen.resolve_reference_method(a.reference_method, a.nrsa_dataset)
    except ValueError as exc:
        print(f"[batch] {exc}")
        return 2
    pressure = reference_method == run_state.REFERENCE_METHOD_PRESSURE
    print(f"[batch] L3-{a.l3} ({a.name}); reference method {reference_method}; "
          + ("" if pressure else f"screen={a.screen} no_screen={a.no_screen}; ")
          + f"policy {dec.policy_version(policy)} enabled+={enabled or 'none'}")

    coverage_exceptions = None
    if a.coverage_exceptions:
        coverage_exceptions = json.loads(Path(a.coverage_exceptions).read_text(encoding="utf-8"))
    owner_decisions = []
    if a.reviewer_decisions:
        owner_decisions = json.loads(Path(a.reviewer_decisions).read_text(encoding="utf-8"))
    owner_finalize = _parse_kv(a.finalize_metric, "--finalize-metric")
    owner_remove = _parse_kv(a.remove_metric, "--remove-metric")
    owner_approvals = _parse_approvals(a.approve_portfolio)

    # 1. the expensive pass, once
    evidence = ra.run_evidence(
        a.l3, a.name, screen_preset=a.screen, do_screen=not a.no_screen,
        use_streamcat=not a.no_streamcat, cache_dir=out_dir,
        diagnostics_n_boot=a.n_boot,
        # Direct attribute reads, deliberately: a getattr fallback here once let a
        # hand-built Namespace (stage-many's) drop the dataset flag and silently
        # run legacy data. A missing attribute must fail loudly.
        nrsa_dataset_id=a.nrsa_dataset,
        nrsa_cycles=a.nrsa_cycles,
        nrsa_max_stream_order=_frame_max_order(getattr(a, "reference_frame", "wadeable")),
        nrsa_protocols=_frame_protocols(getattr(a, "reference_frame", "wadeable")),
        nrsa_keep_sites=_parse_kv(getattr(a, "include_site", None) or [],
                                  "--include-site") or None,
        predictor_source=a.predictor_source,
        screen_retries=a.screen_retries, screen_retry_wait=a.screen_retry_wait,
        engine_config=_engine_config(a),
        exclude_sites=_parse_kv(a.exclude_site, "--exclude-site") or None,
        reference_method=reference_method,
        on_event=ra.event_narrator())
    print(f"[batch] evidence: {evidence['n_retained']} / {evidence['n_candidates']} retained "
          f"(tier {evidence['tier']['reference_tier']}, pool {evidence['reference_pool_disposition']}), "
          f"{len(evidence['curve_rows'])} curves built")
    if pressure:
        support = evidence.get("reference_support") or {}
        by_status: dict[str, int] = {}
        for d in support.values():
            by_status[str(d.get("status"))] = by_status.get(str(d.get("status")), 0) + 1
        print("[batch] reference support: "
              + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
              + f"; {len(evidence.get('fixed_metrics') or {})} fixed-criteria metrics")
        for mk in sorted(evidence.get("insufficient_support") or {}):
            print(f"[batch]   withheld (REF-06): {mk}")
        not_evaluable = (evidence.get("screening_counts") or {}).get("n_unresolved") or 0
        if not_evaluable:
            print(f"[batch]   {not_evaluable} station(s) could not be screened (no station-table "
                  "row or a missing screen variable); they are never reference")
    if evidence.get("nrsa_max_stream_order") is not None:
        summary = evidence.get("nrsa_panel_summary") or {}
        by_order = summary.get("byStreamOrder") or {}
        print(f"[batch] panel: {evidence['n_candidates']} candidates in frame "
              f"(stream order 1 to {evidence['nrsa_max_stream_order']}), "
              f"{evidence.get('nrsa_n_out_of_frame') or 0} out of frame"
              + (f"; by order " + ", ".join(f"{k}:{v}" for k, v in sorted(by_order.items()))
                 if by_order else ""))
        for o in (evidence.get("nrsa_frame_overrides") or []):
            print(f"[batch] frame override: {o.get('station_key')} "
                  f"({o.get('out_of_frame_reason')}): {o.get('reason')}")
    if evidence.get("screening_comid_mode"):
        counts = ((evidence.get("screening_comids") or {}).get("counts") or {})
        cache = evidence.get("screening_cache") or {}
        print(f"[batch] screen: comid mode {evidence['screening_comid_mode']}, "
              + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
              + (f"; cache ignored: {cache['ignored']}" if cache.get("ignored")
                 else "; cache reused" if cache.get("from_cache") else ""))
    for e in (evidence.get("owner_site_exclusions") or []):
        print(f"[batch] owner exclusion: {e.get('site_id')} "
              f"({'was retained' if e.get('was_retained') else 'not in the pool'}): {e.get('reason')}")
    for rep in (evidence.get("source_reports") or []):
        if (rep or {}).get("source") == "site_engine":
            print(f"[batch] site engine: {rep.get('n_ok', 0)}/{rep.get('n_sites', 0)} ok "
                  f"({rep.get('n_cached', 0)} from cache), recomputed "
                  f"{', '.join(rep.get('resourced_metrics') or []) or 'none'}")
    # The unresolved-share gate guards a LIVE screen against a service outage
    # that leaves candidates without a verdict. The pressure screen reads a
    # committed table, so a station without a verdict there is a fact of the
    # data (reported above), not an outage to wait out.
    level, msg = (None, None) if pressure else unresolved_check(
        evidence.get("screening_counts") or {},
        max_share=a.max_unresolved_share, allow=a.allow_unresolved)
    if level == "refuse":
        print(f"[batch] REFUSED: {msg}")
        return 2
    if level == "warn":
        print(f"[batch] WARNING (accepted with --allow-unresolved): {msg}")
    bad_sources = [r for r in (evidence.get("source_reports") or [])
                   if str(r.get("status")) not in ("ok", "skipped")]
    if bad_sources:
        for rep in bad_sources:
            print(f"[batch] FAILED source {rep.get('source')}: {rep.get('reason')}")
            for f in (rep.get("failed_sites") or []):
                print(f"[batch]   {f.get('site_id')} {f.get('status')}: {f.get('reason')}")
            for f in (rep.get("incomplete_sites") or []):
                print(f"[batch]   {f.get('site_id')} incomplete: {f.get('reason')}")
        print("[batch] a landscape source did not join, so its functions are uncovered. "
              "Re-run when the service is up; a batch run never accepts that gap.")
        return 2

    # 2. assemble, apply the policy, and repeat until the queue stops changing
    policy_decisions: list[dict] = []
    policy_finalize: dict[str, str] = {}
    approvals: list[dict] = list(owner_approvals)
    # COV-01: documented gaps the standing decision records, pending the owner
    policy_exceptions: list[dict] = []
    result = doc = None
    pr = None
    for it in range(1, a.max_iterations + 1):
        finalize = {**policy_finalize, **owner_finalize}
        actor = a.maintainer if owner_finalize or owner_remove else dec.pending_reviewer(
            "curve07-thin-metric-finalized", policy)
        owner_fids = {str(x.get("functionId")) for x in coverage_exceptions or []}
        merged_exceptions = (list(coverage_exceptions or [])
                             + [x for x in policy_exceptions
                                if str(x.get("functionId")) not in owner_fids]) or None
        result = ra.assemble(
            evidence, source_citation=a.source_citation,
            coverage_exceptions=merged_exceptions,
            finalize_metrics=finalize or None,
            finalize_actor=actor if finalize or owner_remove else "",
            remove_metrics=owner_remove or None,
            reviewer_decisions=(owner_decisions + policy_decisions) or None)
        result["standing_decisions"] = {
            "policyVersion": dec.policy_version(policy),
            "sha256": policy["meta"]["sha256"],
            "path": policy["meta"]["path"],
            "enabledIds": enabled,
            "appliedIds": sorted({d["decision_class"] for d in policy_decisions}),
            "appliedCount": len(policy_decisions),
            "confirmedBy": None, "confirmedAt": None,
        }
        manifest = pv.build_run_manifest(result, argv=list(sys.argv[1:]),
                                         started_at=started, finished_at=_now())
        doc = pv.build_provenance(result, manifest, timestamp=started)
        if owner_decisions or policy_decisions:
            doc = pv.apply_reviewer_decisions(doc, owner_decisions + policy_decisions,
                                              default_reviewer=a.maintainer, default_date=started)
        pr = dec.apply_policy(doc, policy, result=result, enabled=enabled, date=started)
        have = {(d["rule_id"], d["subject"]) for d in policy_decisions}
        new = [d for d in pr.decisions if (d["rule_id"], d["subject"]) not in have]
        new_finalize = {k: v for k, v in pr.finalize_metrics.items() if k not in policy_finalize}
        known_fids = {x["functionId"] for x in approvals}
        new_approvals = [x for x in pr.portfolio_approvals if x["functionId"] not in known_fids]
        known_gaps = {str(x.get("functionId")) for x in policy_exceptions} | {
            str(x.get("functionId")) for x in coverage_exceptions or []}
        new_gaps = [x for x in pr.coverage_exceptions
                    if str(x.get("functionId")) not in known_gaps]
        print(f"[batch] pass {it}: queue open {doc['reviewQueue']['counts']['open']}, "
              f"policy decided {len(new)} new item(s), {len(pr.uncovered)} left open")
        if not new and not new_finalize and not new_approvals and not new_gaps:
            break
        policy_decisions.extend(new)
        policy_finalize.update(new_finalize)
        approvals.extend(new_approvals)
        policy_exceptions.extend(new_gaps)
    else:
        print(f"[batch] the queue did not settle within {a.max_iterations} passes; "
              "stop and inspect the run folder")
        return 1

    # 3. the assessment as a project file, before any gate has an opinion
    #
    # publish_version is the only other writer of a session, and it runs after the
    # coverage and portfolio gates, so a refused publish used to throw away a payload
    # that was already complete: the Driftless Area run left twenty reports, an empty
    # library folder and nothing anyone could open. session_fields() reads nothing the
    # gates guard, so writing it here costs a file and makes every run reviewable.
    try:
        session_payload = sio.dump_session_fields(
            ra.session_fields(result),
            session_name=(result.get("meta") or {}).get("assessmentName") or a.name)
        (out_dir / "assessment.streamcurves.json").write_text(
            sio.dumps_session(session_payload), encoding="utf-8")
        print("[batch] assessment -> assessment.streamcurves.json")
    except Exception as exc:  # noqa: BLE001 - a report is still worth writing
        print(f"[batch] could not write the session file: {exc}")

    # A SELECT-01 approval carries with its unchanged metric set (methodology
    # 0.14, owner decision 2026-09-21): a rebuild keeps the owner's decision for a
    # function whose metrics are all carried unchanged, and any change to the set
    # needs a new approval. An approval passed for this build wins.
    carried_ok = cf.carried_approvals(
        evidence.get("carried_approvals") or [], result.get("bundle"),
        result.get("carried") or {}, result.get("fixed_metrics") or {},
        have=[str(x.get("functionId")) for x in approvals],
        from_version=(evidence.get("carried_from") or {}).get("fromVersion"))
    if carried_ok:
        approvals.extend(carried_ok)
        print(f"[batch] {len(carried_ok)} portfolio approval(s) carried with an unchanged "
              f"metric set: {', '.join(x['functionId'] for x in carried_ok)}")

    # 4. the staged publish
    publish_info = None
    if result.get("bundle") is not None:
        try:
            publish_info = ra.publish(result, staged_root, maintainer=a.maintainer,
                                      provenance=doc, portfolio_approvals=approvals,
                                      status="draft")
            print(f"[batch] staged v{publish_info['version']} (draft) -> {publish_info['path']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[batch] staged publish refused: {exc}")
    else:
        print(f"[batch] no bundle to stage: {result.get('bundle_error')}")

    # 5. the run folder outputs, the packet, the gallery, the promote command
    from run_regional_analysis import write_outputs  # noqa: E402 (sibling script)
    write_outputs(result, out_dir, publish_info, doc)
    (out_dir / "standing_decisions_applied.json").write_text(
        json.dumps({"policy": policy["meta"], "enabled": enabled,
                    "decisions": policy_decisions, "finalize_metrics": policy_finalize,
                    "portfolio_approvals": approvals, "coverage_exceptions": policy_exceptions,
                    "open_items": pr.uncovered,
                    "hard_stops": pr.hard_stops}, indent=1, default=_json_default) + "\n",
        encoding="utf-8")
    shutil.copy2(policy["meta"]["path"], out_dir / "standing_decisions.applied.yaml")
    prior = None
    try:
        os.environ.pop("STAF_LIBRARY_ROOT", None)
        slug = lib.slugify(result["assessment_id"])
        if lib.read_manifest(slug) and not lib.library_root().resolve() == staged_root:
            prior = lib.load_version_bundle(slug, lib.latest_version(slug))
    except Exception:  # noqa: BLE001
        prior = None
    gallery = rp.write_curve_gallery(result, out_dir / "curve_gallery.png")
    gallery_html = rp.write_curve_gallery_html(result, out_dir / "curve_gallery.html")
    cmd = promote_command(out_dir, a.maintainer)
    # The packet carries every decision the passes made (the last pass alone
    # decides nothing new once the queue has settled) and the items still open.
    policy_summary = {
        "decisions": policy_decisions, "uncovered": pr.uncovered, "hard_stops": pr.hard_stops,
        "finalize_metrics": policy_finalize, "portfolio_approvals": approvals,
        "applied_ids": sorted({d["decision_class"] for d in policy_decisions}),
    }
    packet = rp.build_packet(
        result, doc, policy_summary, policy_meta=policy["meta"], enabled=enabled,
        staged=publish_info, promote_command=cmd, prior_bundle=prior,
        gallery=gallery.name if gallery else None, approvals=approvals,
        gallery_html=gallery_html.name if gallery_html else None)
    jp, mp = rp.write_packet(packet, out_dir)
    (out_dir / "promote_command.txt").write_text(cmd + "\n", encoding="utf-8")
    print(f"[batch] packet -> {mp}")
    print(f"[batch] {len(policy_decisions)} standing decision(s) applied, "
          f"{len(pr.uncovered)} item(s) open for the owner, "
          f"{len(pr.hard_stops)} hard stop(s)")
    return 0


# --------------------------------------------------------------------------- #
# promote
# --------------------------------------------------------------------------- #
# Confirmation itself lives in decisions.confirm_pending_decisions, the one
# implementation the in-app publish shares. The script keeps the CLI concerns:
# SystemExit instead of ValueError. The names below stay as aliases.
SCOPE_CHANGING = dec.SCOPE_CHANGING
PENDING_ORIGIN_SUFFIX = dec.PENDING_ORIGIN_SUFFIX


def _confirm_doc(doc: dict, *, reviewer: str, date: str, overrides: dict) -> tuple[dict, list[str]]:
    try:
        return dec.confirm_pending_decisions(doc, reviewer=reviewer, date=date,
                                             overrides=overrides)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def cmd_promote(a) -> int:
    out_dir = Path(a.out).resolve()
    staged_root = _staged_root(out_dir)
    packet = json.loads((out_dir / "review_packet.json").read_text(encoding="utf-8"))
    slug = lib.slugify((packet.get("region") or {}).get("name") or "")
    staged = packet.get("staged") or {}
    if staged.get("path"):
        vdir = Path(staged["path"])
        version = int(staged.get("version") or 0)
    else:
        version, vdir = _latest_staged_version(staged_root, slug)
    if not vdir.is_dir():
        raise SystemExit(f"staged version folder missing: {vdir}")
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    session = json.loads((vdir / lib.SESSION_FILE).read_text(encoding="utf-8"))
    meta = json.loads((vdir / lib.META_FILE).read_text(encoding="utf-8"))
    doc = json.loads((vdir / lib.PROVENANCE_FILE).read_text(encoding="utf-8"))
    slug = meta.get("assessmentId") or slug

    # nothing may have drifted since the stage: methodology, catalog, policy
    man = doc.get("manifest") or {}
    now_fp = methodology.config_fingerprints()
    then = man.get("methodology") or {}
    drift = [k for k in ("methodology_version", "config_sha256", "rule_catalog_sha256")
             if then.get(k) != now_fp.get(k)]
    sd = man.get("standingDecisions") or {}
    policy_now = dec.load_policy(a.policy)
    if sd.get("sha256") and sd["sha256"] != policy_now["meta"]["sha256"]:
        drift.append("standing_decisions")
    if drift:
        raise SystemExit("the staged version was produced under a different "
                         f"{', '.join(drift)}; re-stage with: {promote_command(out_dir, a.maintainer)}"
                         .replace(" promote ", " stage "))

    date = a.date or _now()
    overrides = {}
    for spec in a.override or []:
        item, _, rest = str(spec).partition("=")
        action, _, rationale = rest.partition(":")
        if not item or action not in dec.ALLOWED_ACTIONS or not rationale.strip():
            raise SystemExit(f"--override needs ITEM=ACTION:RATIONALE with a known action, got {spec!r}")
        overrides[item.strip()] = (action.strip(), rationale.strip())
    doc, applied = _confirm_doc(doc, reviewer=a.maintainer, date=date, overrides=overrides)
    _confirm_coverage_exceptions(bundle, session, doc, maintainer=a.maintainer, date=date)
    if dec.is_pending(doc):
        raise SystemExit("a pending-confirmation marker survived confirmation; refusing to publish")
    # DEEP reads the bundle, so no pending marker may reach it; in the session only
    # the coverage exceptions COV-01 wrote are checked, because a session has
    # always kept the marker on the review actors of the curves it finalized
    fields = session.get("fields") if isinstance(session.get("fields"), dict) else session
    if dec.PENDING_SUFFIX in json.dumps(bundle) or dec.PENDING_SUFFIX in json.dumps(
            fields.get("function_coverage_exceptions") or []):
        raise SystemExit("a pending-confirmation marker survived in the bundle or the coverage "
                         "exceptions; refusing to publish")
    _confirm_approvals(meta, maintainer=a.maintainer, date=date)

    publish_root = Path(a.publish_root).resolve()
    os.environ["STAF_LIBRARY_ROOT"] = str(publish_root)
    if publish_root == ra.CANONICAL_LIBRARY:
        reason = lib.publish_gate_reason(a.maintainer)
        if reason:
            raise SystemExit(f"canonical publish blocked: {reason}")
        os.environ.setdefault("STAF_LIBRARY_MAINTAINER", a.maintainer)
    pub_meta = {k: meta.get(k) for k in ("assessmentName", "region", "stateCode", "stateName",
                                         "sourceCitation", "author", "revisionNotes")}
    if meta.get("portfolioApprovals"):
        pub_meta["portfolioApprovals"] = meta["portfolioApprovals"]
    staged_digest = bundle.get("contentDigest")
    for k in ("library", "contentDigest"):
        bundle.pop(k, None)
    doc.pop("version", None)
    doc.pop("updatedAt", None)
    doc.pop("contentDigest", None)
    new_version = lib.publish_version(slug, pub_meta, session, bundle, provenance=doc,
                                      status=a.status)
    published = lib.load_version_bundle(slug, new_version)
    digest_ok = published.get("contentDigest") == staged_digest
    record = {"stagedVersion": version, "stagedPath": str(vdir), "publishedVersion": new_version,
              "publishedRoot": str(publish_root), "status": a.status,
              "confirmedBy": a.maintainer,
              "confirmedAt": date, "overrides": applied,
              "contentDigest": published.get("contentDigest"),
              "contentDigestMatchesStaged": digest_ok}
    (out_dir / "promote_record.json").write_text(json.dumps(record, indent=1) + "\n",
                                                 encoding="utf-8")
    print(f"[promote] published {slug} v{new_version} as {a.status} -> {publish_root} "
          f"(content digest {'unchanged' if digest_ok else 'DIFFERS'} from the staged version, "
          f"{len(applied)} override(s), confirmed by {a.maintainer})")
    if not digest_ok:
        return 1
    if a.rebake_deep:
        ok, msg = lib.rebake_deep()
        print(f"[promote] rebake DEEP: {'ok' if ok else 'FAILED'} - {msg}")
        if not ok:
            return 1
    return 0


# --------------------------------------------------------------------------- #
# stage-many
# --------------------------------------------------------------------------- #
SUMMARY_COLUMNS = ["l3", "name", "exit", "candidates", "retained", "tier", "curves", "decisions",
                   "open_items", "hard_stops", "staged_version", "seconds", "out", "error"]


def write_batch_summary(rows: list[dict], out_root: Path | str) -> tuple[Path, Path]:
    """``batch_summary.json`` and ``batch_summary.md`` for a multi-region run."""
    out = Path(out_root)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / "batch_summary.json"
    mp = out / "batch_summary.md"
    jp.write_text(json.dumps({"schemaVersion": 1, "regions": rows}, indent=1, default=str) + "\n",
                  encoding="utf-8")
    lines = ["# Batch summary", "",
             f"{len(rows)} region(s), {sum(1 for r in rows if r.get('exit') == 0)} staged, "
             f"{sum(1 for r in rows if r.get('exit') != 0)} not staged. Nothing is promoted by this "
             "command; each staged region carries its own review packet and promote command.", "",
             "| " + " | ".join(SUMMARY_COLUMNS) + " |", "|" + "---|" * len(SUMMARY_COLUMNS)]
    for r in rows:
        lines.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c)).replace("|", "/")
                                       for c in SUMMARY_COLUMNS) + " |")
    lines.append("")
    mp.write_text("\n".join(lines), encoding="utf-8")
    return jp, mp


def cmd_stage_many(a) -> int:
    """Stage several regions one after another with the same flags, one run
    folder each under ``--out-root``, and a summary table. Never promotes."""
    out_root = Path(a.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    names = _parse_kv(a.name, "--name")
    rows: list[dict] = []
    for code in a.l3:
        code = str(code).strip()
        name = names.get(code) or ra.region_name_for(code)
        slug = lib.slugify(name) if name else f"l3-{code}"
        out_dir = out_root / f"l3-{code}-{slug}"
        row: dict = {"l3": code, "name": name, "out": str(out_dir), "exit": None, "error": None}
        t0 = time.monotonic()
        if not name:
            row.update(exit=1, error=f"no NRSA candidate sites for L3 ecoregion {code}")
        else:
            ns = argparse.Namespace(
                l3=code, name=name, out=str(out_dir), screen=a.screen, source_citation="",
                no_screen=a.no_screen, no_streamcat=a.no_streamcat, maintainer=a.maintainer,
                n_boot=a.n_boot, coverage_exceptions=a.coverage_exceptions, policy=a.policy,
                enable_policy=list(a.enable_policy or []), max_iterations=a.max_iterations,
                # SELECT-01 approvals apply to every region staged in one call:
                # a function that carries three metrics carries them everywhere,
                # and without this stage-many could never stage such a region.
                approve_portfolio=list(a.approve_portfolio or []),
                reviewer_decisions=None, finalize_metric=[], remove_metric=[],
                max_unresolved_share=a.max_unresolved_share, allow_unresolved=a.allow_unresolved,
                nrsa_dataset=a.nrsa_dataset, nrsa_cycles=a.nrsa_cycles,
                reference_frame=a.reference_frame, include_site=[],
                screen_retries=a.screen_retries, screen_retry_wait=a.screen_retry_wait,
                engine_snap_tolerance_ft=a.engine_snap_tolerance_ft,
                engine_max_reaches=a.engine_max_reaches, engine_max_hops=a.engine_max_hops,
                exclude_site=[],
                reference_method=a.reference_method,
                predictor_source=a.predictor_source)
            try:
                row["exit"] = int(cmd_stage(ns))
            except SystemExit as exc:
                row.update(exit=exc.code if isinstance(exc.code, int) else 1, error=str(exc))
            except Exception as exc:  # noqa: BLE001 - one region's failure must not end the batch
                row.update(exit=1, error=f"{type(exc).__name__}: {exc}")
        row["seconds"] = round(time.monotonic() - t0, 1)
        packet_path = out_dir / "review_packet.json"
        if packet_path.is_file():
            p = json.loads(packet_path.read_text(encoding="utf-8"))
            scr = p.get("screening") or {}
            row.update(candidates=scr.get("n_candidates"), retained=scr.get("n_retained"),
                       tier=p.get("reference_tier"), curves=len(p.get("curves") or []),
                       decisions=len(p.get("decisions_applied") or []),
                       open_items=len(p.get("open_items") or []),
                       hard_stops=len(p.get("hard_stops") or []),
                       staged_version=(p.get("staged") or {}).get("version"))
        rows.append(row)
        print(f"[batch-many] L3-{code} {name or '?'}: exit {row['exit']}"
              + (f" ({row['error']})" if row.get("error") else ""))
    jp, mp = write_batch_summary(rows, out_root)
    print(f"[batch-many] summary -> {mp}")
    return 0 if all(r.get("exit") == 0 for r in rows) else 1


# --------------------------------------------------------------------------- #
# census
# --------------------------------------------------------------------------- #
REFERENCE_METHOD_HELP = (
    "how reference condition is defined. pressure-screen (methodology 0.12, the default on "
    "the pooled archive) reads the fixed landscape-pressure screen from the committed "
    "station table, borrows comparable stations from the Level II and then the Level I "
    "ecoregion where the region has too few, withholds a metric no pool supports, and "
    "scores pressure metrics on fixed criteria. easi-eci is the legacy ECI gate (the "
    "default with --nrsa-dataset legacy-1819), which is what a replay of a published "
    "version needs")


def cmd_census(a) -> int:
    """Reference support per region and metric (REF-04 to REF-06), before any
    build. Offline and quick: it reads the committed station table and the
    NRSA archive, fits nothing, and writes a table the owner reviews before
    staging."""
    from streamcurves import pressure_evidence as pe
    out_dir = Path(a.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_choice = getattr(a, "reference_frame", "wadeable")
    result = pe.census(
        [str(c).strip() for c in a.l3],
        max_stream_order=_frame_max_order(frame_choice),
        protocols=_frame_protocols(frame_choice),
        keep_stations=_parse_kv(getattr(a, "include_site", None) or [], "--include-site") or None,
        excluded=_parse_kv(getattr(a, "exclude_site", None) or [], "--exclude-site") or None)
    csv_path = out_dir / "reference_support_census.csv"
    md_path = out_dir / "reference_support_census.md"
    result["table"].to_csv(csv_path, index=False)
    md_path.write_text(pe.census_markdown(result), encoding="utf-8")
    for r in result["regions"]:
        print(f"[census] L3-{r['l3']} {r['region']}: {r['n_strict']} of {r['n_frame']} stations "
              f"pass the screen; curves local {r['n_local']}, Level II {r['n_borrowed_l2']}, "
              f"Level I {r['n_borrowed_l1']}, withheld {r['n_insufficient']}")
    if not result.get("registry_present"):
        print("[census] no national scale registry: every borrowed pool's transfer risk is "
              "unassessed (run scripts/run_national_scale_analysis.py)")
    print(f"[census] table -> {csv_path}")
    print(f"[census] page  -> {md_path}")
    return 0


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #
def cmd_replay(a) -> int:
    argv = ["replay"]
    for v in a.version_dir:
        argv += ["--version-dir", v]
    if a.policy:
        argv += ["--policy", a.policy]
    for e in a.enable_policy or []:
        argv += ["--enable-policy", e]
    if a.json:
        argv += ["--json", a.json]
    return dec.main(argv)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stage", help="evidence pass + standing decisions + staged publish + packet")
    s.add_argument("--l3", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--screen", default="functional", choices=["functional", "at_risk_or_better"])
    s.add_argument("--source-citation", default="")
    s.add_argument("--no-screen", action="store_true", help="offline smoke only")
    s.add_argument("--no-streamcat", action="store_true", help="offline smoke only")
    s.add_argument("--maintainer", default="gtmenichino")
    s.add_argument("--n-boot", type=int, default=1000)
    s.add_argument("--coverage-exceptions", default=None)
    s.add_argument("--policy", default=None, help="standing_decisions.yaml (default: the config one)")
    s.add_argument("--enable-policy", action="append", default=[], metavar="ID",
                   help="enable a policy entry that is off by default, on the record")
    s.add_argument("--max-iterations", type=int, default=3)
    s.add_argument("--approve-portfolio", action="append", default=[])
    s.add_argument("--reviewer-decisions", default=None)
    s.add_argument("--finalize-metric", action="append", default=[])
    s.add_argument("--remove-metric", action="append", default=[])
    s.add_argument("--nrsa-dataset", default=nrsa_dataset.default_build_dataset_id(),
                   choices=nrsa_dataset.available_datasets(),
                   help="which NRSA data to read; the default is the pooled multi-cycle "
                        "archive when this checkout has built it; pass legacy-1819 to "
                        "reproduce the published assessments' inputs")
    s.add_argument("--nrsa-cycle", action="append", dest="nrsa_cycles",
                   choices=list(nrsa_dataset.CYCLES_NEWEST_FIRST),
                   help="repeatable; limit a pooled run to these survey cycles")
    s.add_argument("--include-site", action="append", default=[], metavar="SITE_ID=REASON",
                   help="repeatable; readmit one station the reference frame would "
                        "exclude (a large river the owner wants in the pool). The "
                        "reason is recorded on the run, joins the inputs digest like "
                        "an --exclude-site does, and appears in the review packet")
    s.add_argument("--reference-frame", default="wadeable", choices=("wadeable", "all"),
                   help="the reference frame the candidate panel draws from. "
                        "wadeable (the default) is NHDPlus V2 stream order 1 to 5, "
                        "the governed reference_panel.max_stream_order, with the NRSA "
                        "sampling protocol deciding only where a station's order "
                        "cannot be resolved. all keeps every stream, which is what "
                        "the versions published before methodology 0.10 ran")
    s.add_argument("--predictor-source", default="streamcat",
                   choices=("streamcat", "site-engine"),
                   help="which engine computes the curve predictors: streamcat is "
                        "the StreamCat lookup engine (default); site-engine is the "
                        "STAF site engine, which recomputes them at the training "
                        "sites (usually under a minute per uncached site, up to about five on a large basin) and stamps the "
                        "bundle predictorSource")
    s.add_argument("--screen-retries", type=int, default=2,
                   help="re-screen the candidates a transient failure left unresolved "
                        "(a snap service outage) up to this many passes, merging each "
                        "pass into the screening cache. Zero is a single pass")
    s.add_argument("--screen-retry-wait", type=float, default=60.0,
                   help="seconds to wait before each retry pass")
    s.add_argument("--engine-snap-tolerance-ft", type=float, default=None,
                   help="STAF site engine only: how far (ft) a training point may sit "
                        "from the nearest NHDPlus HR flowline (the engine default is 150). "
                        "Recorded in the manifest and the packet")
    s.add_argument("--engine-max-reaches", type=int, default=None,
                   help="STAF site engine only: the reach budget of one watershed walk "
                        "(the engine default is 5000). Recorded like the snap tolerance")
    s.add_argument("--engine-max-hops", type=int, default=None,
                   help="STAF site engine only: the hop budget of one watershed walk "
                        "(the engine default is 200). Recorded like the snap tolerance")
    s.add_argument("--exclude-site", action="append", default=[], metavar="SITE_ID=REASON",
                   help="drop a retained site from the pool on the record (an owner "
                        "decision, e.g. a basin the engine cannot value). Repeatable. "
                        "Marked on the screening table, recorded in the manifest, the "
                        "digest, and the packet")
    s.add_argument("--max-unresolved-share", type=float, default=0.10,
                   help="refuse to stage when more than this share of candidates is unresolved by the screen")
    s.add_argument("--allow-unresolved", action="store_true",
                   help="stage anyway on the record when the unresolved share is above the limit")
    s.add_argument("--reference-method", default=None, choices=run_state.REFERENCE_METHODS,
                   help=REFERENCE_METHOD_HELP)
    s.set_defaults(fn=cmd_stage)

    m = sub.add_parser("stage-many", help="stage several regions in sequence with a summary table; never promotes")
    m.add_argument("--l3", action="append", required=True, metavar="CODE",
                   help="an EPA Level III code (repeat); the name comes from the NRSA site table")
    m.add_argument("--name", action="append", default=[], metavar="CODE=NAME",
                   help="override the region name for a code")
    m.add_argument("--out-root", required=True)
    m.add_argument("--screen", default="functional", choices=["functional", "at_risk_or_better"])
    m.add_argument("--no-screen", action="store_true", help="offline smoke only")
    m.add_argument("--no-streamcat", action="store_true", help="offline smoke only")
    m.add_argument("--maintainer", default="gtmenichino")
    m.add_argument("--n-boot", type=int, default=1000)
    m.add_argument("--coverage-exceptions", default=None)
    m.add_argument("--policy", default=None)
    m.add_argument("--enable-policy", action="append", default=[], metavar="ID")
    m.add_argument("--approve-portfolio", action="append", default=[],
                   metavar="FUNCTIONID=APPROVER[:NOTE]",
                   help="SELECT-01 approval applied to every region in this call (see stage)")
    m.add_argument("--max-iterations", type=int, default=3)
    m.add_argument("--nrsa-dataset", default=nrsa_dataset.default_build_dataset_id(),
                   choices=nrsa_dataset.available_datasets(),
                   help="which NRSA data to read; the default is the pooled multi-cycle "
                        "archive when this checkout has built it; pass legacy-1819 to "
                        "reproduce the published assessments' inputs")
    m.add_argument("--nrsa-cycle", action="append", dest="nrsa_cycles",
                   choices=list(nrsa_dataset.CYCLES_NEWEST_FIRST),
                   help="repeatable; limit a pooled run to these survey cycles")
    m.add_argument("--reference-frame", default="wadeable", choices=("wadeable", "all"),
                   help="the reference frame the candidate panel draws from. "
                        "wadeable (the default) is NHDPlus V2 stream order 1 to 5, "
                        "the governed reference_panel.max_stream_order, with the NRSA "
                        "sampling protocol deciding only where a station's order "
                        "cannot be resolved. all keeps every stream, which is what "
                        "the versions published before methodology 0.10 ran")
    m.add_argument("--predictor-source", default="streamcat",
                   choices=("streamcat", "site-engine"),
                   help="which engine computes the curve predictors (see stage)")
    m.add_argument("--screen-retries", type=int, default=2)
    m.add_argument("--screen-retry-wait", type=float, default=60.0)
    m.add_argument("--engine-snap-tolerance-ft", type=float, default=None)
    m.add_argument("--engine-max-reaches", type=int, default=None)
    m.add_argument("--engine-max-hops", type=int, default=None)
    m.add_argument("--max-unresolved-share", type=float, default=0.10)
    m.add_argument("--allow-unresolved", action="store_true")
    m.add_argument("--reference-method", default=None, choices=run_state.REFERENCE_METHODS,
                   help=REFERENCE_METHOD_HELP)
    m.set_defaults(fn=cmd_stage_many)

    c = sub.add_parser("census", help="reference support per region and metric, before any build "
                                      "(pressure-screen method; seconds, offline)")
    c.add_argument("--l3", action="append", required=True, metavar="CODE",
                   help="an EPA Level III code (repeat)")
    c.add_argument("--out", required=True, help="folder for reference_support_census.csv/.md")
    c.add_argument("--reference-frame", default="wadeable", choices=("wadeable", "all"))
    c.add_argument("--exclude-site", action="append", default=[], metavar="SITE_ID=REASON")
    c.add_argument("--include-site", action="append", default=[], metavar="SITE_ID=REASON")
    c.set_defaults(fn=cmd_census)

    p = sub.add_parser("promote", help="confirm the staged decisions and publish canonically")
    p.add_argument("--out", required=True)
    p.add_argument("--maintainer", required=True)
    p.add_argument("--publish-root", default="apps/library")
    p.add_argument("--policy", default=None)
    p.add_argument("--override", action="append", default=[], metavar="ITEM=ACTION:RATIONALE")
    p.add_argument("--date", default=None)
    p.add_argument("--rebake-deep", action="store_true")
    p.add_argument("--status", choices=("draft", "preliminary"), default="draft",
                   help="lifecycle status for the promoted version: draft (default; the "
                        "decisions are confirmed but no one reviewed the curves in the "
                        "app) or preliminary (the packet was reviewed exhaustively)")
    p.set_defaults(fn=cmd_promote)

    r = sub.add_parser("replay", help="apply the policy to published versions offline")
    r.add_argument("--version-dir", action="append", required=True)
    r.add_argument("--policy", default=None)
    r.add_argument("--enable-policy", action="append", default=[])
    r.add_argument("--json", default=None)
    r.set_defaults(fn=cmd_replay)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
