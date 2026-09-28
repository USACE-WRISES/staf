"""The national campaign's record (campaign Round 3, WP-R3): the manifest a plan freezes,
the index of what a campaign produced, the promotion policy's gates read from a run folder,
the batch summary an owner reads, and the confirmation an operator writes by hand.

Pure helpers: folders and documents in, documents out. ``scripts/run_campaign.py`` is the
command line over them and the only writer of a campaign root; ``run_region_batch.py promote
--status policy`` reads the same gates. Nothing here fills a confirmation: ``confirmedBy``,
``approvedBy`` and an owner's initials are only ever validated, never written by code.

Every document written from here names its schema, the campaign it belongs to and the
sha256 of every input it read, so a reader can tell what a number rests on.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from . import code_identity
from . import decisions as dec
from . import methodology
from . import owner_curves as oc
from .config import read_yaml
from .paths import CONFIG_DIR

# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #
MANIFEST_FILE = "manifest.json"
COMMANDS_FILE = "commands.md"
STATE_FILE = "STATE.md"
INDEX_JSON = "index.json"
INDEX_CSV = "index.csv"
ELIGIBILITY_FILE = "eligibility.json"
RUNS_DIR = "runs"
COMPARE_DIR = "compare"
REGION_STATE_FILE = "region_state.json"
PROMOTION_POLICY_FILE = "promotion_policy.yaml"
STANDING_POLICY_FILE = "standing_decisions.yaml"
PROMOTION_POLICY_PATH = CONFIG_DIR / "methodology" / PROMOTION_POLICY_FILE

MANIFEST_SCHEMA = "staf-campaign-manifest/1"
INDEX_SCHEMA = "staf-campaign-index/1"
ELIGIBILITY_SCHEMA = "staf-campaign-eligibility/1"
BATCH_SCHEMA = "staf-campaign-promotion-batch/1"
REGION_STATE_SCHEMA = "staf-campaign-region-state/1"
PACKAGE_SCHEMA = "staf-campaign-package/1"
COMPARE_SCHEMA = "staf-campaign-compare/1"

PURPOSES = ("fast", "frozen", "d3")
SUPPORT_CLASSES = ("local20plus", "local10to19", "local1to9", "noLocal", "frameUnder10")
STATES = ("staged", "staged-open", "no-data", "unsupported", "incomplete", "refused", "failed",
          "not-started")
GATE_IDS = ("frozen-record", "rules-applied", "pending-confirmable", "owner-decisions-honored",
            "portfolio-approvals", "equivalence-proven", "record-complete",
            # promotion policy 1.2 (methodology 0.16, REF-16)
            "flagged-transfer-disclosed")
#: the gates a run folder answers on its own (``promote --status policy`` without a gate report)
RUN_FOLDER_GATES = tuple(g for g in GATE_IDS if g != "equivalence-proven")
#: the label every rehearsal, test, pilot and gate run records as its maintainer
REHEARSAL_LABEL = "Rehearsal (not an owner decision)"
#: the label of the frozen national pass: what promote resolves under the owner's confirmation
POLICY_CANDIDATE_LABEL = "policy-candidate " + dec.PENDING_SUFFIX
#: the reviewer name the pending-confirmable gate confirms a COPY of the record under; the copy
#: is discarded, so the name is never a confirmation and never a person
CHECK_REVIEWER = "eligibility check (not a confirmation)"
PLACEHOLDER = "<to be confirmed>"
#: stage-many's own words for a code with no candidate stations (``batch_summary.json``)
NO_DATA_ERROR = "no NRSA candidate sites"
#: what ``cmd_stage`` prints when a build has no bundle and when a gate refused the staged publish
BUNDLE_ERROR_MARK = "[batch] no bundle to stage:"
REFUSED_MARK = "[batch] staged publish refused:"
WITHHELD_MARK = "[batch] staged publish withheld:"
#: the coverage gate's own words (``library._require_documented_coverage``)
COVERAGE_WORDS = ("no metric and no documented reason", "coverage exception")
#: the decision files the manifest records per region, under ``<decisions root>/l3-<code>/``
DECISION_FILE_NAMES = ("curve_decisions", "owner_decisions", "coverage_exceptions",
                       "candidate_register")
#: the run-folder files a package carries per region
REGION_PACKAGE_FILES = ("review_packet.json", "stage_complete.json", "rebuild_ledger.json",
                        "standing_decisions_applied.json", "evidence.json", REGION_STATE_FILE)
SOURCE_BASES = ("local", "l2", "nars9", "l1", "national", "modeled", "published")
INDEX_COLUMNS = ("l3", "name", "nars9", "l2", "supportClass", "state", "exit", "seconds",
                 "peakMemoryMB", "stagedVersion", "contentDigest", "functionsCovered",
                 "perFunction", "sourceMix", "withheld", "curves", "decisionsApplied",
                 "openItems", "openBlocking", "openAdvisory", "hardStops", "promoteEligible",
                 "promoteReasons", "inputsDigest", "runFolder", "promoteCommand", "stateDetail")
BUNDLE_FILE = "assessment.deep.json"
PROVENANCE_FILE = "provenance.json"
SESSION_FILE = "session.streamcurves.json"
META_FILE = "meta.json"
EVIDENCE_FILE = "evidence.json"
PACKET_FILE = "review_packet.json"
STAGE_COMPLETE = "stage_complete.json"
APPLIED_FILE = "standing_decisions_applied.json"
LEDGER_FILE = "rebuild_ledger.json"
RUN_MANIFEST_FILE = "run_manifest.json"
STAGE_LOG = "stage.log"


# --------------------------------------------------------------------------- #
# small tools
# --------------------------------------------------------------------------- #
def sha256_hex(path) -> Optional[str]:
    """The bare hex digest of a file, None when it is not a file."""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_file(path) -> Optional[str]:
    """``sha256:<hex>`` of a file, None when it is not a file."""
    hexd = sha256_hex(path)
    return ("sha256:" + hexd) if hexd else None


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def read_json(path) -> Any:
    """A JSON document, or None when the file is absent or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path, doc) -> Path:
    """Canonical JSON (sorted keys, one space indent, LF), so two writes of the same
    facts are the same bytes."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n",
                 encoding="utf-8", newline="\n")
    return p


def write_text(path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")
    return p


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short(digest) -> str:
    """The first twelve hex characters of a digest, for a sentence."""
    text = str(digest or "")
    return text[:19] if text.startswith("sha256:") else text[:12]


def quote(arg) -> str:
    """One command-line token as a person types it: quoted when it holds a space or a
    parenthesis (the maintainer labels do)."""
    text = str(arg)
    if not text:
        return '""'
    if re.search(r"[\s()]", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def render_command(argv: Iterable) -> str:
    return " ".join(quote(a) for a in argv)


def _int_code(code) -> tuple:
    text = str(code).strip()
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def _hex(digest) -> str:
    """The hex part of a digest, with or without the ``sha256:`` prefix."""
    return str(digest or "").split(":", 1)[-1]


def run_folder_name(code) -> str:
    return f"l3-{str(code).strip()}"


# --------------------------------------------------------------------------- #
# the promotion policy file
# --------------------------------------------------------------------------- #
def load_promotion_policy(path=None) -> dict:
    """``config/methodology/promotion_policy.yaml`` with its sha256 and path under
    ``meta`` (the shape ``decisions.load_policy`` gives the standing decisions)."""
    p = Path(path) if path else PROMOTION_POLICY_PATH
    doc = read_yaml(p) or {}
    meta = dict(doc.get("meta") or {})
    meta["path"] = str(p)
    meta["sha256"] = sha256_file(p)
    doc["meta"] = meta
    doc["gates"] = list(doc.get("gates") or [])
    return doc


def promotion_policy_version(policy: Mapping) -> str:
    return str((policy.get("meta") or {}).get("version") or "")


def promotion_policy_record(policy: Mapping) -> dict:
    """What every document that applies the policy records of it."""
    meta = policy.get("meta") or {}
    return {"version": promotion_policy_version(policy), "sha256": meta.get("sha256"),
            "status": meta.get("status"), "path": meta.get("path"),
            # promotion policy 1.2 (D14): the support classes the owner accepted as
            # Preliminary-eligible, so a batch summary can cite the clause
            "acceptedSupportClasses": accepted_support_classes(policy)}


def accepted_support_classes(policy: Mapping) -> list[str]:
    """The support classes promotion policy 1.2's acceptance clause names as
    Preliminary-eligible (``acceptance.preliminary_eligible_support_classes``);
    empty under an older policy file."""
    block = policy.get("acceptance") or {}
    return [str(c) for c in (block.get("preliminary_eligible_support_classes") or [])]


def validate_promotion_policy(policy: Mapping) -> list[str]:
    """Every way the policy file could be wrong, as plain sentences; empty means usable."""
    problems: list[str] = []
    meta = policy.get("meta") or {}
    for key in ("version", "status", "approved_under", "owner_decision"):
        if not meta.get(key):
            problems.append(f"meta.{key} is missing")
    # promotion policy 1.2 (D14): the acceptance clause names known support classes
    # and the owner's decision it quotes
    block = policy.get("acceptance")
    if isinstance(block, Mapping):
        for c in accepted_support_classes(policy):
            if c not in SUPPORT_CLASSES:
                problems.append(f"acceptance names an unknown support class {c!r}")
        for key in ("owner_decision", "statement"):
            if not block.get(key):
                problems.append(f"acceptance.{key} is missing")
    elif block is not None:
        problems.append("acceptance must be a mapping")
    gates = policy.get("gates") or []
    if not gates:
        problems.append("gates is empty")
    seen: list[str] = []
    for i, g in enumerate(gates):
        gid = str((g or {}).get("id") or "")
        where = f"gate {i} ({gid or 'no id'})"
        if not gid:
            problems.append(f"{where}: id is missing")
        elif gid in seen:
            problems.append(f"{where}: duplicate id")
        elif gid not in GATE_IDS:
            problems.append(f"{where}: no check implements it")
        seen.append(gid)
        for key in ("title", "evidence", "check"):
            if not (g or {}).get(key):
                problems.append(f"{where}: {key} is missing")
        expected = f"streamcurves.campaign.gate_{gid.replace('-', '_')}"
        if gid in GATE_IDS and str((g or {}).get("check") or "") != expected:
            problems.append(f"{where}: check must name {expected}")
    for gid in GATE_IDS:
        if gid not in seen:
            problems.append(f"the policy lists no gate {gid}")
    return problems


# --------------------------------------------------------------------------- #
# what a plan reads: crosswalk, census, station screen, an earlier index
# --------------------------------------------------------------------------- #
def read_crosswalk(path) -> list[dict]:
    """``[{l3, name}]`` in file order from ``data/ecoregion_code_crosswalk.csv``."""
    out = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            code = str(row.get("us_l3code") or row.get("l3") or "").strip()
            if code:
                out.append({"l3": code, "name": str(row.get("us_l3name") or row.get("name") or "").strip()})
    return out


def read_census(path) -> dict[str, list[dict]]:
    """The census table by Level III code (``run_region_batch.py census``)."""
    out: dict[str, list[dict]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out.setdefault(str(row.get("l3") or "").strip(), []).append(dict(row))
    return out


def census_counts(rows: Iterable[Mapping]) -> dict:
    """The metric counts by reference-support status of one region's census rows."""
    counts: dict[str, int] = {}
    n = 0
    for r in rows or []:
        n += 1
        st = str(r.get("status") or "")
        counts[st] = counts.get(st, 0) + 1
    return {"nMetrics": n,
            "nLocal": counts.get("local", 0) + counts.get("local_relaxed", 0),
            "nBorrowedL2": counts.get("borrowed_l2", 0),
            "nBorrowedNars9": counts.get("borrowed_nars9", 0),
            "nBorrowedL1": counts.get("borrowed_l1", 0),
            "nNational": counts.get("national", 0),
            "nModeled": counts.get("modeled", 0),
            "nPublished": counts.get("published", 0),
            "nInsufficient": counts.get("insufficient", 0)}


