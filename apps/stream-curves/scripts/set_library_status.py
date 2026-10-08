"""Change the lifecycle status of many published DEEP assessment versions at once.

    python apps/stream-curves/scripts/set_library_status.py --to draft --from preliminary
        --kind ecoregion --exclude northeastern-highlands,eastern-corn-belt-plains
        --actor GM --note "..." [--only ID,ID] [--library-root PATH] [--dry-run] [--no-rebake]

Each change is one audited record appended to the assessment's status.json, the record
library.set_version_status writes (status, actor, timestamp, note), so nothing a version holds
changes: not its content or digest, its calculator, its manifest or its Show in DEEP. The catalog
is rebuilt once, after every record is written, and DEEP's registry is then rebaked
(library.rebake_deep, which also rewrites the site's calculator list; canonical root only).

What is selected: DEEP assessments only (an EASI method is never touched) and never the state SQT
transcriptions (DEEP does not list them); every version whose current status is --from, of the
assessments whose region kind is --kind, minus --exclude, and only those in --only when it is
given. An id in --exclude or --only that the library does not hold is refused, so a misspelled
exclusion can never flip the assessment it meant to protect. --to certified is refused: Final is
the Validate stage's audited step, gated on field validation. Writing the canonical library
needs STAF_LIBRARY_PUBLISH=1, as every write to it does. Run --dry-run first: it lists the
changes and writes nothing.
"""
from __future__ import annotations

import argparse
import errno
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import gallery  # noqa: E402
from streamcurves import library as lib  # noqa: E402

#: The statuses this script sets. Certified (Final) is never set in bulk.
TARGETS = tuple(s for s in lib.VERSION_STATUSES if s != "certified")
#: On Windows a file just written in the library can be held for a moment (a scan or a sync
#: client), and the next write fails with EINVAL (errno 22); such a write is tried again.
RETRIES = 5
RETRY_WAIT_S = 0.5


class Refused(SystemExit):
    """A request this script will not carry out; nothing was written."""


def _retry(fn, *args, **kwargs):
    for attempt in range(RETRIES):
        try:
            return fn(*args, **kwargs)
        except OSError as exc:
            if exc.errno != errno.EINVAL or attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_WAIT_S * (attempt + 1))


def _ids(text) -> tuple:
    return tuple(sorted({lib.slugify(t) for t in str(text or "").split(",") if t.strip()}))


def deep_assessments() -> dict:
    """``{assessment id: manifest}`` of every DEEP assessment DEEP lists."""
    out = {}
    for entry in lib.list_assessments():
        aid = str(entry.get("assessmentId") or "")
        if not aid or lib.entry_type(entry) != "deep" or aid.endswith(gallery.DEEP_HIDDEN_SUFFIXES):
            continue
        manifest = lib.read_manifest(aid) or {}
        if manifest and lib.entry_type(manifest) == "deep":
            out[aid] = manifest
    return out


def select(*, to: str, frm: str | None = None, kind: str | None = None,
           exclude=(), only=()) -> list:
    """``[(assessment id, version, current status)]`` the change applies to, by id then
    version. Raises :class:`Refused` for an unknown id in ``exclude`` or ``only``."""
    held = deep_assessments()
    unknown = [aid for aid in sorted(set(exclude) | set(only)) if aid not in held]
    if unknown:
        raise Refused(f"not a DEEP assessment in {lib.library_root()}: {', '.join(unknown)}")
    out = []
    for aid, manifest in sorted(held.items()):
        region = manifest.get("region") or {}
        if kind and str(region.get("kind") or "") != kind:
            continue
        if aid in exclude or (only and aid not in only):
            continue
        for v in sorted(int(x.get("version") or 0) for x in manifest.get("versions") or []):
            if v < 1:
                continue
            status = lib.version_status(aid, v)
            if status == to or (frm and status != frm):
                continue
            out.append((aid, v, status))
    return out


def apply(changes, *, to: str, actor: str, note: str | None) -> None:
    """One status record per change, then the catalog, rebuilt once."""
    for aid, v, _old in changes:
        _retry(lib._append_status, aid, int(v), to, actor, note)
    _retry(lib._regenerate_catalog)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--to", required=True, choices=TARGETS, help="The status to set.")
    p.add_argument("--from", dest="frm", choices=lib.VERSION_STATUSES,
                   help="Only versions whose current status is this one.")
    p.add_argument("--kind", help="Only assessments whose region kind is this (ecoregion, state).")
    p.add_argument("--exclude", default="", help="Assessment ids to leave alone, comma separated.")
    p.add_argument("--only", default="", help="Only these assessment ids, comma separated.")
    p.add_argument("--actor", required=True, help="The audit name recorded with every change.")
    p.add_argument("--note", default=None, help="The note recorded with every change.")
    p.add_argument("--library-root", type=Path, default=None,
                   help="Another library (default: apps/library, or STAF_LIBRARY_ROOT).")
    p.add_argument("--dry-run", action="store_true", help="List the changes; write nothing.")
    p.add_argument("--no-rebake", action="store_true", help="Leave DEEP's registry as it is.")
    args = p.parse_args(argv)
    if args.library_root is not None:
        os.environ["STAF_LIBRARY_ROOT"] = str(args.library_root)
    actor = (args.actor or "").strip()
    if not actor:
        raise Refused("--actor is empty: every change records who made it")

    changes = select(to=args.to, frm=args.frm, kind=args.kind,
                     exclude=_ids(args.exclude), only=_ids(args.only))
    n_ids = len({aid for aid, _v, _s in changes})
    for aid, v, old in changes:
        print(f"  {aid} v{v}: {lib.status_label(old)} -> {lib.status_label(args.to)}")
    print(f"{len(changes)} version(s) in {n_ids} assessment(s) in {lib.library_root()}")
    if args.dry_run or not changes:
        print("dry run: nothing written" if args.dry_run else "nothing to change")
        return 0
    if lib.is_canonical_root() and not lib._env_flag(lib._ENV_PUBLISH):
        raise Refused("the canonical library changes only with STAF_LIBRARY_PUBLISH=1 set")
    if not lib.writable():
        raise Refused(f"the library at {lib.library_root()} is not writable here")

    apply(changes, to=args.to, actor=actor, note=args.note)
    print(f"recorded {len(changes)} status change(s); catalog rebuilt")
    if args.no_rebake:
        print("DEEP registry not rebaked (--no-rebake)")
    else:
        ok, message = lib.rebake_deep()
        print(("DEEP registry: " if ok else "DEEP registry not rebaked: ") + message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
