"""Complete the state SQT assessments as StreamCurves sessions (maintainer, checkout).

Each ``*-sqt-adapted`` library assessment is v1, migrated on 2026-07-12 with a stub session, so
StreamCurves opened it as an empty project. This publishes the next version of each with the
complete session ``streamcurves/sqt_transcription.py`` builds: the state, the confirmed mapping,
every curve and stratum carried from v1 as a State SQT criterion, the documented gaps and the
approved metric sets (the owner's decisions of 2026-09-24). The bundle is v1's scored content, so
DEEP's digest and scores do not change; DEEP keeps hiding these assessments.

    python apps/stream-curves/scripts/complete_sqt_assessments.py             # dry run: every check
    python apps/stream-curves/scripts/complete_sqt_assessments.py --publish   # STAF_LIBRARY_PUBLISH=1

After a publish: re-bake DEEP (apps/deep/scripts/bake_library_into_deep.py), rebuild the SQT
registry (scripts/build_sqt_registry.py) and commit apps/library, apps/deep and the registry.
An assessment whose latest version is not v1 is left alone (already completed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from shiny import reactive  # noqa: E402

from streamcurves import library as lib  # noqa: E402
from streamcurves import session_io as sio  # noqa: E402
from streamcurves import sqt_transcription as tr  # noqa: E402
from views.state import AppState  # noqa: E402

NOTES = ("The complete StreamCurves session for the state SQT transcribed in v1: every curve and "
         "stratum carried as the tool publishes it (State SQT), the function mapping, the documented "
         "gaps and the approved metric sets. Scores exactly as v1; known defects and open ends are "
         "stated as caveats.")


def session_payload(fields: dict, name: str) -> dict:
    """``fields`` as a saved session (every session field, the rest at their defaults)."""
    state = AppState.fresh()
    with reactive.isolate():
        for key, value in fields.items():
            getattr(state, key).set(value)
        state.session_name.set(name)
    return sio.dump_session_fields({n: state.isolate_get(n) for n in sio.SESSION_FIELDS},
                                   session_name=name)


def prepare(aid: str, *, records: list, adapted: dict, by: str, at: str) -> dict:
    """Build and check one assessment's next version; raises on any failed check."""
    man = lib.read_manifest(aid)
    bundle = lib.load_version_bundle(aid, 1)
    citation = str((adapted.get(aid) or {}).get("sourceCitation") or man.get("sourceCitation") or "")
    done = tr.complete(aid, bundle, records=records, citation=citation, by=by, at=at)
    v2 = done["bundle"]
    if lib.content_digest(v2) != bundle["contentDigest"]:
        raise RuntimeError(f"{aid}: the completed bundle would not score as v1")
    lib._require_documented_coverage(aid, v2)
    meta = {"assessmentName": man.get("assessmentName"), "region": man.get("region"),
            "stateCode": man.get("stateCode"), "stateName": man.get("stateName"),
            "sourceCitation": man.get("sourceCitation"), "author": by, "revisionNotes": NOTES,
            "portfolioApprovals": done["approvals"]}
    lib._require_portfolio_approval(aid, v2, meta)
    payload = session_payload(done["fields"], man.get("assessmentName") or aid)
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    carried = done["fields"]["reference_build"]["carriedMetrics"]
    if sorted((back.get("reference_build") or {}).get("carriedMetrics") or {}) != sorted(carried):
        raise RuntimeError(f"{aid}: the session does not survive a save and reopen")
    prov = done["provenance"]
    reasons: dict = {}
    for e in done["fields"]["function_coverage_exceptions"]:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    return {"aid": aid, "meta": meta, "payload": payload, "bundle": v2, "provenance": prov,
            "summary": {"metrics": len(carried),
                        "layers": sum(len(c["annotations"]["sqtTranscription"]["layers"])
                                      for c in carried.values()),
                        "defective": len(prov["defects"]), "openEnded": len(prov["openEnds"]),
                        "gaps": reasons, "approvals": len(done["approvals"]),
                        "digest": bundle["contentDigest"][7:19]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true", help="write the next version of each")
    ap.add_argument("--by", default="GM", help="the maintainer's initials (default: the owner's, GM)")
    ap.add_argument("--only", action="append", default=[], help="an assessment id; repeatable")
    a = ap.parse_args(argv)
    if a.publish and lib.is_canonical_root():
        reason = lib.publish_gate_reason(a.by)
        if reason:
            raise SystemExit(reason)
    at = dt.datetime.now(dt.timezone.utc).date().isoformat()
    records = json.loads(tr.REGISTRY.read_text(encoding="utf-8"))["records"]
    adapted = {x["assessmentId"]: x for x in json.loads(tr.ADAPTED.read_text(encoding="utf-8"))["assessments"]}
    ids = sorted(e["assessmentId"] for e in lib.read_catalog().get("assessments") or []
                 if tr.is_transcribed(e["assessmentId"]))
    if a.only:
        ids = [i for i in ids if i in set(a.only)]
    for aid in ids:
        latest = int((lib.read_manifest(aid) or {}).get("latestVersion") or 0)
        if latest != 1:
            print(f"{aid}: latest is v{latest}; already completed, left alone")
            continue
        got = prepare(aid, records=records, adapted=adapted, by=a.by, at=at)
        print(f"{aid}: {json.dumps(got['summary'])}")
        if a.publish:
            version = lib.publish_version(aid, got["meta"], got["payload"], got["bundle"],
                                          provenance=got["provenance"], status="preliminary")
            stored = lib.load_version_bundle(aid, version)
            if stored.get("contentDigest") != got["bundle"]["contentDigest"]:
                raise RuntimeError(f"{aid} v{version}: the published digest moved")
            print(f"    published v{version} (Preliminary), digest unchanged")
    if not a.publish:
        print("dry run: nothing written (pass --publish in a maintainer checkout)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