def support_class(n_frame: int, n_strict: int) -> str:
    """The plan's support classes: fewer than ten stations in the frame, no strict
    reference station, one to nine, ten to nineteen, twenty or more."""
    if int(n_frame) < 10:
        return "frameUnder10"
    if int(n_strict) == 0:
        return "noLocal"
    if int(n_strict) < 10:
        return "local1to9"
    if int(n_strict) < 20:
        return "local10to19"
    return "local20plus"


def station_screen_summary(path, codes: Optional[Iterable[str]] = None) -> dict[str, dict]:
    """Per Level III code from ``station_screen.parquet``: the NARS-9 group and the Level II
    and I parents (the most frequent value), the stations in the wadeable frame and the ones
    that pass the strict screen. The frame is the table's ``wadeable`` flag, the same count
    the census reports as in frame."""
    import pandas as pd
    cols = ["l3", "l2", "l1", "nars9", "wadeable", "pass_strict"]
    df = pd.read_parquet(Path(path), columns=cols)
    df["l3"] = df["l3"].astype(str)
    want = {str(c) for c in codes} if codes is not None else None
    out: dict[str, dict] = {}
    for code, grp in df.groupby("l3"):
        code = str(code)
        if want is not None and code not in want:
            continue

        def mode(col):
            vals = grp[col].dropna().astype(str)
            return str(vals.mode().iat[0]) if len(vals) else ""

        out[code] = {"nars9": mode("nars9"), "l2": mode("l2"), "l1": mode("l1"),
                     "nStationsAll": int(len(grp)),
                     "nFrame": int(grp["wadeable"].fillna(False).astype(bool).sum()),
                     "nStrict": int(grp["pass_strict"].fillna(False).astype(bool).sum())}
    return out


def read_order_seconds(index_csv) -> dict[str, float]:
    """``{code: seconds}`` from an earlier campaign's ``index.csv`` (the fast pass)."""
    out: dict[str, float] = {}
    with Path(index_csv).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            code = str(row.get("l3") or "").strip()
            try:
                out[code] = float(row.get("seconds") or 0)
            except ValueError:
                out[code] = 0.0
    return out


def order_regions(rows: list[dict], *, seconds: Optional[Mapping] = None) -> list[dict]:
    """The stage order: the fast pass's seconds descending when an earlier index is
    given, else the frame size descending; ties by code. Sets ``order`` (1-based)."""
    if seconds is not None:
        key = lambda r: (-float(seconds.get(str(r["l3"]), 0.0) or 0.0), _int_code(r["l3"]))  # noqa: E731
    else:
        key = lambda r: (-int(r.get("nFrame") or 0), _int_code(r["l3"]))  # noqa: E731
    ordered = sorted(rows, key=key)
    for i, r in enumerate(ordered, start=1):
        r["order"] = i
    return ordered


def campaign_id(purpose: str, config_sha256: Optional[str], n_boot: int) -> str:
    """``r3-<purpose>-<configsha8>-b<nboot>``: the first eight hex characters of the
    methodology config's sha256 name the frozen rules."""
    hexd = str(config_sha256 or "").split(":", 1)[-1]
    return f"r3-{purpose}-{hexd[:8] or 'noconfig'}-b{int(n_boot)}"


def maintainer_label_problem(label: str) -> Optional[str]:
    """Why a maintainer label cannot run a campaign stage: a bare name (``GM``) is a
    person, and a campaign never records a person as its maintainer. The rehearsal label
    and the policy-candidate label are the two the plan names."""
    text = str(label or "").strip()
    if not text:
        return "the maintainer label is empty"
    if " " not in text and not text.endswith(dec.PENDING_SUFFIX):
        return (f"the maintainer label {text!r} reads as a person's name; a campaign runs "
                f"under {REHEARSAL_LABEL!r} or {POLICY_CANDIDATE_LABEL!r}")
    return None


def decision_file_shas(decisions_root, code) -> dict[str, Optional[str]]:
    """The sha256 of each decision file under ``<root>/l3-<code>/`` that exists, else null."""
    base = Path(decisions_root) / run_folder_name(code) if decisions_root else None
    return {name: (sha256_file(base / f"{name}.json") if base is not None else None)
            for name in DECISION_FILE_NAMES}


# --------------------------------------------------------------------------- #
# the manifest
# --------------------------------------------------------------------------- #
def stage_many_tokens(root, *, workers: int, n_boot: int, maintainer: str, refit: str,
                      decisions_root: Optional[str], codes: Iterable[str]) -> list[str]:
    """The stage-many command a campaign runs, region codes in stage order."""
    tokens = ["stage-many", "--out-root", str(Path(root) / RUNS_DIR), "--workers", str(int(workers)),
              "--n-boot", str(int(n_boot)), "--maintainer", str(maintainer), "--refit", str(refit)]
    if decisions_root:
        tokens += ["--decisions-root", str(decisions_root)]
    for code in codes:
        tokens += ["--l3", str(code)]
    return tokens


def stage_tokens(root, region: Mapping, *, n_boot: int, maintainer: str, refit: str,
                 decisions_root: Optional[str], decision_files: Optional[Mapping] = None) -> list[str]:
    """The single-region stage command that records the same inputs as the stage-many line
    (its decision files passed by name, as stage-many resolves them per region)."""
    code = str(region["l3"])
    tokens = ["stage", "--l3", code, "--name", str(region.get("stageName") or region.get("name") or code),
              "--out", str(Path(root) / RUNS_DIR / run_folder_name(code)), "--n-boot", str(int(n_boot)),
              "--maintainer", str(maintainer), "--refit", str(refit)]
    if decisions_root:
        tokens += ["--decisions-root", str(decisions_root)]
    flags = {"curve_decisions": "--curve-decisions", "reviewer_decisions": "--reviewer-decisions",
             "region_coverage_exceptions": "--coverage-exceptions", "candidate_register": "--candidate-register"}
    for attr, flag in flags.items():
        value = (decision_files or {}).get(attr)
        if value:
            tokens += [flag, str(value)]
    return tokens


def build_manifest(*, purpose: str, worktree: str, commit: Optional[str], git_dirty: Optional[bool],
                   code_fingerprint: str, inputs: Mapping, promotion_policy: Mapping, run: Mapping,
                   gate_report: Optional[str], sources: Mapping, commands: Mapping,
                   regions: list[dict], created_at: Optional[str] = None) -> dict:
    """The frozen record of a campaign (schema ``staf-campaign-manifest/1``)."""
    inputs = dict(inputs)
    inputs["promotionPolicy"] = promotion_policy_record(promotion_policy)
    return {
        "schema": MANIFEST_SCHEMA,
        "identity": {"campaignId": campaign_id(purpose, (inputs.get("methodology") or {}).get("config_sha256"),
                                               int(run["nBoot"])),
                     "purpose": purpose, "worktree": str(worktree), "commit": commit,
                     "gitDirty": git_dirty, "createdAt": created_at or now_iso()},
        "code": {"fingerprint": code_fingerprint,
                 "scope": [f"{sub}/{','.join(patterns)}" for sub, patterns in code_identity.SCOPE]},
        "inputs": inputs,
        "run": dict(run),
        "gateReport": str(gate_report) if gate_report else None,
        "sources": dict(sources),
        "commands": dict(commands),
        "regions": list(regions),
    }


def read_manifest(root) -> dict:
    """The campaign root's manifest; raises ``ValueError`` when there is none or it is
    another schema."""
    p = Path(root) / MANIFEST_FILE
    doc = read_json(p)
    if not isinstance(doc, dict):
        raise ValueError(f"no campaign manifest at {p}; run plan first")
    if doc.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"{p} is {doc.get('schema')!r}, not {MANIFEST_SCHEMA}")
    return doc


def region_of(manifest: Mapping, code) -> Optional[dict]:
    for r in manifest.get("regions") or []:
        if str(r.get("l3")) == str(code):
            return r
    return None


