"""Build one Round 4 candidate method package from the frozen EASI addendum.

    python apps/stream-curves/scripts/build_easi_candidate_package.py --family E1 \\
        --base apps/library/assessments/easi-screening/v1 \\
        --out D:/Data/staf-campaign-2026-09/easi/candidates/E1 [--curve-sets <curve-sets.json>]

``--family`` is one of E1 to E8 (``config/methodology/evaluation_protocol_v1_easi_addendum.yaml``,
frozen: its sha256 is checked before anything is built). ``--base`` is the base method: a
folder holding the eight method files, a published library version folder
(``<library>/assessments/easi-screening/v<N>``) or a method package zip. ``--curve-sets`` is
the refit campaign's output (``scripts/refit_easi_candidate_sets.py``), which E2 and E4 need;
without it they refuse and say so.

The output folder receives ``method/<the eight method files>`` (the family's variant files,
the untouched ones byte for byte), ``<family>.easi-method.zip`` (the ``EASI_METHOD_PACKAGE``
an evaluation arm activates) and ``candidate.json`` (the family and its addendum text, the
addendum's sha256, the base's identity, the edits applied and the package's identity under
this evaluator). The label is the rehearsal label; nothing here records an approval. Never
writes under ``apps/easi/data`` or ``apps/library``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
REPO = APP.parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import easi_env  # noqa: E402

easi_env.sanitize()

from streamcurves._vendor.easi import method_package as mp  # noqa: E402
from streamcurves.easi_method import round4  # noqa: E402

FORBIDDEN = (REPO / "apps" / "easi" / "data", REPO / "apps" / "library")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True) + "\n",
                    encoding="utf-8", newline="\n")


def check_out_folder(out: Path) -> Path:
    out = Path(out).resolve()
    for folder in FORBIDDEN:
        try:
            out.relative_to(folder.resolve())
        except ValueError:
            continue
        raise SystemExit(f"{out} is under {folder}; a candidate package is never written there")
    return out


def build(family: str, base: Path, out: Path, *, curve_sets: Path | None = None,
          version: int = 1) -> dict:
    out = check_out_folder(out)
    sets, provenance = None, None
    if curve_sets is not None:
        doc = json.loads(Path(curve_sets).read_text(encoding="utf-8"))
        sets = doc.get("sets") or {}
        provenance = doc.get("provenance")
    files = round4.base_files(base)
    recorded = round4.recorded_base_identity(base)
    try:
        built = round4.build_candidate(files, family, curve_sets=sets)
    except round4.CandidateError as exc:
        raise SystemExit(str(exc))
    pkg = round4.package(built["files"], family, built["spec"], version=version)
    blob = mp.to_zip(pkg)
    zip_name = f"{family}.easi-method.zip"
    method_dir = out / mp.METHOD_DIR
    method_dir.mkdir(parents=True, exist_ok=True)
    for name, data in built["files"].items():
        (method_dir / name).write_bytes(data)
    (out / zip_name).write_bytes(blob)
    spec = built["spec"]
    base_digest = mp.package_digest({n: _sha(b) for n, b in files.items()})
    record = {
        "schema": "staf-easi-candidate", "schemaVersion": 1,
        "family": family, "familyName": spec.get("family"), "function": spec.get("function"),
        "change": spec.get("change"), "mechanism": spec.get("mechanism"),
        "hypothesis": spec.get("hypothesis"), "primaryOutcome": spec.get("primary_outcome"),
        "decision": spec.get("decision"), "coverageEffect": spec.get("coverage_effect"),
        "addendum": {"file": "apps/stream-curves/config/methodology/" + round4.ADDENDUM_FILE,
                     "sha256": round4.ADDENDUM_SHA256},
        "base": {"source": str(base), "packageDigest": base_digest,
                 "methodVersion": recorded.get("methodVersion") or mp.method_version_for("regional", files),
                 "methodVersionRecordedBy": ("the base's envelope" if recorded.get("methodVersion")
                                             else "this evaluator (the base names none)"),
                 "methodVersionUnderThisEvaluator": mp.method_version_for("regional", files),
                 "recorded": recorded},
        "candidate": {**built["identity"], "zip": zip_name, "zipSha256": _sha(blob), "zipBytes": len(blob),
                      "scoringIdentity": built["scoringIdentity"],
                      "requires": pkg.envelope["evaluator"]["requires"],
                      "label": pkg.envelope["label"], "version": pkg.envelope["version"]},
        "edits": built["edits"], "unchangedFiles": built["unchangedFiles"],
        "curveSets": {**built["curveSets"], **({"provenance": provenance} if provenance else {})},
        "label": "rehearsal", "builtAt": _now(),
        "builder": "apps/stream-curves/scripts/build_easi_candidate_package.py",
    }
    _write_json(out / "candidate.json", record)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--family", required=True, choices=round4.FAMILIES)
    ap.add_argument("--base", required=True, type=Path,
                    help="the base method: a folder of the eight method files, a library version "
                         "folder, or a method package zip")
    ap.add_argument("--out", required=True, type=Path, help="the folder to write (created)")
    ap.add_argument("--curve-sets", type=Path,
                    help="the refit campaign's curve-sets.json (refit_easi_candidate_sets.py), for E2 and E4")
    ap.add_argument("--version", type=int, default=1, help="the envelope's version number (default 1)")
    a = ap.parse_args(argv)
    record = build(a.family, a.base, a.out, curve_sets=a.curve_sets, version=a.version)
    summary = {k: record[k] for k in ("family", "familyName", "addendum", "base", "candidate", "edits")}
    summary["out"] = str(Path(a.out).resolve())
    print(json.dumps(summary, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
