# APP-02: install (if needed) and prepare PostgreSQL 16 on this laptop for the app.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_local_postgres.ps1
#
# 1. If PostgreSQL 16 is not installed (or only a leftover Windows service of an old install
#    remains), removes the leftover and installs PostgreSQL 16 with winget, silently, without
#    pgAdmin/StackBuilder. Windows asks ONCE for administrator rights. Download ~300 MB, 5-10 min.
#    The superuser "postgres" gets a new random password, saved in .env as POSTGRES_ADMIN_PASSWORD
#    (copy it to the password manager).
#    If PostgreSQL is already installed, the script asks for the existing postgres password.
# 2. Creates the user "hrscreen" (random password -> .env POSTGRES_PASSWORD) and the databases
#    "hrscreen" (the site) and "hrscreen_test" (the test suite).
# DATABASE_URL is NOT set: the site keeps running on SQLite until the data is moved (APP-03).
# Safe to run again.
param(
    [string]$PgRoot = "C:\Program Files\PostgreSQL\16",
    [string]$Service = "postgresql-x64-16",
    [int]$Port = 5432
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$psql = Join-Path $PgRoot "bin\psql.exe"

function New-Secret {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $lines = @()
    if (Test-Path $envFile) { $lines = @(Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -notmatch "^\s*$Name\s*=" }) }
    $lines += "$Name=$Value"
    [IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
}

$adminPassword = $null
if (-not (Test-Path $psql)) {
    Write-Host "PostgreSQL 16 не установлен. Устанавливаю (Windows спросит разрешение администратора; загрузка ~300 МБ, 5-10 минут)..."
    $adminPassword = New-Secret
    $secretFile = Join-Path $env:TEMP ("pg-setup-" + [guid]::NewGuid().ToString("N") + ".txt")
    [IO.File]::WriteAllText($secretFile, $adminPassword)
    $elevated = @"
`$ErrorActionPreference = 'Stop'
`$old = Get-CimInstance Win32_Service -Filter "Name='$Service'"
if (`$old -and -not (Test-Path '$psql')) { Stop-Service -Name '$Service' -ErrorAction SilentlyContinue; `$old | Invoke-CimMethod -MethodName Delete | Out-Null; Start-Sleep 2 }
`$pw = Get-Content -Raw '$secretFile'
winget install --id PostgreSQL.PostgreSQL.16 --exact --silent --accept-package-agreements --accept-source-agreements --override "--mode unattended --unattendedmodeui none --superpassword `$pw --servicename $Service --serverport $Port --disable-components pgAdmin,stackbuilder"
exit `$LASTEXITCODE
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($elevated))
    try {
        $proc = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded"
    } finally {
        Remove-Item $secretFile -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path $psql)) { throw "Установка PostgreSQL не удалась (код $($proc.ExitCode)). Запустите скрипт ещё раз или напишите Claude." }
    Set-EnvValue "POSTGRES_ADMIN_PASSWORD" $adminPassword
    Write-Host "PostgreSQL 16 установлен. Пароль суперпользователя postgres записан в .env (POSTGRES_ADMIN_PASSWORD) — скопируйте его в менеджер паролей."
}

$svc = Get-Service $Service
if ($svc.Status -ne "Running" -or $svc.StartType -ne "Automatic") {
    Write-Host "Запускаю службу PostgreSQL (Windows спросит разрешение администратора)..."
    Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "-NoProfile -Command `"Set-Service -Name $Service -StartupType Automatic; Start-Service -Name $Service`""
    (Get-Service $Service).WaitForStatus("Running", "00:00:30")
}
Write-Host "Служба PostgreSQL работает и запускается вместе с Windows."

if (-not $adminPassword) {
    $secure = Read-Host "Введите пароль пользователя postgres (его задавали при установке PostgreSQL)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
$env:PGPASSWORD = $adminPassword
$appPassword = New-Secret

function Invoke-Psql([string]$Sql, [string]$Database = "postgres") {
    $out = & $psql -h 127.0.0.1 -p $Port -U postgres -d $Database -v ON_ERROR_STOP=1 -tAc $Sql 2>&1
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL: $out" }
    return ($out | Out-String).Trim()
}

try {
    if ((Invoke-Psql "SELECT 1 FROM pg_roles WHERE rolname = 'hrscreen'") -eq "1") {
        Invoke-Psql "ALTER ROLE hrscreen WITH LOGIN PASSWORD '$appPassword'" | Out-Null
    } else {
        Invoke-Psql "CREATE ROLE hrscreen WITH LOGIN PASSWORD '$appPassword'" | Out-Null
    }
    foreach ($db in @("hrscreen", "hrscreen_test")) {
        if ((Invoke-Psql "SELECT 1 FROM pg_database WHERE datname = '$db'") -ne "1") {
            Invoke-Psql "CREATE DATABASE $db OWNER hrscreen ENCODING 'UTF8' TEMPLATE template0" | Out-Null
        }
        Invoke-Psql "ALTER SCHEMA public OWNER TO hrscreen" $db | Out-Null
    }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

Set-EnvValue "POSTGRES_PASSWORD" $appPassword
Write-Host "Готово: пользователь hrscreen, базы hrscreen и hrscreen_test. Пароль hrscreen записан в .env (POSTGRES_PASSWORD)."
Write-Host "Сайт пока работает на SQLite; перенос данных — следующий шаг."
