# Make daily data schedules resilient to battery and sleep misses

## Problem
Installed data tasks can miss automatic refresh while the machine is asleep or on battery because their power settings are implicit unsafe defaults; slot-specific catch-up policies must remain semantically bounded.

## Evidence
Read-only actual-user task audit on 2026-08-26 found all ten STOCK_DATA definitions with DisallowStartIfOnBatteries=true, StopIfGoingOnBatteries=true, WakeToRun=false. Today's awake Toss 15:00 and Yahoo 15:02 occurrences passed, proving collectors but not sleep/battery resilience.

## Scope
allow:
- Explicit power-resilient Windows task settings, exact registration/readback validation, release-gate policy, bounded runbook/status truth, and one reviewed update of the ten existing STOCK_DATA definitions.

deny:
- No manual task start, provider/API call, cadence increase, new route or symbol, blind replay, order/account mutation, new task identity, credential output, data/history/state write, or weakening of pre-network eligibility and occurrence-idempotency gates.

## Done When
Both installers explicitly set and read back AllowStartIfOnBatteries, DontStopIfGoingOnBatteries, and WakeToRun for every managed task; existing exact cadence, IgnoreNew, execution limits, occurrence identity, and lane-specific StartWhenAvailable semantics remain unchanged unless a collector pre-network gate proves bounded catch-up safe; release readiness fails on any power-policy mismatch; actual installed definitions are updated and read back without manually starting a task or making a provider call.

## Verify
Unit-test installer source and synthetic readback mismatch cases; run release-readiness units/cold GUI; use installer DryRun then bounded actual registration and read-only Get-ScheduledTask verification for all ten tasks; compare actions/triggers/settings before and after; provider calls 0, task manual starts 0, data mutations 0, scheduler definitions only.
