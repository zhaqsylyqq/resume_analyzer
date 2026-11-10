# Serve the app on the internet through a named Cloudflare Tunnel (Windows).
#
# Usage (from the project folder):
#   powershell -ExecutionPolicy Bypass -File scripts\serve_public.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\serve_public.ps1 -Tunnel hrscreen -Port 8000
#
# Starts waitress on 127.0.0.1 (not reachable from the network directly) and then the tunnel.
# Ctrl+C stops both. Refuses to start without ACCESS_PASSWORD in .env, so the app is never
# public without the sign-in gate. The password itself is never printed.
param(
    [string]$Tunnel = "hrscreen",
    [int]$Port = 8000,
    [string]$Cloudflared = (Join-Path $env:USERPROFILE "cloudflared\cloudflared.exe")
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$waitress = Join-Path $root ".venv\Scripts\waitress-serve.exe"

if (-not (Test-Path $Cloudflared)) {
    $found = Get-Command cloudflared -ErrorAction SilentlyContinue
    if (-not $found) { throw "cloudflared not found at $Cloudflared" }
    $Cloudflared = $found.Source
}

& $python -c "import sys; from dotenv import dotenv_values; v = dotenv_values(r'$root\.env'); sys.exit(0 if len(v.get('ACCESS_PASSWORD') or '') >= 10 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Set ACCESS_PASSWORD (10+ characters) in .env before going public."
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
