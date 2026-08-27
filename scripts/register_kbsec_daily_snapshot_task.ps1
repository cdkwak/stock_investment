[CmdletBinding()]
param(
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$Time = "17:00"
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runnerPath = Join-Path $PSScriptRoot "manual\collect\collect_kbsec_daily_snapshot.py"
$taskName = "StockInvestmentRev1-KBSecDailySnapshot"

if ($Action -eq "Remove") {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "removed=$taskName"
    } else {
        Write-Output "not_found=$taskName"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw ".venv Python was not found"
}
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "KB daily snapshot runner was not found"
}

$scheduledAction = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument ('"{0}" --project-root "{1}"' -f $runnerPath, $projectRoot) `
    -WorkingDirectory $projectRoot
$scheduledTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At ([DateTime]::ParseExact($Time, "HH:mm", $null))
$scheduledSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $scheduledAction `
    -Trigger $scheduledTrigger `
    -Settings $scheduledSettings `
    -Description "Disabled KB IVSA0070 preflight definition; live execution requires a new reviewed incident-recovery authorization." `
    -Force | Out-Null

# The retained 2026-08-21 partial-write incident keeps this definition
# non-runnable.  Its action intentionally omits --confirm-live-daily, so even
# an explicit manual invocation remains provider-free until a later reviewed
# registration change establishes the missing rollback authority.
Disable-ScheduledTask -TaskName $taskName | Out-Null

Write-Output "installed_disabled=$taskName"
Write-Output "time=$Time"
