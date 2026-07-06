# Builds the env payload: a relocatable python-build-standalone 3.12 interpreter with the full
# STAF dependency set installed straight into it (no venv -> no pyvenv.cfg path problems).
#
# Pipeline: pbs fetch+verify -> uv pip install (env.lock) -> fixups (drop Scripts\*.exe trampolines,
# fail on absolute-path .pth, apply prune.txt, long-path check) -> compileall (unchecked-hash pycs,
# CI prefix stripped) -> RELOCATION SMOKE GATE (move the tree, import the heavy stack, boot all
# four real apps) -> zip + sha256. Nothing should ever publish an env zip that skipped the gate.
#
# ENV_VERSION is content-derived: env-cp312-<first 8 hex of sha256(LF(env.lock) + LF(pbs.lock))>.
# The same computation runs in CI to decide whether a rebuild is needed at all.
[CmdletBinding()]
param(
    [string]$RepoRoot = (Join-Path $PSScriptRoot '..\..'),
    [string]$OutDir = (Join-Path $PSScriptRoot '..\build\release'),
    [string]$WorkDir = (Join-Path $PSScriptRoot '..\build\env-work'),
    [string]$UvExe = 'uv',
    [switch]$NoUvCache,
    [switch]$SkipSmoke,  # local debugging only - CI must never pass this
    [switch]$VersionOnly # print ENV_VERSION and exit (CI uses this to decide whether to rebuild)
)
$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$OutDir = [IO.Path]::GetFullPath($OutDir)
$WorkDir = [IO.Path]::GetFullPath($WorkDir)
$payloadDir = Join-Path $RepoRoot 'desktop\payload'
$envLock = Join-Path $payloadDir 'env.lock'
$pbsLock = Join-Path $payloadDir 'pbs.lock'
$pruneFile = Join-Path $payloadDir 'prune.txt'

function Get-EnvVersion {
    # Content hash over ALL env-build inputs (locks + prune recipe) so any change triggers a
    # rebuild in CI. Line endings must not change the version: normalize CRLF->LF so a Windows
    # working tree and a CI checkout agree.
    $envText = (Get-Content $envLock -Raw) -replace "`r`n", "`n"
    $pbsText = (Get-Content $pbsLock -Raw) -replace "`r`n", "`n"
    $pruneText = (Get-Content $pruneFile -Raw) -replace "`r`n", "`n"
    $bytes = [Text.Encoding]::UTF8.GetBytes($envText + $pbsText + $pruneText)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hex = -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })
    } finally { $sha.Dispose() }
    "env-cp312-$($hex.Substring(0, 8))"
}

$envVersion = Get-EnvVersion
Write-Host "[env] ENV_VERSION = $envVersion"
"ENV_VERSION=$envVersion" | Write-Output
if ($VersionOnly) { return }

New-Item -ItemType Directory -Force $OutDir | Out-Null
$zipPath = Join-Path $OutDir "staf-$envVersion.zip"
if (Test-Path $zipPath) {
    Write-Host "[env] $zipPath already exists - nothing to do"
    return
}

if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Force $WorkDir | Out-Null

# -- 1. python-build-standalone: fetch + verify against pbs.lock --
$pbs = Get-Content $pbsLock -Raw | ConvertFrom-Json
$tarPath = Join-Path $WorkDir $pbs.file
Write-Host "[env] fetching $($pbs.file)..."
Invoke-WebRequest $pbs.url -OutFile $tarPath
$actual = (Get-FileHash $tarPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $pbs.sha256) {
    throw "pbs archive hash mismatch: expected $($pbs.sha256), got $actual"
}
tar -xzf $tarPath -C $WorkDir
if ($LASTEXITCODE -ne 0) { throw "tar extraction failed ($LASTEXITCODE)" }
$pyRoot = Join-Path $WorkDir 'python'
$py = Join-Path $pyRoot 'python.exe'
if (-not (Test-Path $py)) { throw 'python.exe missing after extraction' }

