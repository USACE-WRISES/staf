"""Generate desktop-manifest.json (ships inside the StreamCurves apps payload).

StreamCurves Desktop runs one app, stream-curves/ at the payload root (beside library/), so the
app list is a fixed literal here: stdlib-only, no yaml. The shell reads {id, dir, entry} to
spawn the server; the rest is descriptive. desktop/dev/dev-manifest.json carries the same entry
for dev mode.

Usage:
    python gen_desktop_manifest.py --apps-version <v> --env-version <v> --commit <sha> --out <file>
"""
from __future__ import annotations

import argparse
import json

APPS = [
    {
        "id": "streamcurves",
        "dir": "stream-curves",
        "entry": "app.py",
        "name": "StreamCurves",
        "fullName": "Reference & Regional Curve Development",
        "tier": "Detailed",
        "tierNum": 3,
        "role": "Builds reference and regional curves and the detailed assessments DEEP runs",
        "description": "Develops reference and regional curves and publishes detailed assessments for DEEP.",
        "status": "live",
    }
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps-version", required=True)
    parser.add_argument("--env-version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    manifest = {
        "schemaVersion": 1,
        "version": args.apps_version,
        "builtFromCommit": args.commit,
        "requiresEnv": args.env_version,
        "apps": APPS,
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print(f"[manifest] wrote {args.out} ({len(APPS)} app)")


if __name__ == "__main__":
    main()
