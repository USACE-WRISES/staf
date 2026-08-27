"""Driving a batch region build from the app.

``scripts/run_region_batch.py stage`` already runs the whole six-stage workflow for
one Level III ecoregion, applies the standing-decision policy, publishes into a
staged library and writes a review packet. Everything here is the thin layer the UI
needs around it: what a region's candidate count means before you spend half an
hour, the command to run, and how to turn a reviewer's answers into the decisions
file the pipeline already accepts.

The build itself is deliberately NOT reimplemented. ``cmd_stage`` owns the fixpoint
loop over the policy, the refusal gates, the staged publish and the packet; a second
implementation in a view could produce a different assessment from the same inputs,
which would cost the pipeline the reproducibility claim that is its whole point. The
app shells out to the same script the pilots were built with.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from streamcurves import methodology, provenance

_APP_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _APP_DIR.parents[1]
_BATCH_SCRIPT = _APP_DIR / "scripts" / "run_region_batch.py"

#: The four standing-decision entries that ship disabled. Enabling one is a per-region
#: owner judgement recorded on the run, so the UI names what each one accepts rather
#: than showing its id. Mirrors config/methodology/standing_decisions.yaml.
OPTIONAL_POLICIES = [
    ("ref02-accept-best-available",
     "Accept a best-available reference tier",
     "REF-02. When the least-disturbed pool is below 10 sites, accept the wider "
     "at-risk-or-better pool instead. Caps confidence at 59. Two of the three "
     "regions built so far needed this."),
    ("curve07-thin-metric-finalized",
     "Finalize a thin curve that is otherwise clean",
     "CURVE-07. Accepts a curve flagged only for data thinness, when it has no "
     "domain violations."),
    ("data03-thin-metric-finalized",
     "Accept a metric with high missingness",
     "DATA-03. Above 40 percent missing, when at least 5 reference sites remain."),
    ("data06-insufficient-finalized",
     "Accept a metric below the sample floor",
     "DATA-06. Under 10 reference sites, when at least 5 remain."),
]

#: What apply_reviewer_decisions accepts. Anything else raises there.
REVIEWER_ACTIONS = ["accept", "accept_with_conditions", "modify", "reject",
                    "request_additional_analysis"]

#: The two NRSA datasets, said in terms of what you get rather than by their ids.
#: Pooling is easy to misread as "three cycles of measurements": it is not. EPA
#: renames every site each cycle and only 11 stations appear in all three, so the
#: cycles are mostly different places. Each station still contributes one row,
#: taken from its most recent cycle that carries the metrics the run needs.
DATASET_LABELS = {
    "legacy-1819": "NRSA 2018-19 only (what the three published assessments used)",
    "multi-cycle-v1": "Pooled 2013-14, 2018-19 and 2023-24 (one row per station, "
                      "newest visit that has the metrics)",
}

DATASET_NOTE = (
    "Pooling adds places, not repeat measurements: the three surveys mostly visited "
    "different streams, so the station pool goes from about 1,900 to about 4,400. "
    "2018-19 stays the default because the published assessments fingerprint those "
    "exact files, and changing it would break their reproducibility."
)

RESAMPLES_NOTE = (
    "How many times each reference pool is resampled to estimate curve uncertainty and "
    "stability (CURVE-06), redundancy stability (RED-06) and stratifier stability "
    "(STRAT-06, capped at 100). This is nearly all of the runtime: both pilots took "
    "about 8 minutes at 200 and about 33 at 1000. The gain is precision on the "
    "stability figure, plus or minus 5.5 points at 200 against 2.5 at 1000, which only "
    "matters for a curve sitting near the 0.80 threshold. Use 200 for a first look and "
    "1000 for anything you intend to publish."
)


# --------------------------------------------------------------------------- #
# Viability: what a candidate count runs into
# --------------------------------------------------------------------------- #
def _floors() -> tuple[int, int]:
    """(DATA-04 minimum, DATA-05 exploratory floor), read from the methodology."""
    return (int(methodology.threshold("data_rules.min_n_unstratified", 20)),
            int(methodology.threshold("data_rules.exploratory_n_unstratified", 10)))


def viability(n_candidates: int) -> dict:
    """What this many candidate sites means, named by the rule it runs into.

    The count is candidates BEFORE the EASI reference screen, so it is a ceiling and
    never a promise: Interior Plateau went 25 to 23, and Eastern Corn Belt Plains went
    18 to zero Functioning sites, which is what fired the REF-02 fallback. The bands
    are the real thresholds, so a region reading "exploratory" here cannot come out
    better than exploratory.
    """
    minimum, exploratory = _floors()
    n = int(n_candidates or 0)
    if n >= minimum:
        return {
            "band": "adequate", "rule": "DATA-04", "n": n,
            "label": f"clears DATA-04 ({minimum}) if the screen retains enough",
        }
    if n >= exploratory:
        return {
            "band": "exploratory", "rule": "DATA-05", "n": n,
            "label": (f"DATA-05 exploratory band ({exploratory} to {minimum - 1}), "
                      "confidence caps at 59"),
        }
    return {
        "band": "insufficient", "rule": "DATA-06", "n": n,
        "label": f"below the DATA-06 floor of {exploratory}",
    }


def region_choices(sites: pd.DataFrame, *, code_col: str = "us_l3code",
                   name_col: str = "us_l3name",
                   site_col: str = "site_id") -> list[dict]:
    """Every Level III ecoregion in the site table, with its count and what it means.

    Ordered by Level III code, ascending and numerically. EPA's codes are strings, so
    a plain sort puts 10 before 2 and scatters the numbering; the picker matches the
    NRSA explorer's ordering so a code is in the same place in both. A code that is
    not a number sorts to the end rather than raising.
    """
    if sites is None or not len(sites):
        return []
    df = sites.copy()
    df[code_col] = df[code_col].astype(str)
    counted = (df.groupby([code_col, name_col])[site_col].nunique()
               if site_col in df.columns
               else df.groupby([code_col, name_col]).size())
    out = []
    for (code, name), n in counted.items():
        v = viability(int(n))
        out.append({"code": str(code), "name": str(name), "n_candidates": int(n),
                    **{k: v[k] for k in ("band", "rule", "label")}})
    def _order(row):
        code = row["code"]
        digits = code.strip()
        return (0, int(digits), "") if digits.isdigit() else (1, 0, code)

    out.sort(key=_order)
    return out


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #
def run_folder(out_root: Path | str, l3_code: str) -> Path:
    """Where one region's run artifacts live. One folder per region per root."""
    return Path(out_root) / f"l3-{str(l3_code).strip()}"


