# Builds the apps payload: the four app trees + generated desktop-manifest.json.
#
# Staging comes from GIT-TRACKED content only (git archive) - apps/easi contains confidential,
# gitignored material on developer disks (docs/, scripts/.mmw_api_key) that must never enter a
# public release asset. Untracked-but-required runtime content is then added back explicitly
# (allowlist below: easi/www/figures).
[CmdletBinding()]
param(
    [string]$RepoRoot = (Join-Path $PSScriptRoot '..\..'),
    [string]$OutDir = (Join-Path $PSScriptRoot '..\build\release'),
    [string]$WorkDir = (Join-Path $PSScriptRoot '..\build\apps-work'),
    [Parameter(Mandatory)][string]$EnvVersion,
    [string]$AppsVersion,
    [string]$PythonExe,   # local: a python with pyyaml (repo .venv works)
    [string]$UvExe        # CI: use `uv run --with pyyaml` instead of PythonExe
)
$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$OutDir = [IO.Path]::GetFullPath($OutDir)
$WorkDir = [IO.Path]::GetFullPath($WorkDir)

# Untracked paths (relative to apps/) that the apps need at runtime and are safe to publish.
$untrackedAllowlist = @('easi\www\figures')

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
    Write-Host '[apps] staging tracked apps/ content via git archive...'
    $tarPath = Join-Path $WorkDir 'apps.tar'
    git archive --format=tar -o $tarPath 'HEAD:apps'
    if ($LASTEXITCODE -ne 0) { throw "git archive failed ($LASTEXITCODE)" }
    tar -xf $tarPath -C $stage
    if ($LASTEXITCODE -ne 0) { throw "tar extract failed ($LASTEXITCODE)" }

    # -- 2. Allowlisted untracked extras --
    foreach ($rel in $untrackedAllowlist) {
        $src = Join-Path $RepoRoot "apps\$rel"
        if (Test-Path $src) {
            $dest = Join-Path $stage $rel
            New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
            Copy-Item $src $dest -Recurse -Force
            Write-Host "[apps] added untracked runtime content: $rel"
        } else {
            Write-Warning "[apps] allowlisted path missing on disk: $rel"
        }
    }

    # -- 3. Generate desktop-manifest.json from docs/_data/apps.yml --
    $genScript = Join-Path $PSScriptRoot 'gen_desktop_manifest.py'
    $manifestOut = Join-Path $stage 'desktop-manifest.json'
    $genArgs = @($genScript, '--repo-root', $RepoRoot, '--apps-version', $AppsVersion,
                 '--env-version', $EnvVersion, '--commit', $commit, '--out', $manifestOut)
    if ($UvExe) {
        & $UvExe run --with pyyaml -- python @genArgs
    } elseif ($PythonExe) {
        & $PythonExe @genArgs
    } else {
        throw 'Pass -PythonExe (with pyyaml) or -UvExe'
    }
    if ($LASTEXITCODE -ne 0) { throw "manifest generation failed ($LASTEXITCODE)" }

    # -- 4. Zip (root = easi/ sfari/ deep/ stream-curves/ desktop-manifest.json) + sha256 --
    New-Item -ItemType Directory -Force $OutDir | Out-Null
    $zipPath = Join-Path $OutDir "staf-$AppsVersion.zip"
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    tar -a -c -f $zipPath -C $stage '*'
    if ($LASTEXITCODE -ne 0) { throw "zip failed ($LASTEXITCODE)" }
    $zipHash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -Encoding ascii "$zipPath.sha256" $zipHash

    $sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "[apps] done: $zipPath ($sizeMB MB, sha256 $zipHash)"
} finally {
    Pop-Location
}
