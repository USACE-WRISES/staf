"""Cold start and memory of the STAF app against the three standalone apps.

Each app is imported in a fresh process, the work a Connect Cloud worker does before it serves its
first page, with the settings that keep imports off the network (EASI's adopted method, DEEP's
remote library). It reports the seconds the import took and the process's resident memory after it.
Sessions add to both (a tool's server starts when its tool is first shown); this is the floor.

    python apps/staf/scripts/measure_startup.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
APPS = HERE.parent
PROBE = ("import json, time\n"
         "started = time.perf_counter()\n"
         "import app\n"
         "seconds = time.perf_counter() - started\n"
         "import psutil\n"
         "print(json.dumps(dict(seconds=round(seconds, 1), rss_mb=round(psutil.Process().memory_info().rss / 2**20))))\n")


def measure(app_dir: Path) -> dict:
    env = dict(os.environ, EASI_ADOPTED_METHOD="0", DEEP_REMOTE_LIBRARY="0", STAF_DATA_SOURCE="service",
               PYTHONPATH=str(app_dir), PYTHONDONTWRITEBYTECODE="1")
    run = subprocess.run([sys.executable, "-c", PROBE], cwd=app_dir, env=env, capture_output=True, text=True,
                         timeout=900)
    if run.returncode:
        lines = run.stderr.strip().splitlines()
        return dict(error=lines[-1] if lines else "failed")
    return json.loads(run.stdout.strip().splitlines()[-1])


def main() -> int:
    rows = [("STAF, all three tools", measure(HERE))]
    rows += [(f"{key.upper()} alone", measure(APPS / key)) for key in ("easi", "sfari", "deep")]
    for name, result in rows:
        print(f"{name:22} " + "  ".join(f"{k}={v}" for k, v in result.items()))
    alone = [r for _, r in rows[1:] if "rss_mb" in r]
    if len(alone) == 3:
        print(f"{'three apps, summed':22} seconds={round(sum(r['seconds'] for r in alone), 1)}  "
              f"rss_mb={sum(r['rss_mb'] for r in alone)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
