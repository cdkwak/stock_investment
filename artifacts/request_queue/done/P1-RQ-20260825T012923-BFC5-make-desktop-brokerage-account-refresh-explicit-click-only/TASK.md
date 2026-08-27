# Make desktop brokerage account refresh explicit-click only

## Problem
Configured Toss desktop runtime performs read-only account API calls at startup and periodically without a fresh user gesture, despite the desired privacy-transparent explicit-click workflow.

## Evidence
MainWindow schedules AccountRefreshTrigger.STARTUP and starts account_periodic_timer; app.py passes runtime periodic_interval_ms; toss_account_runtime accepts TOSSINVEST_ACCOUNT_REFRESH_SECONDS; tests currently assert startup and periodic calls.

## Scope
allow:
- Remove desktop startup/periodic account triggers and interval environment/configuration; retain local read on startup, explicit manual worker, transactional sanitized storage, rate guards, and failure isolation; align exact active authorities.

deny:
- No live credential inspection or provider call, account identifier/value logging, order/transfer/trading/scheduler changes, KB activation, data deletion, or lower-level read-only contract weakening.

## Done When
Opening the app performs zero account provider calls while still loading validated local snapshots; no periodic account timer or refresh interval configuration exists in desktop wiring; one accepted button click queues at most one MANUAL background refresh, repeated clicks while busy coalesce to at most one further MANUAL cycle, and close remains clean; active Data/GUI/runbook authorities all state manual-only.

## Verify
Focused synthetic runtime and GUI tests prove zero startup/periodic calls, exactly one MANUAL call per accepted click, busy-click coalescing, local snapshot startup display, disabled-runtime API zero, and clean thread shutdown; run full owning runtime and GUI modules plus native offscreen Account smoke with injected zero-network refresher.