def flatten(doc: Any, prefix: str = "") -> dict[str, Any]:
    """Dotted keys to leaf values (lists and None as themselves), for a drift listing."""
    out: dict[str, Any] = {}
    if isinstance(doc, Mapping):
        for k in sorted(doc):
            out.update(flatten(doc[k], f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix] = doc
    return out


def manifest_drift(manifest: Mapping, live: Mapping) -> list[str]:
    """Every recorded value the live tree no longer matches, one sentence each. ``live``:
    ``code`` (the worktree's fingerprint), ``runningCode`` (the fingerprint of the tree the
    checker runs from), ``gitDirty``, ``commit``, ``inputs`` (what ``region_inputs`` gives
    today, without ``reviewerFiles``), ``promotionPolicy`` (its sha256), ``copies`` (the sha of
    each policy copy beside the manifest), ``decisionFiles`` (per code) and ``stageManyLine``
    (the line commands.md carries, or None)."""
    out: list[str] = []
    identity = manifest.get("identity") or {}
    if identity.get("gitDirty") is not False:
        out.append("identity.gitDirty: the manifest does not record a clean tree")
    dirty = live.get("gitDirty")
    if dirty is None:
        out.append("identity.gitDirty: git cannot say whether the worktree is clean now")
    elif dirty:
        out.append("identity.gitDirty: the worktree has uncommitted changes now")
    if live.get("commit") != identity.get("commit"):
        out.append(f"identity.commit: recorded {identity.get('commit')}, now {live.get('commit')}")
    recorded_code = (manifest.get("code") or {}).get("fingerprint")
    for key in ("code", "runningCode"):
        if key in live and live.get(key) != recorded_code:
            out.append(f"code.fingerprint ({key}): recorded {short(recorded_code)}, now {short(live.get(key))}")
    recorded = flatten(manifest.get("inputs") or {})
    current = flatten(live.get("inputs") or {})
    policy_now = live.get("promotionPolicy")
    if policy_now is not None:
        current["promotionPolicy.sha256"] = policy_now
    for key in sorted(set(recorded) | set(current)):
        if key.startswith("promotionPolicy.") and key != "promotionPolicy.sha256":
            continue
        if key not in current:
            continue                      # a live value the checker did not compute
        if recorded.get(key) != current.get(key):
            out.append(f"inputs.{key}: recorded {recorded.get(key)!r}, now {current.get(key)!r}")
    copies = live.get("copies") or {}
    expected = {PROMOTION_POLICY_FILE: ((manifest.get("inputs") or {}).get("promotionPolicy") or {}).get("sha256"),
                STANDING_POLICY_FILE: (((manifest.get("inputs") or {}).get("policy") or {}).get("sha256"))}
    for name, sha in expected.items():
        # region_inputs records the standing policy's sha as bare hex; compare the hex parts
        if name in copies and _hex(copies[name]) != _hex(sha):
            out.append(f"{name} beside the manifest: recorded {short(sha)}, now {short(copies[name])}")
    for code, files in sorted((live.get("decisionFiles") or {}).items(), key=lambda kv: _int_code(kv[0])):
        row = region_of(manifest, code) or {}
        recorded_files = row.get("decisionFiles") or {}
        for name in DECISION_FILE_NAMES:
            if recorded_files.get(name) != (files or {}).get(name):
                out.append(f"regions.l3-{code}.decisionFiles.{name}: recorded "
                           f"{short(recorded_files.get(name)) or 'null'}, now "
                           f"{short((files or {}).get(name)) or 'null'}")
    line = live.get("stageManyLine")
    if line is not None and line != render_command((manifest.get("commands") or {}).get("stageMany") or []):
        out.append("commands.md: the stage-many line differs from the manifest's")
    return out


def commands_markdown(manifest: Mapping, *, python: str, app_root: str, root: str) -> str:
    """``commands.md``: the stage-many line, the single-region lines, the index and
    eligibility lines, every one runnable from the worktree's app folder."""
    ident = manifest.get("identity") or {}
    run = manifest.get("run") or {}
    cmds = manifest.get("commands") or {}
    batch = f"{quote(python)} -B scripts/run_region_batch.py"
    runner = f"{quote(python)} -B scripts/run_campaign.py"
    lines = [f"# Commands for campaign {ident.get('campaignId')}", "",
             f"Run every line from `{app_root}` (commit {ident.get('commit')}) with the shared "
             f"interpreter. `{RUNS_DIR}/` under `{root}` is the stage-many out root.",
             "", "## Stage every region (resume by running the same line again)", "",
             "```", f"{batch} {render_command(cmds.get('stageMany') or [])}", "```", "",
             f"Workers {run.get('workers')}, {run.get('nBoot')} resamples, refit {run.get('refit')}, "
             f"maintainer label {quote(run.get('maintainer'))}"
             + (f", configuration root {run['configRoot']} (STREAMCURVES_CONFIG_ROOT)" if run.get("configRoot") else "")
             + ".", "", "## One region at a time (the same inputs as the stage-many line)", ""]
    for r in manifest.get("regions") or []:
        tokens = (cmds.get("stage") or {}).get(str(r.get("l3")))
        if tokens:
            lines.append(f"- L3-{r.get('l3')} {r.get('name')}: `{batch} {render_command(tokens)}`")
        else:
            lines.append(f"- L3-{r.get('l3')} {r.get('name')}: no NRSA candidate sites, nothing to stage")
    lines += ["", "## After the stages", "", "```", f"{runner} index --root {quote(root)}",
              f"{runner} eligibility --root {quote(root)}",
              f"{runner} batch-summary --root {quote(root)} --batch <id> [--l3 CODE ...]",
              f"{runner} compare --root {quote(root)}", "```", ""]
    return "\n".join(lines)


def state_markdown(manifest: Mapping, *, root: str, python: str) -> str:
    """The ``STATE.md`` stub a campaign root carries (notes are gitignored)."""
    ident = manifest.get("identity") or {}
    run = manifest.get("run") or {}
    regions = manifest.get("regions") or []
    return "\n".join([
        f"# Campaign {ident.get('campaignId')}", "",
        f"- Purpose: {ident.get('purpose')}; commit {ident.get('commit')} in {ident.get('worktree')} "
        f"(clean at plan time); code fingerprint {short((manifest.get('code') or {}).get('fingerprint'))}.",
        f"- Run: {run.get('workers')} workers, {run.get('nBoot')} resamples, refit {run.get('refit')}, "
        f"maintainer label {quote(run.get('maintainer'))}.",
        f"- Regions: {len(regions)} ({sum(1 for r in regions if r.get('inputsDigest'))} with an expected "
        f"inputs digest); promotion policy {((manifest.get('inputs') or {}).get('promotionPolicy') or {}).get('version')}.",
        f"- Planned at {ident.get('createdAt')}.", "",
        "## What to run next", "",
        f"1. `{quote(python)} -B scripts/run_campaign.py run --root {quote(root)} --dry-run`, then without "
        "`--dry-run` (the stage-many line in commands.md; the same line resumes an interrupted pass).",
        f"2. `{quote(python)} -B scripts/run_campaign.py index --root {quote(root)}` and "
        f"`eligibility --root {quote(root)}` once the stages end.",
        "3. `batch-summary --batch <id>` for the owner, a hand-written confirmation, then `confirm`.",
        "", "Rewrite this file as the campaign moves; the manifest is the frozen record.", ""])


# --------------------------------------------------------------------------- #
# reading a run folder
# --------------------------------------------------------------------------- #
def staged_version_dir(run_dir, packet: Optional[Mapping] = None) -> Optional[Path]:
    """The staged version folder a run's packet names (with its bundle on disk), else the
    latest version under the run's own staged library, else None."""
    run_dir = Path(run_dir)
    if packet is None:
        packet = read_json(run_dir / PACKET_FILE)
    staged = (packet or {}).get("staged") or {}
    if staged.get("path"):
        p = Path(str(staged["path"]))
        if (p / BUNDLE_FILE).is_file():
            return p
        # the campaign root moved: the same version under this run folder's library
        parts = p.parts
        if "assessments" in parts:
            i = parts.index("assessments")
            local = run_dir / "library" / Path(*parts[i:])
            if (local / BUNDLE_FILE).is_file():
                return local
    base = run_dir / "library" / "assessments"
    if not base.is_dir():
        return None
    best: Optional[Path] = None
    for man in sorted(base.glob("*/manifest.json")):
        doc = read_json(man) or {}
        ver = int(doc.get("latestVersion") or 0)
        cand = man.parent / f"v{ver}"
        if ver > 0 and (cand / BUNDLE_FILE).is_file():
            best = cand
    return best


def session_fields(session: Optional[Mapping]) -> dict:
    """The raw fields of a session file (promote reads them the same way)."""
    if not isinstance(session, Mapping):
        return {}
    fields = session.get("fields")
    return dict(fields) if isinstance(fields, Mapping) else dict(session)


def record_intact(run_dir, rec: Optional[Mapping]) -> tuple[bool, list[str]]:
    """Whether every output a ``stage_complete.json`` records is still on disk with its
    sha256, and what is not."""
    if not isinstance(rec, Mapping):
        return False, [f"{STAGE_COMPLETE} is missing"]
    outputs = rec.get("outputs") or {}
    if not outputs:
        return False, [f"{STAGE_COMPLETE} records no outputs"]
    problems = []
    for rel, sha in sorted(outputs.items()):
        now = sha256_hex(Path(run_dir) / rel)
        if now is None:
            problems.append(f"{rel}: missing")
        elif now != str(sha).split(":", 1)[-1]:
            problems.append(f"{rel}: changed")
    return (not problems), problems


def stage_refusal(log_text: Optional[str]) -> tuple[Optional[str], str]:
    """``(kind, sentence)`` of what kept a completed stage from a staged version, read from
    its log: ``bundle`` (no bundle to stage), ``coverage`` (the coverage gate refused the
    publish), ``refused`` (another gate), ``withheld`` (the evidence package failed), or
    ``(None, "")``."""
    for line in (log_text or "").splitlines():
        line = line.strip()
        if line.startswith(BUNDLE_ERROR_MARK):
            return "bundle", line[len(BUNDLE_ERROR_MARK):].strip()
        if line.startswith(REFUSED_MARK):
            sentence = line[len(REFUSED_MARK):].strip()
            kind = "coverage" if any(w in sentence for w in COVERAGE_WORDS) else "refused"
            return kind, sentence
        if line.startswith(WITHHELD_MARK):
            return "withheld", line[len(WITHHELD_MARK):].strip()
    return None, ""


#: what ``cmd_stage`` prints when it refuses before a packet exists (exit 2)
STAGE_REFUSED_MARK = "[batch] REFUSED:"


def stage_refused_sentence(log_text: Optional[str]) -> str:
    """The sentence of a stage that refused to proceed (exit 2), or an empty string."""
    for line in (log_text or "").splitlines():
        line = line.strip()
        if line.startswith(STAGE_REFUSED_MARK):
            return line[len(STAGE_REFUSED_MARK):].strip()
    return ""


def campaign_jobs(runs_root) -> dict[str, dict]:
    """The stage-many jobs of a campaign by region code: the last event of each job in
    ``.campaign/index.jsonl`` (a running campaign has no summary yet) with what
    ``summary.json`` adds once the run ended: ``state``, ``exit``, ``seconds``,
    ``peakMemoryMB``, ``attempts``, ``id``, ``label``, ``at``."""
    campaign = Path(runs_root) / ".campaign"
    out: dict[str, dict] = {}
    index = campaign / "index.jsonl"
    if index.is_file():
        for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            code = _code_of_label(ev.get("label"))
            if not code:
                continue
            rec = out.setdefault(code, {"attempts": 0, "id": ev.get("id"), "label": ev.get("label")})
            if ev.get("event") == "started":
                rec["attempts"] += 1
            rec.update({"state": ev.get("event"), "at": ev.get("at"), "id": ev.get("id")})
            for k in ("seconds", "exit", "peakMemoryMB"):
                if k in ev:
                    rec[k] = ev.get(k)
    summary = read_json(campaign / "summary.json") or {}
    for job_id, job in (summary.get("jobs") or {}).items():
        code = _code_of_label((job or {}).get("label"))
        if not code:
            continue
        rec = out.setdefault(code, {"attempts": 0, "id": job_id, "label": job.get("label")})
        rec.update({k: job.get(k) for k in ("state", "seconds", "exit", "peakMemoryMB") if k in job})
        rec["id"] = job_id
    return out


def _code_of_label(label) -> Optional[str]:
    m = re.match(r"^L3-(\S+)", str(label or ""))
    return m.group(1) if m else None


def batch_rows(runs_root) -> dict[str, dict]:
    doc = read_json(Path(runs_root) / "batch_summary.json")
    rows = (doc or {}).get("regions") if isinstance(doc, dict) else doc
    return {str(r.get("l3")): r for r in rows or [] if isinstance(r, dict) and r.get("l3") is not None}


def stage_log_text(runs_root, code, job: Optional[Mapping]) -> str:
    """The stage's own output: ``stage.log`` in the run folder (copied when a job
    completed), else the job's log under ``.campaign/jobs/<id>/``."""
    run_dir = Path(runs_root) / run_folder_name(code)
    p = run_dir / STAGE_LOG
    if not p.is_file() and job and job.get("id"):
        p = Path(runs_root) / ".campaign" / "jobs" / str(job["id"]) / "log.txt"
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    except OSError:
        return ""


def classify_region(run_dir, *, packet: Optional[Mapping], batch_row: Optional[Mapping],
                    job: Optional[Mapping], log_text: str, stage_record: Optional[Mapping]) -> tuple[str, str]:
    """``(state, detail)`` of one region from its artifacts alone (the index's vocabulary)."""
    error = str((batch_row or {}).get("error") or "")
    if NO_DATA_ERROR in error and not packet:
        return "no-data", error
    jstate = (job or {}).get("state")
    jexit = (job or {}).get("exit")
    if jstate == "failed":
        attempts = int((job or {}).get("attempts") or 0)
        tail = f" after {attempts} attempt(s)" if attempts else ""
        if jexit in (2, 3):
            sentence = stage_refused_sentence(log_text) or stage_refusal(log_text)[1]
            why = sentence or ("another run was staging the region" if jexit == 3
                               else "an unresolved share or a source failure")
            return "refused", f"exit {jexit}{tail}: {why}"
        return "failed", f"exit {jexit}{tail}: {(error or 'see the job log').strip()[-200:]}"
    bexit = (batch_row or {}).get("exit")
    if job is None and batch_row and not packet and bexit not in (None, 0):
        if bexit in (2, 3):
            return "refused", f"exit {bexit}: {error or 'refused before a packet was written'}"
        return "failed", f"exit {bexit}: {error or 'see the batch summary'}"
    vdir = staged_version_dir(run_dir, packet) if packet else None
    if packet and vdir is not None:
        n_open = len(packet.get("open_items") or [])
        n_hard = len(packet.get("hard_stops") or [])
        if n_open or n_hard:
            return "staged-open", f"{n_open} open item(s), {n_hard} hard stop(s)"
        ok, problems = record_intact(run_dir, stage_record)
        if not ok:
            return "incomplete", ("staged, but the stage record is " +
                                  ("missing" if stage_record is None else "not intact: " + "; ".join(problems[:3])))
        return "staged", f"staged v{(packet.get('staged') or {}).get('version')}, record intact"
    if packet:
        kind, sentence = stage_refusal(log_text)
        missing = int(((packet.get("coverage") or {}).get("missing")) or 0)
        if kind in ("bundle", "coverage"):
            return "unsupported", sentence
        if kind is None and missing:
            return "unsupported", f"{missing} function(s) neither covered nor documented; the coverage gate refuses the publish"
        return "incomplete", sentence or "the packet names no staged version"
    if jstate in ("started", "retrying"):
        return "not-started", f"stage {jstate} at {(job or {}).get('at')}"
    if jstate == "skipped":
        return "incomplete", "the job was skipped as already staged, but no packet is on disk"
    return "not-started", ""


# --------------------------------------------------------------------------- #
# what a staged region scores: per function and by source
# --------------------------------------------------------------------------- #
_LEDGER_WORDS = {"refitted": "fitted", "carried": "carried", "fixed": "fixed",
                 "owner_sourced": "owner-sourced"}
_ORDER = ("fitted", "carried", "fixed", "owner-sourced")


def staf_function_ids() -> list[str]:
    from . import deep_export
    return [str(f.get("id")) for f in deep_export.deep_read_staf_crosswalk()]


def per_function(ledger: Optional[Mapping], bundle: Optional[Mapping],
                 function_ids: Optional[Iterable[str]] = None) -> dict[str, str]:
    """Function id to disposition: ``fitted`` / ``carried`` / ``fixed`` (joined with ``+``
    when a function's metrics differ; ``owner-sourced`` for a curve the owner chose) from
    the rebuild ledger's selected rows, ``documented-gap`` for a coverage exclusion the
    bundle records, ``undocumented-gap`` otherwise. Without a ledger a scored function
    reads ``scored``."""
    from .provenance import LEDGER_SELECTED
    ids = list(function_ids) if function_ids is not None else staf_function_ids()
    selected: dict[str, set] = {}
    for row in (ledger or {}).get("rows") or []:
        if not isinstance(row, Mapping) or row.get("disposition") not in LEDGER_SELECTED:
            continue
        fid = str(row.get("functionId") or "")
        selected.setdefault(fid, set()).add(_LEDGER_WORDS.get(str(row.get("disposition")), str(row.get("disposition"))))
    scored = {str(b.get("functionId")) for b in (bundle or {}).get("metricsByFunction") or [] if b.get("metrics")}
    excluded = {str(e.get("functionId")) for e in ((bundle or {}).get("functionCoverage") or {}).get("exclusions") or []
                if isinstance(e, Mapping)}
    out: dict[str, str] = {}
    for fid in ids:
        if fid in selected:
            words = sorted(selected[fid], key=lambda w: _ORDER.index(w) if w in _ORDER else 9)
            out[fid] = "+".join(words)
        elif fid in scored and ledger is None:
            out[fid] = "scored"
        elif fid in excluded:
            out[fid] = "documented-gap"
        else:
            out[fid] = "undocumented-gap"
    return out


_OPTION_BASIS = {"l3_local": "local", "l3_regional": "local", "local": "local",
                 "l2_regional": "l2", "nars9_regional": "nars9", "l1_regional": "l1",
                 "national_3c": "national", "national_3a": "national", "national": "national",
                 "modeled": "modeled", "published": "published", "fixed": "fixed",
                 "sqt": "owner", "owner_entered": "owner", "other_assessment": "owner",
                 "earlier_version": "owner"}
_LEVEL_BASIS = {"l3": "local", "l2": "l2", "nars9": "nars9", "l1": "l1"}
_STATUS_BASIS = {"local": "local", "local_relaxed": "local", "borrowed_l2": "l2",
                 "borrowed_nars9": "nars9", "borrowed_l1": "l1", "national": "national",
                 "modeled": "modeled", "published": "published"}


def source_mix(ledger: Optional[Mapping], packet: Optional[Mapping]) -> dict[str, int]:
    """Metrics the version scores by the basis of their curve: ``local``, ``l2``, ``nars9``,
    ``l1``, ``national``, ``modeled``, ``published`` (always present), plus ``fixed``,
    ``carried`` and ``owner`` when any metric reads so. From the ledger's selected rows (one
    count per metric), else from the packet's reference support rows in the bundle."""
    from .provenance import LEDGER_SELECTED
    counts = {k: 0 for k in SOURCE_BASES}
    seen: set = set()
    rows = (ledger or {}).get("rows") or []
    if rows:
        for row in rows:
            if not isinstance(row, Mapping) or row.get("disposition") not in LEDGER_SELECTED:
                continue
            mk = str(row.get("metric"))
            if mk in seen:
                continue
            seen.add(mk)
            option = str(row.get("option") or "")
            basis = _OPTION_BASIS.get(option)
            if option == "carried" or basis is None:
                level = str(((row.get("pool") or {}).get("level")) or "")
                basis = _LEVEL_BASIS.get(level, "carried" if option == "carried" else "other")
            counts[basis] = counts.get(basis, 0) + 1
        return counts
    for r in ((packet or {}).get("reference") or {}).get("support") or []:
        if not isinstance(r, Mapping) or not r.get("in_bundle"):
            continue
        mk = str(r.get("metric"))
        if mk in seen:
            continue
        seen.add(mk)
        basis = _STATUS_BASIS.get(str(r.get("status") or ""), "other")
        counts[basis] = counts.get(basis, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# the index
# --------------------------------------------------------------------------- #
def index_row(root, region: Mapping, *, jobs: Mapping, batch: Mapping, eligibility: Optional[Mapping],
              function_ids: list[str], promote_command: Optional[Callable[[Path], str]] = None) -> dict:
    """One index row for a manifest region, from the run folder's artifacts alone."""
    root = Path(root)
    runs_root = root / RUNS_DIR
    code = str(region.get("l3"))
    run_dir = runs_root / run_folder_name(code)
    packet = read_json(run_dir / PACKET_FILE)
    packet = packet if isinstance(packet, dict) else None
    batch_row = batch.get(code)
    job = jobs.get(code)
    log_text = stage_log_text(runs_root, code, job)
    stage_record = read_json(run_dir / STAGE_COMPLETE)
    stage_record = stage_record if isinstance(stage_record, dict) else None
    state, detail = classify_region(run_dir, packet=packet, batch_row=batch_row, job=job,
                                    log_text=log_text, stage_record=stage_record)
    vdir = staged_version_dir(run_dir, packet) if packet else None
    bundle = read_json(vdir / BUNDLE_FILE) if vdir else None
    meta = read_json(vdir / META_FILE) if vdir else None
    prov = read_json(vdir / PROVENANCE_FILE) if vdir else None
    ledger = read_json(run_dir / LEDGER_FILE)
    if not isinstance(ledger, dict) and isinstance(prov, dict):
        ledger = prov.get("metricLedger")
    ledger = ledger if isinstance(ledger, dict) else None
    run_manifest = read_json(run_dir / RUN_MANIFEST_FILE)
    coverage = (bundle or {}).get("functionCoverage") if isinstance(bundle, dict) else None
    covered = ((coverage or {}).get("covered") if coverage else None)
    if covered is None and packet:
        covered = (packet.get("coverage") or {}).get("covered")
    withheld = None
    if packet and isinstance(packet.get("reference"), dict):
        withheld = len(packet["reference"].get("withheld") or [])
    elif isinstance(bundle, dict):
        withheld = len(bundle.get("insufficientReferenceSupport") or [])
    elig = (eligibility or {}).get("regions", {}).get(code) if eligibility else None
    open_split = split_open_items(None, packet) if packet else None
    exit_code = (batch_row or {}).get("exit")
    if job and job.get("exit") is not None:
        exit_code = job.get("exit")
    seconds = (batch_row or {}).get("seconds")
    if seconds is None and job:
        seconds = job.get("seconds")
    # the stage record's digest is the region digest the manifest expects (the provenance
    # manifest's inputsDigest is the run's own, another quantity), so no fallback
    inputs_digest = (stage_record or {}).get("inputsDigest")
    return {
        "l3": code, "name": region.get("name"), "nars9": region.get("nars9"), "l2": region.get("l2"),
        "supportClass": region.get("supportClass"), "state": state, "stateDetail": detail,
        "exit": exit_code, "seconds": seconds,
        "peakMemoryMB": (job or {}).get("peakMemoryMB"),
        "stagedVersion": ((packet or {}).get("staged") or {}).get("version") if vdir else None,
        "contentDigest": ((meta or {}).get("contentDigest") or (bundle or {}).get("contentDigest")) if vdir else None,
        "functionsCovered": covered,
        "perFunction": per_function(ledger, bundle, function_ids) if (ledger or bundle) else None,
        "sourceMix": source_mix(ledger, packet) if (ledger or packet) else None,
        "withheld": withheld,
        "curves": len(packet.get("curves") or []) if packet else None,
        "decisionsApplied": len(packet.get("decisions_applied") or []) if packet else None,
        "openItems": len(packet.get("open_items") or []) if packet else None,
        "openBlocking": len(open_split["blocking"]) if open_split else None,
        "openAdvisory": len(open_split["advisory"]) if open_split else None,
        "hardStops": len(packet.get("hard_stops") or []) if packet else None,
        "promoteEligible": (bool(elig.get("eligible")) if isinstance(elig, dict) else None),
        "promoteReasons": (list(elig.get("blockers") or []) if isinstance(elig, dict) else None),
        "inputsDigest": inputs_digest,
        "runFolder": str(run_dir),
        "promoteCommand": (promote_command(run_dir) if (promote_command and vdir) else None),
    }


def index_rows(root, manifest: Mapping, *, eligibility: Optional[Mapping] = None,
               promote_command: Optional[Callable[[Path], str]] = None) -> list[dict]:
    """One row per manifest region, in manifest (stage) order."""
    runs_root = Path(root) / RUNS_DIR
    jobs = campaign_jobs(runs_root)
    batch = batch_rows(runs_root)
    ids = staf_function_ids()
    return [index_row(root, r, jobs=jobs, batch=batch, eligibility=eligibility, function_ids=ids,
                      promote_command=promote_command) for r in manifest.get("regions") or []]


def index_document(manifest: Mapping, rows: list[dict], *, inputs: Mapping,
                   generated_at: Optional[str] = None) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"schema": INDEX_SCHEMA, "generatedAt": generated_at or now_iso(),
            "campaign": dict(manifest.get("identity") or {}),
            "code": dict(manifest.get("code") or {}),
            "policy": dict(((manifest.get("inputs") or {}).get("policy")) or {}),
            "promotionPolicy": dict(((manifest.get("inputs") or {}).get("promotionPolicy")) or {}),
            "inputs": dict(inputs), "columns": list(INDEX_COLUMNS),
            "counts": dict(sorted(counts.items())), "regions": rows}


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, sort_keys=True, default=str)
    return str(v)


