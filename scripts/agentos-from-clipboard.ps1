# agentos-from-clipboard.ps1
# Read clipboard content and send to the bus as a message.
#
# Usage:
#   .\agentos-from-clipboard.ps1 -To codex -From openclaw [-Task t-001] [-Type HANDOFF]
#   .\agentos-from-clipboard.ps1 -To codex -From openclaw -PathToMessageFile reply.md

param(
    [Parameter(Mandatory=$true)] [string] $To,
    [Parameter(Mandatory=$true)] [string] $From,
    [string] $Task,
    [string] $Type = "HANDOFF",
    [string] $Priority = "NORMAL",
    [string] $PathToMessageFile
)

# Resolve body: file > clipboard > stdin
if ($PathToMessageFile) {
    if (-not (Test-Path $PathToMessageFile)) {
        Write-Error "File not found: $PathToMessageFile"
        exit 1
    }
    $body = Get-Content -Raw -Path $PathToMessageFile -Encoding UTF8
    $args_cli = @("--from-file", $PathToMessageFile)
} elseif ($input) {
    $body = $input | Out-String
    $args_cli = @()
} else {
    Add-Type -AssemblyName PresentationCore
    $body = [System.Windows.Clipboard]::GetText()
    if (-not $body) {
        Write-Error "Clipboard is empty."
        exit 1
    }
    $args_cli = @("--text", $body)
}

$cli_args = @("send", "--to", $To, "--from", $From, "--type", $Type, "--priority", $Priority)
if ($Task) { $cli_args += @("--task", $Task) }
$cli_args += $args_cli

& agentos @cli_args