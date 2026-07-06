"""Generate desktop-manifest.json (ships inside the apps payload) from docs/_data/apps.yml.

apps.yml stays the single source of truth for app names/tiers/descriptions/URLs; this script maps
it into the camelCase manifest the shell's launcher renders. Run via `uv run --with pyyaml`.

Usage:
    python gen_desktop_manifest.py --repo-root <dir> --apps-version <v> --env-version <v>
                                   --commit <sha> --out <file>
"""
from __future__ import annotations

import argparse
import json
import os

import yaml

# apps.yml id → folder under apps/ (ids deliberately match the apps' STAF_LINKS keys).
DIR_BY_ID = {
    "easi": "easi",
    "sfari": "sfari",
    "deep": "deep",
    "curves": "stream-curves",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--apps-version", required=True)
    parser.add_argument("--env-version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    apps_yml = os.path.join(args.repo_root, "docs", "_data", "apps.yml")
    with open(apps_yml, encoding="utf-8") as fh:
        entries = yaml.safe_load(fh)

    apps = []
    for entry in entries:
        app_id = entry["id"]
        if app_id not in DIR_BY_ID:
            raise SystemExit(f"apps.yml id '{app_id}' has no folder mapping — update DIR_BY_ID")
        apps.append(
            {
                "id": app_id,
                "dir": DIR_BY_ID[app_id],
                "entry": "app.py",
                "name": entry["name"],
                "fullName": entry.get("full_name", ""),
                "tier": entry.get("tier", ""),
                "tierNum": entry.get("tier_num", 0),
                "role": entry.get("role", ""),
                "description": entry.get("description", ""),
                "status": entry.get("status", ""),
                "webUrl": entry.get("url", ""),
            }
        )

    manifest = {
        "schemaVersion": 1,
        "version": args.apps_version,
        "builtFromCommit": args.commit,
        "requiresEnv": args.env_version,
        "apps": apps,
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print(f"[manifest] wrote {args.out} ({len(apps)} apps)")


if __name__ == "__main__":
    main()
