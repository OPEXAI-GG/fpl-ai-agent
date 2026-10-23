param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$workspace = 'C:\Users\bedor\OneDrive\Documents\FPL\fpl-ai-agent-1'
$cli = 'C:\Users\bedor\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe'
$thread = '01a1631b-64bd-7122-9fcf-dbb94b902e19'
$promptPath = Join-Path $taskRoot 'review-prompt.md'
foreach ($required in @($cli, $promptPath, (Join-Path $taskRoot 'notify.ps1'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing required file: $required" }
}
if ($ValidateOnly) { Write-Output 'Validated CLI, prompt, notification helper and target thread configuration. Nothing queued.'; exit 0 }
Set-Location -LiteralPath $workspace
$env:AGENT_MODE = 'dry'
$env:AGENT_ENABLED = 'false'
$message = Get-Content -LiteralPath $promptPath -Raw -Encoding UTF8
$receipt = Join-Path $taskRoot 'queue-receipt.json'
if (Test-Path -LiteralPath $receipt) { throw 'A previous invocation receipt exists; refusing duplicate notification/review.' }
$started = [DateTimeOffset]::Now.ToString('o')
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$output = & $cli queue --thread $thread --message $message 2>&1
$result = $LASTEXITCODE
$ErrorActionPreference = $oldPreference
$output | Out-File -LiteralPath (Join-Path $taskRoot 'queue-output.log') -Encoding utf8
@{ started = $started; exit_code = $result; thread = $thread; status = $(if ($result -eq 0) {'queued'} else {'failed'}) } |
    ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
if ($result -eq 0) {
    & (Join-Path $taskRoot 'notify.ps1') -Title 'FPL review requested' -Message 'The 20:30 dry-only review has been queued in Codex. Keep Codex open. No team changes have been made; await the review result.'
} else {
    & (Join-Path $taskRoot 'notify.ps1') -Title 'FPL scheduled review failed' -Message 'Codex could not queue the review. Open the FPL Codex task now. Your stated deadline is 21:30 Dubai. No account changes were made.'
    exit 1
}
