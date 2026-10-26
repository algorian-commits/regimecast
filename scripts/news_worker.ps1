# Keeps the news mood current: new GDELT batches + CoinDesk, Cointelegraph, The Block and
# Decrypt RSS -> de-duplicate -> FinBERT -> hourly mood (what GET /news-mood serves).
#
# One-time setup, in PowerShell from the repo root (needs the ML packages:
# pip install -r requirements-ml.txt):
#   powershell -ExecutionPolicy Bypass -File scripts\news_worker.ps1 -Register
#
# -Register adds a Windows scheduled task that runs one update every 15 minutes while
# you are logged in. Without -Register it runs one update now.
# -DataDir points at another data folder (e.g. the main clone's data\ when this runs from
# a second checkout); it is kept in the scheduled task.
# On Railway this becomes the `worker` service: python -m regimecast.sentiment.pipeline collect --every 15
param([switch]$Register, [string]$DataDir = "")

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not $DataDir) { $DataDir = Join-Path $repo "data" }
$log = Join-Path $DataDir "logs\news_worker.log"

if ($Register) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -DataDir `"$DataDir`"" -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
    # laptop: also run on battery; one update never needs more than 30 minutes
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName "RegimeCast news mood" -Action $action -Trigger $trigger `
        -Settings $settings -Description "Collects crypto headlines and rebuilds the hourly news mood" -Force | Out-Null
    Write-Output "Registered: runs every 15 minutes. Log: $log"
    exit 0
}

New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$env:REGIMECAST_DATA_DIR = $DataDir
Push-Location $repo
try {
    "$(Get-Date -Format s) start" | Out-File -Append -Encoding utf8 $log
    # Start-Process, not "& python ... 2>&1": PowerShell 5.1 turns a program's stderr
    # (progress messages) into error records that look like failures in the log
    $env:PYTHONIOENCODING = "utf-8"
    $out = Join-Path $DataDir "logs\news_worker.last"
    $p = Start-Process -FilePath $python -ArgumentList "-m regimecast.sentiment.pipeline collect" `
        -WorkingDirectory $repo -RedirectStandardOutput "$out.out" -RedirectStandardError "$out.err" `
        -NoNewWindow -Wait -PassThru
    Get-Content "$out.out", "$out.err" | Where-Object { $_ -notmatch "Loading weights" } |
        Out-File -Append -Encoding utf8 $log
    "$(Get-Date -Format s) exit $($p.ExitCode)" | Out-File -Append -Encoding utf8 $log
} finally {
    Pop-Location
}
