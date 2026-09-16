param(
    [int]$Port = 8004,
    [string]$Rollout = 'D:/Data/easi-national/review/alternative-2-rollout'
)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$pythonPath = Join-Path $repoPath '.venv/Scripts/python.exe'
$appPath = Join-Path $repoPath 'apps/easi'
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Stop the intended local EASI process before relaunching."
}
$env:EASI_CRITERIA_SET = 'regional'
$env:EASI_NATIONAL_BASE = Join-Path $Rollout 'staging'
$env:EASI_REVIEW_ROOT = 'D:/Data/easi-national'
$env:EASI_REVIEW_BASELINE = 'D:/Data/easi-national/review/2026-09-15-regional/baseline'
$env:PYTHONIOENCODING = 'utf-8'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
Push-Location $appPath
try {
    & $pythonPath -c "from easi.national.client import default_dataset; default_dataset().require_current(); print('Completed Alternative 2 bundle verified.')"
    if ($LASTEXITCODE -ne 0) { throw 'Alternative 2 verification failed; the application was not started.' }
} finally {
    Pop-Location
}
$logPath = Join-Path $Rollout 'logs'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutPath = Join-Path $logPath "app-$stamp.out.log"
$stderrPath = Join-Path $logPath "app-$stamp.err.log"
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @(
    '-m', 'shiny', 'run', 'app.py', '--host', '127.0.0.1', '--port', $Port
) -WorkingDirectory $appPath -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
[PSCustomObject]@{
    ParentProcessId = $serverProcess.Id
    App = "http://127.0.0.1:$Port/"
    Bundle = $env:EASI_NATIONAL_BASE
    Stdout = $stdoutPath
    Stderr = $stderrPath
} | ConvertTo-Json
