# QW-01: register a daily Windows scheduled task for the encrypted offsite backup.
#
# Usage (from the project folder, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\register_backup_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_backup_task.ps1 -At 03:30
#
# Before running: put BACKUP_OFFSITE_DIR and BACKUP_KEY into .env (see .env.example) and check
# once by hand: .\.venv\Scripts\python.exe scripts\backup_offsite.py
# The task runs as the current user while they are signed in (the laptop host is signed in
# whenever it serves the site); a missed run starts as soon as the computer is on again.
# Output goes to instance\backups\offsite.log. Remove the task with:
#   Unregister-ScheduledTask -TaskName "hrscreen-offsite-backup" -Confirm:$false
param(
    [string]$At = "03:00",
    [string]$TaskName = "hrscreen-offsite-backup"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python not found at $python (create the .venv first, see README)" }
$logDir = Join-Path $root "instance\backups"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "offsite.log"

$command = "& '$python' '$root\scripts\backup_offsite.py' *>> '$log'; & '$python' '$root\scripts\restore_check.py' *>> '$log'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$command`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "HR Resume Analyzer: encrypted database copy outside this computer (QW-01)" -Force | Out-Null
Write-Host "Task '$TaskName' registered: daily at $At. Log: $log"
Write-Host "Run it now to test: Start-ScheduledTask -TaskName '$TaskName'"
