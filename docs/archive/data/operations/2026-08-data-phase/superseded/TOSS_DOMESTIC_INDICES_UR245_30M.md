# UR-245 Toss KOSPI/KOSDAQ 30-minute future routing

Status: `ACTIVE_FUTURE_DATE_20260824 / PRE_DATE_PROVIDER_CALLS_ZERO / KOSPI200_UNSUPPORTED`.

The supported service contains only the retained Toss symbols `KOSPI` and
`KOSDAQ`; `KOSPI200`/`KPI200` has no verified Toss symbol/schema and remains
numeric-free. It must not be inferred from either supported index or NXT.

## Manifest and eligibility

- Manifest: `data/state/toss_domestic_indices_ur245_activation.json`.
- Future date: 2026-08-24 KST; common local wake 08:00–20:00 KST.
- Provider eligibility only: half-open 30-minute boundaries from 09:00 through
  15:00 KST (`[09:00,15:30)`). Pre-open and post-close produce API-zero
  ineligible results and construct no transport callback.
- Before publication, preserve only a previously verified KRX close/status;
  never claim fresh numeric. After close, preserve verified KRX close, never a
  NXT index, and do not make a new current call under this operation.

## Exact contract and limits

Only `GET /api/v1/market-indicators/prices` with one exact symbol per serial
callback is permitted after a durable window claim: `KOSPI`, then `KOSDAQ`.
The static Toss adapter requires the exact returned symbol, finite `lastPrice`,
timezone-aware provider `timestamp`, and emits `XKRX`, `index points`,
`snapshot`, `PROVISIONAL`, display-only/PIT-blocked observations. A source time
must be today KST and no more than 60 minutes old.

Each accepted body is retained Landing-first with canonical JSON body hash and
readback before parsing. Each index has a separate atomic observation envelope;
failure preserves its prior bytes. No retry, redirect, fallback, backfill,
GUI/history/canonical/Backtest/scheduler action is allowed. UR-126's 2026-08-21
KOSPI OAuth-stage terminal result is never replayed.

The future collector is transport-injected only:
`run_injected(root, now, response_factory)`. It contains no Toss client,
credential/configuration read, HTTP code, or operational CLI. A future live
request must separately authorize one callback per eligible identity/window.
