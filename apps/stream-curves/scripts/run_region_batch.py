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
             stage several Level III codes with the same flags (names from the
             NRSA site table), one run folder l3-<code> each under --out-root,
             and write batch_summary.md; never promotes. --workers N stages N
             regions at once, each in its own process with its own caches and
             thread caps; a region whose stage_complete.json names the same
             inputs (flags, methodology, data, decision files, code) is not
             staged again, so an interrupted batch resumes by running the same
             command. Each region's own decision files (curve_decisions.json,
             owner_decisions.json, coverage_exceptions.json,
             candidate_register.json under <--decisions-root>/l3-<code>/) are
             build inputs, recorded by sha in the manifest.
    verify   re-stage a staged or published version from its recorded command,
             decision files and value policy into a temp root and report whether
             its contentDigest and inputsDigest come back equal.
    open     print a staged run's project path and the candidate=<key> deep link
             that opens one candidate of its register in StreamCurves.

--refit missing (the default, methodology reference_hierarchy.carry_forward)
carries every published curve forward and builds the rest; --refit all builds
every curve afresh and never reads the canonical library, while the owner's
holds and forced sources (REF-15) still apply. Every new manifest declares
digest schema 2, names the value policy the archive was read under
(--value-policy), the decision files it read (reviewerInputs.files) and, when
STREAMCURVES_CONFIG_ROOT names another configuration root or the REF-15
extension flag is on, an experimental label that keeps the version out of
the canonical library (promote and library.publish_version both refuse it).

A stage refuses when the screen left more than --max-unresolved-share of the
candidates unresolved (a service outage shrinks the pool without excluding
anyone on the criteria); --allow-unresolved stages anyway on the record. The
StreamCat join is cached per run (streamcat_cache.json), so a re-stage reads
it back and the evidence pass reproduces offline.

Usage (from the repo root, shared venv):
    .venv/Scripts/python apps/stream-curves/scripts/run_region_batch.py stage \
        --l3 71 --name "Interior Plateau" --out notes/DEEP_Working/analysis/runs/ip-71 \
        --n-boot 1000 --maintainer GM
    .venv/Scripts/python apps/stream-curves/scripts/run_region_batch.py promote \
        --out notes/DEEP_Working/analysis/runs/ip-71 --maintainer GM \
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
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from streamcurves import easi_env as _easi_env  # noqa: E402

_easi_env.sanitize()
from streamcurves import candidates as C  # noqa: E402
from streamcurves import carry_forward as cf  # noqa: E402
from streamcurves import deep_evidence  # noqa: E402
from streamcurves import owner_curves as oc  # noqa: E402
from streamcurves import decisions as dec  # noqa: E402
from streamcurves import library as lib  # noqa: E402
from streamcurves import methodology  # noqa: E402
from streamcurves import nrsa_dataset  # noqa: E402
from streamcurves import provenance as pv  # noqa: E402
from streamcurves import reference_screen as rscreen  # noqa: E402
from streamcurves import region_build as rb  # noqa: E402
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
    """An approver is a name (one token, e.g. ``GM``) or a pending
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


#: Earlier names of the promoting maintainer, comma separated: approvals recorded before
#: StreamCurves recorded initials name the owner's login (the owner's decision of 2026-09-24).
#: Set in the maintainer's own environment (a launch configuration), never in code.
ALIASES_ENV = "STAF_LIBRARY_MAINTAINER_ALIASES"


def maintainer_names(maintainer: str) -> set[str]:
    """The promoting maintainer's name and the earlier names ``ALIASES_ENV`` lists."""
    names = {str(maintainer or "").strip()}
    names |= {n.strip() for n in os.environ.get(ALIASES_ENV, "").split(",")}
    names.discard("")
    return names


def _confirm_approvals(meta: dict, *, maintainer: str, date: str) -> list[dict]:
    """Rewrite pending portfolio approvals to the confirming owner and refuse
    any approval that does not resolve to that owner: a canonical version
    carries one approving person, the one who said go. An approval recorded under
    an earlier name of the owner (``ALIASES_ENV``) is the owner's and keeps the
    name it was recorded under."""
    approvals = meta.get("portfolioApprovals") or []
    dec.confirm_approvals(approvals, maintainer=maintainer, date=date)
    mine = maintainer_names(maintainer)
    strangers = [str(ap.get("functionId")) for ap in approvals
                 if str(ap.get("approvedBy") or "").strip() not in mine]
    if strangers:
        raise SystemExit(
            "portfolio approvals not confirmed by the promoting owner on: "
            f"{', '.join(strangers)}. Re-stage with --approve-portfolio FUNCTIONID=APPROVER:NOTE "
            "(a name or a pending marker as APPROVER), or list the name they were recorded under "
            f"in {ALIASES_ENV} when it is an earlier name of yours.")
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
        n += dec.confirm_exceptions(entries, maintainer=maintainer, date=date)

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


#: The answers to a CURVE-07 item ("Accept this curve as preliminary, adjust it, or
#: drop the metric?") that decide the curve (2026-09-22).
CURVE07_PUBLISH = ("accept", "accept_with_conditions")
CURVE07_DROP = ("reject",)


def curve07_answers(decisions, *, fitted) -> tuple[dict, dict]:
    """``(finalize, remove)``, each ``{metric: rationale}``, from the owner's
    answers to CURVE-07 items. An answer used to close the item and nothing more,
    so the curve stayed held and unscored whatever the answer said. Accepting now
    publishes the curve as preliminary, exactly as ``--finalize-metric`` does, and
    rejecting drops the metric, as ``--remove-metric`` does. ``fitted`` is what
    this build reviews: an answer for a metric it no longer fits changes nothing."""
    fitted = {str(k) for k in fitted or ()}
    finalize: dict[str, str] = {}
    remove: dict[str, str] = {}
    for d in decisions or []:
        mk = str(d.get("subject"))
        if str(d.get("rule_id")) != "CURVE-07" or mk not in fitted:
            continue
        action = str(d.get("action") or "").strip()
        note = str(d.get("rationale") or "").strip()
        if action in CURVE07_PUBLISH:
            finalize[mk] = note
        elif action in CURVE07_DROP:
            remove[mk] = note
    return finalize, remove


def curve07_resolutions(curve_review, finalize, remove, answered, *, reviewer: str,
                        date: Optional[str] = None) -> list[dict]:
    """The reviewer decision that closes the CURVE-07 item of each flagged curve
    the owner finalized or removed without answering the item itself
    (``--finalize-metric``, ``--remove-metric``). The finalization or the removal
    is the decision, so the curve is no longer listed as a hard stop, and its
    record names who decided and why."""
    have = {(str(d.get("rule_id")), str(d.get("subject"))) for d in answered or []}
    decided = ([(mk, note, "accept_with_conditions") for mk, note in (finalize or {}).items()]
               + [(mk, note, "reject") for mk, note in (remove or {}).items()])
    out = []
    for mk, note, action in decided:
        status = ((curve_review or {}).get(mk) or {}).get("status")
        if ("CURVE-07", str(mk)) in have or status in (None, run_state.CURVE_STATUS_AUTO_OK):
            continue
        out.append({"rule_id": "CURVE-07", "subject": str(mk), "action": action,
                    "rationale": str(note or "").strip(), "reviewer": reviewer, "date": date,
                    "rationale_origin": "owner_written"})
    return out


def _without_outcome_asserts(decisions: list[dict]) -> list[dict]:
    """A CURVE-07 answer never asserts the curve's review decision: the answer sets
    it, so the build it feeds records a different value from the run it was written
    against, and the consistency check would refuse the whole run."""
    for d in decisions or []:
        if str(d.get("rule_id")) == "CURVE-07" and isinstance(d.get("asserts"), dict):
            d["asserts"] = {k: v for k, v in d["asserts"].items() if k != "reviewer_decision"}
    return decisions


#: The exit code of a stage that did not start because another run holds its folder.
BUSY_EXIT = 3


def stage_locked(a) -> int:
    """``stage``: cmd_stage with the region folder's lock (``jobs.acquire``) held for the whole
    stage, so two runs never stage one folder at once (a stage first deletes the folder's staged
    library). Another run holding it: :data:`BUSY_EXIT`, with the sentence saying which. A stage
    run this way records no ``stage_complete.json`` and removes an older one, so the record in a
    folder always describes its last stage."""
    from streamcurves import jobs as jb
    out_dir = Path(a.out).resolve()
    try:
        held = jb.acquire(out_dir)
    except jb.CampaignBusy as exc:
        print(f"[batch] {exc}")
        return BUSY_EXIT
    try:
        (out_dir / STAGE_COMPLETE).unlink(missing_ok=True)
        return cmd_stage(a)
    finally:
        jb.release(held)


