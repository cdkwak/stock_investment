[CmdletBinding()]
param(
    [ValidateSet("Install", "Check", "Remove", "DryRun")]
    [string]$Mode = "Check",
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    [string]$OwnerId = "windows-task-scheduler"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# This helper is invoked both interactively and from automated readback checks.
# Force a single wire encoding so a localized Task Scheduler error cannot be
# silently lost by a subprocess reader and mistaken for an empty CIM object.
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$controllerPath = Join-Path $PSScriptRoot "maintenance\workflow_controller.py"
$taskName = "STOCK_PROJECT_PYTHON_PM_EVENT_RUNNER"
$taskPath = "\"
$description = "StockInvestmentRev1 owner=python-pm-event-runner-v1; local Python PM material-event runner."
$executionTimeLimit = "PT15M"
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = ('"{0}" --repository-root "{1}" event-run-once --owner-id "{2}"' -f `
    $controllerPath, $projectRoot, $OwnerId)

function Assert-OwnedTask {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Registered
    )

    if (
        [string]$Registered.TaskName -cne $taskName -or
        [string]$Registered.TaskPath -cne $taskPath -or
        [string]$Registered.Description -cne $description
    ) {
        throw "scheduled task ownership marker mismatch"
    }
}

function Assert-CurrentUserPrincipal {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UserId
    )

    if ([string]::IsNullOrWhiteSpace($UserId)) {
        throw "scheduled task principal mismatch"
    }
    if ($UserId -ieq $currentUser) {
        return
    }
    try {
        $sidType = [System.Security.Principal.SecurityIdentifier]
        $registeredSid = ([System.Security.Principal.NTAccount]::new($UserId)).Translate($sidType)
        $currentSid = ([System.Security.Principal.NTAccount]::new($currentUser)).Translate($sidType)
    } catch {
        throw "scheduled task principal mismatch"
    }
    if ($registeredSid.Value -cne $currentSid.Value) {
        throw "scheduled task principal mismatch"
    }
}

function Assert-ExactTaskDefinition {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Registered
    )

    Assert-OwnedTask -Registered $Registered
    if ([string]$Registered.State -cnotin @("Ready", "Running")) {
        throw "scheduled task is not operational"
    }
    Assert-CurrentUserPrincipal -UserId ([string]$Registered.Principal.UserId)
    if (
        [string]$Registered.Principal.LogonType -cne "Interactive" -or
        [string]$Registered.Principal.RunLevel -cne "Limited"
    ) {
        throw "scheduled task principal mismatch"
    }

    $actions = @($Registered.Actions)
    $triggers = @($Registered.Triggers)
    if ($actions.Count -ne 1) {
        throw "scheduled task action count mismatch"
    }
    if ([string]$actions[0].Execute -cne $pythonPath) {
        throw "scheduled task interpreter mismatch"
    }
    if ([string]$actions[0].Arguments -cne $arguments) {
        throw "scheduled task arguments mismatch"
    }
    if ([string]$actions[0].WorkingDirectory -cne $projectRoot) {
        throw "scheduled task working directory mismatch"
    }
    if ($triggers.Count -ne 1) {
        throw "scheduled task trigger count mismatch"
    }
    if ([string]$triggers[0].CimClass.CimClassName -cne "MSFT_TaskTimeTrigger") {
        throw "scheduled task trigger class mismatch"
    }
    if (-not [bool]$triggers[0].Enabled) {
        throw "scheduled task trigger is disabled"
    }
    if ([string]::IsNullOrWhiteSpace([string]$triggers[0].StartBoundary)) {
        throw "scheduled task trigger boundary is missing"
    }
    try {
        $startBoundary = [DateTimeOffset]::Parse(
            [string]$triggers[0].StartBoundary,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    } catch {
        throw "scheduled task trigger boundary is invalid"
    }
    if ($startBoundary.ToUniversalTime() -gt [DateTimeOffset]::UtcNow.AddMinutes(5)) {
        throw "scheduled task trigger boundary is not operational"
    }
    if ([string]$triggers[0].Repetition.Interval -cne "PT1M") {
        throw "scheduled task trigger interval mismatch"
    }
    if (-not [string]::IsNullOrEmpty([string]$triggers[0].Repetition.Duration)) {
        throw "scheduled task trigger duration mismatch"
    }
    if (-not [bool]$triggers[0].Repetition.StopAtDurationEnd) {
        throw "scheduled task trigger stop policy mismatch"
    }
    if ([string]$Registered.Settings.MultipleInstances -cne "IgnoreNew") {
        throw "scheduled task overlap policy mismatch"
    }
    if ([string]$Registered.Settings.ExecutionTimeLimit -cne $executionTimeLimit) {
        throw "scheduled task time limit mismatch"
    }
    if (-not [bool]$Registered.Settings.StartWhenAvailable) {
        throw "scheduled task availability policy mismatch"
    }
}

function Get-ExactRegisteredTask {
    $registered = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
    Assert-ExactTaskDefinition -Registered $registered
    return $registered
}

if ($Mode -eq "DryRun") {
    if (
        $taskName -cne "STOCK_PROJECT_PYTHON_PM_EVENT_RUNNER" -or
        $taskPath -cne "\" -or
        $description -cne "StockInvestmentRev1 owner=python-pm-event-runner-v1; local Python PM material-event runner." -or
        $executionTimeLimit -cne "PT15M" -or
        [string]::IsNullOrWhiteSpace($currentUser) -or
        -not $pythonPath.EndsWith(".venv\Scripts\pythonw.exe", [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $controllerPath.EndsWith("scripts\maintenance\workflow_controller.py", [System.StringComparison]::OrdinalIgnoreCase) -or
        $arguments -cnotmatch ' --repository-root ".+" event-run-once --owner-id "[A-Za-z0-9][A-Za-z0-9._-]{0,63}"$'
    ) {
        throw "scheduler definition construction failed"
    }
    Write-Output "PYTHON_PM_TASK_DRY_RUN_OK"
    exit 0
}

if ($Mode -eq "Remove") {
    $existing = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Output "PYTHON_PM_TASK_NOT_FOUND"
    } else {
        Assert-OwnedTask -Registered $existing
        Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false
        Write-Output "PYTHON_PM_TASK_REMOVED"
    }
    exit 0
}

if ($Mode -eq "Check") {
    $null = Get-ExactRegisteredTask
    Write-Output "PYTHON_PM_TASK_OK"
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "project venv pythonw.exe was not found"
}
if (-not (Test-Path -LiteralPath $controllerPath -PathType Leaf)) {
    throw "workflow controller was not found"
}

$existing = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Assert-OwnedTask -Registered $existing
    try {
        Assert-ExactTaskDefinition -Registered $existing
        Write-Output "PYTHON_PM_TASK_INSTALLED"
        exit 0
    } catch {
        # Only an exactly marked task may be reconciled below. Ownership was
        # checked outside this catch so a foreign collision can never reach -Force.
    }
}

$scheduledAction = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument $arguments `
    -WorkingDirectory $projectRoot
$scheduledTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$scheduledSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$scheduledPrincipal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$registration = @{
    TaskName = $taskName
    TaskPath = $taskPath
    Action = $scheduledAction
    Trigger = $scheduledTrigger
    Settings = $scheduledSettings
    Principal = $scheduledPrincipal
    Description = $description
}
if ($null -ne $existing) {
    $registration["Force"] = $true
}
Register-ScheduledTask @registration | Out-Null

$null = Get-ExactRegisteredTask
Write-Output "PYTHON_PM_TASK_INSTALLED"
