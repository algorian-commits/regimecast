# Keeps the news mood current: new GDELT batches + CoinDesk, Cointelegraph, The Block and
# Decrypt RSS -> de-duplicate -> FinBERT -> hourly mood (what GET /news-mood serves).
#
# One-time setup, in PowerShell from the repo root (needs the ML packages:
# pip install -r requirements-ml.txt):
#   powershell -ExecutionPolicy Bypass -File scripts\news_worker.ps1 -Register
#
# -Register adds a Windows scheduled task that runs one update every 15 minutes while
# you are logged in. Without -Register it runs one update now.
# On Railway this becomes the `worker` service: python -m regimecast.sentiment.pipeline collect --every 15
param([switch]$Register)

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "data\logs\news_worker.log"

if ($Register) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "RegimeCast news mood" -Action $action -Trigger $trigger `
        -Settings $settings -Description "Collects crypto headlines and rebuilds the hourly news mood" -Force | Out-Null
    Write-Output "Registered: runs every 15 minutes. Log: $log"
    exit 0
}

New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Push-Location $repo
try {
    "$(Get-Date -Format s) start" | Out-File -Append -Encoding utf8 $log
    & $python -m regimecast.sentiment.pipeline collect 2>&1 | Out-File -Append -Encoding utf8 $log
    "$(Get-Date -Format s) exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
} finally {
    Pop-Location
}