def stage_command(l3_code: str, name: str, out_dir: Path | str, *,
                  maintainer: str, n_boot: int = 1000,
                  enable_policies: Optional[list[str]] = None,
                  dataset_id: Optional[str] = None,
                  reviewer_decisions: Optional[Path | str] = None,
                  coverage_exceptions: Optional[Path | str] = None,
                  source_citation: str = "",
                  python: Optional[str] = None) -> list[str]:
    """The argv for one staged build.

    Deliberately the same script the pilots were built with, so the app cannot fork
    the pipeline. ``--out`` is a run folder, never a library root: cmd_stage refuses
    to write the canonical library and derives its staged root as ``<out>/library``.
    """
    out = Path(out_dir)
    cite = source_citation or (
        f"USEPA NRSA (L3 ecoregion {l3_code}), StreamCurves Regional Analysis Agent")
    argv = [
        # -u because the caller reads stdout through a pipe: Python block-buffers a
        # non-tty stdout, so without it the runner's narration sits in an 8 KB buffer
        # inside the child and the first progress line can be half an hour late.
        python or sys.executable, "-u", str(_BATCH_SCRIPT), "stage",
        "--l3", str(l3_code), "--name", str(name),
        "--out", str(out),
        "--maintainer", str(maintainer),
        "--n-boot", str(int(n_boot)),
        "--source-citation", cite,
    ]
    for pid in (enable_policies or []):
        argv += ["--enable-policy", str(pid)]
    if dataset_id:
        argv += ["--nrsa-dataset", str(dataset_id)]
    if reviewer_decisions:
        argv += ["--reviewer-decisions", str(reviewer_decisions)]
    if coverage_exceptions:
        # The documented gaps ride in as an input, so the next build stages cleanly
        # rather than being patched afterwards in the app.
        argv += ["--coverage-exceptions", str(coverage_exceptions)]
    return argv


