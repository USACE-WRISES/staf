# Builds a MINIATURE payload release for end-to-end testing of the payload pipeline:
#   env  = python-build-standalone 3.12 + pip-installed shiny (relocatable, ~60 MB)
#   apps = one stub Shiny app + desktop-manifest.json
#   latest-desktop.json with real sha256/sizes and absolute URLs under -BaseUrl
# The real env payload (full geospatial stack from env.lock) is built by build-env-payload.ps1;
# this exists so the manager/locator/supervisor chain can be exercised in minutes, not tens of them.
[CmdletBinding()]
param(
    [string]$OutDir = (Join-Path $PSScriptRoot '..\build\mini-release'),
    [string]$BaseUrl = 'http://127.0.0.1:8020/build/mini-release',
    [string]$EnvVersion = 'env-cp312-mini0001',
    [string]$AppsVersion = 'apps-mini0001'
)
$ErrorActionPreference = 'Stop'

$buildDir = Join-Path $PSScriptRoot '..\build\mini-work'
$OutDir = [IO.Path]::GetFullPath($OutDir)
$buildDir = [IO.Path]::GetFullPath($buildDir)
New-Item -ItemType Directory -Force $OutDir | Out-Null
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
New-Item -ItemType Directory -Force $buildDir | Out-Null

# -- 1. Fetch python-build-standalone (latest release, 3.12 x64 windows install_only) --
Write-Host '[mini] resolving python-build-standalone release...'
$release = Invoke-RestMethod 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'
$asset = $release.assets |
    Where-Object { $_.name -match '^cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$' } |
    Select-Object -First 1
if (-not $asset) {
    $asset = $release.assets |
        Where-Object { $_.name -match '^cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only\.tar\.gz$' } |
        Select-Object -First 1
}
if (-not $asset) { throw 'No suitable cpython 3.12 windows asset found in the latest python-build-standalone release.' }
Write-Host "[mini] using $($asset.name)"

$tarPath = Join-Path $buildDir $asset.name
Invoke-WebRequest $asset.browser_download_url -OutFile $tarPath
tar -xzf $tarPath -C $buildDir   # yields $buildDir\python
if ($LASTEXITCODE -ne 0) { throw "tar extraction failed ($LASTEXITCODE)" }
$py = Join-Path $buildDir 'python\python.exe'
if (-not (Test-Path $py)) { throw "python.exe not found after extraction" }

# -- 2. Install shiny into the standalone interpreter (no venv -> relocatable) --
Write-Host '[mini] pip install shiny...'
& $py -m pip install --quiet --no-warn-script-location shiny
if ($LASTEXITCODE -ne 0) { throw "pip install failed ($LASTEXITCODE)" }

# pip's Scripts\*.exe trampolines embed this machine's absolute path - the shell only ever runs
# `python.exe -m ...`, so drop them (same fixup as the real env build).
Get-ChildItem (Join-Path $buildDir 'python\Scripts') -Filter '*.exe' -ErrorAction SilentlyContinue | Remove-Item -Force

# -- 3. Stub apps payload --
Write-Host '[mini] staging stub app...'
$appsStage = Join-Path $buildDir 'apps-stage'
New-Item -ItemType Directory -Force (Join-Path $appsStage 'stub') | Out-Null
@'
from shiny import App, ui

app = App(
    ui.page_fluid(
        ui.h2("STAF payload smoke app"),
        ui.p("If you can read this, the payload pipeline works end to end."),
    ),
    None,
)
'@ | Set-Content -Encoding utf8 (Join-Path $appsStage 'stub\app.py')

$manifest = [ordered]@{
    schemaVersion   = 1
    version         = $AppsVersion
    builtFromCommit = 'mini'
    requiresEnv     = $EnvVersion
    apps            = @(
        [ordered]@{
            id = 'stub'; dir = 'stub'; entry = 'app.py'; name = 'Stub'
            fullName = 'Payload smoke app'; tier = 'Test'; tierNum = 1
            role = 'pipeline verification'
            description = 'Tiny app proving the payload pipeline: download, verify, extract, commit, boot.'
            status = 'test'; webUrl = ''
        }
    )
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $appsStage 'desktop-manifest.json')

# -- 4. Zip both components (zip ROOT = payload dir contents) --
Write-Host '[mini] zipping...'
$envZip = Join-Path $OutDir "staf-$EnvVersion.zip"
$appsZip = Join-Path $OutDir "staf-$AppsVersion.zip"
Remove-Item $envZip, $appsZip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $buildDir 'python') -DestinationPath $envZip -CompressionLevel Optimal
Compress-Archive -Path (Join-Path $appsStage '*') -DestinationPath $appsZip -CompressionLevel Optimal

# -- 5. latest-desktop.json --
function Get-Meta([string]$path) {
    $item = Get-Item $path
    [ordered]@{
        sha  = (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant()
        size = $item.Length
    }
}
$envMeta = Get-Meta $envZip
$appsMeta = Get-Meta $appsZip

$latest = [ordered]@{
    schemaVersion   = 1
    minShellVersion = '0.1.0'
    components      = [ordered]@{
        env  = [ordered]@{
            version = $EnvVersion
            url = "$BaseUrl/$(Split-Path $envZip -Leaf)"
            sha256 = $envMeta.sha; sizeBytes = $envMeta.size
            installedSizeBytes = [long]($envMeta.size * 3); python = '3.12'
        }
        apps = [ordered]@{
            version = $AppsVersion
            url = "$BaseUrl/$(Split-Path $appsZip -Leaf)"
            sha256 = $appsMeta.sha; sizeBytes = $appsMeta.size
            installedSizeBytes = [long]($appsMeta.size * 3); requiresEnv = $EnvVersion
        }
    }
}
$latest | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $OutDir 'latest-desktop.json')

Write-Host "[mini] done -> $OutDir"
Get-ChildItem $OutDir | Format-Table Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}} -AutoSize
