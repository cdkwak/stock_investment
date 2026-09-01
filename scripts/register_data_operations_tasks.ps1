[CmdletBinding()]
param(
    [ValidateSet("Install", "Remove", "DryRun")]
    [string]$Action = "DryRun",
    [ValidateSet("Health", "IssueState", "Fred", "Lending", "ShortSelling", "Vkospi", "KrIndex", "InvestorFlow", "KrMarketDaily", "GlobalIndex", "Soxx", "Futures", "YahooMarket", "BokTreasury", "TossAccount", "KbAccount", "All")]
    [string]$Target = "Health",
    [switch]$EnableIssueState,
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$HealthTime = "06:30",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$IssueStateTime = "06:45",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$FredTime = "06:00",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$LendingTime = "14:00",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$ShortSellingTime = "09:10",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$VkospiTime = "19:00",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$KrIndexTime = "19:10",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$InvestorFlowTime = "19:25",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$KrMiddayTime = "14:10",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$KrPostCloseTime = "20:30",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$GlobalIndexTime = "06:20",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$SoxxTime = "06:10",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$FuturesTime = "22:10",
    [ValidateRange(0, 59)]
    [int]$YahooMarketMinute = 2,
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$BokTreasuryTime = "17:10",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$TossAccountTime = "07:00",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$KbAccountTime = "07:10"
)

function Test-KrMarketDailyTaskDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [object]$Registered,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedExecute,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedArguments,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedWorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string[]]$ExpectedTimes
    )

    $errors = [System.Collections.Generic.List[string]]::new()
    $actions = @($Registered.Actions)
    if ($actions.Count -ne 1) {
        [void]$errors.Add("KR_ACTION_COUNT")
    } else {
        if ([string]$actions[0].Execute -ne $ExpectedExecute) {
            [void]$errors.Add("KR_ACTION_EXECUTE")
        }
        if ([string]$actions[0].Arguments -ne $ExpectedArguments) {
            [void]$errors.Add("KR_ACTION_ARGUMENTS")
        }
        if ([string]$actions[0].WorkingDirectory -ne $ExpectedWorkingDirectory) {
            [void]$errors.Add("KR_ACTION_WORKDIR")
        }
    }

    $triggers = @($Registered.Triggers)
    if ($triggers.Count -ne $ExpectedTimes.Count) {
        [void]$errors.Add("KR_TRIGGER_COUNT")
    }
    $actualTimes = [System.Collections.Generic.List[string]]::new()
    foreach ($trigger in $triggers) {
        if ([string]$trigger.CimClass.CimClassName -ne "MSFT_TaskDailyTrigger") {
            [void]$errors.Add("KR_TRIGGER_TYPE")
        }
        if (-not [bool]$trigger.Enabled) {
            [void]$errors.Add("KR_TRIGGER_DISABLED")
        }
        if ([int]$trigger.DaysInterval -ne 1) {
            [void]$errors.Add("KR_TRIGGER_DAYS_INTERVAL")
        }
        try {
            [void]$actualTimes.Add(([DateTime]$trigger.StartBoundary).ToString("HH:mm"))
        } catch {
            [void]$errors.Add("KR_TRIGGER_START_BOUNDARY")
        }
        $interval = if ($null -eq $trigger.Repetition) { "" } else { [string]$trigger.Repetition.Interval }
        $duration = if ($null -eq $trigger.Repetition) { "" } else { [string]$trigger.Repetition.Duration }
        $intervalIsEmpty = [string]::IsNullOrWhiteSpace($interval) -or $interval -eq "PT0S"
        $durationIsEmpty = [string]::IsNullOrWhiteSpace($duration) -or $duration -eq "PT0S"
        if (-not $intervalIsEmpty -or -not $durationIsEmpty) {
            [void]$errors.Add("KR_TRIGGER_REPETITION")
        }
    }
    $expectedSorted = @($ExpectedTimes | Sort-Object)
    $actualSorted = @($actualTimes | Sort-Object)
    if (@(Compare-Object $expectedSorted $actualSorted).Count -ne 0) {
        [void]$errors.Add("KR_TRIGGER_TIMES")
    }

    if ($null -eq $Registered.Settings) {
        [void]$errors.Add("KR_SETTINGS_MISSING")
    } else {
        $expectedStartWhenAvailable = @($ExpectedTimes) -contains "20:30"
        if ([bool]$Registered.Settings.StartWhenAvailable -ne $expectedStartWhenAvailable) {
            [void]$errors.Add("KR_SETTINGS_START_WHEN_AVAILABLE")
        }
        if ([string]$Registered.Settings.MultipleInstances -ne "IgnoreNew") {
            [void]$errors.Add("KR_SETTINGS_MULTIPLE_INSTANCES")
        }
        if ([string]$Registered.Settings.ExecutionTimeLimit -ne "PT30M") {
            [void]$errors.Add("KR_SETTINGS_EXECUTION_TIME_LIMIT")
        }
        if ([bool]$Registered.Settings.DisallowStartIfOnBatteries) {
            [void]$errors.Add("KR_SETTINGS_BATTERY_START")
        }
        if ([bool]$Registered.Settings.StopIfGoingOnBatteries) {
            [void]$errors.Add("KR_SETTINGS_BATTERY_STOP")
        }
        if (-not [bool]$Registered.Settings.WakeToRun) {
            [void]$errors.Add("KR_SETTINGS_WAKE_TO_RUN")
        }
    }
    return @($errors | Select-Object -Unique)
}

