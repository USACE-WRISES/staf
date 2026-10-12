"""What a click cost in the static app: reads ``parity/static/<stem>.perf.json`` (``capture.py --perf``).

Prints each step the app timed (its ``staf:`` measures) with the requests that ran while it did (how
many, how many bytes, how long the longest waited), then the data files read in all, largest first, each
with its requests and bytes. Request addresses are named by the data manifest (``web-manifest.json`` of
the local layout, ``STAF_WEB_DATA``, default ``D:\\Data\\staf-web\\site``).

    .venv\\Scripts\\python apps/staf-web/parity/perf.py easi-perfA [--step easi:prefetch]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("STAF_WEB_DATA") or r"D:\Data\staf-web\site")


def names() -> dict:
    """{stored path: logical name} from the local layout's manifest."""
    m = DATA / "web-manifest.json"
    if not m.is_file():
        return {}
    files = json.loads(m.read_text(encoding="utf-8")).get("files", {})
    return dict((e["path"], logical) for logical, e in files.items())


def label(url: str, by_path: dict) -> str:
    if "/data/" in url:
        path = url.split("/data/", 1)[1].split("?")[0]
        return by_path.get(path, path)
    if "/resolve/main/" in url:
        path = url.split("/resolve/main/", 1)[1].split("?")[0]
        return by_path.get(path, path)
    return re.sub(r"^https?://localhost:\d+/", "", url.split("?")[0])


def main(stem: str, step: str | None, trace: bool = False) -> int:
    data = json.loads((HERE / "static" / f"{stem}.perf.json").read_text(encoding="utf-8"))
    by_path = names()
    reqs = [(label(u, by_path), s, e, b) for u, s, e, b in data["resources"]]
    print(f"{len(reqs)} requests, {sum(r[3] for r in reqs) / 1e6:.2f} MB; page {data.get('page')}")
    if data.get("data"):
        d = data["data"]
        print(f"data: {d['requests']} requests ({d['hostRequests']} to the host), {d['bytes'] / 1e6:.2f} MB; "
              f"from the browser's cache {d['cacheHits']} reads, {d['cacheBytes'] / 1e6:.2f} MB")
    print("\nsteps (start ms, took ms; requests while it ran, MB, the longest request ms):")
    for name, s, e in sorted(data["measures"], key=lambda m: m[1]):
        inside = [r for r in reqs if r[1] >= s and r[2] <= e + 1]
        longest = max((r[2] - r[1] for r in inside), default=0)
        print(f"  {name:28s} {s:7d} {e - s:7d}   {len(inside):4d} req {sum(r[3] for r in inside) / 1e6:7.2f} MB  {longest:6d}")
    window = None
    if step:
        hit = [m for m in data["measures"] if m[0] == step]
        if not hit:
            print(f"no step {step}")
            return 1
        window = (hit[0][1], hit[0][2])
        print(f"\nduring {step} ({window[1] - window[0]} ms):")
    files = defaultdict(lambda: [0, 0, 0, 10 ** 9, 0])
    for name, s, e, b in reqs:
        if window and not (s >= window[0] and e <= window[1] + 1):
            continue
        f = files[name]
        f[0] += 1
        f[1] += b
        f[2] += e - s
        f[3] = min(f[3], s)
        f[4] = max(f[4], e)
    print("\nfiles (requests, MB, summed request ms, first start, last end):")
    for name, (n, b, ms, first, last) in sorted(files.items(), key=lambda kv: -kv[1][1])[:40]:
        print(f"  {n:4d} {b / 1e6:7.3f} {ms:7d} {first:7d} {last:7d}  {name}")
    if trace and data.get("trace"):
        # the app's own log of its data requests (?trace=1): each with its file and bytes, in the order
        # asked for, times from the step's start (or the page's)
        t0 = window[0] if window else 0
        rows = [r for r in data["trace"] if not window or (window[0] - 1 <= r["t0"] <= window[1])]
        print(f"\ndata requests{' during ' + step if step else ''} (asked, answered, ms, KB, via, file, bytes):")
        for r in sorted(rows, key=lambda r: r["t0"]):
            print(f"  {r['t0'] - t0:7.0f} {r['t1'] - t0:7.0f} {r['t1'] - r['t0']:6.0f} {(r['end'] - r['start']) / 1e3:8.1f} "
                  f"{r['via']:4s} {r['file']}  {r['start']}-{r['end']}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stem")
    ap.add_argument("--step", help="only the requests made during this step")
    ap.add_argument("--trace", action="store_true", help="also list the app's data requests (a capture with ?trace=1)")
    a = ap.parse_args()
    sys.exit(main(a.stem, a.step, a.trace))
