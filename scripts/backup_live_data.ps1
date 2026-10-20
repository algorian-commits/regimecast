# Copies the recorder's files from Railway to data/live on this PC (our backup).
#
# One-time setup, in PowerShell from the repo root:
#   [Environment]::SetEnvironmentVariable("RECORDER_URL", "https://recorder-production-796f.up.railway.app", "User")
#   [Environment]::SetEnvironmentVariable("RECORDER_TOKEN", "<token from Railway -> recorder -> Variables>", "User")
#   powershell -ExecutionPolicy Bypass -File scripts\backup_live_data.ps1 -Register
#
# -Register adds a Windows scheduled task that runs this every 6 hours while you are
# logged in. Without -Register it just runs the backup once.
param([switch]$Register)

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "data\logs\backup_live_data.log"

if ($Register) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 6)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
    Register-ScheduledTask -TaskName "RegimeCast live data backup" -Action $action -Trigger $trigger `
        -Settings $settings -Description "Copies RegimeCast recorder files from Railway to data/live" -Force | Out-Null
    Write-Output "Registered: runs every 6 hours. Log: $log"
    exit 0
}

New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$env:RECORDER_URL = [Environment]::GetEnvironmentVariable("RECORDER_URL", "User")
$env:RECORDER_TOKEN = [Environment]::GetEnvironmentVariable("RECORDER_TOKEN", "User")
Push-Location $repo
try {
    "$(Get-Date -Format s) start" | Out-File -Append -Encoding utf8 $log
    & $python scripts\pull_live_data.py 2>&1 | Out-File -Append -Encoding utf8 $log
    "$(Get-Date -Format s) exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
} finally {
    Pop-Location
}