def index_csv_text(rows: Iterable[Mapping]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(INDEX_COLUMNS)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in INDEX_COLUMNS])
    return buf.getvalue()


def region_state_document(row: Mapping, *, batch_summary_sha: Optional[str]) -> dict:
    """``runs/l3-<code>/region_state.json`` for a region that has no run folder of its own
    (no NRSA candidate sites), so the folder exists and says why it is empty."""
    return {"schema": REGION_STATE_SCHEMA, "l3": row.get("l3"), "name": row.get("name"),
            "state": row.get("state"), "detail": row.get("stateDetail"),
            "inputs": {"batch_summary.json": batch_summary_sha}}


# --------------------------------------------------------------------------- #
# the promotion policy's gates, read from a run folder
# --------------------------------------------------------------------------- #
def run_n_boot(run_dir, vdir: Optional[Path]) -> Optional[int]:
    """The resample count a stage ran at: ``run_manifest.json``, else the staged
    provenance's manifest, else the packet."""
    run_dir = Path(run_dir)
    for doc in (read_json(run_dir / RUN_MANIFEST_FILE),
                (read_json(vdir / PROVENANCE_FILE) or {}).get("manifest") if vdir else None):
        n = ((doc or {}).get("diagnostics") or {}).get("nBoot") if isinstance(doc, dict) else None
        if n is not None:
            return int(n)
    packet = read_json(run_dir / PACKET_FILE) or {}
    return int(packet["n_boot"]) if isinstance(packet, dict) and packet.get("n_boot") is not None else None


