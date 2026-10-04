"""A worker process for the USGS fetch jobs (``python -m hrslim.worker``).

GDAL reads through pyogrio hold Python's GIL, so a streamed read (a minute or
more) run on a thread of the web app would stall every other request. The app
starts one worker per USGS mode and sends it jobs, one JSON line each, on stdin.
The worker runs them in order, keeps its regions open between jobs (later clicks
show the warm cost), writes progress and results to the job files
(``hrslim.jobs``), and prints one JSON line per finished job on stdout.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    from hrslim import jobs
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except ValueError:
            continue
        done = jobs.run(job, jobs.pick_usgs)
        sys.stdout.write(json.dumps({"id": done["id"], "status": done["status"]}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
