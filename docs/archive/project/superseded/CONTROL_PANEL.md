# Control Panel

> **SUPERSEDED.** This former fast-status view is retained for history only.
> Start with [`AGENTS.md`](../../../../AGENTS.md), then current
> [`PROJECT_STATUS.md`](../../../project/PROJECT_STATUS.md), then the active domain
> status. This file is not an active routing authority.

Fast current-state view. Follow links for evidence and detail; do not turn this file
into an execution log.

## RUNNING

- Primary workstream: **Backtest v0 foundation** — interfaces and implementation have
  not started. [Project Status](../../../project/PROJECT_STATUS.md) · [Backtest Status](../../../backtest/BACKTEST_STATUS.md)
- External collection: **none reported active**. Provider locks/processes were absent
  at the latest Data status update. [Data Status](../../../data/DATA_STATUS.md)

## READY

- Retained local Data baseline and deterministic inventory evidence are available for
  frozen-dataset selection. [Data Status](../../../data/DATA_STATUS.md) ·
  [Dataset Index](../../../data/DATASET_INDEX.md)
- Backtest v0 scope is approved: local dataset → PIT feature → baseline strategy →
  simulated execution/accounting → reproducible result. [Backtest Status](../../../backtest/BACKTEST_STATUS.md)

## BLOCKED

- Backtest packages and minimal domain interfaces do not yet exist.
- Corporate-action identity/PIT timing and selected historical gaps limit dataset choice.
- KB snapshot per-slice date semantics are unresolved; no operational promotion.
- GUI and live-account/trading work remain gated/out of current scope.

Details: [Project blockers](../../../project/PROJECT_STATUS.md#blockers) ·
[Data blockers](../../../data/DATA_STATUS.md)

## COMPLETE

- Data v1 core historical baseline is retained; Data is in maintenance and high-value
  gap-filling mode.
- Current registry/inventory distinction and exact Data routing are maintained only
  in [Data Status](../../../data/DATA_STATUS.md); the retained immutable inventory is historical
  point-in-time evidence.
- The 2026-08-12 equity integration and dedicated KOSPI200 futures investor
  net-purchase import are complete for their stated contracts.

Details: [Project validation](../../../project/PROJECT_STATUS.md#latest-validation-state) ·
[D001 inventory](../../data/evidence/2026-08-data-phase/inventory/D001_DATASET_INVENTORY.md)

## NEXT TOP 5

1. Freeze the first local backtest input dataset/version and availability rules.
2. Define `market_features`, `market_trading`, `market_account`, and `market_backtest`
   minimal interfaces.
3. Implement one API-free deterministic vertical slice.
4. Add reproducibility and no-network tests around that slice.
5. Expose stable service/result interfaces before any GUI implementation.

Authority: [Project Status](../../../project/PROJECT_STATUS.md#next-three-priorities) ·
[Roadmap](../../../project/PROJECT_ROADMAP.md)