def gate_frozen_record(run_dir, expect: Mapping) -> tuple[bool, str]:
    """``stage_complete.json`` present; its inputsDigest equals the expected one; the code
    fingerprint did not move during the stage and equals the campaign's; n-boot equals the
    campaign's."""
    run_dir = Path(run_dir)
    rec = read_json(run_dir / STAGE_COMPLETE)
    if not isinstance(rec, dict):
        return False, f"{STAGE_COMPLETE} is missing: the region was not staged by stage-many, or its stage did not complete"
    problems = []
    want = expect.get("inputsDigest")
    if not want:
        problems.append("no expected inputs digest is recorded for the region")
    elif rec.get("inputsDigest") != want:
        problems.append(f"inputsDigest {short(rec.get('inputsDigest'))} is not the expected {short(want)}")
    code = rec.get("code") or {}
    if code.get("start") != code.get("end"):
        problems.append("the code fingerprint moved during the stage")
    if expect.get("codeFingerprint") and code.get("end") != expect.get("codeFingerprint"):
        problems.append(f"code fingerprint {short(code.get('end'))} is not the campaign's {short(expect.get('codeFingerprint'))}")
    n_boot = run_n_boot(run_dir, staged_version_dir(run_dir))
    if expect.get("nBoot") is not None and n_boot != int(expect["nBoot"]):
        problems.append(f"n-boot {n_boot} is not the campaign's {expect['nBoot']}")
    if problems:
        return False, "; ".join(problems)
    return True, (f"stage record intact: inputs {short(rec.get('inputsDigest'))}, code "
                  f"{short(code.get('end'))}, {n_boot} resamples")


def blocking_trigger(trigger) -> bool:
    """Whether an open item with this trigger blocks promotion (promotion policy 1.1):
    the queue's own blocking flag for the trigger (``provenance._TRIGGER_TIERS``) or an
    uncovered hard-stop trigger (``provenance.UNCOVERED_HARD_STOP_TRIGGERS``). Everything
    else is advisory: listed, never hidden, never a blocker."""
    from . import provenance as pv
    name = str(trigger or "")
    tiers = getattr(pv, "_TRIGGER_TIERS", {}) or {}
    entry = tiers.get(name)
    if entry and bool(entry[1]):
        return True
    return name in pv.UNCOVERED_HARD_STOP_TRIGGERS


def item_blocks(item: Mapping) -> bool:
    """An open item blocks when it carries the queue's blocking flag or its trigger blocks."""
    return bool((item or {}).get("blocking")) or blocking_trigger((item or {}).get("trigger"))


def split_open_items(applied: Optional[Mapping], packet: Optional[Mapping]) -> dict:
    """``{"blocking": [ids], "advisory": [ids], "hardStops": [ids]}`` over the applied file
    and the packet together (sorted, de-duplicated). A hard stop is never advisory."""
    blocking: set = set()
    advisory: set = set()
    hard: set = set()
    for src in (applied, packet):
        if not isinstance(src, Mapping):
            continue
        for i in src.get("hard_stops") or []:
            if isinstance(i, Mapping):
                hard.add(str(i.get("item_id")))
        for i in src.get("open_items") or []:
            if not isinstance(i, Mapping):
                continue
            (blocking if item_blocks(i) else advisory).add(str(i.get("item_id")))
    blocking |= hard
    advisory -= blocking
    return {"blocking": sorted(blocking), "advisory": sorted(advisory), "hardStops": sorted(hard)}


def advisory_open_items(run_dir) -> list[str]:
    """The advisory open item ids of a run folder, from the applied file and the packet."""
    run_dir = Path(run_dir)
    return split_open_items(read_json(run_dir / APPLIED_FILE), read_json(run_dir / PACKET_FILE))["advisory"]


def gate_rules_applied(run_dir) -> tuple[bool, str]:
    """Promotion policy 1.1: ``standing_decisions_applied.json`` and the packet carry no
    hard stop and no blocking open item (``item_blocks``). Advisory open items pass and
    are named in the detail, never hidden."""
    run_dir = Path(run_dir)
    applied = read_json(run_dir / APPLIED_FILE)
    packet = read_json(run_dir / PACKET_FILE)
    if not isinstance(applied, dict):
        return False, f"{APPLIED_FILE} is missing"
    if not isinstance(packet, dict):
        return False, f"{PACKET_FILE} is missing"
    split = split_open_items(applied, packet)
    hard_ids = split["hardStops"]
    blocking_ids = [i for i in split["blocking"] if i not in hard_ids]
    advisory_ids = split["advisory"]
    problems = []
    if blocking_ids:
        problems.append(f"{len(blocking_ids)} blocking open item(s): {', '.join(blocking_ids[:6])}")
    if hard_ids:
        problems.append(f"{len(hard_ids)} hard stop(s): {', '.join(hard_ids[:6])}")
    if problems:
        return False, "; ".join(problems)
    advisory = (f"{len(advisory_ids)} advisory open item(s) listed: {', '.join(advisory_ids[:6])}"
                if advisory_ids else "nothing left open")
    return True, (f"{len(applied.get('decisions') or [])} standing decision(s) applied, no hard stop "
                  f"and no blocking open item; {advisory}")


def gate_pending_confirmable(run_dir) -> tuple[bool, str]:
    """``decisions.confirm_pending_decisions`` on a deep copy of the staged provenance, with
    no override, succeeds and leaves nothing pending. The copy is discarded."""
    vdir = staged_version_dir(run_dir)
    doc = read_json(vdir / PROVENANCE_FILE) if vdir else None
    if not isinstance(doc, dict):
        return False, "no staged provenance to confirm"
    work = copy.deepcopy(doc)
    n_pending = sum(1 for r in work.get("records") or [] if dec.PENDING_SUFFIX in str((r or {}).get("reviewer") or ""))
    try:
        dec.confirm_pending_decisions(work, reviewer=CHECK_REVIEWER, date="eligibility-check", overrides=None)
    except ValueError as exc:
        return False, f"confirmation would be refused: {exc}"
    left = dec.pending_locations(work)
    if left:
        return False, f"still pending after confirmation at: {', '.join(left[:5])}"
    return True, f"{n_pending} pending decision(s) confirm cleanly with no override"


