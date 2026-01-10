param(
  [switch]$SkipInstall
)

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $repoRoot

try {
  Set-Location (Join-Path $repoRoot "docs")
  if (-not $SkipInstall) {
    bundle install
  }
  bundle exec jekyll serve --livereload
} finally {
  Pop-Location
}
