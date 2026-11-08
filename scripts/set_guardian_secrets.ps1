# Saves the Position Guardian's secrets in your Windows user settings (environment variables), for
# scripts/p13c_pass_marks.py and a local API. Everything is typed here, hidden, and never written to the repo,
# a log, or a chat. Press Enter on a question to skip it (and keep what is already saved).
#
#   & .\scripts\set_guardian_secrets.ps1
#
# 1. TELEGRAM_BOT_TOKEN: from @BotFather on Telegram (/newbot), looks like 123456789:AA...
# 2. Binance futures TESTNET keys (https://testnet.binancefuture.com -> API Key):
#      a read-only key  -> BINANCE_TESTNET_READ_KEY / _SECRET   (the pass mark's watched account)
#      a trading key    -> BINANCE_TESTNET_TRADE_KEY / _SECRET  (only to show it is refused)
# 3. GUARDIAN_SECRET for Railway: offered as a fresh random value, copied to the clipboard, never shown.
# Open a NEW terminal afterwards so it sees the values.

function Read-Secret([string]$prompt) {
    $s = Read-Host -Prompt $prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Save([string]$name, [string]$value, [string]$pattern, [string]$what) {
    if (-not $value) { Write-Output "  $name skipped."; return }
    if ($value -notmatch $pattern) { Write-Output "  $name NOT saved: that does not look like $what."; return }
    [Environment]::SetEnvironmentVariable($name, $value, "User")
    Write-Output ("  $name saved (ends in ..." + $value.Substring([Math]::Max(0, $value.Length - 4)) + ").")
}

$tok = Read-Secret "Telegram bot token (hidden, Enter to skip)"
Save "TELEGRAM_BOT_TOKEN" $tok '^\d{6,12}:[A-Za-z0-9_-]{30,}$' "a bot token (digits, a colon, then letters)"

$key = '^[A-Za-z0-9]{16,128}$'
Save "BINANCE_TESTNET_READ_KEY" (Read-Secret "Testnet READ-ONLY API key (hidden, Enter to skip)") $key "a Binance API key"
Save "BINANCE_TESTNET_READ_SECRET" (Read-Secret "Testnet READ-ONLY secret (hidden, Enter to skip)") $key "a Binance secret"
Save "BINANCE_TESTNET_TRADE_KEY" (Read-Secret "Testnet TRADING API key (hidden, Enter to skip)") $key "a Binance API key"
Save "BINANCE_TESTNET_TRADE_SECRET" (Read-Secret "Testnet TRADING secret (hidden, Enter to skip)") $key "a Binance secret"

$ans = Read-Host "Make a new GUARDIAN_SECRET and copy it to the clipboard for Railway? (y/N)"
if ($ans -match '^[yY]') {
    $bytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
    Set-Clipboard -Value $secret
    Write-Output "  Copied (48 characters). Paste it as GUARDIAN_SECRET in Railway's API service -> Variables, then copy something else."
    Write-Output "  Keep it the same afterwards: changing it makes every saved Binance key unreadable (users would add them again)."
}
Write-Output "Done. Open a new terminal, then tell Claude it's done (never paste the values in chat)."
