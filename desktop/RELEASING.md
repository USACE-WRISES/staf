# StreamCurves Desktop: release model

StreamCurves Desktop is a C#/.NET 10 WinForms + WebView2 shell (`desktop/src/StreamCurves.Desktop`)
that runs ONE app, StreamCurves (`apps/stream-curves`), on the user's machine from a self-managed
payload: a relocatable python-build-standalone interpreter (the env component) plus the app tree
and the assessment library beside it (the apps component). Velopack packages the shell as a
per-user `Setup.exe` and a self-updating portable zip. Everything is published on this
repository's GitHub Releases (`USACE-WRISES/staf`), next to whatever other streams the
repository carries.

| Stream | Tag | Kind | Contains | Cadence |
|---|---|---|---|---|
| Shell | `streamcurves-vX.Y.Z` | **normal** release | `StreamCurvesDesktop-streamcurves-Setup.exe`, `StreamCurvesDesktop-streamcurves-Portable.zip`, full/delta `.nupkg`, the `releases.streamcurves.json` feed (+ `RELEASES-streamcurves`, `assets.streamcurves.json`) | only when the C# shell changes (every shell tag also ships a payload) |
| Payload | `streamcurves-payload-YYYY.MM.DD-<sha>` | prerelease | `streamcurves-apps-YYYY.MM.DD-<sha>.zip` (~31 MB, every run) and `streamcurves-env-cp312-<hash>.zip` (~295 MB, only when the env inputs changed) | every `streamcurves-v*` tag, or by hand |
| Manifest | `streamcurves-current` | rolling prerelease | `latest-desktop.json`, the one URL every installed shell polls | refreshed by both workflows |
| Library | `library` | rolling prerelease | `library.json` (the catalog) and, per assessment version, a pack, a DEEP bundle and an Excel calculator ([Library release stream](#library-release-stream)) | every push to `main` that changes `apps/library/**`, or by hand |

Workflows: `.github/workflows/streamcurves-shell.yml` (shell) and
`.github/workflows/streamcurves-payload.yml` (payload + manifest). Both run on a
`streamcurves-v*` tag, each in its own concurrency group: GitHub keeps only one pending run per
group, so a shared group let one tag's runs cancel each other (the v1.0.0 payload was lost that
way and was run by hand). GitHub can also deliver one tag push twice; each workflow's first job
then finds its release already published, and the duplicate does nothing. The shell's
manifest stamp and the payload's manifest refresh may interleave; the shell block is
informational (Velopack owns shell updates). `.github/workflows/library-release.yml`
publishes the library stream on its own schedule.

## The rules

### 1. Prerelease rule

**`streamcurves-payload-*` and `streamcurves-current` are ALWAYS prereleases. Only
`streamcurves-v*` shell releases are normal releases.** The shell's updater reads normal
releases only (`prerelease: false` in `ShellUpdater.cs`), and GitHub's "Latest" badge and
`releases/latest` go to the newest normal release of the repository. A payload published as a
normal release takes that badge away from an installer. CI passes `--prerelease` every time; if
one ever slips through by hand, fix it at once:

```
gh release edit <tag> --repo USACE-WRISES/staf --prerelease
```

### 2. Channel rule

**StreamCurves Desktop lives on its own Velopack channel, `streamcurves`, and keeps it.**
Velopack's GitHub source merges the `releases.<channel>.json` feed of EVERY release it lists,
and its `UpdateManager` never checks the package id. On the default channel (`win`), an
installed StreamCurves Desktop would offer any other Velopack app's installer that this
repository publishes on that channel, and the other way round. So every vpk command in
`streamcurves-shell.yml` passes `--channel streamcurves` (`vpk download`, `vpk pack`,
`vpk upload`), and the code sets no channel: an installed package updates on the channel it was
packed with. **Any future desktop app published from this repository needs its own channel.**

The channel also names the assets (`StreamCurvesDesktop-streamcurves-Setup.exe`, not
`-win-Setup.exe`); the shell workflow's normalization and manifest-stamp steps use those names.

### 3. The 10-release horizon

Velopack 1.2.0's GitHub source asks the API for the **10 newest releases of the whole
repository** (`releases?per_page=10&page=1`, prereleases included) and only then drops the
prereleases and looks for `releases.streamcurves.json`. A new shell release is always visible
(it is the newest), but once ten newer releases of any stream exist, installed shells that
have not updated yet stop being offered it, and `vpk download` stops finding it as a delta base
(the next release then ships full packages only). Each routine app update adds one payload
prerelease. Keep the count of releases between shell releases in mind: after a long run of
payload updates, cutting a patch shell release puts the current installer back on top, and
pruning dead payload prereleases (see [Release retention](#release-retention)) keeps the
horizon clear.

## Routine app update

```
gh workflow run streamcurves-payload.yml --repo USACE-WRISES/staf
```

(or Actions > streamcurves-payload > Run workflow, from `main`). The workflow checks the lock,
builds the apps zip from the tracked tree (`git archive` of `apps/stream-curves` without
`tests/` and `brand/`, plus `apps/library`), publishes the `streamcurves-payload-*`
prerelease and refreshes `streamcurves-current`. The archive is taken from the full tree with
`core.autocrlf` off, so the root `.gitattributes` applies and the zip holds exactly a
checkout's bytes: LF text, plus the CRLF files that `.gitattributes` pins because a record
holds their bytes. `desktop/scripts/check_payload_records.py` then compares the staged files
with `data/nrsa_provenance.json` and `data/nrsa/manifest.json` and fails the build on any
difference. (A subtree archive, `HEAD:apps`, would skip the root `.gitattributes` and emit
CRLF on a Windows runner.) On their next check (at start, then every
4 hours) installed shells show the native banner "A StreamCurves app update is ready (31 MB).
It installs in this window." with **Install update**.

The env component is rebuilt only when `desktop/payload/{env.lock,pbs.lock,prune.txt}` change
(ENV_VERSION is a content hash over those three files, CRs stripped) or the run ticks
`force_env_rebuild`; otherwise the previous manifest's env block is carried forward. A rebuilt
env always passes the **relocation smoke gate** first: the tree is moved, the heavy stack is
imported, and StreamCurves must answer HTTP 200 from the relocated interpreter. Never build
with `-SkipSmoke` for anything that ships.

## Versioned release (streamcurves-vX.Y.Z)

One user-facing number covers app and shell: `APP_VERSION` in
`apps/stream-curves/streamcurves/version.py` = `<Version>` in
`desktop/src/StreamCurves.Desktop/StreamCurves.Desktop.csproj` = the tag.

1. Add the `## vX.Y.Z (YYYY-MM-DD)` section to `apps/stream-curves/CHANGELOG.md`. It ships in the
   apps payload (the app's What's new) and becomes the release body.
2. Bump `APP_VERSION` and the csproj `<Version>` together. The shell workflow refuses a tag
   whose X.Y.Z differs from the csproj.
3. `git push origin main`, then:
   ```
   git tag streamcurves-vX.Y.Z
   git push origin streamcurves-vX.Y.Z
   ```

The tag fires both workflows. `streamcurves-shell` runs the unit tests, publishes self-contained
win-x64, packs with vpk 1.2.0 on the `streamcurves` channel (delta against the previous
release when it is within the 10-release horizon), renames the portable zip's launcher to the
id name, uploads a **normal** release titled `StreamCurves Desktop X.Y.Z`, fills its body from
the CHANGELOG section, and stamps the installer URLs onto `streamcurves-current`'s manifest.
`streamcurves-payload` ships the matching payload. Installed shells then show ONE banner, "A
StreamCurves update is ready: the app (N MB) and the desktop shell (X.Y.Z).", whose **Update
and restart** installs the payload and chains the shell download into a single restart; a
shell-only update reads "A new version of StreamCurves Desktop (X.Y.Z) is ready to download."
(`StreamCurves.Desktop.Core/UpdatePlanner.cs` composes the banner from both pending streams).

Code signing is deliberately dormant (unsigned decision). When a certificate exists, add the vpk
signing flags in `streamcurves-shell.yml` (marked comment), gated on repository secrets.

## Dependency (wheel) update

1. Edit the pin(s) in `apps/stream-curves/requirements.txt`.
2. Re-lock from the repository root and commit the lock with it:
   ```
   uv pip compile apps/stream-curves/requirements.txt --python-version 3.12 --python-platform windows --no-header -o desktop/payload/env.lock
   ```
3. Run the payload workflow (or tag a release). Its lock gate
   (`desktop/scripts/check_lock_consistency.py`) fails the run when a direct pin and env.lock
   disagree; the changed lock changes ENV_VERSION, so the env is rebuilt and smoke-gated.

`uv pip compile` prefers the versions already in the output file; if it warns that a kept pin
is yanked, add `--upgrade-package <name>` for that package. To bump the embedded Python itself,
update `desktop/payload/pbs.lock` (url + sha256 of a python-build-standalone release).
`desktop/payload/prune.txt` deletes paths from the env after install; the aiodns/pycares entry
fixes a Windows DNS failure in every HyRiver call and must stay (the smoke gate checks it).

## Library release stream

`apps/library` in this repository is the one source of truth for the STAF assessment library.
Only the maintainer writes it, from a checkout. The rolling prerelease `library` is what everyone
else reads: the Assessment library on StreamCurves Desktop's start page lists every version
(draft, preliminary and final), and DEEP (`apps/deep/deep/remote_library.py`) serves the
preliminary and final ones without a redeploy.

| Asset | What it is |
|---|---|
| `library.json` | the catalog (schema 1): every assessment and version, with status, validation, content digest, metric and function counts, revision notes and the assets below |
| `<id>-v<N>-p1-<sha8>.streamcurves` | the pack a user downloads and opens as their own project (the version's session plus its `meta.json`, `provenance.json` and `assessment.deep.json`) |
| `<id>-v<N>-<sha8>.deep.json` | the version's DEEP bundle, byte for byte |
| `<id>-v<N>-calculator-<sha8>.xlsx` | the version's Excel calculator, when it has one |

Asset names carry a content hash, so a name never changes meaning, and a rerun uploads nothing
new. `library.json` goes up last with `--clobber`, so a reader never sees a catalog that names an
asset still in flight (both readers treat a missing catalog as "being updated" and keep what they
had). A validation change rewrites only `library.json`. A status change (Approve as Preliminary,
Certify as Final) also rebuilds that version's pack, whose origin block names the status (packs
run 10 to 250 KB); the old pack stays on the release as superseded.

`library-release.yml` runs on every push to `main` that touches `apps/library/**` (or the
builder: `apps/stream-curves/scripts/library_release.py`, `streamcurves/gallery.py`,
`streamcurves/project_file.py`), and by hand:

```
gh workflow run library-release.yml --repo USACE-WRISES/staf
```

It creates `library` as a **prerelease** the first time (rule 1 applies here too) and never
recreates it, so it counts once against the [10-release horizon](#3-the-10-release-horizon).
The same script runs locally:

```
.venv\Scripts\python.exe apps\stream-curves\scripts\library_release.py build --out build\library
.venv\Scripts\python.exe apps\stream-curves\scripts\library_release.py check --dir build\library
.venv\Scripts\python.exe apps\stream-curves\scripts\library_release.py upload --dir build\library --dry-run
.venv\Scripts\python.exe apps\stream-curves\scripts\library_release.py prune --dir build\library
```

`check` compares a build with the live release; `prune` lists the assets the current catalog no
longer names (superseded packs) and deletes them only with `--yes`.

**How the readers find it.** StreamCurves reads
`https://github.com/USACE-WRISES/staf/releases/download/library/`, caches the catalog for 6
hours (the gallery's refresh button forces a fetch) and verifies every pack by size and sha256.
Offline, it falls back to the library snapshot the apps payload ships. DEEP refreshes every 10
minutes on a background thread, and on a link to a version it does not know yet. For local
testing, point both at a build folder: `STREAMCURVES_GALLERY_SOURCE=release` plus
`STREAMCURVES_LIBRARY_BASE_URL=<folder>` for StreamCurves, `DEEP_LIBRARY_URL=<folder>` for DEEP.

**The review round trip.**

1. A reviewer downloads a version from the Assessment library ("Download and open"). It becomes a
   project folder of their own under `Documents\StreamCurves Projects`.
2. They revise it; the project saves itself. Publish (stage 6) tells them publishing is done by
   the maintainer and offers **Save a copy for the maintainer**, which writes
   `<Name>.streamcurves`. They send that file.
3. The maintainer opens it in a checkout with `STAF_LIBRARY_PUBLISH=1` and
   `STAF_LIBRARY_MAINTAINER=<name>` (the dev-mode shell or `shiny run`) through **Projects > Open
   project**. Its REF-15 curve-source choices merge into the region record.
4. Publish preselects the assessment the copy came from and publishes the next version, as Draft
   by default or as Preliminary. DEEP is re-baked as part of it.
5. Commit `apps/library/**`, `apps/deep/data/**` and `apps/deep/www/calculators/**`, then push
   `main`. `library-release` refreshes the release: the gallery shows the new version on its next
   refresh, and DEEP picks up a preliminary version within 10 minutes.
6. Validate's **Approve as Preliminary** and **Certify as Final** move a version on later; commit
   and push again.

The bake stays as DEEP's offline fallback, so CLAUDE.md guardrail 11 still applies: re-bake,
commit and redeploy DEEP when the cloud copy should carry a version even with the release
unreachable.

## Local dev & QA

```
# unit tests (Core + tests only)
dotnet test desktop/StreamCurves.Desktop.slnx

# the WinForms host: dotnet test does NOT build it, so after any shell change
dotnet build desktop/src/StreamCurves.Desktop -c Debug
desktop/src/StreamCurves.Desktop/bin/Debug/net10.0-windows/StreamCurvesDesktop.exe
```

- **Dev mode** switches on when the exe runs from a checkout (walking up from the exe to a
  folder holding `apps\stream-curves\app.py` and `desktop\`): the app runs from the repo `.venv`
  (`<repo>\.venv\Scripts\python.exe`, working directory `apps\stream-curves`) using
  `desktop/dev/dev-manifest.json`, and the payload machinery is off. `STREAMCURVES_DESKTOP_DEV=0`
  disables it; `STREAMCURVES_REPO_ROOT=<checkout>` points it at another checkout.
- `STREAMCURVES_FORCE_PAYLOAD=1` exercises the installed-payload path from a checkout.
- `STREAMCURVES_MANIFEST_URL=<url>` points the payload manager at another `latest-desktop.json`
  and trusts that URL's origin for component downloads (QA only).
- `STREAMCURVES_DATA_ROOT=<dir>` relocates all shell state (default `%LOCALAPPDATA%\StreamCurves`:
  `payloads\`, `downloads\`, `cache\`, `logs\`, `webview-data\`, `tmp\`, `state.json`). Logs are
  `logs\shell.log` and `logs\streamcurves.log`; the launcher page's **Logs** link opens them.
- The app process gets `STREAMCURVES_DESKTOP=1`, `STREAMCURVES_DATA_ROOT`,
  `HYRIVER_CACHE_NAME=<root>\cache\hyriver.sqlite`, `MPLCONFIGDIR`, `PYTHONNOUSERSITE`,
  `PYTHONUTF8`, `PYTHONDONTWRITEBYTECODE` and the system proxy. An installed copy also REMOVES
  `STAF_LIBRARY_ROOT`, `STAF_LIBRARY_PUBLISH` and `STAF_LIBRARY_MAINTAINER`; dev mode passes
  them through, because canonical library publishing is a checkout workflow.
- Opt-in integration tests: `STREAMCURVES_ITEST=1` boots the real app from the repo `.venv` and
  stops it gracefully (`DevVenvIntegrationTests`); `STREAMCURVES_ITEST_PAYLOAD=1` installs a
  locally served payload over HTTP and boots it (`PayloadE2ETests`, recipe below). Both need the
  Debug host built first (its exe is the stop helper).

Payload builds, locally (Windows PowerShell 5.1 or pwsh; outputs in the gitignored
`desktop\build\`):

```
# env: python-build-standalone + env.lock, relocation smoke gate (a few minutes with a warm uv cache)
powershell -File desktop\scripts\build-env-payload.ps1 -UvExe .venv\Scripts\uv.exe
powershell -File desktop\scripts\build-env-payload.ps1 -VersionOnly     # just ENV_VERSION

# apps: git archive of HEAD (commit first: uncommitted edits are not in the zip)
powershell -File desktop\scripts\build-apps-payload.ps1 -EnvVersion <env-cp312-...> -PythonExe .venv\Scripts\python.exe
```

Run the build scripts in a console, not with all streams redirected inside Windows PowerShell
(`*> log`): 5.1 turns uv's ordinary stderr lines into terminating errors under
`$ErrorActionPreference = 'Stop'`. Process-level redirection (`Start-Process
-RedirectStandardOutput/-RedirectStandardError`, or `> log 2>&1` from another shell) is fine.

## Local end-to-end (no GitHub)

PowerShell, from the repository root:

1. Build both zips (above) into `desktop\build\release`.
2. Compose a manifest that points at a local server:
   ```
   .venv\Scripts\python.exe desktop\scripts\gen_latest_manifest.py `
     --apps-zip desktop\build\release\streamcurves-apps-<date>-<sha>.zip --apps-version apps-<date>-<sha> `
     --apps-url http://127.0.0.1:8020/build/release/streamcurves-apps-<date>-<sha>.zip `
     --env-zip desktop\build\release\streamcurves-env-cp312-<hash>.zip --env-version env-cp312-<hash> `
     --env-url http://127.0.0.1:8020/build/release/streamcurves-env-cp312-<hash>.zip `
     --python 3.12.13 --out desktop\build\release\latest-desktop.json
   ```
3. Serve the `desktop\` folder (leave it running):
   `.venv\Scripts\python.exe -m http.server 8020 --bind 127.0.0.1 --directory desktop`
4. Then one of:
   - **checkout shell**: first-run setup downloads, verifies, unpacks and starts the app.
     ```
     $env:STREAMCURVES_FORCE_PAYLOAD = '1'
     $env:STREAMCURVES_MANIFEST_URL = 'http://127.0.0.1:8020/build/release/latest-desktop.json'
     $env:STREAMCURVES_DATA_ROOT = "$PWD\desktop\build\qa-data"
     & desktop\src\StreamCurves.Desktop\bin\Debug\net10.0-windows\StreamCurvesDesktop.exe
     ```
   - **installed shell**: pack an installer the way CI does, then install it from a terminal
     where `$env:STREAMCURVES_MANIFEST_URL` is set, so the app Setup launches inherits the
     variable (a Start-menu launch does not; relaunch
     `$env:LOCALAPPDATA\StreamCurvesDesktop\StreamCurvesDesktop.exe` from that terminal).
     Uninstall through Settings > Apps.
     ```
     dotnet publish desktop/src/StreamCurves.Desktop -c Release -r win-x64 --self-contained -p:Version=1.0.0 -o desktop/build/publish
     vpk pack --packId StreamCurvesDesktop --packVersion 1.0.0 --packDir desktop/build/publish --mainExe StreamCurvesDesktop.exe --packTitle "StreamCurves Desktop" --packAuthors "USACE WRISES" --icon desktop/resources/icon.ico --channel streamcurves --outputDir desktop/build/vpk
     $env:STREAMCURVES_MANIFEST_URL = 'http://127.0.0.1:8020/build/release/latest-desktop.json'
     & desktop\build\vpk\StreamCurvesDesktop-streamcurves-Setup.exe
     ```
   - **automated**: the first path as a test (its default manifest URL is the one above).
     ```
     $env:STREAMCURVES_ITEST_PAYLOAD = '1'
     dotnet test desktop/tests/StreamCurves.Desktop.Core.Tests --filter PayloadE2ETests
     ```

## First-time bootstrap

1. Commit the tree with `apps/stream-curves/CHANGELOG.md` carrying `## v1.0.0 (YYYY-MM-DD)` and
   `<Version>1.0.0</Version>` / `APP_VERSION = "1.0.0"`.
2. `git tag streamcurves-v1.0.0 && git push origin streamcurves-v1.0.0`. The shell release is
   created; the payload run builds the env (~20-30 min in CI with a cold cache) and the apps
   zip and creates `streamcurves-current`. If the shell workflow runs first, its manifest-stamp
   step skips (no manifest yet) and the manifest carries no `shell` block until the next shell
   release. That block is informational only (Velopack owns shell updates).
3. Install `StreamCurvesDesktop-streamcurves-Setup.exe` on a clean machine: first-run setup
   downloads ~330 MB (resumable, sha256-verified) and the app opens.

## Prerequisites & recovery

- Users need the **Microsoft WebView2 Runtime** (ships with Edge on Windows 10/11); the shell
  explains when it is missing. The installer is per-user, no admin rights needed.
- A Velopack install, Setup or portable, is `StreamCurvesDesktop.exe` (a stub launcher),
  `Update.exe` (Velopack's updater), `current\` (the running shell) and `packages\`, plus a
  `.portable` marker for the zip. Setup installs to `%LOCALAPPDATA%\StreamCurvesDesktop`; shell
  data lives apart in `%LOCALAPPDATA%\StreamCurves`, so shell updates never touch payloads.
  Without `Update.exe`, shell update checks are skipped (`[shell-update] not a packaged
  install - skipping check`) and only the payload stream keeps updating.
- vpk 1.2.0 writes the portable zip's launcher as `StreamCurves Desktop.exe` (the title) while
  Setup and the updater write `StreamCurvesDesktop.exe` (the id). The shell workflow renames the
  zip entry to the id name; `PortableJanitor` deletes a stale title-named copy at startup if a
  zip ever escapes that step.
- Offline or air-gapped install: put the Setup (or portable zip), both payload zips and
  `latest-desktop.json` in one folder, install the shell, then use **Install from file...** on
  the setup screen and pick that folder (the manifest's GitHub URLs resolve to the local zip
  names).

## Release retention

Published versions are immutable: never rebuild or move a released tag; roll forward with a new
patch version (updaters only offer strictly higher versions, and moved tags break the delta
chain). Deleting a release tag turns anything still pointing at it into a draft, so remove
releases, not tags.

- **Keep every `streamcurves-v*` shell release**: history, rollback targets, delta bases.
- **Payload prereleases**: only two are load-bearing at any time, the NEWEST (the manifest's
  apps zip) and whichever one hosts the env zip the current `latest-desktop.json` references
  (the env carries forward across payload runs, so that release may be weeks old; check the
  manifest's env URL before deleting anything). Everything older is dead storage, and deleting
  it keeps the [10-release horizon](#3-the-10-release-horizon) clear.