def repo_root() -> Path:
    """cwd for the subprocess: the script resolves its paths from the repo root."""
    return _REPO_ROOT


#: cmd_stage's exit codes, as sentences rather than numbers.
EXIT_MEANINGS = {
    0: "Staged.",
    1: "The review queue did not settle within the pass limit. Nothing was staged.",
    2: "A landscape data source failed, so the run would have had a silent gap. "
       "Nothing was staged.",
}


def exit_meaning(code: int) -> str:
    return EXIT_MEANINGS.get(int(code), f"The build exited {code}. Nothing was staged.")


def refusal_from_log(text: str) -> Optional[str]:
    """The gate's own sentence when a staged publish was refused, or None."""
    marker = "[batch] staged publish refused: "
    for line in (text or "").splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def outcome(code: int, packet: Optional[dict], log: str = "") -> tuple[str, str, Optional[str]]:
    """(headline, severity, detail) for a finished run.

    Read from the packet, never from the exit code alone. cmd_stage exits 0 whenever
    it completed and wrote a packet, and that includes a run whose staged publish a
    gate refused: the Driftless Area run exited 0 with ``staged`` null and an empty
    library folder, and reporting the code alone announced "Staged." over it.
    """
    if int(code) != 0:
        return exit_meaning(code), "warning", None
    staged = (packet or {}).get("staged") or None
    if staged:
        return (f'Staged v{staged.get("version")}.', "success",
                str(staged.get("path") or "") or None)
    return ("The run finished, but a gate refused to stage it. The packet below says "
            "what it wants.", "warning", refusal_from_log(log))


