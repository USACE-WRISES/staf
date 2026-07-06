# Releasing STAF Desktop

Two independent release streams share this repo's GitHub Releases:

| Stream | Tag | Release type | Contents | Cadence |
|---|---|---|---|---|
| **Shell** | `v*` (e.g. `v0.2.0`) | **normal** release | `StafDesktop-win-Setup.exe`, `StafDesktop-win-Portable.zip`, delta packages, `RELEASES` | rare |
| **Payload** | `desktop-payload-*` | **prerelease** | `staf-apps-….zip` (+ `staf-env-….zip` when pins changed) | routine |
| Manifest | `desktop-current` (rolling) | **prerelease** | `latest-desktop.json` — polled by every installed shell | auto-updated by both workflows |

## The one hard rule

**Payload releases and `desktop-current` must ALWAYS be prereleases.** GitHub defines
"latest release" as the newest non-prerelease — Velopack's updater and every human landing on
the Releases page resolve it. A payload release published as a normal release hijacks
`releases/latest` away from the installers. CI passes `--prerelease` automatically; if one ever
slips through by hand, edit the release and re-tick the prerelease checkbox.

## Routine app update (the common case)

```
git push origin main                                # your app changes, tests green
git tag desktop-payload-2026.07.15
git push origin desktop-payload-2026.07.15
```

~8 min later every desktop's next update check offers a ~12 MB apps update. If
`desktop/payload/env.lock` or `pbs.lock` changed since the previous manifest, the same run
automatically rebuilds and ships the env component too (~300 MB, users download it once).
Nothing different to do. (Manual alternative: Actions → desktop-payload → Run workflow.)

## Changing Python dependencies

1. Edit the relevant `apps/<app>/requirements.txt` pin(s).
2. Regenerate the union lock and commit it alongside:
   ```
   uv pip compile apps/easi/requirements.txt apps/sfari/requirements.txt \
     apps/deep/requirements.txt apps/stream-curves/requirements.txt \
     --python-version 3.12 --python-platform windows --no-header -o desktop/payload/env.lock
   ```
3. Tag a payload release as above. CI's consistency gate fails the build if a direct pin and
   env.lock disagree; the relocation smoke gate (all four apps boot from the relocated
   interpreter) must pass before anything publishes.

To bump the embedded Python itself, update `desktop/payload/pbs.lock` (url + sha256 from a
python-build-standalone release).

## Shell release

```
# optionally bump <Version> in desktop/src/Staf.Desktop/Staf.Desktop.csproj to match
git tag v0.2.0
git push origin v0.2.0
```

CI runs the unit tests, publishes self-contained win-x64, `vpk pack`s installer + portable +
deltas, uploads them to the `v0.2.0` release, and stamps the new shell block onto
`desktop-current`. Installed shells offer "Restart & update" on their next check; portable
copies self-update the same way (Velopack).

Signing is deliberately dormant (unsigned v1 decision). When a certificate exists (USACE org
cert like HEC-RAS 2025's, or Azure Trusted Signing), add the vpk signing flags in
`.github/workflows/desktop-shell.yml` (marked comment) gated on repo secrets.

## Local build & test commands

```
# unit tests (74)
dotnet test desktop/Staf.Desktop.slnx

# real env payload with the relocation smoke gate (~5-10 min warm)
desktop/scripts/build-env-payload.ps1 -UvExe .venv/Scripts/uv.exe

# apps payload (tracked content only + easi/www/figures allowlist)
desktop/scripts/build-apps-payload.ps1 -EnvVersion <env-…> -PythonExe .venv/Scripts/python.exe

# compose a local latest-desktop.json, serve desktop/ on :8020 (launcher-preview config),
# then run the gated full-pipeline E2E:
#   STAF_ITEST_PAYLOAD=1 [STAF_MINI_MANIFEST_URL=…] dotnet test --filter PayloadE2ETests
# tiny fixture release instead of the real one: desktop/scripts/build-mini-payload.ps1

# packaged artifacts exactly as CI ships them
dotnet publish desktop/src/Staf.Desktop -c Release -r win-x64 --self-contained -o desktop/build/publish
vpk pack --packId StafDesktop --packVersion 0.0.0-local --packDir desktop/build/publish --mainExe StafDesktop.exe --icon desktop/resources/icon.ico

# regenerate the app icon + site favicon (deterministic; both derive from the same art)
.venv/Scripts/python.exe desktop/scripts/make_icon.py
```

For fully offline sites: after a shell release exists, run the **desktop-offline-bundle** workflow
(Actions tab) — it attaches `staf-desktop-offline-<tag>.zip` (installer + portable + payload zips +
manifest + README) to that release. Users install the shell from it, then use Troubleshooting →
"Install from file…" pointed at the extracted folder.

Dev loop: launch `StafDesktop.exe` from a checkout and it runs the four apps from the repo
`.venv` (dev mode). `STAF_FORCE_PAYLOAD=1` exercises installed-payload mode in a checkout;
`STAF_MANIFEST_URL=<url>` points the payload manager somewhere else (QA);
`STAF_DATA_ROOT=<dir>` relocates all state.

## Pre-release manual checklist (fresh Windows VM, non-admin user)

1. Portable zip: extract anywhere, run `STAF Desktop.exe` (note SmartScreen behavior — unsigned).
2. Setup.exe: per-user install, no elevation prompt.
3. First run: runtime downloads with progress; kill the network mid-download, relaunch → resumes.
4. All four apps launch; EASI delineation runs; PDF/CSV/GeoJSON exports raise save dialogs.
5. Cross-link topnav (EASI → SFARI) opens/focuses native windows; external links open the browser.
6. Close everything → Task Manager shows no stray `python.exe`.
7. Publish a `desktop-payload-*` tag → footer chip appears; Install applies; apps restart onto it.
8. Install the previous shell version → in-app "Restart & update" lands on the new one.
