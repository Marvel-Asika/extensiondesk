# ExtensionDesk: Windows setup, model download and evals.
# Run from the repository folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\windows_setup.ps1
# Optional: -Model qwen3:4b   (default picks by RAM)
#           -SkipEvals        (only install and check)

param(
  [string]$Model = "",
  [switch]$SkipEvals
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path += ";$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\Ollama"
}

Write-Host "== 1/5 Installing tools if missing" -ForegroundColor Cyan
if (-not (Have "uv")) {
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  Refresh-Path
}
if (-not (Have "node")) {
  winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
  Refresh-Path
}
if (-not (Have "ollama")) {
  winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
  Refresh-Path
}
if (-not (Have "git")) {
  winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
  Refresh-Path
}

Write-Host "== 2/5 Choosing the model" -ForegroundColor Cyan
if ($Model -eq "") {
  $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
  if ($ramGB -ge 16) { $Model = "qwen3:8b" } else { $Model = "qwen3:4b" }
  Write-Host "RAM: $ramGB GB -> $Model"
}
$env:EXTENSIONDESK_MODEL = $Model

Write-Host "== 3/5 Starting Ollama and pulling $Model (one time, several GB)" -ForegroundColor Cyan
try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 3 | Out-Null }
catch { Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 5 }
ollama pull $Model

Write-Host "== 4/5 Installing Python dependencies and checking" -ForegroundColor Cyan
uv sync --extra dev
uv run extensiondesk --model $Model check

if (-not $SkipEvals) {
  Write-Host "== 5/5 Running evals (this calls the model many times; expect 10 to 40 minutes on a laptop)" -ForegroundColor Cyan
  uv run extensiondesk --model $Model evals
  Write-Host "Results written to EVALS.md and evals\results\" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. To record the demo, run:  uv run extensiondesk --model $Model demo" -ForegroundColor Green
