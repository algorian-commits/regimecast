# Saves your Anthropic API key in your Windows user settings (environment variables), for the trade parser
# (POST /parse-trade) and scripts/m8_parser_eval.py. The key is typed here, hidden, and never written to the
# repo, a log, or a chat.
#
#   & .\scripts\set_anthropic_key.ps1
#
# Create the key at https://platform.claude.com -> API keys. Open a NEW terminal afterwards so it sees the key.

function Read-Secret([string]$prompt) {
    $s = Read-Host -Prompt $prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

$key = Read-Secret "Paste the Anthropic API key (hidden), then Enter"
if (-not $key) { Write-Output "Nothing saved: the key was empty."; exit 1 }
if (-not $key.StartsWith("sk-ant-")) { Write-Output "Nothing saved: Anthropic keys start with 'sk-ant-'."; exit 1 }
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $key, "User")
Write-Output ("Saved. Key ends in ..." + $key.Substring([Math]::Max(0, $key.Length - 4)) + ". Open a new terminal, then tell Claude it's done.")
