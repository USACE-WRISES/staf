"""Compose latest-desktop.json: the rolling manifest every installed StreamCurves Desktop polls.

It is published as an asset of the rolling `streamcurves-current` prerelease. Component URLs
normally sit under the official prefix (OFFICIAL_PREFIX below, the only prefix installed shells
accept without an override) and name the release assets
    streamcurves-env-cp312-<hash>.zip          (env component,  version env-cp312-<hash>)
    streamcurves-apps-YYYY.MM.DD-<sha>.zip     (apps component, version apps-YYYY.MM.DD-<sha>)

Two modes for the env component:
  --env-zip + --env-version + --env-url     env was rebuilt this run
  --carry-env-from <previous latest json>   env unchanged; copy its block forward verbatim

The apps component is always fresh. The shell block is carried from the previous manifest unless
--shell-version/--shell-installer-url/--shell-portable-url are given (the shell release workflow
stamps it itself). minShellVersion carries forward unless --min-shell overrides it.

Usage (CI):
    python gen_latest_manifest.py --apps-zip <zip> --apps-version <v> --apps-url <url>
        [--env-zip <zip> --env-version <v> --env-url <url> --python <ver>]
        [--carry-env-from previous.json]
        [--min-shell 1.0.0] [--shell-version v --shell-installer-url u --shell-portable-url u]
        --out latest-desktop.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os

OFFICIAL_PREFIX = "https://github.com/USACE-WRISES/staf/releases/download/"


def file_meta(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest(), os.path.getsize(path)


def installed_size_estimate(zip_size: int) -> int:
    return zip_size * 3  # zip -> on-disk rough factor; only used for the disk preflight


def note_prefix(name: str, url: str) -> None:
    # Local end-to-end manifests legitimately point elsewhere (http://127.0.0.1:8020/...); the
    # shell accepts that origin only when STREAMCURVES_MANIFEST_URL is served from it.
    if not url.startswith(OFFICIAL_PREFIX):
        print(f"[latest] note: the {name} URL is outside {OFFICIAL_PREFIX}; installed shells accept it "
              "only with STREAMCURVES_MANIFEST_URL served from the same origin")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps-zip", required=True)
    parser.add_argument("--apps-version", required=True)
    parser.add_argument("--apps-url", required=True)
    parser.add_argument("--env-zip")
    parser.add_argument("--env-version")
    parser.add_argument("--env-url")
    parser.add_argument("--python", default="")
    parser.add_argument("--carry-env-from")
    parser.add_argument("--min-shell")
    parser.add_argument("--shell-version")
    parser.add_argument("--shell-installer-url")
    parser.add_argument("--shell-portable-url")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    previous = None
    if args.carry_env_from and os.path.exists(args.carry_env_from):
        with open(args.carry_env_from, encoding="utf-8") as fh:
            previous = json.load(fh)

    if args.env_zip:
        if not (args.env_version and args.env_url):
            raise SystemExit("--env-zip requires --env-version and --env-url")
        sha, size = file_meta(args.env_zip)
        env = {
            "version": args.env_version,
            "url": args.env_url,
            "sha256": sha,
            "sizeBytes": size,
            "installedSizeBytes": installed_size_estimate(size),
            "python": args.python,
        }
    elif previous is not None:
        env = previous["components"]["env"]
    else:
        raise SystemExit("No env source: pass --env-zip ... or --carry-env-from with an existing manifest")

    apps_sha, apps_size = file_meta(args.apps_zip)
    apps = {
        "version": args.apps_version,
        "url": args.apps_url,
        "sha256": apps_sha,
        "sizeBytes": apps_size,
        "installedSizeBytes": installed_size_estimate(apps_size),
        "requiresEnv": env["version"],
    }
    note_prefix("env", env["url"])
    note_prefix("apps", apps["url"])

    shell = previous.get("shell") if previous else None
    if args.shell_version:
        shell = {
            "version": args.shell_version,
            "installerUrl": args.shell_installer_url or "",
            "portableUrl": args.shell_portable_url or "",
            "sha256": "",
        }

    min_shell = args.min_shell or (previous.get("minShellVersion") if previous else None) or "1.0.0"

    manifest = {
        "schemaVersion": 1,
        "minShellVersion": min_shell,
        **({"shell": shell} if shell else {}),
        "components": {"env": env, "apps": apps},
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print(f"[latest] wrote {args.out} (env={env['version']}, apps={apps['version']}, minShell={min_shell})")


if __name__ == "__main__":
    main()
