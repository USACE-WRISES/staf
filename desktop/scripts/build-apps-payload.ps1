# Builds the StreamCurves apps payload: the app tree, the assessment library beside it, and a
# generated desktop-manifest.json.
#
# Staging comes from GIT-TRACKED content only (git archive of HEAD): a checkout carries
# gitignored material (workspaces, caches, local keys) that must never enter a public release
# asset, and uncommitted edits are not part of a release either. The archive is taken from the
# FULL tree so the root .gitattributes applies: text comes out LF and the -text pins keep their
# recorded bytes, exactly as in any checkout. (A subtree archive, HEAD:apps, does not read the
# root .gitattributes, so core.autocrlf would decide: CRLF on a Windows build machine, and the
# app would then hash different bytes than the maintainer's checkout.) core.autocrlf=false
# takes the machine's setting out of it. Two trees ship, as SIBLINGS at the zip root, because
# the app resolves its library as ..\library (streamcurves/library.py):
#   stream-curves\   apps/stream-curves minus tests\ and brand\ (development-only)
#   library\         apps/library (the published assessment library)
#
# Output: <OutDir>\streamcurves-<AppsVersion>.zip + .sha256, AppsVersion = apps-YYYY.MM.DD-<sha>.
[CmdletBinding()]
param(
    [string]$RepoRoot,   # default: the checkout this script sits in
    [string]$OutDir,     # default: desktop\build\release
    [string]$WorkDir,    # default: desktop\build\apps-work
    [Parameter(Mandatory)][string]$EnvVersion,
    [string]$AppsVersion,
    [string]$PythonExe = 'python'   # any python 3 - the manifest generator is stdlib-only
)
$ErrorActionPreference = 'Stop'
# Defaults resolve here, not in param(): Windows PowerShell 5.1 leaves $PSScriptRoot empty
# while binding an advanced script's param() defaults under `powershell -File`.
if (-not $RepoRoot) { $RepoRoot = Join-Path $PSScriptRoot '..\..' }
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot '..\build\release' }
if (-not $WorkDir) { $WorkDir = Join-Path $PSScriptRoot '..\build\apps-work' }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$OutDir = [IO.Path]::GetFullPath($OutDir)
$WorkDir = [IO.Path]::GetFullPath($WorkDir)

# Pathspecs from the repository root; extraction strips the leading apps/ so the zip root
# holds stream-curves/ and library/ directly.
$pathspecs = @(
    'apps/stream-curves',
    'apps/library',
    ':(exclude)apps/stream-curves/tests',
    ':(exclude)apps/stream-curves/brand'
)

Push-Location $RepoRoot
try {
    $commit = (git rev-parse --short HEAD).Trim()
    if (-not $AppsVersion) {
        $AppsVersion = "apps-$(Get-Date -Format 'yyyy.MM.dd')-$commit"
    }
    Write-Host "[apps] APPS_VERSION = $AppsVersion"
    "APPS_VERSION=$AppsVersion" | Write-Output

    if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
    $stage = Join-Path $WorkDir 'stage'
    New-Item -ItemType Directory -Force $stage | Out-Null

    # -- 1. Stage tracked content only --
    Write-Host '[apps] staging tracked stream-curves + library content via git archive...'
    $tarPath = Join-Path $WorkDir 'apps.tar'
    git -c core.autocrlf=false archive --format=tar -o $tarPath HEAD -- @pathspecs
    if ($LASTEXITCODE -ne 0) { throw "git archive failed ($LASTEXITCODE)" }
    tar -xf $tarPath -C $stage --strip-components=1
    if ($LASTEXITCODE -ne 0) { throw "tar extract failed ($LASTEXITCODE)" }
    Remove-Item $tarPath -Force

    # The layout the shell and the app depend on: fail the build, not the install.
    foreach ($required in @('stream-curves\app.py', 'library\catalog.json')) {
        if (-not (Test-Path (Join-Path $stage $required))) { throw "staged payload is missing $required" }
    }
    foreach ($excluded in @('stream-curves\tests', 'stream-curves\brand')) {
        if (Test-Path (Join-Path $stage $excluded)) { throw "staged payload must not contain $excluded" }
    }
    # The bytes StreamCurves fingerprints must be the bytes the committed records describe
    # (data/nrsa_provenance.json, data/nrsa/manifest.json), and the configs must be LF.
    & $PythonExe (Join-Path $PSScriptRoot 'check_payload_records.py') --stage $stage
    if ($LASTEXITCODE -ne 0) { throw "staged payload bytes differ from their records ($LASTEXITCODE)" }

    # -- 2. Generate desktop-manifest.json (fixed single-app entry; stdlib-only) --
    $genScript = Join-Path $PSScriptRoot 'gen_desktop_manifest.py'
    $manifestOut = Join-Path $stage 'desktop-manifest.json'
    & $PythonExe $genScript --apps-version $AppsVersion --env-version $EnvVersion `
        --commit $commit --out $manifestOut
    if ($LASTEXITCODE -ne 0) { throw "manifest generation failed ($LASTEXITCODE)" }

    # -- 3. Zip (root = stream-curves\ library\ desktop-manifest.json) + sha256 --
    New-Item -ItemType Directory -Force $OutDir | Out-Null
    $zipPath = Join-Path $OutDir "streamcurves-$AppsVersion.zip"
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    tar -a -c -f $zipPath -C $stage stream-curves library desktop-manifest.json
    if ($LASTEXITCODE -ne 0) { throw "zip failed ($LASTEXITCODE)" }
    $zipHash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -Encoding ascii "$zipPath.sha256" $zipHash

    $sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "[apps] done: $zipPath ($sizeMB MB, sha256 $zipHash)"
} finally {
    Pop-Location
}
