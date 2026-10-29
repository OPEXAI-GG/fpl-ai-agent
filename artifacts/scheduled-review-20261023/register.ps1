$ErrorActionPreference = 'Stop'
$name = 'FPL-Codex-Dry-Review-20261023-2030-Dubai'
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw 'Task already exists; refusing to overwrite.' }
$runAt = [DateTimeOffset]::Parse('2026-10-23T20:30:00+04:00').LocalDateTime
if ($runAt -le (Get-Date)) { throw 'Requested time has passed; refusing to schedule for another day.' }
$runner = Join-Path $PSScriptRoot 'queue-review.ps1'
& $runner -ValidateOnly
$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $runner + '"') -WorkingDirectory 'C:\Users\bedor\OneDrive\Documents\FPL\fpl-ai-agent-1'
$trigger = New-ScheduledTaskTrigger -Once -At $runAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'One-time 20:30 Dubai Codex review and dry-plan request. No live FPL actions. Requires logged-in user and Codex running.'
Register-ScheduledTask -TaskName $name -InputObject $task | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName $name | Select-Object NextRunTime,LastTaskResult