def gate_owner_decisions_honored(run_dir, decisions_file) -> tuple[bool, str]:
    """``owner_curves.decisions_changed`` false against the decision file the record names,
    and every decision the file records was applied by the staged build."""
    vdir = staged_version_dir(run_dir)
    session = read_json(vdir / SESSION_FILE) if vdir else None
    if not isinstance(session, dict):
        return False, "no staged session to read the applied decisions from"
    staged = [d for d in (session_fields(session).get("owner_curve_decisions") or []) if isinstance(d, Mapping)]
    now = oc.load_file(decisions_file) if decisions_file else []
    where = str(decisions_file) if decisions_file else "no decision file"
    if oc.decisions_changed(staged, now):
        return False, f"the region's curve decisions ({where}) are not the ones the staged build applied"
    staged_ids = {str(d.get("id")) for d in staged}
    unapplied = [str(d.get("id")) for d in now if str(d.get("id")) not in staged_ids]
    if unapplied:
        return False, f"recorded decisions the staged build did not apply: {', '.join(unapplied)}"
    return True, f"{len(now)} recorded decision(s) in {where}, all applied by the staged build"


def gate_portfolio_approvals(run_dir) -> tuple[bool, str]:
    """``meta.portfolioApprovals`` covers every function block with more than the
    portfolio maximum of metrics; the staged publish succeeded (a bundle is on disk)."""
    vdir = staged_version_dir(run_dir)
    bundle = read_json(vdir / BUNDLE_FILE) if vdir else None
    meta = read_json(vdir / META_FILE) if vdir else None
    if not isinstance(bundle, dict):
        return False, "no staged version with a bundle: the staged publish did not succeed"
    try:
        max_per = int(methodology.threshold("metric_portfolio.default_maximum_metrics_per_function", 2))
    except (KeyError, TypeError, ValueError):
        max_per = 2
    approved = {str(a.get("functionId")) for a in ((meta or {}).get("portfolioApprovals") or [])
                if isinstance(a, Mapping) and a.get("functionId") and str(a.get("approvedBy") or "").strip()}
    need = [(str(b.get("functionId")), len(b.get("metrics") or [])) for b in bundle.get("metricsByFunction") or []
            if len(b.get("metrics") or []) > max_per]
    missing = [f"{fid} ({n} metrics)" for fid, n in need if fid not in approved]
    if missing:
        return False, f"no recorded approval on: {', '.join(missing)}"
    return True, (f"{len(need)} function(s) carry more than {max_per} metrics, every one approved on the record"
                  if need else f"no function carries more than {max_per} metrics; the staged publish succeeded")


def gate_equivalence_proven(gate_report, commit) -> tuple[bool, str]:
    """``<gate root>/report.json`` has ``gatePassed: true`` for the campaign's commit (the
    report names the commit, or its folder does, as ``gate/<commit8>/``)."""
    if not gate_report:
        return False, "no gate report is named (the manifest's gateReport is null)"
    p = Path(str(gate_report))
    doc = read_json(p)
    if not isinstance(doc, dict):
        return False, f"no readable gate report at {p}"
    if doc.get("gatePassed") is not True:
        return False, f"{p} records gatePassed {doc.get('gatePassed')!r}"
    commit8 = str(commit or "")[:8]
    if not commit8:
        return False, "the campaign records no commit to match the gate report against"
    named = str(doc.get("commit") or "")
    if named:
        if not named.startswith(commit8):
            return False, f"{p} names commit {named[:8]}, not the campaign's {commit8}"
    elif not any(commit8 in part for part in p.resolve().parts):
        return False, f"{p} does not name the campaign's commit {commit8} (neither the report nor its folder)"
    return True, f"gatePassed true at {p} for commit {commit8}"


def cited_files(run_dir, packet: Optional[Mapping], vdir: Optional[Path]) -> set[str]:
    """Every run-folder file the packet cites, the staged version's files and the record
    files themselves, as paths relative to the run folder."""
    run_dir = Path(run_dir).resolve()
    out = {PACKET_FILE, "review_packet.md", APPLIED_FILE}
    p = packet or {}
    for key in ("ledger", "gallery", "gallery_html"):
        if isinstance(p.get(key), str) and p.get(key):
            out.add(str(p[key]))
    if p.get("evidence"):
        out.add(EVIDENCE_FILE)
        name = ((p.get("evidence") or {}).get("archive") or {}).get("name") if isinstance(p.get("evidence"), Mapping) else None
        if name:
            out.add(f"evidence/{name}")
    if (run_dir / "assessment.streamcurves.json").is_file():
        out.add("assessment.streamcurves.json")
    if vdir is not None:
        try:
            rel = Path(vdir).resolve().relative_to(run_dir)
        except ValueError:
            rel = None
        if rel is not None:
            for f in sorted(Path(vdir).rglob("*")):
                if f.is_file():
                    out.add(str(rel / f.relative_to(Path(vdir))).replace("\\", "/"))
    return out


def gate_record_complete(run_dir) -> tuple[bool, str]:
    """Provenance ``candidateRegister`` present; ``metricLedger`` rows with a known
    disposition; ``stage_complete.outputs`` names every file the packet cites and each is
    intact; ``evidence.json`` present in the staged version."""
    from .provenance import LEDGER_DISPOSITIONS
    run_dir = Path(run_dir)
    packet = read_json(run_dir / PACKET_FILE)
    packet = packet if isinstance(packet, dict) else None
    vdir = staged_version_dir(run_dir, packet)
    doc = read_json(vdir / PROVENANCE_FILE) if vdir else None
    problems = []
    if not isinstance(doc, dict):
        problems.append("no staged provenance")
    else:
        reg = doc.get("candidateRegister")
        if not (isinstance(reg, dict) and isinstance(reg.get("rows"), list)):
            problems.append("the provenance carries no candidateRegister")
        ledger = doc.get("metricLedger")
        rows = ledger.get("rows") if isinstance(ledger, dict) else None
        if not rows:
            problems.append("the provenance carries no metricLedger rows")
        else:
            bad = [f"{r.get('metric')}/{r.get('functionId')}: {r.get('disposition') or 'unknown'}"
                   for r in rows if not isinstance(r, Mapping) or r.get("disposition") not in LEDGER_DISPOSITIONS]
            if bad:
                problems.append(f"ledger rows with an unknown disposition: {', '.join(bad[:5])}")
        if not (vdir / EVIDENCE_FILE).is_file():
            problems.append(f"{EVIDENCE_FILE} is missing from the staged version")
    rec = read_json(run_dir / STAGE_COMPLETE)
    if not isinstance(rec, dict):
        problems.append(f"{STAGE_COMPLETE} is missing")
    else:
        outputs = rec.get("outputs") or {}
        missing = sorted(c for c in cited_files(run_dir, packet, vdir) if c not in outputs)
        if missing:
            problems.append(f"{STAGE_COMPLETE} does not name: {', '.join(missing[:6])}")
        ok, changed = record_intact(run_dir, rec)
        if not ok:
            problems.append("recorded outputs are not intact: " + "; ".join(changed[:4]))
    if problems:
        return False, "; ".join(problems)
    return True, "register, ledger, evidence reference and the output record are complete and intact"


def gate_flagged_transfer_disclosed(run_dir) -> tuple[bool, str]:
    """Promotion policy 1.2 (methodology 0.16, REF-16): every scoring entry of the
    staged bundle on a flagged transfer carries the four disclosure fields at the
    entry (``transferRisk`` unvalidated, ``transferValidation``, ``transferNote``,
    ``confidenceCap``) and a caveat stating that the transfer was not confirmed by
    the recovery test and that confidence is capped, the words DEEP prints. A
    bundle with no flagged curve passes."""
    from . import deep_export as dx
    vdir = staged_version_dir(run_dir)
    bundle = read_json(vdir / BUNDLE_FILE) if vdir else None
    if not isinstance(bundle, dict):
        return False, "no staged version with a bundle to read the flagged transfers from"
    flagged = dx.flagged_entries(bundle)
    problems = []
    for item in flagged:
        m = item["entry"]
        label = f"{item['functionId']}: {item['metricId']}"
        missing = [k for k in dx.FLAGGED_ENTRY_KEYS if m.get(k) in (None, "", {})]
        if str(m.get("transferRisk") or "") != dx.UNVALIDATED_RISK:
            missing.append("transferRisk=unvalidated")
        caveats = [str(c) for c in (m.get("curveCaveats") or [])]
        if not any("not confirmed by the recovery test" in c and "confidence capped" in c
                   for c in caveats):
            missing.append("limitation caveat")
        if missing:
            problems.append(f"{label} lacks {', '.join(dict.fromkeys(missing))}")
    if problems:
        return False, f"{len(problems)} flagged curve(s) not fully disclosed: " + "; ".join(problems[:6])
    if not flagged:
        return True, "no curve on a flagged transfer in the staged bundle"
    return True, (f"{len(flagged)} flagged curve(s), every one carrying transferRisk, "
                  "transferValidation, transferNote, confidenceCap and the limitation caveat")


