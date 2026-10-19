# RippleBack dev launcher (Windows PowerShell).
#
# Starts all three processes: the MCP server, the API adapter, and the Vite dev
# UI. Ctrl+C stops everything it started.
#
#   .\scripts\dev.ps1
#
# Requires: a .venv with the backend installed, and Node.js for the frontend.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "No .venv found. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e '.[dev]'"
}

$frontend = Join-Path $root "frontend"
if (-not (Test-Path -LiteralPath (Join-Path $frontend "node_modules"))) {
    Write-Host "Installing frontend dependencies (npm ci)..."
    Push-Location $frontend
    try { npm ci } finally { Pop-Location }
}

$children = @()
function Start-Ripple {
    param([string]$Label, [string]$File, [string[]]$Arguments, [string]$WorkingDir)
    Write-Host "Starting $Label..."
    $proc = Start-Process -FilePath $File -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDir -PassThru -NoNewWindow
    $script:children += $proc
    return $proc
}

$adapterPort = if ($env:RIPPLEBACK_ADAPTER_PORT) { $env:RIPPLEBACK_ADAPTER_PORT } else { "8001" }

try {
    Start-Ripple "MCP server (http://127.0.0.1:8000/mcp)" $python @("-m", "rippleback.server") $root | Out-Null
    Start-Ripple "API adapter (http://127.0.0.1:$adapterPort)" $python @("-m", "rippleback.adapter") $root | Out-Null
    Start-Ripple "Vite UI (http://localhost:5173)" "npm" @("run", "dev") $frontend | Out-Null

    Write-Host ""
    Write-Host "  Dashboard : http://localhost:5173"
    Write-Host "  Adapter   : http://127.0.0.1:$adapterPort/api/health"
    Write-Host "  MCP server: http://127.0.0.1:8000/mcp"
    Write-Host ""
    Write-Host "Press Ctrl+C to stop."
    Wait-Process -Id ($children | ForEach-Object { $_.Id })
}
finally {
    foreach ($proc in $children) {
        if ($proc -and -not $proc.HasExited) {
            Write-Host "Stopping $($proc.ProcessName) ($($proc.Id))..."
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