def progress_from_log(text: str) -> Optional[str]:
    """The most recent progress line the batch runner printed, or None.

    cmd_stage narrates itself with "[batch] ..." lines, so the log tail is the
    progress signal and no second channel is needed.
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("[batch]")]
    return lines[-1] if lines else None


def phase_from_log(text: str) -> str:
    """What the run is doing, for the long stretch before it narrates anything.

    The reference screen is most of the wall clock and prints nothing per site, so a
    banner keyed only on the "[batch]" lines sits on one sentence for half an hour --
    indistinguishable from a hang, which is the failure this app has already had once.
    The screen's DEM work is loud on stderr, so its volume is the liveness signal.
    """
    latest = progress_from_log(text)
    if latest:
        return latest
    if not text:
        return "Starting the build."
    n_dem = text.count("pygeoutils") + text.count("rioxarray")
    if n_dem:
        return ("Screening candidate sites against EASI. This is the long phase and it "
                f"reports only at the end ({n_dem} elevation reads so far).")
    return "Loading sites and configuration."


# --------------------------------------------------------------------------- #
# Turning answers into the decisions file
# --------------------------------------------------------------------------- #
def _record_for(doc: dict, rule_id: str, subject: str) -> Optional[dict]:
    for rec in (doc or {}).get("records") or []:
        if rec.get("rule_id") == rule_id and str(rec.get("subject")) == str(subject):
            return rec
    return None


def build_decision(doc: dict, item: dict, action: str, rationale: str, *,
                   reviewer: str) -> dict:
    """One reviewer answer, in the shape apply_reviewer_decisions accepts.

    ``asserts`` is filled from the record's own computed evidence rather than typed.
    That field is a consistency contract, not a note: a value that disagrees with the
    record makes the whole run raise, and hand-authoring it is what cost the Interior
    Plateau build a stage iteration.
    """
    action = str(action or "").strip()
    if action not in REVIEWER_ACTIONS:
        raise ValueError(f"unknown reviewer action {action!r}")
    rule_id = item.get("rule_id")
    subject = item.get("subject")
    rec = _record_for(doc, rule_id, subject)
    computed = (rec or {}).get("computed") or {}
    evidence = item.get("evidence") or {}
    # Only fields the record actually computes can be asserted; anything else is
    # refused by decision_consistency_problems as "the record does not compute".
    asserts = {k: computed[k] for k in evidence if k in computed}
    return {
        "rule_id": rule_id,
        "subject": subject,
        "action": action,
        "rationale": str(rationale or "").strip(),
        "reviewer": reviewer,
        "rationale_origin": "owner_written",
        "asserts": asserts,
    }


def decision_problems(doc: dict, decision: dict) -> list[str]:
    """Why this answer would be refused downstream, or an empty list.

    Two checks, both already owned by provenance: the explicit ``asserts`` block, and
    the rationale's own wording, which is linted for templated phrases (writing "no
    decision flip" over a record that computed one is a contradiction). Running them
    here turns a refusal 35 minutes later into a message beside the field.
    """
    problems = []
    if not str(decision.get("rationale") or "").strip():
        problems.append("A rationale is required.")
    rec = _record_for(doc, decision.get("rule_id"), decision.get("subject"))
    if rec is None:
        problems.append("No record on this run matches "
                        f"{decision.get('rule_id')}:{decision.get('subject')}.")
        return problems
    problems.extend(provenance.decision_consistency_problems(rec, decision))
    return problems


# --------------------------------------------------------------------------- #
# Coverage gaps
#
# An undocumented STAF function is the gate that refused the Driftless Area run,
# and it is answerable the same way a policy item is: stage already accepts
# --coverage-exceptions, so the answer is a build input rather than an edit made
# afterwards. Keeping it on that path is what lets the run stage cleanly and
# publish with its own provenance instead of an interactive one.
# --------------------------------------------------------------------------- #
def coverage_gaps(packet: Optional[dict]) -> list[dict]:
    """Functions the run covers with neither a metric nor a documented reason.

    Keyed on ``coverage.missingFunctionIds``, which is the same list the publish
    gate reads. ``uncovered_functions`` carries the readable label and the metrics
    that could have informed it, joined by name rather than by position so a
    reordering cannot mislabel a gap.
    """
    packet = packet or {}
    missing = list(((packet.get("coverage") or {}).get("missingFunctionIds")) or [])
    if not missing:
        return []
    from streamcurves import staf_library

    lookup = staf_library._staf_function_lookup()
    by_label = {str(u.get("function") or "").strip(): u
                for u in (packet.get("uncovered_functions") or [])}
    out = []
    for fid in missing:
        label = (lookup.get(fid) or {}).get("name") or fid
        u = by_label.get(label) or {}
        out.append({
            "item_id": f"COVERAGE:{fid}",
            "function_id": fid,
            "label": label,
            "discipline": (lookup.get(fid) or {}).get("discipline") or "",
            "candidates": list(u.get("candidates") or []),
            "question": (f"No metric informs {label}, and no reason is recorded. "
                         "Document why it is out of scope, or add a metric that "
                         "informs it."),
        })
    return out


def coverage_reasons() -> tuple:
    """The vocabulary the gate accepts, from the exporter rather than a copy here."""
    from streamcurves import deep_export

    return deep_export.FUNCTION_EXCLUSION_REASONS


def build_coverage_exception(function_id: str, reason: str,
                             justification: str, *, recorded_by: str = "") -> dict:
    """One documented gap, in the shape validate_coverage_exceptions accepts."""
    out = {
        "functionId": str(function_id or "").strip(),
        "reason": str(reason or "").strip(),
        "justification": str(justification or "").strip(),
    }
    if recorded_by:
        out["recordedBy"] = recorded_by
    return out


def coverage_problems(exceptions: list[dict]) -> list[str]:
    """Why the gate would refuse these, or an empty list.

    Runs the exporter's own validator, so an off-vocabulary reason or a placeholder
    justification is caught beside the field rather than 35 minutes later. The
    justification floor and the six reasons live there; nothing is duplicated here.
    """
    from streamcurves import config, deep_export

    if not exceptions:
        return []
    crosswalk = config.staf_functions_raw().get("functions") or []
    try:
        deep_export.validate_coverage_exceptions(exceptions, crosswalk)
    except ValueError as exc:
        return [str(exc)]
    return []


def promote_command(out_dir, *, maintainer: str, publish_root: str = "apps/library",
                    rebake: bool = True, python: Optional[str] = None) -> list[str]:
    """The argv that confirms a staged run's decisions and publishes it.

    The same script stage uses, so the published version carries the run's own
    provenance rather than the thin interactive document an in-app publish writes.
    """
    argv = [
        python or sys.executable, "-u", str(_BATCH_SCRIPT), "promote",
        "--out", str(out_dir),
        "--maintainer", str(maintainer),
        "--publish-root", str(publish_root),
    ]
    if rebake:
        argv.append("--rebake-deep")
    return argv