function Test-AccountTaskDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)] [object]$Registered,
        [Parameter(Mandatory = $true)] [string]$ExpectedExecute,
        [Parameter(Mandatory = $true)] [string]$ExpectedArguments,
        [Parameter(Mandatory = $true)] [string]$ExpectedWorkingDirectory,
        [Parameter(Mandatory = $true)] [string]$ExpectedTime
    )
    $errors = [System.Collections.Generic.List[string]]::new()
    $actions = @($Registered.Actions)
    $triggers = @($Registered.Triggers)
    if ($actions.Count -ne 1) { [void]$errors.Add("ACCOUNT_ACTION_COUNT") }
    else {
        if ([string]$actions[0].Execute -ne $ExpectedExecute) { [void]$errors.Add("ACCOUNT_ACTION_EXECUTE") }
        if ([string]$actions[0].Arguments -ne $ExpectedArguments) { [void]$errors.Add("ACCOUNT_ACTION_ARGUMENTS") }
        if ([string]$actions[0].WorkingDirectory -ne $ExpectedWorkingDirectory) { [void]$errors.Add("ACCOUNT_ACTION_WORKDIR") }
    }
    if ($triggers.Count -ne 1) { [void]$errors.Add("ACCOUNT_TRIGGER_COUNT") }
    else {
        $trigger = $triggers[0]
        if ([string]$trigger.CimClass.CimClassName -ne "MSFT_TaskDailyTrigger") { [void]$errors.Add("ACCOUNT_TRIGGER_TYPE") }
        if (-not [bool]$trigger.Enabled) { [void]$errors.Add("ACCOUNT_TRIGGER_DISABLED") }
        if ([int]$trigger.DaysInterval -ne 1) { [void]$errors.Add("ACCOUNT_TRIGGER_DAYS_INTERVAL") }
        if (([DateTime]$trigger.StartBoundary).ToString("HH:mm") -ne $ExpectedTime) { [void]$errors.Add("ACCOUNT_TRIGGER_TIME") }
        $interval = if ($null -eq $trigger.Repetition) { "" } else { [string]$trigger.Repetition.Interval }
        $duration = if ($null -eq $trigger.Repetition) { "" } else { [string]$trigger.Repetition.Duration }
        if ((-not [string]::IsNullOrWhiteSpace($interval) -and $interval -ne "PT0S") -or (-not [string]::IsNullOrWhiteSpace($duration) -and $duration -ne "PT0S")) { [void]$errors.Add("ACCOUNT_TRIGGER_REPETITION") }
    }
    if ($null -eq $Registered.Settings) { [void]$errors.Add("ACCOUNT_SETTINGS_MISSING") }
    else {
        if (-not [bool]$Registered.Settings.StartWhenAvailable) { [void]$errors.Add("ACCOUNT_SETTINGS_START_WHEN_AVAILABLE") }
        if ([string]$Registered.Settings.MultipleInstances -ne "IgnoreNew") { [void]$errors.Add("ACCOUNT_SETTINGS_MULTIPLE_INSTANCES") }
        if ([string]$Registered.Settings.ExecutionTimeLimit -ne "PT5M") { [void]$errors.Add("ACCOUNT_SETTINGS_EXECUTION_TIME_LIMIT") }
        if ([bool]$Registered.Settings.DisallowStartIfOnBatteries) { [void]$errors.Add("ACCOUNT_SETTINGS_BATTERY_START") }
        if ([bool]$Registered.Settings.StopIfGoingOnBatteries) { [void]$errors.Add("ACCOUNT_SETTINGS_BATTERY_STOP") }
        if (-not [bool]$Registered.Settings.WakeToRun) { [void]$errors.Add("ACCOUNT_SETTINGS_WAKE_TO_RUN") }
    }
    return @($errors | Select-Object -Unique)
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$runnerPath = Join-Path $PSScriptRoot "maintenance\reconcile_daily_health_artifact.py"
$corePath = Join-Path $projectRoot "artifacts\daily_health\core_data_20260818.json"
$universePath = Join-Path $projectRoot "artifacts\daily_health\universe_data_v2_20260819.json"
$logPath = Join-Path $projectRoot "artifacts\scheduler_logs\STOCK_DATA_DAILY_HEALTH_last.json"
$healthTaskName = "STOCK_DATA_DAILY_HEALTH"
$healthArguments = ('"{0}" --artifact "{1}" --universe-output "{2}" --execution-log "{3}" --universe-only' -f `
    $runnerPath, $corePath, $universePath, $logPath)
$issueStateTaskName = "STOCK_PROJECT_ISSUE_STATE_SYNC"
$issueStateRunnerPath = Join-Path $PSScriptRoot "maintenance\sync_issue_state.py"
$issueStateArguments = ('"{0}" --project-root "{1}" --enable-discovery' -f $issueStateRunnerPath, $projectRoot)
$fredTaskName = "STOCK_DATA_FRED_DAILY"
$fredRunnerPath = Join-Path $PSScriptRoot "maintenance\run_provider_scheduler.py"
$fredArguments = ('"{0}" --lane FRED_DAILY' -f $fredRunnerPath)
$lendingTaskName = "STOCK_DATA_LENDING_DAILY"
$lendingArguments = ('"{0}" --lane LENDING_DAILY' -f $fredRunnerPath)
$shortSellingTaskName = "STOCK_DATA_SHORT_SELLING_DAILY"
$shortSellingArguments = ('"{0}" --lane SHORT_SELLING_DAILY' -f $fredRunnerPath)
$vkospiTaskName = "STOCK_DATA_VKOSPI_DAILY"
$vkospiArguments = ('"{0}" --lane VKOSPI_DAILY' -f $fredRunnerPath)
$krIndexTaskName = "STOCK_DATA_KR_INDEX_DAILY"
$krIndexArguments = ('"{0}" --lane KR_INDEX_DAILY' -f $fredRunnerPath)
$investorFlowTaskName = "STOCK_DATA_MARKET_INVESTOR_DAILY"
$investorFlowArguments = ('"{0}" --lane MARKET_INVESTOR_DAILY' -f $fredRunnerPath)
$legacyKrMarketDailyTaskName = "STOCK_DATA_KR_MARKET_DAILY"
$krMarketDailyTaskSlots = [ordered]@{
    "STOCK_DATA_KR_MARKET_DAILY_0910" = $ShortSellingTime
    "STOCK_DATA_KR_MARKET_DAILY_1410" = $KrMiddayTime
    "STOCK_DATA_KR_MARKET_DAILY_2030" = $KrPostCloseTime
}
$krMarketDailyTaskNames = @($krMarketDailyTaskSlots.Keys)
$globalIndexTaskName = "STOCK_DATA_GLOBAL_INDEX_DAILY"
$globalIndexArguments = ('"{0}" --lane GLOBAL_INDEX_DAILY' -f $fredRunnerPath)
$soxxTaskName = "STOCK_DATA_GLOBAL_ETF_SOXX_DAILY"
$soxxArguments = ('"{0}" --lane GLOBAL_ETF_DAILY' -f $fredRunnerPath)
$futuresTaskName = "STOCK_DATA_GLOBAL_FUTURES_DAILY"
$futuresArguments = ('"{0}" --lane GLOBAL_COMMODITY_DAILY' -f $fredRunnerPath)
$yahooMarketTaskName = "STOCK_DATA_YAHOO_MARKET_30M"
$yahooMarketRunnerPath = Join-Path $PSScriptRoot "maintenance\run_yahoo_market_current.py"
$yahooMarketArguments = ('"{0}" --project-root "{1}"' -f $yahooMarketRunnerPath, $projectRoot)
$bokTreasuryTaskName = "STOCK_DATA_BOK_TREASURY_DAILY"
$bokTreasuryRunnerPath = Join-Path $PSScriptRoot "maintenance\run_bok_ecos_treasury_finality_observation.py"
$bokTreasuryArguments = ('"{0}" --project-root "{1}"' -f $bokTreasuryRunnerPath, $projectRoot)
$tossAccountTaskName = "STOCK_DATA_TOSS_ACCOUNT_DAILY"
$tossAccountRunnerPath = Join-Path $PSScriptRoot "maintenance\run_toss_account_snapshot.py"
$tossAccountArguments = ('"{0}" --project-root "{1}"' -f $tossAccountRunnerPath, $projectRoot)
$kbAccountTaskName = "STOCK_DATA_KBSEC_ACCOUNT_DAILY"
$kbAccountRunnerPath = Join-Path $PSScriptRoot "maintenance\run_kbsec_account_snapshot.py"
$kbAccountArguments = ('"{0}" --project-root "{1}"' -f $kbAccountRunnerPath, $projectRoot)
$legacyYahooTaskNames = @(
    "STOCK_DATA_GLOBAL_MARKET_60M",
    "STOCK_DATA_GLOBAL_MARKET_15M_CBOE_VIX",
    "STOCK_DATA_GLOBAL_MARKET_15M_TREASURY_QUOTE"
)
$legacyKrDailyTaskNames = @(
    $legacyKrMarketDailyTaskName,
    $shortSellingTaskName,
    $lendingTaskName,
    $vkospiTaskName,
    $krIndexTaskName,
    $investorFlowTaskName
)
$taskNames = if ($Target -eq "All") {
    @($healthTaskName, $issueStateTaskName, $fredTaskName) + $krMarketDailyTaskNames + @(
        $globalIndexTaskName, $soxxTaskName, $futuresTaskName, $yahooMarketTaskName,
        $bokTreasuryTaskName,
        $tossAccountTaskName,
        $kbAccountTaskName
    )
} elseif ($Target -eq "IssueState") { @($issueStateTaskName) } elseif ($Target -eq "Fred") { @($fredTaskName) } elseif ($Target -eq "Lending") { @($lendingTaskName) } elseif ($Target -eq "ShortSelling") { @($shortSellingTaskName) } elseif ($Target -eq "Vkospi") { @($vkospiTaskName) } elseif ($Target -eq "KrIndex") { @($krIndexTaskName) } elseif ($Target -eq "InvestorFlow") { @($investorFlowTaskName) } elseif ($Target -eq "KrMarketDaily") { $krMarketDailyTaskNames } elseif ($Target -eq "GlobalIndex") { @($globalIndexTaskName) } elseif ($Target -eq "Soxx") { @($soxxTaskName) } elseif ($Target -eq "Futures") { @($futuresTaskName) } elseif ($Target -eq "YahooMarket") { @($yahooMarketTaskName) } elseif ($Target -eq "BokTreasury") { @($bokTreasuryTaskName) } elseif ($Target -eq "TossAccount") { @($tossAccountTaskName) } elseif ($Target -eq "KbAccount") { @($kbAccountTaskName) } else { @($healthTaskName) }

if ($Action -ne "Remove" -and $Target -in @("KrMarketDaily", "All") -and (
    $ShortSellingTime -ne "09:10" -or
    $KrMiddayTime -ne "14:10" -or
    $KrPostCloseTime -ne "20:30"
)) {
    throw "KR market daily slots are fixed at 09:10, 14:10, and 20:30"
}
if ($Action -ne "Remove" -and $Target -in @("TossAccount", "All") -and $TossAccountTime -ne "07:00") {
    throw "Toss account daily slot is fixed at 07:00"
}
if ($Action -ne "Remove" -and $Target -in @("KbAccount", "All") -and $KbAccountTime -ne "07:10") {
    throw "KB account daily slot is fixed at 07:10"
}
if ($Action -ne "Remove" -and $Target -in @("BokTreasury", "All") -and $BokTreasuryTime -ne "17:10") {
    throw "BOK Treasury observation slot is fixed at 17:10"
}

if ($Action -eq "Remove") {
    foreach ($name in $taskNames) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Output "removed=$name"
        } else {
            Write-Output "not_found=$name"
        }
    }
    exit 0
}

$requiredPaths = if ($Target -in @("Fred", "Lending", "ShortSelling", "Vkospi", "KrIndex", "InvestorFlow", "KrMarketDaily", "GlobalIndex", "Soxx", "Futures")) { @($pythonPath, $fredRunnerPath) } elseif ($Target -eq "YahooMarket") { @($pythonPath, $yahooMarketRunnerPath) } elseif ($Target -eq "BokTreasury") { @($pythonPath, $bokTreasuryRunnerPath) } elseif ($Target -eq "TossAccount") { @($pythonPath, $tossAccountRunnerPath) } elseif ($Target -eq "KbAccount") { @($pythonPath, $kbAccountRunnerPath) } elseif ($Target -eq "IssueState") { @($pythonPath, $issueStateRunnerPath) } elseif ($Target -eq "All") { @($pythonPath, $runnerPath, $corePath, $issueStateRunnerPath, $fredRunnerPath, $yahooMarketRunnerPath, $bokTreasuryRunnerPath, $tossAccountRunnerPath, $kbAccountRunnerPath) } else { @($pythonPath, $runnerPath, $corePath) }
foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "required scheduler input not found: $path"
    }
}

foreach ($name in $taskNames) {
    $isIssueState = $name -eq $issueStateTaskName
    $isFred = $name -eq $fredTaskName
    $isLending = $name -eq $lendingTaskName
    $isShortSelling = $name -eq $shortSellingTaskName
    $isVkospi = $name -eq $vkospiTaskName
    $isKrIndex = $name -eq $krIndexTaskName
    $isInvestorFlow = $name -eq $investorFlowTaskName
    $isKrMarketDaily = $krMarketDailyTaskSlots.Contains($name)
    $krScheduledSlot = if ($isKrMarketDaily) { [string]$krMarketDailyTaskSlots[$name] } else { $null }
    $krAllowLatestOccurrence = if ($krScheduledSlot -eq "20:30") { " --allow-latest-occurrence" } else { "" }
    $krMarketDailyArguments = if ($isKrMarketDaily) {
        ('"{0}" --bundle KR_MARKET_DAILY --scheduled-slot {1}{2}' -f $fredRunnerPath, $krScheduledSlot, $krAllowLatestOccurrence)
    } else { $null }
    $isGlobalIndex = $name -eq $globalIndexTaskName
    $isSoxx = $name -eq $soxxTaskName
    $isFutures = $name -eq $futuresTaskName
    $isYahooMarket = $name -eq $yahooMarketTaskName
    $isBokTreasury = $name -eq $bokTreasuryTaskName
    $isTossAccount = $name -eq $tossAccountTaskName
    $isKbAccount = $name -eq $kbAccountTaskName
    $isProvider = $isFred -or $isLending -or $isShortSelling -or $isVkospi -or $isKrIndex -or $isInvestorFlow -or $isKrMarketDaily -or $isGlobalIndex -or $isSoxx -or $isFutures -or $isYahooMarket -or $isBokTreasury -or $isTossAccount -or $isKbAccount
    Write-Output "task=$name"
    Write-Output "execute=$pythonPath"
    Write-Output "arguments=$(if ($isIssueState) { $issueStateArguments } elseif ($isFred) { $fredArguments } elseif ($isLending) { $lendingArguments } elseif ($isShortSelling) { $shortSellingArguments } elseif ($isVkospi) { $vkospiArguments } elseif ($isKrIndex) { $krIndexArguments } elseif ($isInvestorFlow) { $investorFlowArguments } elseif ($isKrMarketDaily) { $krMarketDailyArguments } elseif ($isGlobalIndex) { $globalIndexArguments } elseif ($isSoxx) { $soxxArguments } elseif ($isFutures) { $futuresArguments } elseif ($isYahooMarket) { $yahooMarketArguments } elseif ($isBokTreasury) { $bokTreasuryArguments } elseif ($isTossAccount) { $tossAccountArguments } elseif ($isKbAccount) { $kbAccountArguments } else { $healthArguments })"
    Write-Output "working_directory=$projectRoot"
    Write-Output "schedule=$(if ($isYahooMarket) { 'every30m@minute={0:D2}' -f $YahooMarketMinute } elseif ($isKrMarketDaily) { 'daily@' + $krScheduledSlot } else { 'daily@' + $(if ($isIssueState) { $IssueStateTime } elseif ($isFred) { $FredTime } elseif ($isLending) { $LendingTime } elseif ($isShortSelling) { $ShortSellingTime } elseif ($isVkospi) { $VkospiTime } elseif ($isKrIndex) { $KrIndexTime } elseif ($isInvestorFlow) { $InvestorFlowTime } elseif ($isGlobalIndex) { $GlobalIndexTime } elseif ($isSoxx) { $SoxxTime } elseif ($isFutures) { $FuturesTime } elseif ($isBokTreasury) { $BokTreasuryTime } elseif ($isTossAccount) { $TossAccountTime } elseif ($isKbAccount) { $KbAccountTime } else { $HealthTime }) })"
    Write-Output "network_calls=$(if ($isProvider) { 'bounded_by_lane' } else { '0' })"
    Write-Output "enabled_by_default=$(-not $isIssueState)"
    Write-Output "requested_enabled=$(-not $isIssueState -or $EnableIssueState)"
    Write-Output "power_policy=allow_battery_start,dont_stop_on_battery,wake_to_run"
}

if ($Action -eq "DryRun") {
    exit 0
}

foreach ($name in $taskNames) {
    $isIssueState = $name -eq $issueStateTaskName
    $isFred = $name -eq $fredTaskName
    $isLending = $name -eq $lendingTaskName
    $isShortSelling = $name -eq $shortSellingTaskName
    $isVkospi = $name -eq $vkospiTaskName
    $isKrIndex = $name -eq $krIndexTaskName
    $isInvestorFlow = $name -eq $investorFlowTaskName
    $isKrMarketDaily = $krMarketDailyTaskSlots.Contains($name)
    $krScheduledSlot = if ($isKrMarketDaily) { [string]$krMarketDailyTaskSlots[$name] } else { $null }
    $krAllowLatestOccurrence = if ($krScheduledSlot -eq "20:30") { " --allow-latest-occurrence" } else { "" }
    $krMarketDailyArguments = if ($isKrMarketDaily) {
        ('"{0}" --bundle KR_MARKET_DAILY --scheduled-slot {1}{2}' -f $fredRunnerPath, $krScheduledSlot, $krAllowLatestOccurrence)
    } else { $null }
    $isGlobalIndex = $name -eq $globalIndexTaskName
    $isSoxx = $name -eq $soxxTaskName
    $isFutures = $name -eq $futuresTaskName
    $isYahooMarket = $name -eq $yahooMarketTaskName
    $isBokTreasury = $name -eq $bokTreasuryTaskName
    $isTossAccount = $name -eq $tossAccountTaskName
    $isKbAccount = $name -eq $kbAccountTaskName
    $isProvider = $isFred -or $isLending -or $isShortSelling -or $isVkospi -or $isKrIndex -or $isInvestorFlow -or $isKrMarketDaily -or $isGlobalIndex -or $isSoxx -or $isFutures -or $isYahooMarket -or $isBokTreasury -or $isTossAccount -or $isKbAccount
    $scheduledAction = New-ScheduledTaskAction `
        -Execute $pythonPath `
        -Argument $(if ($isIssueState) { $issueStateArguments } elseif ($isFred) { $fredArguments } elseif ($isLending) { $lendingArguments } elseif ($isShortSelling) { $shortSellingArguments } elseif ($isVkospi) { $vkospiArguments } elseif ($isKrIndex) { $krIndexArguments } elseif ($isInvestorFlow) { $investorFlowArguments } elseif ($isKrMarketDaily) { $krMarketDailyArguments } elseif ($isGlobalIndex) { $globalIndexArguments } elseif ($isSoxx) { $soxxArguments } elseif ($isFutures) { $futuresArguments } elseif ($isYahooMarket) { $yahooMarketArguments } elseif ($isBokTreasury) { $bokTreasuryArguments } elseif ($isTossAccount) { $tossAccountArguments } elseif ($isKbAccount) { $kbAccountArguments } else { $healthArguments }) `
        -WorkingDirectory $projectRoot
    if ($isYahooMarket) {
        $firstRun = (Get-Date).Date.AddHours((Get-Date).Hour).AddMinutes($YahooMarketMinute)
        if ($firstRun -le (Get-Date)) { $firstRun = $firstRun.AddMinutes(30) }
        $scheduledTrigger = New-ScheduledTaskTrigger -Once -At $firstRun -RepetitionInterval (New-TimeSpan -Minutes 30)
    } elseif ($isKrMarketDaily) {
        $scheduledTrigger = New-ScheduledTaskTrigger `
            -Daily `
            -At ([DateTime]::ParseExact($krScheduledSlot, "HH:mm", $null))
    } else {
        $scheduledTrigger = New-ScheduledTaskTrigger `
            -Daily `
            -At ([DateTime]::ParseExact($(if ($isIssueState) { $IssueStateTime } elseif ($isFred) { $FredTime } elseif ($isLending) { $LendingTime } elseif ($isShortSelling) { $ShortSellingTime } elseif ($isVkospi) { $VkospiTime } elseif ($isKrIndex) { $KrIndexTime } elseif ($isInvestorFlow) { $InvestorFlowTime } elseif ($isGlobalIndex) { $GlobalIndexTime } elseif ($isSoxx) { $SoxxTime } elseif ($isFutures) { $FuturesTime } elseif ($isBokTreasury) { $BokTreasuryTime } elseif ($isTossAccount) { $TossAccountTime } elseif ($isKbAccount) { $KbAccountTime } else { $HealthTime }), "HH:mm", $null))
    }
    $startWhenAvailable = if ($isIssueState -or $isBokTreasury) { $false } else { (-not $isKrMarketDaily) -or ($krScheduledSlot -eq "20:30") }
    $scheduledSettings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable:$startWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -WakeToRun `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $(if ($isKrMarketDaily) { 30 } elseif ($isTossAccount -or $isKbAccount) { 5 } elseif ($isProvider) { 15 } else { 5 }))
    $description = if ($isIssueState) { "Local sanitized issue aggregation and explicit thresholded Inbox discovery; zero provider calls." } elseif ($isFred) { "Source-availability-aware FRED daily refresh; Landing-first and fail-closed." } elseif ($isLending) { "Official D+1 13:00 KST stock-lending refresh; Landing-first and fail-closed." } elseif ($isShortSelling) { "Next-XKRX-session official short-selling trading refresh; two-market atomic and fail-closed." } elseif ($isVkospi) { "Bounded-empirical KRX post-close VKOSPI refresh; Landing-first and fail-closed." } elseif ($isKrIndex) { "Atomic KOSPI/KOSDAQ/KOSPI200 post-close refresh; Landing-first and fail-closed." } elseif ($isInvestorFlow) { "Read-only KOSPI/KOSDAQ investor-flow refresh with joint bridge promotion." } elseif ($isKrMarketDaily) { "Slot-specific Korean daily refresh for $krScheduledSlot KST; gated datasets remain disabled." } elseif ($isGlobalIndex) { "Registered SP500/NASDAQ/NASDAQ100 completed-session refresh; Landing-first and fail-closed." } elseif ($isSoxx) { "Registered SOXX-only completed-XNYS-session refresh; Landing-first and fail-closed." } elseif ($isFutures) { "Completed-session NQ, Gold, and WTI daily refresh; Landing-first and fail-closed." } elseif ($isYahooMarket) { "Unified every-30-minute Yahoo current refresh: completed 30m global bars plus native 15m VIX/Treasury quotes; no history writes." } elseif ($isBokTreasury) { "BOK ECOS Korean Treasury six-tenor finality observation at the exact 17:10 KST window; Landing-only and auto-stops at the three-batch review gate." } elseif ($isTossAccount) { "Daily identifier-free Toss read-only account snapshot; exact occurrence claim and fail-closed prior preservation." } elseif ($isKbAccount) { "Daily identifier-free KB Securities read-only account snapshot; exact occurrence claim and fail-closed prior preservation." } else { "Offline projection of the typed Dataset Universe health registry; zero provider calls." }
    Register-ScheduledTask `
        -TaskName $name `
        -Action $scheduledAction `
        -Trigger $scheduledTrigger `
        -Settings $scheduledSettings `
        -Description $description `
        -Force | Out-Null
    Write-Output "installed=$name"

    $registeredPower = Get-ScheduledTask -TaskName $name -ErrorAction Stop
    if (
        [bool]$registeredPower.Settings.DisallowStartIfOnBatteries -or
        [bool]$registeredPower.Settings.StopIfGoingOnBatteries -or
        -not [bool]$registeredPower.Settings.WakeToRun
    ) {
        throw "registered task failed power-policy readback: $name"
    }
    Write-Output "validated_power=$name"

    if ($isIssueState) {
        if ($EnableIssueState) {
            Enable-ScheduledTask -TaskName $name | Out-Null
        } else {
            Disable-ScheduledTask -TaskName $name | Out-Null
        }
        $registered = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $registeredAction = @($registered.Actions)
        $registeredTrigger = @($registered.Triggers)
        if (
            [string]$registered.State -ne $(if ($EnableIssueState) { "Ready" } else { "Disabled" }) -or
            $registeredAction.Count -ne 1 -or
            [string]$registeredAction[0].Execute -ne $pythonPath -or
            [string]$registeredAction[0].Arguments -ne $issueStateArguments -or
            [string]$registeredAction[0].WorkingDirectory -ne $projectRoot -or
            $registeredTrigger.Count -ne 1 -or
            ([DateTime]$registeredTrigger[0].StartBoundary).ToString("HH:mm") -ne $IssueStateTime -or
            [bool]$registered.Settings.StartWhenAvailable -ne $false -or
            [bool]$registered.Settings.DisallowStartIfOnBatteries -or
            [bool]$registered.Settings.StopIfGoingOnBatteries -or
            -not [bool]$registered.Settings.WakeToRun -or
            [string]$registered.Settings.MultipleInstances -ne "IgnoreNew" -or
            [string]$registered.Settings.ExecutionTimeLimit -ne "PT5M"
        ) {
            throw "registered issue-state task failed semantic readback"
        }
        Write-Output "$(if ($EnableIssueState) { 'validated_enabled' } else { 'validated_disabled' })=$name"
    }

    if ($isKrMarketDaily) {
        $registered = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $definitionErrors = @(Test-KrMarketDailyTaskDefinition `
            -Registered $registered `
            -ExpectedExecute $pythonPath `
            -ExpectedArguments $krMarketDailyArguments `
            -ExpectedWorkingDirectory $projectRoot `
            -ExpectedTimes @($krScheduledSlot))
        if ($definitionErrors.Count -gt 0) {
            throw "registered Korean daily task failed semantic readback: $($definitionErrors -join ',')"
        }
        Write-Output "validated=$name"
    }

    if ($isTossAccount -or $isKbAccount) {
        $registered = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $definitionErrors = @(Test-AccountTaskDefinition `
            -Registered $registered `
            -ExpectedExecute $pythonPath `
            -ExpectedArguments $(if ($isTossAccount) { $tossAccountArguments } else { $kbAccountArguments }) `
            -ExpectedWorkingDirectory $projectRoot `
            -ExpectedTime $(if ($isTossAccount) { $TossAccountTime } else { $KbAccountTime }))
        if ($definitionErrors.Count -gt 0) {
            throw "registered account task failed semantic readback: $name $($definitionErrors -join ',')"
        }
        Write-Output "validated=$name"
    }

    if ($isBokTreasury) {
        $registered = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $actions = @($registered.Actions)
        $triggers = @($registered.Triggers)
        if (
            [string]$registered.State -ne "Ready" -or
            $actions.Count -ne 1 -or
            [string]$actions[0].Execute -ne $pythonPath -or
            [string]$actions[0].Arguments -ne $bokTreasuryArguments -or
            [string]$actions[0].WorkingDirectory -ne $projectRoot -or
            $triggers.Count -ne 1 -or
            ([DateTime]$triggers[0].StartBoundary).ToString("HH:mm") -ne $BokTreasuryTime -or
            [bool]$registered.Settings.StartWhenAvailable -ne $false -or
            [string]$registered.Settings.MultipleInstances -ne "IgnoreNew" -or
            [string]$registered.Settings.ExecutionTimeLimit -ne "PT15M"
        ) {
            throw "registered BOK Treasury observation task failed semantic readback"
        }
        Write-Output "validated=$name"
    }

    if ($isYahooMarket) {
        foreach ($legacyName in $legacyYahooTaskNames) {
            $legacy = Get-ScheduledTask -TaskName $legacyName -ErrorAction SilentlyContinue
            if ($null -ne $legacy) {
                Unregister-ScheduledTask -TaskName $legacyName -Confirm:$false
                Write-Output "removed_legacy=$legacyName"
            }
        }
    }
}

if ($Target -in @("KrMarketDaily", "All")) {
    foreach ($legacyName in $legacyKrDailyTaskNames) {
        $legacy = Get-ScheduledTask -TaskName $legacyName -ErrorAction SilentlyContinue
        if ($null -ne $legacy) {
            Unregister-ScheduledTask -TaskName $legacyName -Confirm:$false
            Write-Output "removed_legacy=$legacyName"
        }
    }
}
