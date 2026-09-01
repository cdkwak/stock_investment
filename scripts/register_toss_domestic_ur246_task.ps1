[CmdletBinding()]
param(
    [ValidateSet("Install", "Remove", "DryRun")]
    [string]$Action = "DryRun",
    [string]$TaskName = "STOCK_DATA_TOSS_DOMESTIC_30M"
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$runnerPath = Join-Path $projectRoot "scripts\manual\collect\collect_toss_domestic_ur246.py"
$taskRunnerPath = Join-Path $projectRoot "scripts\manual\collect\run_toss_domestic_ur246_task.ps1"
$taskCommandPath = Join-Path $projectRoot "scripts\run_toss_domestic_ur246_task.cmd"

if ($Action -eq "Remove") {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Output "removed=$TaskName" }
    else { Write-Output "not_found=$TaskName" }
    exit 0
}

foreach ($path in @($pythonPath, $runnerPath, $taskRunnerPath, $taskCommandPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "required UR-246 scheduler input not found: $path" }
}

# schtasks supports a weekly weekday trigger with a bounded repetition duration.
# The verified Toss routes are KRX-session-only. 09:00 plus 06:30 duration
# Windows includes a repetition at the duration endpoint. A 06:00 duration
# therefore produces exact 09:00..15:00 half-hour wakes; 15:30 is excluded.
$taskCommand = ('cmd.exe /d /c ""{0}""' -f $taskCommandPath)
Write-Output "task=$TaskName"
Write-Output "schedule=MON-FRI@09:00 repetition=30m duration=6h (09:00..15:00)"
Write-Output "execution_limit=25m multiple_instances=IgnoreNew"
Write-Output "missed_start=bounded_current_window_or_api_zero"
Write-Output "power_policy=allow_battery_start,dont_stop_on_battery,wake_to_run"
Write-Output "command=UR-246 coordinator CLI (calendar gate before runtime credentials)"
Write-Output "manual_trigger=forbidden"

if ($Action -eq "DryRun") { exit 0 }

if ($taskCommand.Length -gt 261) { throw "UR-246 scheduled command exceeds schtasks limit: $($taskCommand.Length)" }
& schtasks.exe /Create /TN $TaskName /TR $taskCommand /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:00 /RI 30 /DU 06:00 /RL LIMITED /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks registration failed: exit=$LASTEXITCODE" }
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 25) `
    -MultipleInstances IgnoreNew
$scheduledAction = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument ('"{0}" --project-root "{1}" --confirm-ur246-window' -f $runnerPath, $projectRoot) `
    -WorkingDirectory $projectRoot
Set-ScheduledTask -TaskName $TaskName -Action $scheduledAction -Settings $settings | Out-Null
$registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($registered.TaskName -ne $TaskName) { throw "UR-246 task name readback mismatch" }
if (@($registered.Actions).Count -ne 1) { throw "UR-246 task action count readback mismatch" }
$registeredAction = @($registered.Actions)[0]
if ([IO.Path]::GetFileName([string]$registeredAction.Execute) -ine "pythonw.exe") {
    throw "UR-246 task executable readback mismatch"
}
if ([string]$registeredAction.Arguments -notlike ('*{0}*' -f $runnerPath) -or
    [string]$registeredAction.Arguments -notlike '*--confirm-ur246-window*') {
    throw "UR-246 task runner action readback mismatch"
}
if (@($registered.Triggers).Count -ne 1) { throw "UR-246 task trigger count readback mismatch" }
$trigger = @($registered.Triggers)[0]
$start = [datetime]::Parse([string]$trigger.StartBoundary)
if ($start.Hour -ne 9 -or $start.Minute -ne 0) {
    throw "UR-246 task 09:00 start readback mismatch"
}
if ([string]$trigger.Repetition.Interval -ne "PT30M" -or
    [string]$trigger.Repetition.Duration -ne "PT6H") {
    throw "UR-246 task repetition readback mismatch"
}
$taskXml = [xml](Export-ScheduledTask -TaskName $TaskName)
$days = @(
    $taskXml.Task.Triggers.CalendarTrigger.ScheduleByWeek.DaysOfWeek.ChildNodes |
        ForEach-Object { $_.Name } |
        Sort-Object
) -join ","
if ($days -ne "Friday,Monday,Thursday,Tuesday,Wednesday") {
    throw "UR-246 task weekday readback mismatch"
}
if ([string]$registered.Settings.MultipleInstances -ne "IgnoreNew") {
    throw "UR-246 task overlap policy readback mismatch"
}
if (-not [bool]$registered.Settings.StartWhenAvailable) {
    throw "UR-246 task missed-start policy readback mismatch"
}
if ([bool]$registered.Settings.DisallowStartIfOnBatteries -or
    [bool]$registered.Settings.StopIfGoingOnBatteries -or
    -not [bool]$registered.Settings.WakeToRun) {
    throw "UR-246 task power-policy readback mismatch"
}
$executionLimit = [System.Xml.XmlConvert]::ToTimeSpan(
    [string]$registered.Settings.ExecutionTimeLimit
)
if ($executionLimit -le [timespan]::Zero -or $executionLimit -ge (New-TimeSpan -Minutes 30)) {
    throw "UR-246 task execution limit readback mismatch"
}
Write-Output "installed=$TaskName"
