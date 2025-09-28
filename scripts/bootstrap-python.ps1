param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not installed. Install it from https://github.com/astral-sh/uv and ensure it is on PATH."
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projects = @(
    @{ Name = "orchestrator"; Path = "src/orchestrator" },
    @{ Name = "session-manager"; Path = "src/session-manager" },
    @{ Name = "hero-inference"; Path = "src/hero-inference" },
    @{ Name = "hero-training"; Path = "src/hero-training" }
)

foreach ($project in $projects) {
    $projectPath = Join-Path $repoRoot $project.Path
    $venvPath = Join-Path $projectPath ".venv"

    Write-Host "[uv] Bootstrapping $($project.Name)" -ForegroundColor Cyan

    if ($Force -and (Test-Path $venvPath)) {
        Remove-Item -Recurse -Force $venvPath
    }

    if (-not (Test-Path $venvPath)) {
        uv venv $venvPath
    }

    Push-Location $projectPath
    try {
        uv pip install -e .
    } finally {
        Pop-Location
    }
}

Write-Host "All Python projects are ready to go." -ForegroundColor Green