def cmd_stage(a) -> int:
    out_dir = Path(a.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    staged_root = _staged_root(out_dir)
    if staged_root.exists():
        # a stage replaces what an earlier stage of this folder left: its staged publish
        # would otherwise number after the old one (a staged v2 beside a stale v1)
        print(f"[batch] replacing the earlier staged library at {staged_root}")
        shutil.rmtree(staged_root)
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
    # The refit mode (H1) and the value policy (DATA-11) are direct reads too: a
    # namespace that forgets them fails here, not in the evidence pass.
    try:
        mode = ra.refit_mode(a.refit)
    except ValueError as exc:
        print(f"[batch] {exc}")
        return 2
    value_policy = a.value_policy
    print(f"[batch] L3-{a.l3} ({a.name}); reference method {reference_method}; "
          + ("" if pressure else f"screen={a.screen} no_screen={a.no_screen}; ")
          + f"policy {dec.policy_version(policy)} enabled+={enabled or 'none'}; "
          f"refit {mode}" + (f"; value policy {value_policy}" if value_policy else ""))

    # The batch's documented gaps, with the region's own file (stage-many's
    # --decisions-root, one per function) merged over them.
    coverage_exceptions = None
    if a.coverage_exceptions:
        coverage_exceptions = json.loads(Path(a.coverage_exceptions).read_text(encoding="utf-8"))
    region_gaps_file = getattr(a, "region_coverage_exceptions", None)
    if region_gaps_file:
        region_gaps = json.loads(Path(region_gaps_file).read_text(encoding="utf-8"))
        region_fids = {str(g.get("functionId")) for g in region_gaps or []}
        coverage_exceptions = [x for x in coverage_exceptions or []
                               if str(x.get("functionId")) not in region_fids] + list(region_gaps or [])
    owner_decisions = []
    if a.reviewer_decisions:
        owner_decisions = json.loads(Path(a.reviewer_decisions).read_text(encoding="utf-8"))
    owner_finalize = _parse_kv(a.finalize_metric, "--finalize-metric")
    owner_remove = _parse_kv(a.remove_metric, "--remove-metric")
    # REF-15: the owner's standing decisions on the region's curves (the Region
    # builder passes the region's curve_decisions.json), and the documented gaps
    # the ones that empty a function carry
    curve_decisions = []
    if a.curve_decisions:
        loaded = json.loads(Path(a.curve_decisions).read_text(encoding="utf-8"))
        curve_decisions = list((loaded.get("decisions") if isinstance(loaded, dict)
                                else loaded) or [])
    # "Your choice stands" (owner decision 2026-09-22): a curve the published version
    # scores under an owner decision is never carried forward, so the standing record
    # this build reads has to hold that decision, or the metric would silently walk
    # the hierarchy again. Checked before the expensive pass, and only where the
    # published version is an input (a full refit never reads the canonical library).
    carry_root = _carry_root(a)
    if mode == "missing":
        unheld = oc.published_vs_standing(published_bundle(a.l3, root=carry_root), curve_decisions)
        if unheld:
            print("[batch] REFUSED: the published version scores "
                  f"{', '.join(unheld)} under an owner decision that "
                  f"{'the curve decisions file passed' if a.curve_decisions else 'no curve decisions file'} "
                  "does not hold. Pass the region's curve_decisions.json (--curve-decisions; the Region "
                  "builder seeds it from the published version) so your choice stands.")
            return 2
    decision_gaps = oc.coverage_exceptions(curve_decisions)
    if decision_gaps:
        gap_fids = {str(g.get("functionId")) for g in decision_gaps}
        coverage_exceptions = [x for x in coverage_exceptions or []
                               if str(x.get("functionId")) not in gap_fids] + decision_gaps
    owner_approvals = _parse_approvals(a.approve_portfolio)
    # the candidates a person added for comparison (Select final curves writes
    # candidate_register.json beside curve_decisions.json), read back whole
    candidate_register = None
    register_file = getattr(a, "candidate_register", None)
    if register_file:
        candidate_register = json.loads(Path(register_file).read_text(encoding="utf-8"))
        if not isinstance(candidate_register, dict):
            print(f"[batch] REFUSED: {register_file} is not a candidate register document")
            return 2
    # every decision file this stage reads, by path relative to the decisions root
    # and sha (reviewerInputs.files, in the inputs digest), and the label of an
    # experimental run (D4a)
    decisions_root = decisions_root_of(a, out_dir)
    reviewer_files = ra.reviewer_input_files(decision_paths(vars(a)), decisions_root)
    experimental = ra.experimental_block()
    if experimental:
        print(f"[batch] EXPERIMENTAL run: {lib.experimental_label({'manifest': {'experimental': experimental}})}; "
              "its version can never reach the canonical library")

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
        # REF-15: the refused sources the owner accepted, computed in this pass
        force=oc.forced_sources(curve_decisions) or None,
        # and "your choice stands": what the owner removed or re-sourced stays
        # out of this build's own fit
        hold=oc.held_metrics(curve_decisions) or None,
        # H1: missing carries the published curves forward (methodology 0.14);
        # all builds every curve afresh and never reads the canonical library
        carry=carry_argument(mode, a.l3, carry_root),
        # DATA-11: which value policy the archive is read under (recorded as used)
        value_policy=value_policy,
        on_event=ra.event_narrator())
    print(f"[batch] evidence: {evidence['n_retained']} / {evidence['n_candidates']} retained "
          f"(tier {evidence['tier']['reference_tier']}, pool {evidence['reference_pool_disposition']}), "
          f"{len(evidence['curve_rows'])} curves built")
    if mode == "all":
        held = sorted(str(m) for m in evidence.get("owner_hold") or [])
        print("[batch] refit all: no published curve carried forward"
              + (f"; held out of the fit by the owner's decisions: {', '.join(held)}" if held
                 else "; no owner hold"))
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

    # A CURVE-07 answer does what its question asks (curve07_answers): accepting the
    # curve publishes it, rejecting it drops the metric. A flag names the same thing
    # and wins over an answer; a metric both published and dropped is refused.
    owner_decisions = _without_outcome_asserts(owner_decisions)
    answer_finalize, answer_remove = curve07_answers(
        owner_decisions, fitted=(evidence.get("curve_review") or {}))
    both = ((set(answer_finalize) | set(owner_finalize))
            & (set(answer_remove) | set(owner_remove)))
    if both:
        print(f"[batch] REFUSED: {', '.join(sorted(both))} is both published and dropped "
              "by the owner's answers and flags")
        return 2
    for mk in sorted(answer_finalize):
        print(f"[batch] CURVE-07 answer: {mk} publishes as preliminary")
    for mk in sorted(answer_remove):
        print(f"[batch] CURVE-07 answer: {mk} is dropped")
    owner_finalize = {**answer_finalize, **owner_finalize}
    owner_remove = {**answer_remove, **owner_remove}

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
            reviewer_decisions=(owner_decisions + policy_decisions) or None,
            curve_decisions=curve_decisions or None)
        result["standing_decisions"] = {
            "policyVersion": dec.policy_version(policy),
            "sha256": policy["meta"]["sha256"],
            "path": policy["meta"]["path"],
            "enabledIds": enabled,
            "appliedIds": sorted({d["decision_class"] for d in policy_decisions}),
            "appliedCount": len(policy_decisions),
            "confirmedBy": None, "confirmedAt": None,
        }
        # what the manifest's digest names beyond the legacy rules (schema 2): the
        # refit mode, every reviewer and owner input (provenance.decision_inputs_of
        # reads these), the decision files by sha and the experimental label
        result["refit_mode"] = mode
        result["reviewer_decisions"] = list(owner_decisions)
        result["portfolio_approvals"] = list(approvals)
        result["candidate_register"] = candidate_register
        result["reviewer_input_files"] = dict(reviewer_files)
        result["experimental"] = experimental
        # the candidate register of this build (H4): what the Reference Curves page
        # would show, plus the candidates a person added for comparison
        register = C.register_for_result(result, considered=(candidate_register or {}).get("considered"))
        manifest = pv.build_run_manifest(
            result, argv=list(getattr(a, "argv", None) or sys.argv[1:]),
            started_at=started, finished_at=_now(),
            defaults={**pv.new_manifest_defaults(), "refit": mode,
                      "reviewerInputs": {"files": dict(reviewer_files)},
                      "experimental": experimental})
        record_stage_inputs(manifest, mode=mode, evidence=evidence, decisions_root=decisions_root)
        doc = pv.build_provenance(result, manifest, timestamp=started, register=register)
        # a curve the owner finalized or removed by flag closes its own CURVE-07 item
        resolutions = curve07_resolutions(
            result.get("curve_review") or {}, owner_finalize, owner_remove,
            owner_decisions + policy_decisions, reviewer=a.maintainer, date=started)
        answers = owner_decisions + resolutions + policy_decisions
        if answers:
            doc = pv.apply_reviewer_decisions(doc, answers,
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

    # A SELECT-01 approval carries with its unchanged metric set (methodology
    # 0.14, owner decision 2026-09-21): a rebuild keeps the owner's decision for a
    # function whose metrics are all carried unchanged, and any change to the set
    # needs a new approval. An approval passed for this build wins. Decided before
    # the session and the evidence package are written, so both carry it.
    carried_ok = cf.carried_approvals(
        evidence.get("carried_approvals") or [], result.get("bundle"),
        result.get("carried") or {}, result.get("fixed_metrics") or {},
        have=[str(x.get("functionId")) for x in approvals],
        from_version=(evidence.get("carried_from") or {}).get("fromVersion"))
    if carried_ok:
        approvals.extend(carried_ok)
        print(f"[batch] {len(carried_ok)} portfolio approval(s) carried with an unchanged "
              f"metric set: {', '.join(x['functionId'] for x in carried_ok)}")
    result["portfolio_approvals"] = list(approvals)
    result["meta"]["portfolioApprovals"] = list(approvals)
    # the per-metric rebuild ledger (H7), kept with the session's reference statement
    result["metric_ledger"] = doc.get("metricLedger")

    # 3. the evidence package (H5): the stations, values, pools, curves, candidates,
    # ledger and decisions behind this build, referenced by digest from the
    # provenance, the session and the staged version. Written before the session
    # and the publish so every copy of the record names it.
    evidence_ref = None
    package_error = None
    try:
        evidence_ref = deep_evidence.write_package(result, doc, out_dir / EVIDENCE_DIR,
                                                  register=register)
        doc["evidenceReferences"] = [evidence_ref]
        result["evidence_reference"] = evidence_ref
        write_evidence_reference(out_dir, evidence_ref)
        print(f"[batch] evidence package {evidence_ref['packageId']} {evidence_ref['version']} "
              f"({evidence_ref.get('reproducibility')}) -> "
              f"{EVIDENCE_DIR}/{(evidence_ref.get('archive') or {}).get('name')}")
    except Exception as exc:  # noqa: BLE001 - reported, and the staged publish is withheld
        package_error = f"{type(exc).__name__}: {exc}"
        print(f"[batch] could not write the evidence package: {package_error}")

    # 4. the assessment as a project file, before any gate has an opinion
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

    # 5. the staged publish (withheld without its evidence package: a version
    # without the data behind its curves is not a self-contained record)
    publish_info = None
    if result.get("bundle") is None:
        print(f"[batch] no bundle to stage: {result.get('bundle_error')}")
    elif package_error:
        print("[batch] staged publish withheld: the evidence package failed "
              f"({package_error})")
    else:
        try:
            publish_info = ra.publish(result, staged_root, maintainer=a.maintainer,
                                      provenance=doc, portfolio_approvals=approvals,
                                      status="draft")
            print(f"[batch] staged v{publish_info['version']} (draft) -> {publish_info['path']}")
            if evidence_ref is not None:
                write_evidence_reference(Path(publish_info["path"]), evidence_ref)
        except Exception as exc:  # noqa: BLE001
            print(f"[batch] staged publish refused: {exc}")

    # 6. the run folder outputs, the ledger, the packet, the gallery, the promote command
    from run_regional_analysis import write_outputs  # noqa: E402 (sibling script)
    write_outputs(result, out_dir, publish_info, doc)
    write_ledger(out_dir, doc.get("metricLedger"))
    (out_dir / "standing_decisions_applied.json").write_text(
        json.dumps({"policy": policy["meta"], "enabled": enabled,
                    "decisions": policy_decisions, "finalize_metrics": policy_finalize,
                    "portfolio_approvals": approvals, "coverage_exceptions": policy_exceptions,
                    "open_items": pr.uncovered,
                    "hard_stops": pr.hard_stops}, indent=1, default=_json_default) + "\n",
        encoding="utf-8")
    shutil.copy2(policy["meta"]["path"], out_dir / "standing_decisions.applied.yaml")
    prior = None
    if mode == "missing" and carry_root is not None:
        # verify: the version the re-stage carried from, read from the same view
        prior = published_bundle(a.l3, root=carry_root)
    elif mode == "missing":
        # the packet's diff against the published version; a full refit reads the
        # canonical library for nothing, its comparison is a separate step (D3)
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
    # the refit mode and the metrics the owner's decisions held out of the fit
    # (H1: the packet lists them), the evidence reference and the record files
    packet["refit"] = refit_block(result, mode)
    packet["evidence"] = evidence_ref
    packet["ledger"] = LEDGER_FILE if doc.get("metricLedger") is not None else None
    packet["value_policy"] = result.get("nrsa_policy")
    jp, mp = rp.write_packet(packet, out_dir)
    (out_dir / "promote_command.txt").write_text(cmd + "\n", encoding="utf-8")
    print(f"[batch] packet -> {mp}")
    print(f"[batch] {len(policy_decisions)} standing decision(s) applied, "
          f"{len(pr.uncovered)} item(s) open for the owner, "
          f"{len(pr.hard_stops)} hard stop(s)")
    return 1 if package_error else 0


# --------------------------------------------------------------------------- #
# what a stage records beside the run (campaign Round 1)
# --------------------------------------------------------------------------- #
#: The per-metric rebuild ledger beside the packet (rebuild-ledger/1, H7).
LEDGER_FILE = "rebuild_ledger.json"
#: The evidence package folder under the run folder (``<out>/evidence/<packageId>/``).
EVIDENCE_DIR = "evidence"
#: The evidence reference beside the packet and in a staged version folder.
EVIDENCE_FILE = "evidence.json"
#: The decision files a stage reads, by the namespace attribute that names each.
DECISION_FILE_ATTRS = ("curve_decisions", "reviewer_decisions", "coverage_exceptions",
                       "region_coverage_exceptions", "candidate_register")


def decision_paths(args) -> dict:
    """``{role: path or None}`` of the decision files a stage namespace (or its
    ``vars``) names."""
    get = args.get if isinstance(args, dict) else lambda k, d=None: getattr(args, k, d)
    return {attr: get(attr) for attr in DECISION_FILE_ATTRS}


def decisions_root_of(a, out_dir: Path) -> Path:
    """The folder decision-file paths are recorded relative to: ``--decisions-root``
    when the run names one (stage-many's per-region files live under it), else the
    run folder's parent, where the Region builder keeps a region's files beside its
    run (``<runs root>/l3-<code>/``)."""
    root = getattr(a, "decisions_root", None)
    return Path(root).resolve() if root else Path(out_dir).resolve().parent


def record_stage_inputs(manifest: dict, *, mode: str, evidence: dict,
                        decisions_root: Optional[Path]) -> None:
    """What the manifest says beyond the digest: ``inputs.reference.carryForward``
    (``{mode, fromVersion}``, ``off`` under a full refit; a pressure-screen run only,
    the legacy method never carries) and ``reviewerInputs.decisionsRoot`` (where the
    recorded files are read from, never in the digest, so a moved root re-derives the
    same digest). Added after the digest was computed, which the digest payload
    never reads, so the stored digest still re-derives."""
    inputs = manifest.setdefault("inputs", {})
    if isinstance(inputs.get("reference"), dict):
        inputs["reference"]["carryForward"] = ra.carry_forward_record(mode, evidence)
    reviewer = manifest.setdefault("reviewerInputs", {})
    reviewer["decisionsRoot"] = str(decisions_root) if decisions_root else None


def refit_block(result: dict, mode: str) -> dict:
    """The packet's account of the refit mode (H1): what was carried and from
    where, and every metric the owner's standing decisions held out of the fit
    ("your choice stands"), with the pool a held metric would have had."""
    from streamcurves import pressure_evidence as pe
    held = result.get("held_by_owner") or {}
    return {
        "mode": mode,
        "carry_forward": ra.carry_forward_record(mode, result),
        "n_carried": len(result.get("carried") or {}),
        "held": sorted(str(m) for m in result.get("owner_hold") or []),
        "held_pool_supported": {str(mk): pe.held_summary((h or {}).get("decision") or {})
                                for mk, h in sorted(held.items())},
    }


def write_ledger(out_dir: Path, ledger: Optional[dict]) -> Optional[Path]:
    """``rebuild_ledger.json`` beside the packet (LF, no absolute path: the
    document carries none)."""
    if ledger is None:
        return None
    p = Path(out_dir) / LEDGER_FILE
    p.write_text(json.dumps(ledger, indent=1, default=_json_default) + "\n",
                 encoding="utf-8", newline="\n")
    return p


def write_evidence_reference(folder: Path, ref: dict) -> Path:
    """``evidence.json``: the package reference (id, version, digests, archive) in a
    run folder or a version folder, the manifest-only shape the release feed and
    the gallery read (KB, never the package itself)."""
    p = Path(folder) / EVIDENCE_FILE
    p.write_text(json.dumps(ref, indent=1, sort_keys=True, default=_json_default) + "\n",
                 encoding="utf-8", newline="\n")
    return p


def published_bundle(code: str, root: Optional[Path] = None) -> Optional[dict]:
    """The bundle of the region's latest published version in the canonical
    library (what a build carries forward from), or in the library view ``root``
    (a verify re-stage), or None."""
    got = cf.find_published(str(code), root=root)
    if got is None:
        return None
    aid, ver = got
    base = Path(root) if root is not None else ra.CANONICAL_LIBRARY
    p = base / "assessments" / aid / f"v{ver}" / lib.BUNDLE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _carry_root(a) -> Optional[Path]:
    """The library view a stage carries forward from instead of the canonical
    library (``verify`` sets it to what the recorded stage could see), or None."""
    root = getattr(a, "carry_root", None)
    return Path(root).resolve() if root else None


def carry_argument(mode: str, code: str, root: Optional[Path]):
    """What ``run_evidence`` carries forward: the canonical library's latest
    published version (True) under ``missing``; under ``missing`` with a library
    view, the carry prepared from that view (an empty dict when the view holds
    no published version, so nothing is carried); nothing (False) under ``all``."""
    if mode != "missing":
        return False
    if root is None:
        return True
    return cf.prepare(str(code), root=Path(root)) or {}


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


def _argv_value(argv: list, flag: str) -> Optional[str]:
    """The value of ``flag`` in a recorded command line (``--flag VALUE`` or
    ``--flag=VALUE``), or None."""
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(flag + "="):
            return tok[len(flag) + 1:]
    return None


def _resolve_recorded(p: Path) -> Path:
    if p.is_absolute():
        return p
    repo = _APP_ROOT.parent.parent
    return next((b / p for b in (Path.cwd(), repo) if (b / p).exists()), repo / p)


def _decisions_file(out_dir: Path, argv, manifest: Optional[dict] = None) -> Path:
    """The curve decisions file a stage read: its recorded ``--curve-decisions``
    (a single stage), else the region's file under the decisions root a stage-many
    read (``--decisions-root`` in the command, else the root the manifest records),
    else the region's own record beside the run, where the workspace records the
    owner's decisions (REF-15)."""
    argv = [str(x) for x in argv or []]
    named = _argv_value(argv, "--curve-decisions")
    if named:
        return _resolve_recorded(Path(named))
    manifest = manifest or {}
    code = str((manifest.get("region") or {}).get("code") or "")
    root = _argv_value(argv, "--decisions-root") or (manifest.get("reviewerInputs") or {}).get("decisionsRoot")
    if argv and argv[0] == "stage-many" and code:
        base = _resolve_recorded(Path(root)) if root else out_dir.parent
        return rb.run_folder(base, code) / oc.DECISIONS_FILE
    return out_dir / oc.DECISIONS_FILE


#: The same helper as a plain function, for the campaign runner (``run_campaign.py``).
decisions_file_of = _decisions_file


def resolve_policy_status(out_dir: Path, *, gate_report: Optional[str] = None,
                          policy_path: Optional[str] = None) -> tuple[str, dict]:
    """``promote --status policy``: ``preliminary`` when the run folder passes every gate
    of the promotion policy (``config/methodology/promotion_policy.yaml``) that can be
    checked from the run folder, else ``draft``, with the blockers. The equivalence gate
    needs a gate report (``--gate-report``, or the campaign manifest's) and is not evaluated
    without one. The expectations (inputs digest, code fingerprint, n-boot, commit) come
    from the campaign manifest when the run folder is ``<root>/runs/l3-<code>``, else from
    the run's own record. Nothing is refitted and nothing is written."""
    from streamcurves import campaign as camp
    out_dir = Path(out_dir).resolve()
    policy = camp.load_promotion_policy(policy_path)
    problems = camp.validate_promotion_policy(policy)
    if problems:
        raise SystemExit("the promotion policy is not usable: " + "; ".join(problems))
    found = camp.campaign_manifest_for_run(out_dir)
    code = str(out_dir.name[3:]) if out_dir.name.startswith("l3-") else ""
    expect = camp.expectation_from_manifest(found[1], code) if found else None
    if expect is None:
        expect = camp.expectation_from_run(out_dir)
    vdir = camp.staged_version_dir(out_dir)
    man = ((camp.read_json(vdir / lib.PROVENANCE_FILE) or {}).get("manifest") if vdir else None) or {}
    decisions_file = _decisions_file(out_dir, (man.get("agent") or {}).get("argv"), man)
    report = gate_report or expect.get("gateReport")
    skip = () if report else ("equivalence-proven",)
    res = camp.evaluate_gates(out_dir, expect=expect, decisions_file=decisions_file, policy=policy,
                              gate_report=report, skip=skip)
    status = "preliminary" if res["eligible"] else "draft"
    return status, {"policyVersion": policy["meta"].get("version"), "sha256": policy["meta"].get("sha256"),
                    "status": status, "blockers": res["blockers"], "gates": res["gates"],
                    "expectation": expect.get("source"),
                    "campaignManifest": str(found[0]) if found else None}


def reviewer_inputs_drift(manifest: dict, out_dir: Path) -> list[str]:
    """Every decision file the manifest records (``reviewerInputs.files``, path
    relative to ``reviewerInputs.decisionsRoot``) that is not on disk today with
    the recorded sha: ``"<path>: changed"`` or ``"<path>: missing"``. An answer
    recorded or withdrawn after the stage is not in the staged version, which
    would publish without it, so promote refuses on any drift."""
    reviewer = (manifest or {}).get("reviewerInputs") or {}
    files = reviewer.get("files") or {}
    root = reviewer.get("decisionsRoot")
    base = Path(root) if root else Path(out_dir).resolve().parent
    out = []
    for rel, sha in sorted(dict(files).items()):
        p = Path(rel)
        if not p.is_absolute():
            p = base / p
        now = ("sha256:" + _file_sha(p)) if p.is_file() else None
        if now is None:
            out.append(f"{rel}: missing")
        elif now != sha:
            out.append(f"{rel}: changed")
    return out


def _confirm_session_approvals(session: dict, *, maintainer: str, date: str) -> int:
    """Put the confirming owner's name on every pending SELECT-01 approval the
    session field carries (``portfolio_approvals``, the field's ``approver``
    spelling), as :func:`_confirm_approvals` does for the meta. Returns how many."""
    fields = session.get("fields") if isinstance(session.get("fields"), dict) else session
    raw = fields.get("portfolio_approvals")
    entries = raw.get("value") if isinstance(raw, dict) and isinstance(raw.get("value"), list) else raw
    n = 0
    for ap in entries if isinstance(entries, list) else []:
        if isinstance(ap, dict) and dec.PENDING_SUFFIX in str(ap.get("approver") or ""):
            ap["approver"] = maintainer
            ap["date"] = date
            n += 1
    return n


def cmd_promote(a) -> int:
    out_dir = Path(a.out).resolve()
    staged_root = _staged_root(out_dir)
    packet = json.loads((out_dir / "review_packet.json").read_text(encoding="utf-8"))
    slug = lib.slugify((packet.get("region") or {}).get("name") or "")
    staged = packet.get("staged") or {}
    vdir = Path(staged["path"]) if staged.get("path") else None
    if vdir is not None and vdir.is_dir():
        version = int(staged.get("version") or 0)
    else:
        # a run folder renamed since its stage (stage-many --rename-legacy-folders)
        # keeps its staged library under the new name
        version, vdir = _latest_staged_version(staged_root, slug)
    if not vdir.is_dir():
        raise SystemExit(f"staged version folder missing: {vdir}")
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    session = json.loads((vdir / lib.SESSION_FILE).read_text(encoding="utf-8"))
    meta = json.loads((vdir / lib.META_FILE).read_text(encoding="utf-8"))
    doc = json.loads((vdir / lib.PROVENANCE_FILE).read_text(encoding="utf-8"))
    slug = meta.get("assessmentId") or slug

    # --status policy: preliminary when every promotion-policy gate the run folder can
    # answer passes, else draft with the blockers on the record (owner decision D2)
    policy_status = None
    if a.status == "policy":
        a.status, policy_status = resolve_policy_status(out_dir, gate_report=getattr(a, "gate_report", None))
        print(f"[promote] promotion policy {policy_status['policyVersion']} "
              f"({str(policy_status['sha256'])[:19]}) resolves the status to {a.status}"
              + (f"; {len(policy_status['blockers'])} blocker(s):" if policy_status["blockers"] else ""))
        for b in policy_status["blockers"]:
            print(f"[promote]   {b}")

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
    publish_root = Path(a.publish_root).resolve()
    # an experimental arm (another configuration root, the REF-15 extension flag)
    # is labeled and isolated (D4a): it never reaches the canonical library
    experimental = lib.experimental_label(doc)
    if experimental and publish_root == ra.CANONICAL_LIBRARY:
        raise SystemExit("the staged version is experimental (" + experimental + "); it "
                         "publishes into an isolated library root, never the canonical one.")
    # nor the owner's curve decisions (REF-15): one recorded or undone after the
    # stage is not in the staged version, which would publish without it, or with it
    fields_ = session.get("fields") if isinstance(session.get("fields"), dict) else session
    now_decisions = oc.load_file(_decisions_file(out_dir, (man.get("agent") or {}).get("argv"), man))
    if oc.decisions_changed(fields_.get("owner_curve_decisions") or [], now_decisions):
        raise SystemExit("the region's curve decisions changed after this run was staged, so "
                         "the staged version does not apply them; build the region again, "
                         "then promote.")
    # nor any decision file the stage read (answers, documented gaps, candidates;
    # reviewerInputs.files by sha): the staged version was built from those bytes
    moved = reviewer_inputs_drift(man, out_dir)
    if moved:
        raise SystemExit("the decision files this run was staged from changed after the stage "
                         f"({'; '.join(moved)}), so the staged version does not apply them; build "
                         "the region again, then promote.")

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
    _confirm_session_approvals(session, maintainer=a.maintainer, date=date)

    os.environ["STAF_LIBRARY_ROOT"] = str(publish_root)
    if publish_root == ra.CANONICAL_LIBRARY:
        reason = lib.publish_gate_reason(a.maintainer)
        if reason:
            raise SystemExit(f"canonical publish blocked: {reason}")
        os.environ.setdefault("STAF_LIBRARY_MAINTAINER", a.maintainer)
    pub_meta = {k: meta.get(k) for k in ("assessmentName", "region", "stateCode", "stateName",
                                         "sourceCitation", "author", "revisionNotes")}
    # a version staged under a pending label (a policy-candidate batch) is the
    # confirming maintainer's once promoted: the author is who said go
    if dec.PENDING_SUFFIX in str(pub_meta.get("author") or ""):
        pub_meta["author"] = a.maintainer
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
    # the evidence reference travels with the version (the release feed and the
    # gallery read <vN>/evidence.json; the package itself is hosted by digest)
    staged_evidence = vdir / EVIDENCE_FILE
    if staged_evidence.is_file():
        shutil.copyfile(staged_evidence, lib.version_dir(slug, new_version) / EVIDENCE_FILE)
    record = {"stagedVersion": version, "stagedPath": str(vdir), "publishedVersion": new_version,
              "publishedRoot": str(publish_root), "status": a.status,
              "confirmedBy": a.maintainer,
              "confirmedAt": date, "overrides": applied,
              "contentDigest": published.get("contentDigest"),
              "contentDigestMatchesStaged": digest_ok,
              # --status policy: the policy that resolved the status and its blockers
              "policyStatus": policy_status}
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


STAGE_COMPLETE = "stage_complete.json"
#: The stage flags stage-many passes to every region (the Namespace cmd_stage reads).
_STAGE_MANY_FLAGS = ("screen", "no_screen", "no_streamcat", "maintainer", "n_boot",
                     "coverage_exceptions", "policy", "enable_policy", "max_iterations",
                     "approve_portfolio", "max_unresolved_share", "allow_unresolved", "nrsa_dataset",
                     "nrsa_cycles", "reference_frame", "screen_retries", "screen_retry_wait",
                     "engine_snap_tolerance_ft", "engine_max_reaches", "engine_max_hops",
                     "reference_method", "predictor_source", "refit", "value_policy",
                     "decisions_root")
#: The flags a region's inputs digest names: every stage flag but the decisions
#: root, whose files join the digest by sha (a moved root re-derives the same digest).
_DIGEST_FLAGS = tuple(f for f in _STAGE_MANY_FLAGS if f != "decisions_root")
#: The run-folder files a stage_complete.json records beside the staged library and
#: the evidence package: every file the packet and the ledger cite.
STAGE_OUTPUT_FILES = ("review_packet.json", "review_packet.md", "run_manifest.json",
                      "standing_decisions_applied.json", LEDGER_FILE, EVIDENCE_FILE,
                      "assessment.streamcurves.json", "decision_provenance_log.json",
                      "review_queue.json", "decision_records.csv", "curve_registry.csv",
                      "reference_support.csv", "reference_pool_ledger.csv",
                      "coverage_exceptions.draft.json", "curve_gallery.png",
                      "curve_gallery.html", "promote_command.txt")
STAGE_OUTPUT_FOLDERS = ("library", EVIDENCE_DIR)
#: A run folder named the way stage-many named them before campaign Round 1.
LEGACY_FOLDER = re.compile(r"^l3-(\d+)-(.+)$")


def region_decision_files(root, code: str, *, refit=None) -> dict:
    """The region's own decision files under ``<root>/l3-<code>/`` (H2), by the
    stage attribute each feeds: ``curve_decisions`` (REF-15; under ``--refit missing``
    seeded from the published version when the region has never recorded any, as the
    Region builder does, never under ``all``, which reads no published version),
    ``reviewer_decisions`` (the answers), ``region_coverage_exceptions`` (the documented
    gaps, merged over the batch flag) and ``candidate_register``. None where the file is
    absent, and every entry None without a root."""
    empty = {"curve_decisions": None, "reviewer_decisions": None,
             "region_coverage_exceptions": None, "candidate_register": None}
    if not root:
        return empty
    run_dir = rb.run_folder(root, code)
    if ra.refit_mode(refit) == "missing":
        curve = rb.curve_decisions_path(run_dir, code)
    else:
        curve = oc.path_of(run_dir)
    answers = run_dir / rb.OWNER_DECISIONS_FILE
    gaps = run_dir / rb.COVERAGE_EXCEPTIONS_FILE
    register = run_dir / rb.CANDIDATE_REGISTER_FILE
    return {"curve_decisions": str(curve) if curve else None,
            "reviewer_decisions": str(answers) if answers.is_file() else None,
            "region_coverage_exceptions": str(gaps) if gaps.is_file() else None,
            "candidate_register": str(register) if register.is_file() else None}


def _is_prefix_of(token: str, flag: str, shortest: str) -> bool:
    """True when ``token`` is ``flag`` or an abbreviation argparse accepts for it (at least
    ``shortest``; no other stage-many flag starts that way)."""
    return len(token) >= len(shortest) and flag.startswith(token)


def recorded_argv(argv) -> list:
    """The stage-many command as provenance records it: the worker count and ``--isolated``
    only schedule the work, so every way of running the same regions records the same command,
    however the flags are spelled (argparse accepts ``--w 3``, ``--wor=3`` and ``--i``)."""
    out, skip = [], False
    for x in [str(v) for v in argv or []]:
        if skip:
            skip = False
            continue
        flag, eq, _ = x.partition("=")
        if _is_prefix_of(flag, "--workers", "--w"):
            skip = not eq                     # a bare flag takes the next token as its value
            continue
        if not eq and _is_prefix_of(flag, "--isolated", "--i"):
            continue
        out.append(x)
    return out


def region_stage_namespace(a, code: str, name: str, out_dir: Path, argv=None) -> argparse.Namespace:
    """The namespace cmd_stage reads for one region of stage-many, built by hand so that no
    flag is dropped silently. Serial and parallel runs both use it. The region's own
    decision files under ``<a.decisions_root>/l3-<code>/`` are its inputs (H2, H3):
    the owner's curve decisions, answers, documented gaps and candidates."""
    files = region_decision_files(a.decisions_root, code, refit=a.refit)
    return argparse.Namespace(
        l3=code, name=name, out=str(out_dir), screen=a.screen, source_citation="",
        no_screen=a.no_screen, no_streamcat=a.no_streamcat, maintainer=a.maintainer,
        n_boot=a.n_boot, coverage_exceptions=a.coverage_exceptions, policy=a.policy,
        enable_policy=list(a.enable_policy or []), max_iterations=a.max_iterations,
        # SELECT-01 approvals apply to every region staged in one call:
        # a function that carries three metrics carries them everywhere,
        # and without this stage-many could never stage such a region.
        approve_portfolio=list(a.approve_portfolio or []),
        # the region's own record: answers, documented gaps (merged over the batch
        # flag), the owner's standing curve decisions (REF-15) and its candidates
        reviewer_decisions=files["reviewer_decisions"], finalize_metric=[], remove_metric=[],
        curve_decisions=files["curve_decisions"],
        region_coverage_exceptions=files["region_coverage_exceptions"],
        candidate_register=files["candidate_register"],
        decisions_root=a.decisions_root,
        max_unresolved_share=a.max_unresolved_share, allow_unresolved=a.allow_unresolved,
        nrsa_dataset=a.nrsa_dataset, nrsa_cycles=a.nrsa_cycles,
        reference_frame=a.reference_frame, include_site=[],
        screen_retries=a.screen_retries, screen_retry_wait=a.screen_retry_wait,
        engine_snap_tolerance_ft=a.engine_snap_tolerance_ft,
        engine_max_reaches=a.engine_max_reaches, engine_max_hops=a.engine_max_hops,
        exclude_site=[],
        reference_method=a.reference_method,
        argv=recorded_argv(argv if argv is not None else sys.argv[1:]),
        predictor_source=a.predictor_source,
        # the refit mode (H1) and the value policy (DATA-11) ride the same namespace
        refit=a.refit, value_policy=a.value_policy)


def code_fingerprint() -> str:
    """SHA-256 over what a stage reads from the app, by relative path and raw bytes: every file
    under streamcurves/ (vendored copies and their data included), data/ and config/, and the
    scripts. A stage records it at its start and its end."""
    root = _APP_ROOT
    h = hashlib.sha256()
    for sub_dir, patterns in (("streamcurves", ("*",)), ("scripts", ("*.py",)), ("config", ("*",)),
                              ("data", ("*",))):
        for pattern in patterns:
            for p in sorted((root / sub_dir).rglob(pattern)):
                if "__pycache__" in p.parts or not p.is_file():
                    continue
                h.update(str(p.relative_to(root)).replace("\\", "/").encode("utf-8"))
                h.update(b"\0")
                h.update(p.read_bytes())
                h.update(b"\0")
    return h.hexdigest()


def _file_sha(path) -> Optional[str]:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None


def carried_from(code: str, root: Optional[Path] = None) -> Optional[dict]:
    """The canonical library's published version of the region a stage carries forward from
    (its approvals and curves), or the library view's under a verify re-stage, or None."""
    from streamcurves import carry_forward as cf
    got = cf.find_published(str(code), root=root)
    if got is None:
        return None
    aid, ver = got
    base = Path(root) if root is not None else ra.CANONICAL_LIBRARY
    man = json.loads((base / "assessments" / aid / "manifest.json").read_text(encoding="utf-8"))
    row = next((v for v in man.get("versions") or [] if int(v.get("version") or 0) == int(ver)), {})
    return {"assessmentId": aid, "version": int(ver), "contentDigest": row.get("contentDigest")}


def region_inputs(args: dict) -> dict:
    """Everything a region's stage depends on, beside the region itself: the flags
    (the refit mode resolved, so the digest always names the effective one), the
    methodology, the policy, the batch's documented gaps, the data, the code, and
    the region's own decision files by sha (``reviewerFiles``: a changed answer
    re-stages the region)."""
    policy = dec.load_policy(args.get("policy"))
    flags = {k: args.get(k) for k in _DIGEST_FLAGS}
    flags["refit"] = ra.refit_mode(args.get("refit"))
    root = args.get("decisions_root") or (
        Path(args["out"]).resolve().parent if args.get("out") else None)
    return {"flags": flags,
            "methodology": methodology.config_fingerprints(),
            "policy": {"version": dec.policy_version(policy),
                       "sha256": _file_sha(policy["meta"]["path"])},
            "coverageExceptions": _file_sha(args.get("coverage_exceptions")),
            "reviewerFiles": ra.reviewer_input_files(decision_paths(args), root),
            "nrsaManifest": _file_sha(_APP_ROOT / "data" / "nrsa" / "manifest.json"),
            "legacyNrsa": {n: _file_sha(_APP_ROOT / "data" / n)
                           for n in ("nrsa_metrics.parquet", "nrsa_sites.csv")},
            "stationScreen": _file_sha(_APP_ROOT / "data" / "nrsa" / "station_screen.parquet"),
            "code": code_fingerprint()}


def region_digest(code: str, name: str, inputs: dict, carried: Optional[dict]) -> str:
    """The inputs digest of one region's stage. ``carried`` names the published
    version a stage carries forward from, None under ``--refit all`` (the mode
    itself rides in ``inputs["flags"]["refit"]``)."""
    return hashlib.sha256(json.dumps({"region": code, "name": name, "inputs": inputs,
                                      "carriedFrom": carried},
                                     sort_keys=True, default=str).encode("utf-8")).hexdigest()


def carried_for(args, code: str) -> Optional[dict]:
    """What the region's stage carries forward from: the canonical library's
    published version under ``--refit missing``, nothing under ``all`` (which never
    reads the canonical library)."""
    get = args.get if isinstance(args, dict) else lambda k, d=None: getattr(args, k, d)
    if ra.refit_mode(get("refit")) == "all":
        return None
    root = get("carry_root")
    return carried_from(code, root=Path(root).resolve() if root else None)


def outputs_intact(region_dir: Path, rec: dict) -> bool:
    """Every output a stage_complete.json records is still there with its SHA-256."""
    outs = rec.get("outputs") or {}
    return bool(outs) and all(_file_sha(region_dir / rel) == sha for rel, sha in outs.items())


def stage_job(spec: dict, out_dir) -> dict:
    """A job target (streamcurves.jobs): stage one region in this process, then record what
    it was staged from and what it wrote (``stage_complete.json``, written only when the stage
    succeeded and its inputs, code and data included, were the batch's at its start and end)."""
    from streamcurves import jobs as jb
    args = dict(spec["args"])
    region_dir = Path(args["out"])
    try:
        held = jb.acquire(region_dir)
    except jb.CampaignBusy as exc:
        raise SystemExit(f"{exc}")
    try:
        return _stage_job_locked(spec, args, region_dir)
    finally:
        jb.release(held)


def _stage_job_locked(spec: dict, args: dict, region_dir: Path) -> dict:
    (region_dir / STAGE_COMPLETE).unlink(missing_ok=True)

    def now_digest() -> str:
        return region_digest(args["l3"], args["name"], region_inputs(args), carried_for(args, args["l3"]))

    started, code_start = _now(), code_fingerprint()
    if now_digest() != spec["inputsDigest"]:
        raise SystemExit("the inputs changed after this batch started; run the batch again")
    exit_code = int(cmd_stage(argparse.Namespace(**args)))
    code_end = code_fingerprint()
    if exit_code != 0:
        raise SystemExit(exit_code)
    if code_end != code_start or now_digest() != spec["inputsDigest"]:
        raise SystemExit("the inputs changed while this region was staged; stage it again")
    outputs = stage_outputs(region_dir)
    rec = {"l3": args["l3"], "name": args["name"], "inputsDigest": spec["inputsDigest"],
           "code": {"start": code_start, "end": code_end}, "startedAt": started,
           "finishedAt": _now(), "outputs": outputs}
    tmp = region_dir / (STAGE_COMPLETE + ".part")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, region_dir / STAGE_COMPLETE)
    return {"l3": args["l3"], "exit": 0, "outputs": len(outputs)}


