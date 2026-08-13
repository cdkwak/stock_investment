[CmdletBinding()]
param(
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$Time = "17:00"
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runnerPath = Join-Path $PSScriptRoot "manual\collect_kbsec_daily_snapshot.py"
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
    -Argument ('"{0}" --project-root "{1}" --confirm-live-daily' -f $runnerPath, $projectRoot) `
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
    -Description "One Landing-first read-only KB IVSA0070 snapshot near 17:00 KST." `
    -Force | Out-Null

Write-Output "installed=$taskName"
Write-Output "time=$Time"
