# Saves a READ-ONLY Binance API key in your Windows user settings (environment
# variables), for scripts/fetch_brackets.py. The key is typed here, hidden, and
# never written to the repo or shown in a chat.
#
#   & .\scripts\set_binance_key.ps1
#
# Create the key on Binance: Profile -> API Management -> Create API, then tick
# ONLY "Enable Reading" (no trading, no withdrawals).

function Read-Secret([string]$prompt) {
    $s = Read-Host -Prompt $prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

$key = Read-Secret "Paste the Binance API key (hidden), then Enter"
$secret = Read-Secret "Paste the Binance API secret (hidden), then Enter"
if (-not $key -or -not $secret) { Write-Output "Nothing saved: key or secret was empty."; exit 1 }
[Environment]::SetEnvironmentVariable("BINANCE_API_KEY", $key, "User")
[Environment]::SetEnvironmentVariable("BINANCE_API_SECRET", $secret, "User")
Write-Output ("Saved. Key ends in ..." + $key.Substring([Math]::Max(0, $key.Length - 4)) + ". You can tell Claude it's done.")
