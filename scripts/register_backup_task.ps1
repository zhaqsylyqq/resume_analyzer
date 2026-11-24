# QW-01: register the Windows scheduled task for the encrypted offsite backup.
#
# Usage (from the project folder, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\register_backup_task.ps1
#
# The USB drive does not have to stay in the laptop. The task runs every 30 minutes and at
# sign-in (`flask --app app backup-auto`, no window):
#   - drive connected and the newest copy older than 20 hours -> make a copy and verify it;
#   - drive not connected for more than 3 days -> a Windows notification asks to plug it in
#     (at most once a day); a failed backup also shows a notification.
# The drive may get another letter next time: any drive with a "hrscreen-backups" folder at
# its root is used. Runs on battery too. Output: instance\backups\offsite.log.
#
# Before running: BACKUP_OFFSITE_DIR and BACKUP_KEY in .env (see .env.example).
# Remove the task with:
#   Unregister-ScheduledTask -TaskName "hrscreen-offsite-backup" -Confirm:$false
param(
    [string]$TaskName = "hrscreen-offsite-backup",
    [int]$EveryMinutes = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Python not found at $pythonw (create the .venv first, see README)" }
New-Item -ItemType Directory -Force -Path (Join-Path $root "instance\backups") | Out-Null

# pythonw: no console window flashing every half hour.
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m flask --app app backup-auto" -WorkingDirectory $root
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($repeat, $logon) -Settings $settings `
    -Description "HR Resume Analyzer: encrypted database copy to the USB drive when it is connected (QW-01)" -Force | Out-Null
Write-Host "Task '$TaskName' registered: every $EveryMinutes min and at sign-in. Log: instance\backups\offsite.log"
Write-Host "Run it now to test: Start-ScheduledTask -TaskName '$TaskName'"
