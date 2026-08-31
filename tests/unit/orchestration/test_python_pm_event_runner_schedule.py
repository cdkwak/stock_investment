from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

import pytest

from stock_data.orchestration.workflow_control.event_runner import (
    EVENT_RUNNER_EXECUTION_LIMIT_SECONDS,
    EVENT_RUNNER_MAX_WAKES_PER_INVOCATION,
    EVENT_WAKE_TIMEOUT_SECONDS,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts" / "register_python_pm_event_runner_task.ps1"
TASK_NAME = "STOCK_PROJECT_PYTHON_PM_EVENT_RUNNER"
OWNER_ID = "windows-task-scheduler"
_UTF8_PREAMBLE = """
$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
"""


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _decode_powershell_stream(payload: bytes) -> str:
    """Decode the UTF-8 transport established by ``_UTF8_PREAMBLE``.

    The scheduler module emits localized host errors.  Replacement is
    intentional for a malformed host stream: the structured probe below never
    decides success from human-readable error text.
    """

    return payload.decode("utf-8", errors="replace")


def _run_powershell(*arguments: str, command: str | None = None) -> subprocess.CompletedProcess[str]:
    invocation = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
    if command is None:
        assert len(arguments) % 2 == 0
        parameters: list[str] = []
        for name, value in zip(arguments[::2], arguments[1::2], strict=True):
            assert name.startswith("-")
            parameters.append(f"{name[1:]} = {_powershell_literal(value)}")
        parameter_values = "; ".join(parameters)
        script = _powershell_literal(str(SCRIPT))
        command = f"""
& {{
{_UTF8_PREAMBLE}
  $scriptParameters = @{{ {parameter_values} }}
  & {script} @scriptParameters
}}
"""
    else:
        command = f"""
& {{
{_UTF8_PREAMBLE}
{command}
}}
"""

    completed = subprocess.run(invocation + [command], capture_output=True, check=False)
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        _decode_powershell_stream(completed.stdout),
        _decode_powershell_stream(completed.stderr),
    )


def _native_trigger_probe_command() -> str:
    """Return a structured native constructor probe with no text-error parsing.

    Scheduler access can be unavailable in a sandbox.  Such a result is not a
    trigger observation and must never become a default-filled ``observed``
    record.  HRESULTs are stable machine-readable evidence; localized exception
    messages are deliberately excluded from the receipt.
    """

    return """
$probe = $null
try {
  $trigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
  if ($null -eq $trigger) {
    throw [System.InvalidOperationException]::new('native trigger probe returned no object')
  }
  if ($null -eq $trigger.CimClass -or [string]::IsNullOrWhiteSpace([string]$trigger.CimClass.CimClassName)) {
    throw [System.InvalidOperationException]::new('native trigger probe returned an incomplete object')
  }
  $probe = [ordered]@{
    status = 'observed'
    trigger = [ordered]@{
      class = [string]$trigger.CimClass.CimClassName
      interval = [string]$trigger.Repetition.Interval
      duration = [string]$trigger.Repetition.Duration
      stop_at_duration_end = [bool]$trigger.Repetition.StopAtDurationEnd
      enabled = [bool]$trigger.Enabled
    }
  }
} catch {
  $hresults = @()
  $exception = $_.Exception
  while ($null -ne $exception) {
    $hresult = [int64]$exception.HResult
    if ($hresult -lt 0) {
      $hresult += 4294967296
    }
    $hresults += ('0x{0:X8}' -f $hresult)
    $exception = $exception.InnerException
  }
  $reason = if (
    $hresults -contains '0x80070005' -or
    $hresults -contains '0x80041003'
  ) {
    'task_scheduler_access_denied'
  } elseif ($_.Exception -is [System.InvalidOperationException]) {
    'trigger_object_missing_or_incomplete'
  } else {
    'task_scheduler_unavailable'
  }
  $probe = [ordered]@{
    status = 'unavailable'
    reason = $reason
    hresults = @($hresults)
  }
}
$probe | ConvertTo-Json -Compress
"""


