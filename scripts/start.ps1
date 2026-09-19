# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License").

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NoOpen = $false
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "       Artemis Autonomous Mobile Agent UI             " -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# Use the same idempotent dependency installer as manual first-run setup.
& "$ScriptDir\install_deps.ps1"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "   [INFO] Would you like to configure ARTEMIS MCP and testing rules for your AI IDEs?" -ForegroundColor Cyan
$installMcp = "N"
if ([Console]::IsInputRedirected -eq $false) {
    $response = Read-Host "      Install MCP configuration and rules now? [Y/n]"
    $installMcp = if ($response -eq "") { "Y" } else { $response }
}
if ($installMcp -match "^[Yy]") {
    uv run python -m artemis mcp --install all
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   [WARN] MCP setup failed; retry with: uv run artemis mcp --install all" -ForegroundColor Yellow
    }
}

Write-Host "   [INFO] Launching Artemis Showcase UI and Admin Console..." -ForegroundColor Green
if ($NoOpen) {
    uv run python -m artemis ui --port $Port --no-open
} else {
    uv run python -m artemis ui --port $Port --open
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