# -- 2. Install the locked dependency set straight into the interpreter --
Write-Host '[env] uv pip install (env.lock)...'
$uvArgs = @('pip', 'install', '--python', $py, '--link-mode=copy', '-r', $envLock)
if ($NoUvCache) { $uvArgs += '--no-cache' }
& $UvExe @uvArgs
if ($LASTEXITCODE -ne 0) { throw "uv pip install failed ($LASTEXITCODE)" }

# -- 3. Fixups --
# 3a. pip's Scripts\*.exe trampolines embed this machine's absolute python path; the shell only
#     ever invokes python.exe -m ..., so they are dead weight that would break after relocation.
Get-ChildItem (Join-Path $pyRoot 'Scripts') -Filter '*.exe' -ErrorAction SilentlyContinue | Remove-Item -Force

# 3b. Absolute-path .pth files would silently re-point imports at the build machine.
$badPth = Get-ChildItem (Join-Path $pyRoot 'Lib\site-packages') -Filter '*.pth' |
    Where-Object { (Get-Content $_.FullName) -match '^[A-Za-z]:[\\/]' }
if ($badPth) {
    throw "Absolute paths found in .pth files: $($badPth.Name -join ', ') - not relocatable"
}

# 3c. prune.txt globs + long-path guard (python's ** globbing is authoritative here).
Write-Host '[env] pruning + path-length check...'
& $py -c @"
import pathlib, shutil, sys

root = pathlib.Path(r'$pyRoot')
prune_file = pathlib.Path(r'$pruneFile')
removed = 0
for line in prune_file.read_text(encoding='utf-8').splitlines():
    pattern = line.strip()
    if not pattern or pattern.startswith('#'):
        continue
    pattern = pattern.rstrip('/*').rstrip('/')
    for match in sorted(root.glob(pattern), reverse=True):
        if match.is_dir():
            shutil.rmtree(match, ignore_errors=True)
        else:
            match.unlink(missing_ok=True)
        removed += 1
print(f'[env] pruned {removed} path(s)')

too_long = [p for p in root.rglob('*') if len(str(p)) - len(str(root)) > 180]
if too_long:
    print('[env] FAIL: payload-relative paths too long (would break at 260-char limits):')
    for p in too_long[:10]:
        print('   ', p)
    sys.exit(1)
"@
if ($LASTEXITCODE -ne 0) { throw 'prune / path-length step failed' }

# -- 4. Precompile: pycs valid regardless of extraction mtime/path; CI prefix stripped --
Write-Host '[env] compileall...'
& $py -m compileall -f -q -j 0 --invalidation-mode unchecked-hash -s $WorkDir (Join-Path $pyRoot 'Lib')
if ($LASTEXITCODE -ne 0) { throw "compileall failed ($LASTEXITCODE)" }

# -- 5. RELOCATION SMOKE GATE: move the tree, then import + boot all four apps from it --
$relocated = Join-Path $WorkDir 'relocated'
New-Item -ItemType Directory -Force $relocated | Out-Null
Move-Item $pyRoot (Join-Path $relocated 'python')
$relocatedPy = Join-Path $relocated 'python\python.exe'

if ($SkipSmoke) {
    Write-Warning '[env] SMOKE GATE SKIPPED - do not publish this zip'
} else {
    & $relocatedPy (Join-Path $PSScriptRoot 'smoke_boot_apps.py') `
        --python $relocatedPy --apps-root (Join-Path $RepoRoot 'apps')
    if ($LASTEXITCODE -ne 0) { throw "relocation smoke gate FAILED ($LASTEXITCODE)" }
}

# -- 6. Zip (root = python\) + sha256 --
Write-Host '[env] zipping...'
tar -a -c -f $zipPath -C $relocated python
if ($LASTEXITCODE -ne 0) { throw "zip failed ($LASTEXITCODE)" }
$zipHash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Encoding ascii "$zipPath.sha256" $zipHash

$sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "[env] done: $zipPath ($sizeMB MB, sha256 $zipHash)"