def evaluate_gates(run_dir, *, expect: Mapping, decisions_file, policy: Mapping,
                   gate_report=None, skip: Iterable[str] = ()) -> dict:
    """Every gate of the promotion policy on one run folder, from its artifacts alone,
    never refitting. ``expect``: ``inputsDigest``, ``codeFingerprint``, ``nBoot``,
    ``commit`` and ``gateReport`` (the campaign manifest's, or the run's own record when
    promote reads a run outside a campaign). A gate in ``skip`` is recorded as not
    evaluated and does not count."""
    run_dir = Path(run_dir)
    report = gate_report or expect.get("gateReport")
    skip = set(skip)
    checks: dict[str, Callable[[], tuple[bool, str]]] = {
        "frozen-record": lambda: gate_frozen_record(run_dir, expect),
        "rules-applied": lambda: gate_rules_applied(run_dir),
        "pending-confirmable": lambda: gate_pending_confirmable(run_dir),
        "owner-decisions-honored": lambda: gate_owner_decisions_honored(run_dir, decisions_file),
        "portfolio-approvals": lambda: gate_portfolio_approvals(run_dir),
        "equivalence-proven": lambda: gate_equivalence_proven(report, expect.get("commit")),
        "record-complete": lambda: gate_record_complete(run_dir),
        "flagged-transfer-disclosed": lambda: gate_flagged_transfer_disclosed(run_dir),
    }
    gates: dict[str, dict] = {}
    for gate in policy.get("gates") or []:
        gid = str((gate or {}).get("id") or "")
        if gid in skip:
            gates[gid] = {"passed": None, "evaluated": False,
                          "detail": "not evaluated here (needs a gate report)" if gid == "equivalence-proven"
                          else "not evaluated here"}
            continue
        fn = checks.get(gid)
        if fn is None:
            gates[gid] = {"passed": False, "evaluated": True, "detail": "no check implements this gate"}
            continue
        passed, detail = fn()
        gates[gid] = {"passed": bool(passed), "evaluated": True, "detail": detail}
    blockers = [f"{gid}: {g['detail']}" for gid, g in gates.items() if g["evaluated"] and not g["passed"]]
    vdir = staged_version_dir(run_dir)
    return {"eligible": not blockers, "gates": gates, "blockers": blockers,
            "evaluated": [g for g, r in gates.items() if r["evaluated"]],
            "skipped": [g for g, r in gates.items() if not r["evaluated"]],
            # promotion policy 1.1: the advisory open items the rules-applied gate let
            # through, listed per region so a batch summary can show them
            "advisoryOpen": advisory_open_items(run_dir),
            "runFolder": str(run_dir), "stagedPath": str(vdir) if vdir else None}


def expectation_from_manifest(manifest: Mapping, code) -> Optional[dict]:
    """What the campaign manifest expects of a region's stage, or None when the manifest
    does not list the region."""
    row = region_of(manifest, code)
    if row is None:
        return None
    return {"inputsDigest": row.get("inputsDigest"),
            "codeFingerprint": (manifest.get("code") or {}).get("fingerprint"),
            "nBoot": (manifest.get("run") or {}).get("nBoot"),
            "commit": (manifest.get("identity") or {}).get("commit"),
            "gateReport": manifest.get("gateReport"),
            "source": f"campaign manifest {(manifest.get('identity') or {}).get('campaignId')}"}


def expectation_from_run(run_dir) -> dict:
    """The run's own record as the expectation (a run promoted outside a campaign)."""
    vdir = staged_version_dir(run_dir)
    man = ((read_json(vdir / PROVENANCE_FILE) or {}).get("manifest") if vdir else None) or {}
    if not man:
        man = read_json(Path(run_dir) / RUN_MANIFEST_FILE) or {}
    rec = read_json(Path(run_dir) / STAGE_COMPLETE)
    rec = rec if isinstance(rec, dict) else {}
    return {"inputsDigest": rec.get("inputsDigest"),
            "codeFingerprint": (man.get("agent") or {}).get("codeFingerprint"),
            "nBoot": (man.get("diagnostics") or {}).get("nBoot"),
            "commit": (man.get("agent") or {}).get("gitCommit"),
            "gateReport": None, "source": "the run's own record"}


def campaign_manifest_for_run(run_dir) -> Optional[tuple[Path, dict]]:
    """The campaign manifest a run folder belongs to (``<root>/runs/l3-<code>``), or None."""
    run_dir = Path(run_dir).resolve()
    root = run_dir.parent.parent
    if run_dir.parent.name != RUNS_DIR:
        return None
    try:
        return root / MANIFEST_FILE, read_manifest(root)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# the batch summary and the confirmation
# --------------------------------------------------------------------------- #
def region_batch_facts(run_dir) -> dict:
    """What the batch summary says of a staged region beyond the index: the policy
    decision ids applied, the approvals carried from an earlier version, the open items
    verbatim and the hard stops."""
    run_dir = Path(run_dir)
    packet = read_json(run_dir / PACKET_FILE)
    packet = packet if isinstance(packet, dict) else {}
    vdir = staged_version_dir(run_dir, packet or None)
    meta = read_json(vdir / META_FILE) if vdir else None
    approvals = (meta or {}).get("portfolioApprovals") or []
    return {"policyDecisionIds": list((packet.get("policy") or {}).get("applied_ids") or []),
            "carriedApprovals": sorted(str(a.get("functionId")) for a in approvals
                                       if isinstance(a, Mapping) and a.get("carriedFrom") is not None),
            "approvals": sorted(str(a.get("functionId")) for a in approvals if isinstance(a, Mapping)),
            "openItems": [{"item_id": i.get("item_id"), "trigger": i.get("trigger"),
                           "blocking": item_blocks(i), "question": i.get("question")}
                          for i in packet.get("open_items") or [] if isinstance(i, Mapping)],
            "hardStops": [str(i.get("item_id")) for i in packet.get("hard_stops") or [] if isinstance(i, Mapping)]}


def promote_tokens(run_folder, *, maintainer: str, date: str, rebake: bool, status: str = "preliminary") -> list[str]:
    tokens = ["promote", "--out", str(run_folder), "--maintainer", str(maintainer), "--status", status,
              "--date", str(date), "--publish-root", "apps/library"]
    if rebake:
        tokens.append("--rebake-deep")
    return tokens


def batch_summary_document(*, batch_id: str, manifest: Mapping, index_doc: Mapping, eligibility_doc: Mapping,
                           codes: Iterable[str], python: str, script: str, inputs: Mapping,
                           facts: Callable[[Path], dict], generated_at: Optional[str] = None) -> dict:
    """``promotion_batch_<id>.json``: the policy and the frozen identities, the eligible
    table, the exceptions table with their open items verbatim, and the promote commands in
    batch order with placeholders where the confirmation will speak."""
    rows = {str(r.get("l3")): r for r in index_doc.get("regions") or []}
    elig = eligibility_doc.get("regions") or {}
    inputs_block = manifest.get("inputs") or {}
    eligible, exceptions = [], []
    for code in codes:
        code = str(code)
        row = rows.get(code) or {}
        e = elig.get(code) or {}
        run_dir = Path(row.get("runFolder") or (Path(str(index_doc.get("root") or "")) / RUNS_DIR / run_folder_name(code)))
        f = facts(run_dir)
        per = row.get("perFunction") or {}
        gaps = sorted(fid for fid, d in per.items() if str(d).endswith("-gap"))
        base = {"l3": code, "name": row.get("name"), "supportClass": row.get("supportClass"),
                "state": row.get("state"), "runFolder": str(run_dir)}
        if e.get("eligible"):
            eligible.append({**base, "sourceMix": row.get("sourceMix"),
                             "functionsCovered": row.get("functionsCovered"), "gaps": gaps,
                             "policyDecisionIds": f["policyDecisionIds"],
                             "carriedApprovals": f["carriedApprovals"],
                             # promotion policy 1.1: what stays open under an eligible
                             # region is listed, never hidden
                             "advisoryOpen": [str(i) for i in (e.get("advisoryOpen") or [])],
                             "contentDigest": row.get("contentDigest"),
                             "stagedVersion": row.get("stagedVersion"),
                             "inputsDigest": row.get("inputsDigest")})
        else:
            exceptions.append({**base, "stateDetail": row.get("stateDetail"),
                               "openItems": f["openItems"], "hardStops": f["hardStops"],
                               "blockers": list(e.get("blockers") or []) or
                               (["not evaluated by eligibility"] if not e else [])})
    commands = []
    for i, r in enumerate(eligible):
        tokens = promote_tokens(r["runFolder"], maintainer=PLACEHOLDER, date=PLACEHOLDER,
                                rebake=(i == len(eligible) - 1))
        commands.append(render_command([python, "-B", script, *tokens]))
    return {"schema": BATCH_SCHEMA, "batchId": str(batch_id), "generatedAt": generated_at or now_iso(),
            "campaign": dict(manifest.get("identity") or {}),
            "policy": dict(inputs_block.get("promotionPolicy") or {}),
            "frozenIdentities": {"code": dict(manifest.get("code") or {}),
                                 "methodology": dict(inputs_block.get("methodology") or {}),
                                 "standingDecisions": dict(inputs_block.get("policy") or {}),
                                 "nrsaManifest": inputs_block.get("nrsaManifest"),
                                 "stationScreen": inputs_block.get("stationScreen"),
                                 "coverageExceptions": inputs_block.get("coverageExceptions"),
                                 "gateReport": manifest.get("gateReport")},
            "inputs": dict(inputs), "regions": [str(c) for c in codes],
            "eligible": eligible, "exceptions": exceptions, "promoteCommands": commands,
            "confirmation": {"file": f"promotion_batch_{batch_id}.confirmation.json",
                             "fields": ["batchId", "policyVersion", "campaignId", "regions", "confirmedBy",
                                        "confirmedAt", "statement"],
                             "note": "Written by hand after the owner answers; no script fills confirmedBy. "
                                     "confirm validates it and prints the promote commands with the "
                                     "confirming maintainer and date in place of the placeholders."}}


def _md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v).replace("|", "/").replace("\n", " ")
                                     for v in r) + " |")
    return out


def _mix_text(mix: Optional[Mapping]) -> str:
    if not mix:
        return ""
    return ", ".join(f"{k} {v}" for k, v in mix.items() if v)