def _parse_native_trigger_probe(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip(), f"missing structured native probe receipt; stderr={completed.stderr!r}"
    payload = json.loads(completed.stdout)
    assert payload["status"] in {"observed", "unavailable"}
    if payload["status"] == "unavailable":
        assert set(payload) == {"status", "reason", "hresults"}
        assert payload["reason"] in {
            "task_scheduler_access_denied",
            "task_scheduler_unavailable",
            "trigger_object_missing_or_incomplete",
        }
        assert payload["hresults"]
        assert all(re.fullmatch(r"0x[0-9A-F]{8}", value) for value in payload["hresults"])
    else:
        assert set(payload) == {"status", "trigger"}
        assert isinstance(payload["trigger"], dict)
    return payload


def _check_command(
    *, override: str = "", mode: str = "Check", extra_functions: str = ""
) -> str:
    project_root = str(PROJECT_ROOT).replace("'", "''")
    script = str(SCRIPT).replace("'", "''")
    pythonw = str(PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe").replace("'", "''")
    controller = str(
        PROJECT_ROOT / "scripts" / "maintenance" / "workflow_controller.py"
    ).replace("'", "''")
    expected_arguments = (
        f'"{controller}" --repository-root "{project_root}" '
        f'event-run-once --owner-id "{OWNER_ID}"'
    ).replace("'", "''")
    return f"""
& {{
  $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $registered = [pscustomobject]@{{
    TaskName = '{TASK_NAME}'
    TaskPath = '\\'
    Description = 'StockInvestmentRev1 owner=python-pm-event-runner-v1; local Python PM material-event runner.'
    State = 'Ready'
    Principal = [pscustomobject]@{{ UserId = $currentUser; LogonType = 'Interactive'; RunLevel = 'Limited' }}
    Actions = @([pscustomobject]@{{
      Execute = '{pythonw}'
      Arguments = '{expected_arguments}'
      WorkingDirectory = '{project_root}'
    }})
    Triggers = @([pscustomobject]@{{
      Enabled = $true
      StartBoundary = (Get-Date).AddMinutes(-1).ToString('o')
      CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskTimeTrigger' }}
      Repetition = [pscustomobject]@{{
        Interval = 'PT1M'
        Duration = ''
        StopAtDurationEnd = $true
      }}
    }})
    Settings = [pscustomobject]@{{
      MultipleInstances = 'IgnoreNew'
      ExecutionTimeLimit = 'PT15M'
      StartWhenAvailable = $true
    }}
  }}
  function Get-ScheduledTask {{
    param($TaskName, $TaskPath, $ErrorAction)
    if ($TaskName -cne '{TASK_NAME}' -or $TaskPath -cne '\\') {{
      throw 'readback target mismatch'
    }}
    $registered
  }}
  {override}
  {extra_functions}
  & '{script}' -Mode {mode} -OwnerId '{OWNER_ID}'
}}
"""


def test_schedule_source_contract_reconcile_pins_exact_hidden_runner() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'ValidateSet("Install", "Check", "Remove", "DryRun")' in source
    assert f'$taskName = "{TASK_NAME}"' in source
    assert '$taskPath = "\\"' in source
    assert "owner=python-pm-event-runner-v1" in source
    assert '.venv\\Scripts\\pythonw.exe' in source
    assert 'maintenance\\workflow_controller.py' in source
    assert 'event-run-once --owner-id' in source
    assert '--owner-id' in source
    assert 'MultipleInstances IgnoreNew' in source
    assert 'ExecutionTimeLimit (New-TimeSpan -Minutes 15)' in source
    assert '$executionTimeLimit = "PT15M"' in source
    assert 'RepetitionInterval (New-TimeSpan -Minutes 1)' in source
    assert "-not [bool]$triggers[0].Repetition.StopAtDurationEnd" in source
    assert "New-ScheduledTaskPrincipal" in source
    assert "-LogonType Interactive" in source
    assert "-RunLevel Limited" in source
    assert "PYTHON_PM_TASK_OK" in source
    assert "PYTHON_PM_TASK_DRY_RUN_OK" in source
    assert "[Console]::OutputEncoding = $utf8NoBom" in source
    assert "$OutputEncoding = $utf8NoBom" in source
    assert EVENT_RUNNER_MAX_WAKES_PER_INVOCATION == 1
    assert EVENT_WAKE_TIMEOUT_SECONDS == 600
    assert EVENT_RUNNER_EXECUTION_LIMIT_SECONDS == 15 * 60
    assert EVENT_RUNNER_EXECUTION_LIMIT_SECONDS >= EVENT_WAKE_TIMEOUT_SECONDS + 120

    lowered = source.lower()
    assert "get-scheduledtask |" not in lowered
    assert not any(term in lowered for term in ("orca", "provider", "broker"))
    # ``NTAccount`` is used only to compare the scheduled principal SID; the
    # runner has no investment-account integration or mutation route.
    assert "investment_account" not in lowered


def test_schedule_dry_run_restart_is_non_mutating() -> None:
    completed = _run_powershell("-Mode", "DryRun", "-OwnerId", OWNER_ID)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_DRY_RUN_OK"


def test_schedule_native_constructor_reconcile_matches_exact_trigger_contract() -> None:
    payload = _parse_native_trigger_probe(
        _run_powershell(command=_native_trigger_probe_command())
    )
    if payload["status"] == "unavailable":
        pytest.skip(
            "native Task Scheduler constructor unavailable: "
            f"{payload['reason']} {payload['hresults']}"
        )

    assert payload == {
        "status": "observed",
        "trigger": {
        "class": "MSFT_TaskTimeTrigger",
        "interval": "PT1M",
        "duration": "",
        "stop_at_duration_end": True,
        "enabled": True,
        },
    }


def test_schedule_native_probe_access_denied_is_unavailable_not_empty_values() -> None:
    completed = _run_powershell(
        command="""
function New-ScheduledTaskTrigger {
  throw [System.UnauthorizedAccessException]::new('localized error is intentionally ignored')
}
"""
        + _native_trigger_probe_command()
    )

    assert _parse_native_trigger_probe(completed) == {
        "status": "unavailable",
        "reason": "task_scheduler_access_denied",
        "hresults": ["0x80070005"],
    }


def test_schedule_native_probe_null_object_is_never_observed() -> None:
    completed = _run_powershell(
        command="""
function New-ScheduledTaskTrigger { return $null }
"""
        + _native_trigger_probe_command()
    )

    payload = _parse_native_trigger_probe(completed)
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "trigger_object_missing_or_incomplete"
    assert "trigger" not in payload


def test_schedule_dry_run_safety_never_calls_task_scheduler() -> None:
    script = str(SCRIPT).replace("'", "''")
    command = f"""
& {{
  function Get-ScheduledTask {{ throw 'unexpected scheduler read' }}
  function Register-ScheduledTask {{ throw 'unexpected scheduler mutation' }}
  function Unregister-ScheduledTask {{ throw 'unexpected scheduler mutation' }}
  & '{script}' -Mode DryRun -OwnerId '{OWNER_ID}'
}}
"""
    completed = _run_powershell(command=command)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_DRY_RUN_OK"


@pytest.mark.parametrize(
    "owner_id",
    ["contains space", "../escape", "", "x" * 65],
)
def test_schedule_safety_rejects_unsafe_owner_before_scheduler_access(owner_id: str) -> None:
    completed = _run_powershell("-Mode", "DryRun", "-OwnerId", owner_id)

    assert completed.returncode != 0
    assert "PYTHON_PM_TASK_DRY_RUN_OK" not in completed.stdout


def test_schedule_check_reconcile_accepts_only_exact_definition() -> None:
    completed = _run_powershell(command=_check_command())

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_OK"


def test_schedule_check_accepts_windows_normalized_current_user_name() -> None:
    completed = _run_powershell(command=_check_command(
        override="$registered.Principal.UserId = $currentUser.Split('\\')[-1]",
    ))

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_OK"


def test_schedule_install_restart_is_idempotent_for_exact_owned_task() -> None:
    completed = _run_powershell(
        command=_check_command(
            mode="Install",
            extra_functions="""
function Test-Path { $true }
function Register-ScheduledTask { throw 'idempotent install attempted mutation' }
""",
        )
    )

    assert completed.returncode == 0, completed.stderr
    assert "idempotent install attempted mutation" not in completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_INSTALLED"


def test_schedule_install_reconciles_owned_old_action_and_pt2m_definition() -> None:
    completed = _run_powershell(
        command=_check_command(
            mode="Install",
            override=(
                "$registered.Actions[0].Arguments = '\"old-runner.py\"'; "
                "$registered.Settings.ExecutionTimeLimit = 'PT2M'"
            ),
            extra_functions="""
function Test-Path { $true }
function New-ScheduledTaskAction {
  param($Execute, $Argument, $WorkingDirectory)
  [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
}
function New-ScheduledTaskTrigger {
  param([switch]$Once, $At, $RepetitionInterval)
  if (-not $Once -or $RepetitionInterval.TotalMinutes -ne 1) { throw 'trigger reconstruction mismatch' }
  [pscustomobject]@{
    Enabled = $true
    StartBoundary = $At.ToString('o')
    CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskTimeTrigger' }
    Repetition = [pscustomobject]@{ Interval = 'PT1M'; Duration = ''; StopAtDurationEnd = $true }
  }
}
function New-ScheduledTaskSettingsSet {
  param([switch]$StartWhenAvailable, $MultipleInstances, $ExecutionTimeLimit)
  if (-not $StartWhenAvailable -or $MultipleInstances -ne 'IgnoreNew' -or $ExecutionTimeLimit.TotalMinutes -ne 15) {
    throw 'settings reconstruction mismatch'
  }
  [pscustomobject]@{ MultipleInstances = 'IgnoreNew'; ExecutionTimeLimit = 'PT15M'; StartWhenAvailable = $true }
}
function New-ScheduledTaskPrincipal {
  param($UserId, $LogonType, $RunLevel)
  [pscustomobject]@{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel }
}
function Register-ScheduledTask {
  param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
  if (-not $Force) { throw 'owned revision was not force-reconciled' }
  $registered.Actions = @($Action)
  $registered.Triggers = @($Trigger)
  $registered.Settings = $Settings
  $registered.Principal = $Principal
  $registered.Description = $Description
}
""",
        )
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_INSTALLED"


def test_schedule_install_restart_registers_and_reads_back_exact_task() -> None:
    project_root = str(PROJECT_ROOT).replace("'", "''")
    script = str(SCRIPT).replace("'", "''")
    pythonw = str(PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe").replace("'", "''")
    controller = str(
        PROJECT_ROOT / "scripts" / "maintenance" / "workflow_controller.py"
    ).replace("'", "''")
    expected_arguments = (
        f'"{controller}" --repository-root "{project_root}" '
        f'event-run-once --owner-id "{OWNER_ID}"'
    ).replace("'", "''")
    command = f"""
& {{
  $global:schedulerFixtureRegistered = $null
  function Test-Path {{ $true }}
  function New-ScheduledTaskAction {{
    param($Execute, $Argument, $WorkingDirectory)
    if ($Execute -cne '{pythonw}' -or $Argument -cne '{expected_arguments}' -or $WorkingDirectory -cne '{project_root}') {{
      throw 'action construction mismatch'
    }}
    [pscustomobject]@{{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }}
  }}
  function New-ScheduledTaskTrigger {{
    param([switch]$Once, $At, $RepetitionInterval)
    if (-not $Once -or $RepetitionInterval.TotalMinutes -ne 1) {{ throw 'trigger construction mismatch' }}
    [pscustomobject]@{{
      Enabled = $true
      StartBoundary = $At.ToString('o')
      CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskTimeTrigger' }}
      Repetition = [pscustomobject]@{{ Interval = 'PT1M'; Duration = ''; StopAtDurationEnd = $true }}
    }}
  }}
  function New-ScheduledTaskSettingsSet {{
    param([switch]$StartWhenAvailable, $MultipleInstances, $ExecutionTimeLimit)
    if (-not $StartWhenAvailable -or $MultipleInstances -ne 'IgnoreNew' -or $ExecutionTimeLimit.TotalMinutes -ne 15) {{
      throw 'settings construction mismatch'
    }}
    [pscustomobject]@{{ MultipleInstances = 'IgnoreNew'; ExecutionTimeLimit = 'PT15M'; StartWhenAvailable = $true }}
  }}
  function Register-ScheduledTask {{
    param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
    if ($TaskName -cne '{TASK_NAME}' -or $TaskPath -cne '\\' -or $Force) {{
      throw 'registration target mismatch'
    }}
    $global:schedulerFixtureRegistered = [pscustomobject]@{{
      TaskName = $TaskName
      TaskPath = $TaskPath
      Description = $Description
      State = 'Ready'
      Principal = $Principal
      Actions = @($Action)
      Triggers = @($Trigger)
      Settings = $Settings
    }}
  }}
  function Get-ScheduledTask {{
    param($TaskName, $TaskPath, $ErrorAction)
    if ($TaskName -cne '{TASK_NAME}' -or $TaskPath -cne '\\') {{
      throw 'readback target mismatch'
    }}
    $global:schedulerFixtureRegistered
  }}
  function New-ScheduledTaskPrincipal {{
    param($UserId, $LogonType, $RunLevel)
    if ($UserId -cne [System.Security.Principal.WindowsIdentity]::GetCurrent().Name -or $LogonType -ne 'Interactive' -or $RunLevel -ne 'Limited') {{
      throw 'principal construction mismatch'
    }}
    [pscustomobject]@{{ UserId = $UserId; LogonType = 'Interactive'; RunLevel = 'Limited' }}
  }}
  & '{script}' -Mode Install -OwnerId '{OWNER_ID}'
}}
"""
    completed = _run_powershell(command=command)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout, completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_INSTALLED"


def test_schedule_remove_safety_unregisters_only_exact_task() -> None:
    script = str(SCRIPT).replace("'", "''")
    command = f"""
& {{
  function Get-ScheduledTask {{
    param($TaskName, $TaskPath, $ErrorAction)
    if ($TaskName -cne '{TASK_NAME}' -or $TaskPath -cne '\\') {{
      throw 'lookup target mismatch'
    }}
    [pscustomobject]@{{
      TaskName = $TaskName
      TaskPath = $TaskPath
      Description = 'StockInvestmentRev1 owner=python-pm-event-runner-v1; local Python PM material-event runner.'
    }}
  }}
  function Unregister-ScheduledTask {{
    param($TaskName, $TaskPath, [switch]$Confirm)
    if ($TaskName -cne '{TASK_NAME}' -or $TaskPath -cne '\\') {{
      throw 'removal target mismatch'
    }}
  }}
  & '{script}' -Mode Remove -OwnerId '{OWNER_ID}'
}}
"""
    completed = _run_powershell(command=command)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[-1] == "PYTHON_PM_TASK_REMOVED"


def test_schedule_install_safety_rejects_foreign_collision_before_force() -> None:
    completed = _run_powershell(
        command=_check_command(
            mode="Install",
            override="$registered.Description = 'foreign task'",
            extra_functions="""
function Test-Path { $true }
function Register-ScheduledTask { Write-Output 'FOREIGN_TASK_MUTATED'; throw 'foreign overwrite' }
""",
        )
    )

    assert completed.returncode != 0
    assert "FOREIGN_TASK_MUTATED" not in completed.stdout
    assert "PYTHON_PM_TASK_INSTALLED" not in completed.stdout


def test_schedule_remove_safety_rejects_foreign_collision_before_delete() -> None:
    completed = _run_powershell(
        command=_check_command(
            mode="Remove",
            override="$registered.Description = 'foreign task'",
            extra_functions="""
function Unregister-ScheduledTask { Write-Output 'FOREIGN_TASK_MUTATED'; throw 'foreign delete' }
""",
        )
    )

    assert completed.returncode != 0
    assert "FOREIGN_TASK_MUTATED" not in completed.stdout
    assert "PYTHON_PM_TASK_REMOVED" not in completed.stdout


@pytest.mark.parametrize(
    "override",
    [
        "$registered.Actions[0].Execute = 'python.exe'",
        "$registered.Actions[0].Arguments += ' --extra'",
        "$registered.Triggers[0].Repetition.Interval = 'PT5M'",
        "$registered.Triggers[0].Repetition.Duration = 'PT1H'",
        "$registered.Triggers[0].Repetition.StopAtDurationEnd = $false",
        "$registered.Triggers[0].Enabled = $false",
        "$registered.State = 'Disabled'",
        "$registered.Triggers[0].StartBoundary = (Get-Date).AddDays(7).ToString('o')",
        "$registered.Principal.UserId = 'SYSTEM'; $registered.Principal.RunLevel = 'Highest'",
        "$registered.Settings.MultipleInstances = 'Parallel'",
        "$registered.Settings.ExecutionTimeLimit = 'PT10M'",
        "$registered.Settings.StartWhenAvailable = $false",
    ],
)
def test_schedule_check_safety_fails_closed_on_tampered_definition(override: str) -> None:
    completed = _run_powershell(command=_check_command(override=override))

    assert completed.returncode != 0
    assert "PYTHON_PM_TASK_OK" not in completed.stdout


def test_schedule_remove_contract_targets_only_canonical_task() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Get-ScheduledTask -TaskName $taskName" in source
    assert "Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false" in source
    assert "Get-ScheduledTask |" not in source
    assert "Unregister-ScheduledTask -TaskName *" not in source