def stage_outputs(region_dir: Path) -> dict[str, str]:
    """``{relative path: sha256}`` of what a stage wrote that its record binds
    (H5): the run-folder files the packet and the ledger cite, the staged library
    and the evidence package, every one that exists."""
    outputs: dict[str, str] = {}
    for rel in STAGE_OUTPUT_FILES:
        p = region_dir / rel
        if p.is_file():
            outputs[rel] = _file_sha(p)
    for folder in STAGE_OUTPUT_FOLDERS:
        base = region_dir / folder
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    outputs[str(p.relative_to(region_dir)).replace("\\", "/")] = _file_sha(p)
    return outputs


def _region_row(code: str, name: Optional[str], out_dir: Path) -> dict:
    row: dict = {"l3": code, "name": name, "out": str(out_dir), "exit": None, "error": None}
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
    return row


def _stage_record(out_dir: Path) -> Optional[dict]:
    try:
        rec = json.loads((out_dir / STAGE_COMPLETE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _stage_many_parallel(a, out_root: Path, regions: list[tuple[str, Optional[str], Path]], held) -> int:
    """Stage the regions as jobs, the campaign's lock (``held``) kept from the first check to
    the summary. A region whose ``stage_complete.json`` names the same inputs and whose outputs
    are intact is reported as already staged and gets no job, so resuming never depends on the
    job id (which covers the whole command) and a changed region list stages only what it adds."""
    from streamcurves import jobs as jb
    campaign = out_root / ".campaign"
    inputs = None
    todo, rows = [], {}
    for code, name, out_dir in regions:
        if not name:
            rows[code] = {"l3": code, "name": None, "out": str(out_dir), "exit": 1,
                          "error": f"no NRSA candidate sites for L3 ecoregion {code}"}
            continue
        args = vars(region_stage_namespace(a, code, name, out_dir))
        # per region: the inputs carry the region's own decision files by sha
        region_in = region_inputs(args)
        if inputs is None:
            inputs = {k: v for k, v in region_in.items() if k != "reviewerFiles"}
        digest = region_digest(code, name, region_in, carried_for(args, code))
        rec = _stage_record(out_dir)
        if rec is not None and rec.get("inputsDigest") == digest and outputs_intact(out_dir, rec):
            row = _region_row(code, name, out_dir)
            row.update(exit=0, error="already staged from the same inputs", seconds=None)
            rows[code] = row
            print(f"[batch-many] L3-{code} {name}: already staged from the same inputs", flush=True)
            continue
        job = jb.Job(kind="python", target="run_region_batch:stage_job",
                     spec={"task": "stage-region", "l3": code, "inputsDigest": digest, "args": args},
                     env={"PYTHONPATH": str(_SCRIPTS) + os.pathsep + str(_APP_ROOT),
                          "HYRIVER_CACHE_NAME": str(out_dir / "hyriver_cache.sqlite")},
                     label=f"L3-{code} {name}")
        # staged from other inputs, a lost record or changed outputs: it stages again (a stage
        # job writes nothing under its job folder, so an old completion would read as intact)
        (campaign / "jobs" / job.id / "complete.json").unlink(missing_ok=True)
        todo.append((code, name, out_dir, job))
    t0 = time.monotonic()
    summary = {"jobs": {}}
    if todo:
        summary = jb.run([t[3] for t in todo], campaign, workers=a.workers,
                         meta={"task": "stage-many", "inputs": inputs}, lock_held=held,
                         on_event=lambda e: print(f"[batch-many] {e['label']}: {e['event']}", flush=True))
    for code, name, out_dir, job in todo:
        row = _region_row(code, name, out_dir)
        state = summary["jobs"].get(job.id, {}).get("state")
        row["exit"] = 0 if state in ("completed", "skipped") else 1
        if state == "skipped":
            row["error"] = "already staged from the same inputs"
        elif state not in ("completed",):
            failed = out_root / ".campaign" / "jobs" / job.id / "failed.json"
            row["error"] = (json.loads(failed.read_text(encoding="utf-8")).get("tail", "")[-300:]
                            if failed.is_file() else state)
        row["seconds"] = summary["jobs"].get(job.id, {}).get("seconds")
        log = out_root / ".campaign" / "jobs" / job.id / "log.txt"
        if log.is_file() and state == "completed":
            shutil.copyfile(log, out_dir / "stage.log")
        rows[code] = row
    ordered = [rows[c] for c, _, _ in regions]
    jp, mp = write_batch_summary(ordered, out_root)
    print(f"[batch-many] {len(regions)} region(s) in {time.monotonic() - t0:.0f} s with "
          f"{a.workers} worker(s); summary -> {mp}")
    return 0 if all(r.get("exit") == 0 for r in ordered) else 1


def cmd_stage_many(a) -> int:
    """Stage several regions with the same flags, one run folder each under ``--out-root``,
    and a summary table; ``--workers`` above 1 stages that many at once. Never promotes. One
    batch at a time per ``--out-root`` (the lock in its ``.campaign`` folder), and each region
    is staged under its own folder's lock."""
    from streamcurves import jobs as jb
    out_root = Path(a.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    try:
        held = jb.acquire(out_root / ".campaign")
    except jb.CampaignBusy as exc:
        print(f"[batch-many] {exc}")
        return BUSY_EXIT
    try:
        return _stage_many_locked(a, out_root, held)
    finally:
        jb.release(held)


def rename_legacy_folders(out_root: Path) -> list[tuple[Path, Path]]:
    """One-time: rename every ``l3-<code>-<slug>`` run folder under ``out_root`` to
    ``l3-<code>`` (``region_build.run_folder``), the name stage-many, the Region
    builder and the campaign index share since campaign Round 1, and point the
    folder's packet and promote command at the new path. A folder whose target
    already exists is left alone and named. Returns the renames made."""
    out_root = Path(out_root)
    done = []
    for p in sorted(out_root.iterdir()):
        m = LEGACY_FOLDER.match(p.name) if p.is_dir() else None
        if not m or not m.group(1).isdigit():
            continue
        target = rb.run_folder(out_root, m.group(1))
        if target.exists():
            print(f"[batch-many] {p.name}: not renamed, {target.name} already exists")
            continue
        p.rename(target)
        old, new = str(p), str(target)
        for rel in ("review_packet.json", "promote_command.txt", "review_packet.md"):
            f = target / rel
            if f.is_file():
                text = f.read_text(encoding="utf-8")
                for a_, b_ in ((old, new), (old.replace("\\", "\\\\"), new.replace("\\", "\\\\")),
                               (old.replace("\\", "/"), new.replace("\\", "/"))):
                    text = text.replace(a_, b_)
                f.write_text(text, encoding="utf-8")
        print(f"[batch-many] renamed {p.name} -> {target.name}")
        done.append((p, target))
    return done


def _stage_many_locked(a, out_root: Path, held) -> int:
    if getattr(a, "rename_legacy_folders", False):
        rename_legacy_folders(out_root)
    codes = [str(c).strip() for c in (a.l3 or [])]
    if not codes:
        if getattr(a, "rename_legacy_folders", False):
            return 0
        print("[batch-many] no region: pass --l3 CODE (repeatable)")
        return 2
    names = _parse_kv(a.name, "--name")
    if int(getattr(a, "workers", 1) or 1) > 1 or getattr(a, "isolated", False):
        regions = []
        for code in codes:
            name = names.get(code) or ra.region_name_for(code)
            regions.append((code, name, rb.run_folder(out_root, code)))
        return _stage_many_parallel(a, out_root, regions, held)
    rows: list[dict] = []
    for code in codes:
        name = names.get(code) or ra.region_name_for(code)
        # one folder per region per root, the Region builder's own name (H3)
        out_dir = rb.run_folder(out_root, code)
        row: dict = {"l3": code, "name": name, "out": str(out_dir), "exit": None, "error": None}
        t0 = time.monotonic()
        if not name:
            row.update(exit=1, error=f"no NRSA candidate sites for L3 ecoregion {code}")
        else:
            ns = region_stage_namespace(a, code, name, out_dir)
            try:
                row["exit"] = int(stage_locked(ns))
                if row["exit"] == BUSY_EXIT:
                    row["error"] = "another run is staging this region"
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
    build. Offline: it reads the committed station table and the NRSA archive,
    fits nothing, and writes a table the owner reviews before staging. Minutes
    per sparse region (about twelve where the basis ladder walks the national
    donors for every insufficient metric), seconds for a well-supported one."""
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
def recorded_value_policy(vdir) -> Optional[str]:
    """The NRSA value policy a version's manifest records
    (``inputs.nrsa_dataset.policy``: the literal every published pressure-screen
    version carries, ``latest_non_null_index_visit``, or a newer id), or None for a
    legacy-data version or one without a manifest. What a replay or a verify passes
    back so the archive is read exactly as the version read it."""
    p = Path(vdir) / lib.PROVENANCE_FILE
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    manifest = doc.get("manifest") or {}
    return ((manifest.get("inputs") or {}).get("nrsa_dataset") or {}).get("policy")


def recorded_refit_mode(manifest: Optional[dict]) -> str:
    """The refit mode a manifest records (``inputs.refit.mode``, digest schema 2),
    else ``missing``: a version built before the mode existed carried forward
    (methodology 0.14) or, before that, had nothing to carry."""
    mode = (((manifest or {}).get("inputs") or {}).get("refit") or {}).get("mode")
    return ra.refit_mode(mode) if mode else "missing"


def cmd_replay(a) -> int:
    """Apply the policy to published versions offline (``decisions.replay``, no
    recomputation), each version's recorded value policy named on the record."""
    for v in a.version_dir:
        policy_id = recorded_value_policy(v)
        print(f"[replay] {v}: value policy {policy_id or 'none recorded (legacy data)'}")
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


# --------------------------------------------------------------------------- #
# verify: re-stage a version from its own record and compare
# --------------------------------------------------------------------------- #
VERIFY_REPORT = "verify_report.json"


def restage_namespace(argv: list, region: dict, root: Path, manifest: Optional[dict] = None,
                      decisions_root: Optional[str] = None):
    """The stage namespace that re-stages one version from its recorded command
    (``stage`` or ``stage-many``) into ``root``: the same flags and decision files,
    the decisions root, refit mode and value policy the manifest records (an older
    command line without the flags still re-stages as it ran), the region of the
    version for a stage-many command. ``decisions_root`` (verify's flag) replaces
    the recorded root: a decision file the command names by a relative path (the
    stage ran from a repo root) is read from ``<decisions_root>/l3-<code>/<file>``,
    else from this checkout's repo root, when it is not where the record says."""
    argv = [str(x) for x in argv or []]
    if not argv or argv[0] not in ("stage", "stage-many"):
        raise SystemExit("the version records no stage command to re-run "
                         f"(agent.argv starts with {argv[:1] or 'nothing'})")
    manifest = manifest or {}
    reviewer = manifest.get("reviewerInputs") or {}
    parsed = build_parser().parse_args(argv)
    if decisions_root:
        parsed.decisions_root = str(decisions_root)
    elif reviewer.get("decisionsRoot"):
        parsed.decisions_root = reviewer["decisionsRoot"]
    resolve_decision_paths(parsed, decisions_root)
    parsed.refit = recorded_refit_mode(manifest)
    parsed.value_policy = recorded_value_policy_of(manifest) or parsed.value_policy
    code = str(region.get("code") or "")
    if parsed.cmd == "stage":
        ns = parsed
        ns.out = str(root)
        ns.argv = argv
    else:
        names = _parse_kv(parsed.name, "--name")
        name = names.get(code) or region.get("name") or ra.region_name_for(code)
        if not name:
            raise SystemExit(f"no name for L3 ecoregion {code}")
        ns = region_stage_namespace(parsed, code, name, rb.run_folder(root, code), argv=argv)
    return ns


def recorded_value_policy_of(manifest: Optional[dict]) -> Optional[str]:
    return (((manifest or {}).get("inputs") or {}).get("nrsa_dataset") or {}).get("policy")


def resolve_decision_paths(parsed, decisions_root: Optional[str] = None) -> dict:
    """Recorded decision-file paths that are relative and not on disk from here:
    each resolves to ``<decisions_root>/<its l3-<code>/<file> tail>`` when that
    file exists, else to the same relative path under this checkout's repo root.
    Returns ``{attribute: resolved path}`` for the paths it moved."""
    moved = {}
    repo_root = _APP_ROOT.parent.parent
    for attr in DECISION_FILE_ATTRS:
        value = getattr(parsed, attr, None)
        if not value or not isinstance(value, str):
            continue
        p = Path(value)
        if p.is_absolute() or p.is_file():
            continue
        candidates = []
        if decisions_root:
            parts = p.parts
            for i, part in enumerate(parts):
                if part.startswith("l3-"):
                    candidates.append(Path(decisions_root, *parts[i:]))
                    break
        candidates.append(repo_root / p)
        for c in candidates:
            if c.is_file():
                setattr(parsed, attr, str(c))
                moved[attr] = str(c)
                break
    return moved


def carry_view(vdir: Path, region: dict, manifest: dict, root: Path,
               library: Optional[Path] = None) -> Optional[dict]:
    """A library view of what the stage that built ``vdir`` could carry forward
    from, written under ``root/carry-view``: the region's canonical assessment
    with only the versions before the one the record carried from
    (``inputs.reference.carryForward.fromVersion``; else, for a version in the
    canonical library, the versions before it; else every version), its
    manifest's ``latestVersion`` cut to match. A re-stage that carried from the
    canonical library would find the version under test itself there (or a
    version published since) and carry from it, stamping ``carriedForward``
    blocks the record never had, so its content digest could never come back
    equal. None under a full refit (nothing is carried)."""
    if recorded_refit_mode(manifest) != "missing":
        return None
    code = str((region or {}).get("code") or "")
    base = Path(library) if library is not None else ra.CANONICAL_LIBRARY
    view = Path(root) / "carry-view"
    if view.exists():
        shutil.rmtree(view)
    (view / "assessments").mkdir(parents=True)
    out = {"root": str(view), "assessmentId": None, "cutoff": None, "latestVersion": 0}
    found = cf.find_published(code, root=base) if code else None
    if found is None:
        return out
    aid, latest = found
    adir = base / "assessments" / aid
    recorded = (((manifest or {}).get("inputs") or {}).get("reference") or {}).get("carryForward") or {}
    vdir = Path(vdir).resolve()
    if recorded.get("fromVersion"):
        cutoff = int(recorded["fromVersion"]) + 1
    elif vdir.parent == adir.resolve() and vdir.name[1:].isdigit():
        cutoff = int(vdir.name[1:])
    else:
        cutoff = latest + 1
    man = json.loads((adir / "manifest.json").read_text(encoding="utf-8"))
    kept = [v for v in man.get("versions") or [] if int(v.get("version") or 0) < cutoff]
    man = dict(man)
    man["versions"] = kept
    man["latestVersion"] = max((int(v.get("version") or 0) for v in kept), default=0)
    target = view / "assessments" / aid
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    for p in adir.iterdir():
        if p.is_file() and p.name != "manifest.json":
            shutil.copy2(p, target / p.name)
    for v in kept:
        src = adir / f"v{int(v.get('version') or 0)}"
        if src.is_dir():
            shutil.copytree(src, target / src.name)
    out.update({"assessmentId": aid, "cutoff": cutoff, "latestVersion": man["latestVersion"]})
    return out


def cmd_verify(a) -> int:
    """Re-stage a staged or published version from its recorded argv, decision
    files and value policy into a temp root and report whether the version comes
    back equal: ``contentDigest`` (the bundle) and ``inputsDigest`` (the manifest),
    with the differences ``streamcurves.compare`` finds listed. Exit 0 when both are equal."""
    try:
        from streamcurves.compare import compare
    except ImportError:  # a checkout without the module: the sibling script's report
        from compare_runs import compare  # noqa: E402
    vdir = Path(a.version_dir).resolve()
    doc = json.loads((vdir / lib.PROVENANCE_FILE).read_text(encoding="utf-8"))
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    manifest = doc.get("manifest") or {}
    region = manifest.get("region") or {}
    argv = list((manifest.get("agent") or {}).get("argv") or [])
    root = Path(a.out).resolve() if a.out else Path(tempfile.mkdtemp(prefix="streamcurves-verify-"))
    root.mkdir(parents=True, exist_ok=True)
    # the decision files the version was staged from, checked before the re-stage
    # runs an hour on changed inputs
    moved = reviewer_inputs_drift(manifest, vdir)
    for m in moved:
        print(f"[verify] decision file {m} since the stage")
    ns = restage_namespace(argv, region, root, manifest,
                           decisions_root=getattr(a, "decisions_root", None))
    # what the recorded stage could carry forward from (never the version under test)
    view = carry_view(vdir, region, manifest, root)
    ns.carry_root = view["root"] if view else None
    carried = ("nothing (full refit)" if view is None
               else f"nothing (no published version before)" if not view["latestVersion"]
               else f"{view['assessmentId']} v{view['latestVersion']}")
    print(f"[verify] re-staging L3-{region.get('code')} ({region.get('name')}) from "
          f"{argv[0]} (refit {ns.refit}, value policy {ns.value_policy or 'default'}, "
          f"carrying from {carried}) -> {root}")
    rc = int(cmd_stage(ns))
    out_dir = Path(ns.out)
    packet_path = out_dir / "review_packet.json"
    staged = (json.loads(packet_path.read_text(encoding="utf-8")).get("staged") or {}
              if packet_path.is_file() else {})
    report = {"versionDir": str(vdir), "root": str(root), "exit": rc,
              "restagedDir": staged.get("path"), "decisionFiles": moved, "carryView": view}
    if rc != 0 or not staged.get("path"):
        report["equal"] = False
        report["reason"] = (f"the re-stage exited {rc}" if rc != 0
                            else "the re-stage produced no staged version")
        _write_verify_report(root, report)
        print(f"[verify] NOT EQUAL: {report['reason']}; report -> {root / VERIFY_REPORT}")
        return 1
    new_vdir = Path(staged["path"])
    new_bundle = json.loads((new_vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    new_doc = json.loads((new_vdir / lib.PROVENANCE_FILE).read_text(encoding="utf-8"))
    new_manifest = new_doc.get("manifest") or {}
    report["contentDigest"] = {"recorded": bundle.get("contentDigest"),
                               "restaged": new_bundle.get("contentDigest")}
    report["inputsDigest"] = {"recorded": manifest.get("inputsDigest"),
                              "restaged": new_manifest.get("inputsDigest")}
    for key in ("contentDigest", "inputsDigest"):
        report[key]["equal"] = bool(report[key]["recorded"]) and (
            report[key]["recorded"] == report[key]["restaged"])
    # which digest inputs moved (the code fingerprint moves whenever the tree is
    # edited between the two stages; a decision file, the policy or a config
    # names itself here too)
    report["inputsDigest"]["differingKeys"] = digest_payload_differences(manifest, new_manifest)
    report["compare"] = compare(vdir, new_vdir)
    report["equal"] = report["contentDigest"]["equal"] and report["inputsDigest"]["equal"]
    _write_verify_report(root, report)
    c = report["compare"]["curves"]
    d = report["compare"]["decisions"]
    moved_keys = report["inputsDigest"]["differingKeys"]
    print(f"[verify] contentDigest {'equal' if report['contentDigest']['equal'] else 'DIFFERS'}; "
          f"inputsDigest {'equal' if report['inputsDigest']['equal'] else 'DIFFERS'}"
          + (f" ({', '.join(moved_keys)})" if moved_keys else "") + "; "
          f"curves {len(c['identical'])} identical, {len(c['differ'])} differ, "
          f"{len(c['only_a'])} only recorded, {len(c['only_b'])} only re-staged; "
          f"decisions {d['same']} same, {len(d['differ'])} differ")
    for mid, notes in c["differ"].items():
        print(f"[verify]   {mid}: {'; '.join(notes)}")
    for label, diffs in d["differ"].items():
        print(f"[verify]   {label}: " + "; ".join(f"{f} {va!r} -> {vb!r}"
                                                  for f, (va, vb) in diffs.items()))
    print(f"[verify] {'EQUAL' if report['equal'] else 'NOT EQUAL'}; report -> {root / VERIFY_REPORT}")
    return 0 if report["equal"] else 1


def digest_payload_differences(recorded: dict, restaged: dict) -> list[str]:
    """The keys of the inputs digest payload (``provenance.digest_payload_from_manifest``)
    whose values differ between two manifests, sorted; empty when the two digest the
    same inputs. A manifest an older app wrote is compared under its own rules."""
    try:
        a = pv.digest_payload_from_manifest(recorded or {})
        b = pv.digest_payload_from_manifest(restaged or {})
    except ValueError as exc:
        return [f"unreadable: {exc}"]
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def _write_verify_report(root: Path, report: dict) -> Path:
    p = Path(root) / VERIFY_REPORT
    p.write_text(json.dumps(report, indent=1, default=_json_default) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# open: the project path and the deep link of a staged run's candidate
# --------------------------------------------------------------------------- #
DEFAULT_APP_URL = "http://127.0.0.1:8012/"


def project_path_of(out_dir: Path) -> Path:
    """The project a run folder opens in StreamCurves: the staged version's
    session when the run was staged, else the run's own assessment file."""
    out_dir = Path(out_dir)
    packet_path = out_dir / "review_packet.json"
    if packet_path.is_file():
        staged = json.loads(packet_path.read_text(encoding="utf-8")).get("staged") or {}
        if staged.get("path") and (Path(staged["path"]) / lib.SESSION_FILE).is_file():
            return Path(staged["path"]) / lib.SESSION_FILE
    return out_dir / "assessment.streamcurves.json"


def candidate_keys_of(out_dir: Path) -> set[str]:
    """Every candidate key the run's register names (the staged provenance's
    ``candidateRegister`` rows, else the evidence package's candidates.json)."""
    out_dir = Path(out_dir)
    keys: set[str] = set()
    packet_path = out_dir / "review_packet.json"
    docs = []
    if packet_path.is_file():
        staged = json.loads(packet_path.read_text(encoding="utf-8")).get("staged") or {}
        prov = Path(staged["path"]) / lib.PROVENANCE_FILE if staged.get("path") else None
        if prov and prov.is_file():
            docs.append(json.loads(prov.read_text(encoding="utf-8")).get("candidateRegister") or {})
    for p in sorted((out_dir / EVIDENCE_DIR).rglob("candidates.json")) if (out_dir / EVIDENCE_DIR).is_dir() else []:
        docs.append(json.loads(p.read_text(encoding="utf-8")))
    for reg in docs:
        for row in reg.get("rows") or []:
            if row.get("candidateKey"):
                keys.add(str(row["candidateKey"]))
    return keys


def deep_link(project: Path, candidate: Optional[str], base: str = DEFAULT_APP_URL) -> str:
    """The URL that opens ``project`` in a running StreamCurves and lands on one
    candidate of its register (``candidate=<key>``; the page is the Compare section
    of Select final curves)."""
    url = base.rstrip("/") + "/?project=" + quote(str(project), safe="")
    if candidate:
        url += "&candidate=" + quote(str(candidate), safe="")
    return url


def cmd_open(a) -> int:
    out_dir = Path(a.out).resolve()
    if not out_dir.is_dir():
        raise SystemExit(f"no run folder at {out_dir}")
    project = project_path_of(out_dir)
    if a.candidate:
        known = candidate_keys_of(out_dir)
        if known and a.candidate not in known:
            print(f"[open] the run's register names no candidate {a.candidate!r} "
                  f"({len(known)} keys recorded)")
            return 2
    print(project)
    print(deep_link(project, a.candidate, base=a.base_url))
    return 0


def _default_refit() -> str:
    """What ``--refit`` means when absent: the methodology's carry-forward default."""
    try:
        return ra.refit_mode(None)
    except Exception:  # noqa: BLE001 - a broken config is reported by the stage itself
        return "missing"


def build_parser() -> argparse.ArgumentParser:
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
    s.add_argument("--maintainer", default="GM", help="initials recorded as the reviewer and the author "
                   "(default: the owner's, GM)")
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
    s.add_argument("--curve-decisions", default=None, metavar="FILE",
                   help="the owner's curve decisions (REF-15): the curve_decisions.json the "
                        "workspace keeps in the region's run folder, applied after SELECT-04")
    s.add_argument("--candidate-register", default=None, metavar="FILE",
                   help="the candidate_register.json Select final curves keeps beside "
                        "curve_decisions.json: the curves a person added for comparison and "
                        "the reasons for not selecting one; merged into the build's register")
    s.add_argument("--decisions-root", default=None, metavar="FOLDER",
                   help="the folder the decision files are recorded relative to "
                        "(reviewerInputs.files); default: the run folder's parent, where the "
                        "Region builder keeps a region's files beside its run")
    s.add_argument("--refit", choices=ra.REFIT_MODES, default=_default_refit(),
                   help="missing (the methodology's carry-forward default) carries every "
                        "published curve forward and builds the rest; all builds every curve "
                        "afresh and never reads the canonical library (owner holds and forced "
                        "sources still apply). Recorded as inputs.refit and "
                        "inputs.reference.carryForward")
    s.add_argument("--value-policy", default=nrsa_dataset.DEFAULT_VALUE_POLICY,
                   choices=list(nrsa_dataset.VALUE_POLICY_IDS) + [nrsa_dataset.VALUE_POLICY_V1_ALIAS],
                   help="the id the pooled archive's values are read under (DATA-11); the "
                        f"default is {nrsa_dataset.DEFAULT_VALUE_POLICY}, a replay passes the "
                        "id its version recorded (inputs.nrsa_dataset.policy)")
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
    s.set_defaults(fn=stage_locked)

    m = sub.add_parser("stage-many",
                       help="stage several regions with the same flags, one run folder "
                            "l3-<code> each under --out-root, with a summary table; "
                            "--workers stages several at once; never promotes")
    m.add_argument("--l3", action="append", default=[], metavar="CODE",
                   help="an EPA Level III code (repeat); the name comes from the NRSA site table")
    m.add_argument("--name", action="append", default=[], metavar="CODE=NAME",
                   help="override the region name for a code")
    m.add_argument("--out-root", required=True)
    m.add_argument("--decisions-root", default=str(rb.default_runs_root()), metavar="FOLDER",
                   help="where each region's own decision files are read from: "
                        "<FOLDER>/l3-<code>/curve_decisions.json (REF-15, seeded from the "
                        "published version under --refit missing), owner_decisions.json (the "
                        "answers), coverage_exceptions.json (merged over --coverage-exceptions) "
                        "and candidate_register.json. Default: the Region builder's runs root")
    m.add_argument("--refit", choices=ra.REFIT_MODES, default=_default_refit(),
                   help="missing carries every published curve forward; all builds every curve "
                        "afresh and never reads the canonical library (see stage)")
    m.add_argument("--value-policy", default=nrsa_dataset.DEFAULT_VALUE_POLICY,
                   choices=list(nrsa_dataset.VALUE_POLICY_IDS) + [nrsa_dataset.VALUE_POLICY_V1_ALIAS],
                   help="the id the pooled archive's values are read under (see stage)")
    m.add_argument("--rename-legacy-folders", action="store_true",
                   help="one-time: rename the l3-<code>-<slug> run folders under --out-root to "
                        "l3-<code> before staging (with no --l3, rename and stop)")
    m.add_argument("--screen", default="functional", choices=["functional", "at_risk_or_better"])
    m.add_argument("--no-screen", action="store_true", help="offline smoke only")
    m.add_argument("--no-streamcat", action="store_true", help="offline smoke only")
    m.add_argument("--maintainer", default="GM", help="initials recorded as the reviewer and the author "
                   "(default: the owner's, GM)")
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
    m.add_argument("--isolated", action="store_true",
                   help="stage each region in its own process with thread caps, as --workers above 1 "
                        "does, even one at a time (compare a parallel run with this, not with "
                        "the in-process serial path)")
    m.add_argument("--workers", type=int, default=1,
                   help="regions staged at once, each in its own process (1: one after another "
                        "in this process, as before). Keep it low when the screen calls live "
                        "services")
    m.set_defaults(fn=cmd_stage_many)

    c = sub.add_parser("census", help="reference support per region and metric, before any build "
                                      "(pressure-screen method; offline; minutes per sparse region, "
                                      "where the basis ladder walks the national donors for every "
                                      "insufficient metric)")
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
    p.add_argument("--status", choices=("draft", "preliminary", "policy"), default="draft",
                   help="lifecycle status for the promoted version: draft (default; the "
                        "decisions are confirmed but no one reviewed the curves in the "
                        "app), preliminary (the packet was reviewed exhaustively), or "
                        "policy (preliminary when the run folder passes every gate of "
                        "config/methodology/promotion_policy.yaml it can answer, else "
                        "draft with the blockers on promote_record.json)")
    p.add_argument("--gate-report", default=None, metavar="REPORT_JSON",
                   help="with --status policy: the equivalence gate's report.json, so that "
                        "gate counts too (without it the gate is not evaluated)")
    p.set_defaults(fn=cmd_promote)

    r = sub.add_parser("replay", help="apply the policy to published versions offline")
    r.add_argument("--version-dir", action="append", required=True)
    r.add_argument("--policy", default=None)
    r.add_argument("--enable-policy", action="append", default=[])
    r.add_argument("--json", default=None)
    r.set_defaults(fn=cmd_replay)

    v = sub.add_parser("verify", help="re-stage a version from its recorded command, decision "
                                      "files and value policy into a temp root and report "
                                      "whether its digests come back equal")
    v.add_argument("--version-dir", required=True,
                   help="a staged (<out>/library/assessments/<id>/vN) or published version folder")
    v.add_argument("--out", default=None,
                   help="the root to re-stage into (default: a fresh temp folder, kept)")
    v.add_argument("--decisions-root", default=None, metavar="FOLDER",
                   help="where the recorded decision files are today (<FOLDER>/l3-<code>/...), "
                        "when the version records them by a path relative to another checkout")
    v.set_defaults(fn=cmd_verify)

    o = sub.add_parser("open", help="print a staged run's project path and the candidate=<key> "
                                    "deep link that opens one candidate in StreamCurves")
    o.add_argument("--out", required=True, help="the run folder")
    o.add_argument("--candidate", default=None, metavar="KEY",
                   help="a candidate key of the run's register (cand-...)")
    o.add_argument("--base-url", default=DEFAULT_APP_URL,
                   help=f"where StreamCurves runs (default {DEFAULT_APP_URL})")
    o.set_defaults(fn=cmd_open)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