def batch_summary_markdown(doc: Mapping) -> str:
    """``promotion_batch_<id>.md``, what the owner reads."""
    camp = doc.get("campaign") or {}
    pol = doc.get("policy") or {}
    ids = doc.get("frozenIdentities") or {}
    meth = ids.get("methodology") or {}
    lines = [f"# Promotion batch {doc.get('batchId')}: campaign {camp.get('campaignId')}", "",
             f"Promotion policy {pol.get('version')} ({pol.get('status')}, {short(pol.get('sha256'))}), owner "
             "decision D2 (Preliminary by policy, 2026-09-24). A region listed as eligible passed every gate "
             "from its recorded artifacts; every exception keeps its staged Draft in the campaign root.",
             "", "## Frozen identities", "",
             f"- Campaign {camp.get('campaignId')} ({camp.get('purpose')}), commit {camp.get('commit')}, "
             f"worktree {camp.get('worktree')}, planned {camp.get('createdAt')}.",
             f"- Code fingerprint {short((ids.get('code') or {}).get('fingerprint'))}; methodology "
             f"{meth.get('methodology_version')} (config {short(meth.get('config_sha256'))}, rule catalog "
             f"{short(meth.get('rule_catalog_sha256'))}).",
             f"- Standing decisions {(ids.get('standingDecisions') or {}).get('version')} "
             f"({short((ids.get('standingDecisions') or {}).get('sha256'))}); NRSA manifest "
             f"{short(ids.get('nrsaManifest'))}; station screen {short(ids.get('stationScreen'))}.",
             f"- Gate report: {ids.get('gateReport') or 'none named'}.",
             "", f"## Eligible ({len(doc.get('eligible') or [])})", ""]
    eligible = doc.get("eligible") or []
    if eligible:
        lines += _md_table(["L3", "region", "support", "source mix", "functions", "gaps", "policy decisions",
                            "carried approvals", "advisory open", "content digest"],
                           [[r["l3"], r.get("name"), r.get("supportClass"), _mix_text(r.get("sourceMix")),
                             f"{r.get('functionsCovered')} of 20", ", ".join(r.get("gaps") or []) or "none",
                             ", ".join(r.get("policyDecisionIds") or []) or "none",
                             ", ".join(r.get("carriedApprovals") or []) or "none",
                             ", ".join(r.get("advisoryOpen") or []) or "none",
                             short(r.get("contentDigest"))] for r in eligible])
    else:
        lines.append("None.")
    lines += ["", f"## Exceptions ({len(doc.get('exceptions') or [])})", ""]
    exceptions = doc.get("exceptions") or []
    if exceptions:
        lines += _md_table(["L3", "region", "state", "open items", "blockers"],
                           [[r["l3"], r.get("name"), f"{r.get('state')}: {r.get('stateDetail') or ''}".strip(": "),
                             "; ".join(f"{i.get('item_id')} ({i.get('trigger')}{', blocking' if i.get('blocking') else ''}): "
                                       f"{i.get('question')}" for i in r.get("openItems") or []) or "none",
                             "; ".join(r.get("blockers") or []) or "none"] for r in exceptions])
    else:
        lines.append("None.")
    lines += ["", "## Promote commands, in batch order", "",
              "Run from a checkout at the campaign's commit with STAF_LIBRARY_PUBLISH=1 after the "
              "confirmation is recorded; `confirm` prints the same lines with the placeholders filled "
              "from the confirmation file.", "", "```"]
    lines += list(doc.get("promoteCommands") or []) or ["(no eligible region)"]
    lines += ["```", "", "## Confirmation", "",
              f"After the owner answers, write `{(doc.get('confirmation') or {}).get('file')}` by hand with "
              "batchId, policyVersion, campaignId, regions, confirmedBy, confirmedAt and statement (the "
              "owner's words and date). Nothing in the runner fills confirmedBy.", ""]
    return "\n".join(lines)


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def validate_confirmation(doc: Any, *, batch: Mapping, manifest: Mapping) -> list[str]:
    """Why a hand-written ``promotion_batch_<id>.confirmation.json`` cannot authorize the
    batch, one sentence each; empty means it can."""
    problems: list[str] = []
    if not isinstance(doc, Mapping):
        return ["the confirmation is not a JSON object"]
    if str(doc.get("batchId") or "") != str(batch.get("batchId") or ""):
        problems.append(f"batchId {doc.get('batchId')!r} is not the batch summary's {batch.get('batchId')!r}")
    want_policy = str((batch.get("policy") or {}).get("version") or "")
    if str(doc.get("policyVersion") or "") != want_policy:
        problems.append(f"policyVersion {doc.get('policyVersion')!r} is not the policy applied ({want_policy!r})")
    want_id = str((manifest.get("identity") or {}).get("campaignId") or "")
    if str(doc.get("campaignId") or "") != want_id:
        problems.append(f"campaignId {doc.get('campaignId')!r} is not the campaign's ({want_id!r})")
    regions = doc.get("regions")
    eligible = {str(r.get("l3")) for r in batch.get("eligible") or []}
    if not isinstance(regions, list) or not regions:
        problems.append("regions must list at least one region code")
    else:
        for code in regions:
            if str(code) not in eligible:
                problems.append(f"region {code} is not eligible in the batch summary")
    if not str(doc.get("confirmedBy") or "").strip():
        problems.append("confirmedBy is empty")
    if not str(doc.get("statement") or "").strip():
        problems.append("statement is empty (the owner's words and date)")
    when = str(doc.get("confirmedAt") or "").strip()
    if not _ISO_DATE.match(when):
        problems.append("confirmedAt is not an ISO date (YYYY-MM-DD)")
    else:
        try:
            datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            problems.append(f"confirmedAt {when!r} is not a valid ISO date")
    return problems


def confirmed_promote_commands(batch: Mapping, confirmation: Mapping, *, python: str, script: str) -> list[str]:
    """The promote lines of the confirmed regions in batch order, the confirming
    maintainer and date in place of the placeholders, ``--rebake-deep`` on the last."""
    by_code = {str(r.get("l3")): r for r in batch.get("eligible") or []}
    wanted = [str(c) for c in confirmation.get("regions") or []]
    ordered = [c for c in (str(x) for x in batch.get("regions") or []) if c in wanted and c in by_code]
    out = []
    for i, code in enumerate(ordered):
        tokens = promote_tokens(by_code[code]["runFolder"], maintainer=str(confirmation.get("confirmedBy")),
                                date=str(confirmation.get("confirmedAt")), rebake=(i == len(ordered) - 1))
        out.append(render_command([python, "-B", script, *tokens]))
    return out


# --------------------------------------------------------------------------- #
# the package
# --------------------------------------------------------------------------- #
def package_members(root, manifest: Mapping, *, notes=None) -> list[tuple[str, Path]]:
    """``(member path, source file)`` of everything a campaign package carries, sorted."""
    root = Path(root)
    members: dict[str, Path] = {}

    def add(arc: str, p: Path) -> None:
        if p.is_file():
            members[arc.replace("\\", "/")] = p

    for name in (MANIFEST_FILE, PROMOTION_POLICY_FILE, STANDING_POLICY_FILE, COMMANDS_FILE, STATE_FILE,
                 INDEX_JSON, INDEX_CSV, ELIGIBILITY_FILE):
        add(name, root / name)
    for p in sorted(root.glob("promotion_batch_*")):
        add(p.name, p)
    for p in sorted((root / COMPARE_DIR).rglob("*")) if (root / COMPARE_DIR).is_dir() else []:
        if p.is_file():
            add(str(p.relative_to(root)), p)
    decisions_root = (manifest.get("run") or {}).get("decisionsRoot")
    for r in manifest.get("regions") or []:
        code = str(r.get("l3"))
        run_dir = root / RUNS_DIR / run_folder_name(code)
        for name in REGION_PACKAGE_FILES:
            add(f"{RUNS_DIR}/{run_folder_name(code)}/{name}", run_dir / name)
        for name, sha in (r.get("decisionFiles") or {}).items():
            if sha and decisions_root:
                add(f"decisions/{run_folder_name(code)}/{name}.json",
                    Path(decisions_root) / run_folder_name(code) / f"{name}.json")
    if notes:
        notes = Path(notes)
        for p in sorted(notes.rglob("*")):
            if p.is_file():
                add(f"notes/{p.relative_to(notes)}", p)
    return sorted(members.items())


def package_downloads(root, manifest: Mapping) -> list[dict]:
    """What the package does not carry and a reader downloads by digest: each staged
    region's evidence archive."""
    out = []
    for r in manifest.get("regions") or []:
        code = str(r.get("l3"))
        ref = read_json(Path(root) / RUNS_DIR / run_folder_name(code) / EVIDENCE_FILE)
        if not isinstance(ref, dict):
            continue
        arch = ref.get("archive") or {}
        out.append({"l3": code, "packageId": ref.get("packageId"), "version": ref.get("version"),
                    "packageDigest": ref.get("packageDigest"), "archive": arch.get("name"),
                    "sha256": arch.get("sha256"), "bytes": arch.get("bytes"),
                    "where": f"{RUNS_DIR}/{run_folder_name(code)}/evidence/{arch.get('name')} in the campaign root, "
                             "or the evidence store by digest"})
    return out


def build_package(root, out_dir, manifest: Mapping, *, notes=None) -> tuple[Path, dict]:
    """A zip of the campaign's record, byte-deterministic for the same members (fixed
    entry dates, sorted names), with ``package.json`` inside listing every member with
    its sha256 and what still needs a download."""
    root = Path(root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ident = manifest.get("identity") or {}
    members = package_members(root, manifest, notes=notes)
    listing = [{"path": arc, "sha256": sha256_file(src), "bytes": src.stat().st_size} for arc, src in members]
    doc = {"schema": PACKAGE_SCHEMA, "campaign": dict(ident), "code": dict(manifest.get("code") or {}),
           "members": listing, "downloads": package_downloads(root, manifest),
           "notes": str(notes) if notes else None}
    zip_path = out_dir / f"{ident.get('campaignId') or 'campaign'}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, src in members:
            _write_member(zf, arc, src.read_bytes())
        _write_member(zf, "package.json", (json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8"))
    write_json(out_dir / f"{ident.get('campaignId') or 'campaign'}.package.json",
               {**doc, "zip": zip_path.name, "zipSha256": sha256_file(zip_path)})
    return zip_path, doc


def _write_member(zf: zipfile.ZipFile, arc: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


__all__ = [
    "MANIFEST_SCHEMA", "INDEX_SCHEMA", "ELIGIBILITY_SCHEMA", "BATCH_SCHEMA", "PACKAGE_SCHEMA",
    "PURPOSES", "SUPPORT_CLASSES", "STATES", "GATE_IDS", "RUN_FOLDER_GATES", "INDEX_COLUMNS",
    "REHEARSAL_LABEL", "POLICY_CANDIDATE_LABEL", "CHECK_REVIEWER", "PLACEHOLDER",
    "load_promotion_policy", "validate_promotion_policy", "promotion_policy_record",
    "read_crosswalk", "read_census", "census_counts", "support_class", "station_screen_summary",
    "read_order_seconds", "order_regions", "campaign_id", "maintainer_label_problem",
    "decision_file_shas", "stage_many_tokens", "stage_tokens", "build_manifest", "read_manifest",
    "region_of", "manifest_drift", "commands_markdown", "state_markdown", "staged_version_dir",
    "record_intact", "stage_refusal", "campaign_jobs", "batch_rows", "classify_region",
    "per_function", "source_mix", "index_rows", "index_document", "index_csv_text",
    "region_state_document", "blocking_trigger", "item_blocks", "split_open_items",
    "advisory_open_items", "gate_frozen_record", "gate_rules_applied", "gate_pending_confirmable",
    "gate_owner_decisions_honored", "gate_portfolio_approvals", "gate_equivalence_proven",
    "gate_record_complete", "evaluate_gates", "expectation_from_manifest", "expectation_from_run",
    "campaign_manifest_for_run", "region_batch_facts", "batch_summary_document",
    "batch_summary_markdown", "validate_confirmation", "confirmed_promote_commands",
    "package_members", "build_package", "sha256_file", "read_json", "write_json", "write_text",
    "render_command", "quote",
]
