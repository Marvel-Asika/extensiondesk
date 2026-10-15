# Push this folder to your new, empty GitHub repository.
#   powershell -ExecutionPolicy Bypass -File scripts\windows_push.ps1 -RepoUrl https://github.com/<you>/extensiondesk.git
# Git Credential Manager opens a browser window to sign you in the first time.

param([Parameter(Mandatory = $true)][string]$RepoUrl)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".git")) { git init -b main }
if (-not (git config user.name)) {
  $n = Read-Host "Your name for git commits"
  git config user.name "$n"
}
if (-not (git config user.email)) {
  $e = Read-Host "Your email for git commits"
  git config user.email "$e"
}
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -m "Add eval results and demo run" }
git branch -M main
$remotes = git remote
if ($remotes -contains "origin") { git remote set-url origin $RepoUrl } else { git remote add origin $RepoUrl }
git push -u origin main
Write-Host "Pushed to $RepoUrl" -ForegroundColor Green
