param(
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
$WebDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PORT = "$Port"
$Url = "http://127.0.0.1:$Port"

Write-Host "Starting Student Assistant Website on $Url ..." -ForegroundColor Cyan

# Open browser after a brief delay
Start-Job -ScriptBlock {
    param($targetUrl)
    Start-Sleep -Seconds 1
    Start-Process $targetUrl
} -ArgumentList $Url | Out-Null

if (Get-Command python -ErrorAction SilentlyContinue) {
    python "$WebDir\server.py"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py "$WebDir\server.py"
} elseif (Get-Command node -ErrorAction SilentlyContinue) {
    node "$WebDir\server.mjs"
} else {
    Write-Host "Neither Python nor Node.js was found in PATH." -ForegroundColor Red
    Write-Host "Install Python (https://python.org) or Node.js (https://nodejs.org), then run this script again."
    exit 1
}
