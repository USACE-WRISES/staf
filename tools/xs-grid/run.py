"""Cross-section grid builder: a section every 100 ft on every lower-48 stream and canal.

    python tools/xs-grid/run.py place 0710             # place the sections only, report counts
    python tools/xs-grid/run.py build 0710              # sample, score, write (resumable)
    python tools/xs-grid/run.py build 0710 --cells 3    # a test run on three cells (not merged)
    python tools/xs-grid/run.py national                # every lower-48 region not done yet
    python tools/xs-grid/run.py stop                    # pause a run (it finishes the cells in hand)
    python tools/xs-grid/run.py status                  # running or paused, regions done, drive space
    python tools/xs-grid/run.py verify 0710 --sample 2000  # rederive from the archive, compare

A run resumes where the last one paused. ``--log FILE`` appends the output to a file (a run
started without a console needs it); ``--window 20:00-07:00`` limits a run to those hours
(it pauses itself at the end). Runs below normal priority unless ``--full-priority``. Paths:
``xsgrid/config.py``.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xsgrid import build, config  # noqa: E402


def lower48() -> list:
    m = json.loads((config.BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    vpus = sorted(m["vpus"])
    return [v for v in vpus if v[:2] <= "18"]


def cmd_place(args) -> int:
    for vpu in args.vpus:
        t = build.region_sections(vpu)
        res = t.column("wide_m").to_numpy()
        build.log(f"[{vpu}] {t.num_rows:,} sections, {len(set(t.column('cell').to_pylist()))} cells, "
                  f"half-width 250 m on {100 * (res == 250).mean():.1f}%, mean {res.mean():.0f} m")
    return 0


def _window(args):
    return build.parse_window(args.window) if args.window else None


def cmd_build(args) -> int:
    return build.run(args.vpus, samplers=args.samplers, scorers=args.scorers, priority_low=not args.full_priority,
                     cell_limit=args.cells, cache_gb=args.cache_gb, window=_window(args))


def cmd_national(args) -> int:
    vpus = lower48()
    if args.start:
        vpus = vpus[vpus.index(args.start):] + vpus[:vpus.index(args.start)]
    if args.first:
        first = [v for v in args.first.split(",") if v in vpus]
        vpus = first + [v for v in vpus if v not in first]
    return build.run(vpus, samplers=args.samplers, scorers=args.scorers, priority_low=not args.full_priority,
                     cache_gb=args.cache_gb, window=_window(args))


def cmd_stop(args) -> int:
    pid = build.running_pid()
    if pid is None:
        print("no run is going")
        return 0
    build.stop_path().parent.mkdir(parents=True, exist_ok=True)
    build.stop_path().write_text("stop\n", encoding="utf-8")
    print(f"asked run {pid} to pause; it finishes the cells in hand (usually a minute or two)")
    return 0


def cmd_status(args) -> int:
    pid = build.running_pid()
    print(("RUNNING (process %d)" % pid) if pid else "PAUSED (no run is going)")
    drive = Path(config.ARCHIVE.anchor)
    if not drive.exists():
        print(f"the archive drive {drive} is not connected (archive {config.ARCHIVE}); "
              f"plug it in, or set XSGRID_ARCHIVE")
        return 1
    if build.archive_missing():
        print(f"no archive at {config.ARCHIVE}, though regions are done (their metrics are in "
              f"{config.WORK / 'metrics'}); is another drive at {drive}?")
        return 1
    done = sorted(p.stem for p in (config.ARCHIVE / "regions").glob("*.json"))
    total = len(lower48())
    secs = arch = met = 0
    for v in done:
        r = json.loads((config.ARCHIVE / "regions" / f"{v}.json").read_text(encoding="utf-8"))
        secs += r["sections"]
        arch += r["archive_bytes"]
        met += r["metrics_bytes"]
    usage = shutil.disk_usage(drive)
    print(f"regions done {len(done)} of {total}; sections {secs:,}; archive {arch / 1e9:.1f} GB "
          f"({arch / max(secs, 1):.0f} B/section); metrics {met / 1e9:.2f} GB; "
          f"drive free {usage.free / 1e9:.0f} GB")
    parts = config.ARCHIVE / "parts"
    if parts.exists():
        for folder in sorted(q for q in parts.iterdir() if q.is_dir()):
            sampled = sum(1 for q in folder.glob("c*.parquet") if not q.name.endswith(".metrics.parquet"))
            scored = sum(1 for _q in folder.glob("c*.metrics.parquet"))
            table = build.sections_path(folder.name)
            cells = "?"
            if table.exists():
                import pyarrow.parquet as pq
                cells = len(set(pq.read_table(table, columns=["cell"]).column("cell").to_pylist()))
            print(f"  in progress {folder.name}: {sampled} cells sampled, {scored} scored, of {cells}")
    return 0


def cmd_verify(args) -> int:
    """Rederive sampled sections from the archive and compare with the stored metrics."""
    import numpy as np
    import pyarrow.parquet as pq
    from xsgrid import archive, derive
    xs = config.ARCHIVE / "archive" / f"xs_{args.vpu}.parquet"
    xm = config.ARCHIVE / "metrics" / f"xsm_{args.vpu}.parquet"
    if xs.exists():
        at, mt = pq.read_table(xs), pq.read_table(xm)
    else:                                        # a test run: the part files
        folder = config.ARCHIVE / "parts" / args.vpu
        import pyarrow as pa
        at = pa.concat_tables([pq.read_table(p) for p in sorted(folder.glob("c*[0-9].parquet"))])
        mt = pa.concat_tables([pq.read_table(p) for p in sorted(folder.glob("c*.metrics.parquet"))])
    n = at.num_rows
    rng = random.Random(7)
    idx = sorted(rng.sample(range(n), min(args.sample, n)))
    sub = at.take(idx)
    msub = mt.take(idx).to_pylist()
    z32s = archive.transects(sub)
    rows = sub.to_pylist()
    same = diff = 0
    for row, z, m in zip(rows, z32s, msub):
        if row["res_m"] == 0:
            continue
        got = derive.metrics(z, row["wide_m"], row["n_pts"], row["res_m"], row["da_sqkm"], row["bf_width_m"],
                             row["bf_depth_m"], row["bf_area_m2"], row["division"], verify=True)
        keys = [k for k in got if k not in ("verified",)]
        if all(got.get(k) == m.get(k) or (got.get(k) is None and m.get(k) is None) for k in keys
               if k != "status") and got["status"] in (m["status"], "ok"):
            same += 1
        else:
            diff += 1
            if diff <= 5:
                print("differs:", row["nhdplusid"], row["k"], {k: (got.get(k), m.get(k)) for k in keys
                                                               if got.get(k) != m.get(k)})
    print(f"rederived {same + diff} sections from the archive: {same} identical, {diff} different")
    return 1 if diff else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("place")
    p.add_argument("vpus", nargs="+")
    p.set_defaults(fn=cmd_place)
    for name, fn in (("build", cmd_build), ("national", cmd_national)):
        p = sub.add_parser(name)
        if name == "build":
            p.add_argument("vpus", nargs="+")
            p.add_argument("--cells", type=int, default=None)
        else:
            p.add_argument("--start", default=None, help="begin with this region (the rest follow in order)")
            p.add_argument("--first", default=None, help="comma-separated regions to build before the rest")
        p.add_argument("--samplers", type=int, default=3)
        p.add_argument("--scorers", type=int, default=7)
        p.add_argument("--cache-gb", type=float, default=150.0)
        p.add_argument("--full-priority", action="store_true")
        p.add_argument("--window", default=None, help="run only between these hours, e.g. 20:00-07:00")
        p.add_argument("--log", default=None, help="append the output to this file")
        p.set_defaults(fn=fn)
    p = sub.add_parser("stop")
    p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("verify")
    p.add_argument("vpu")
    p.add_argument("--sample", type=int, default=2000)
    p.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    log_file = getattr(args, "log", None)
    if log_file or sys.stdout is None:                 # no console (pythonw): write to the log
        target = Path(log_file or (config.WORK / "logs" / "national.log"))
        target.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(target, "a", encoding="utf-8", buffering=1)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
