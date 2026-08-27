# Align UR246 pointer runbook with lock-before-read behavior and retain crash-release proof

## Problem
UR246 runbook misstates lock-before-read ordering and crash-release behavior lacks a retained subprocess regression.

## Evidence
Production locks before terminal/pointer read; isolated Windows os._exit lock-holder probe released the lock and preserved valid pointer, but permanent suite lacks it.

## Scope
allow:
- Update only the UR246 runbook concurrency wording and owning pointer tests.

deny:
- No provider call, scheduler or production state mutation, implementation change, or unrelated documentation.

## Done When
Runbook states lock-before-read/re-read accurately and a provider-free child-crash regression proves the next process acquires the lock and preserves or advances only valid exact pointer state.

## Verify
Owning UR246 CLI suite including Windows subprocess child os._exit crash release and exact pointer validation; no provider call or production state mutation.
