# Serve the app on the internet through a named Cloudflare Tunnel (Windows).
#
# Usage (from the project folder):
#   powershell -ExecutionPolicy Bypass -File scripts\serve_public.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\serve_public.ps1 -Tunnel hrscreen -Port 8000
#
# Starts waitress on 127.0.0.1 (not reachable from the network directly) and then the tunnel.
# Ctrl+C stops both. Every page needs a login (app/auth.py); create the first admin once with
#   .\.venv\Scripts\flask.exe --app app create-admin
param(
    [string]$Tunnel = "hrscreen",
    [int]$Port = 8000,
    [string]$Cloudflared = (Join-Path $env:USERPROFILE "cloudflared\cloudflared.exe")
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$waitress = Join-Path $root ".venv\Scripts\waitress-serve.exe"

if (-not (Test-Path $Cloudflared)) {
    $found = Get-Command cloudflared -ErrorAction SilentlyContinue
    if (-not $found) { throw "cloudflared not found at $Cloudflared" }
    $Cloudflared = $found.Source
}

$env:SESSION_COOKIE_SECURE = "1"
$app = Start-Process -FilePath $waitress -WorkingDirectory $root -NoNewWindow -PassThru `
    -ArgumentList "--host=127.0.0.1", "--port=$Port", "--threads=8", "--call", "app:create_app"
Write-Host "App started on http://127.0.0.1:$Port (pid $($app.Id)); starting tunnel '$Tunnel'..."
try {
    & $Cloudflared tunnel run --url "http://127.0.0.1:$Port" $Tunnel
}
finally {
    if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force }
    Write-Host "Stopped."
}
