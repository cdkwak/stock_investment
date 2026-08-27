# Local Account Value History

## Status

`ACTIVE / USER_REQUESTED_20260827 / IDENTIFIER_FREE / READ_ONLY`

The user requested longitudinal account-size tracking because deposits,
withdrawals, cashing out, repurchases and salary-funded contributions can make
account value more useful than a position return alone. This contract records
accepted read-only account-scale observations. It does **not** call a provider,
place an order, infer a cash-flow ledger, or claim that account-size change is
investment performance.

## Grain and storage

One immutable observation file has grain:

`(source_id, observed_at, currency)`

`observed_at` is normalized to UTC before identity comparison and storage
validation. Different textual offsets for the same instant are therefore one
identity and cannot create two points.

Allowed sources are the non-identifying local aliases `toss_self` and
`kb_self`. Files live under:

- `data/local/account_value_history/toss_self/*.json`
- `data/local/account_value_history/kb_self/*.json`

An observation is promoted in the same rollback transaction as its accepted
sanitized account snapshot. Duplicate `(source_id, observed_at, currency)`
observations fail history loading closed. The Account-page privacy deletion
control removes these exact files together with retained account snapshots.
Refresh/recovery and privacy removal share one crash-released OS lifecycle
lock. The lock spans the provider/supplier call and persistence, so an already
running Toss or KB refresh cannot repopulate retained data after removal
returns.
The same privacy control also preflights and removes only the exact bootstrap
stage shape `data/staging/account_value_history/<run>/observation.json`, so an
interrupted bootstrap cannot leave account values behind after removal.

No account number, account sequence, token, person name, position symbol,
position name, order, transaction or raw provider payload is retained here.

## Metric semantics

| Source | GUI history metric | Meaning | Explicit limitation |
|---|---|---|---|
| KB Securities | `TOTAL_ASSETS` | Exact provider `total_assets`, KRW | No cash-flow ledger; changes are not returns. |
| Toss schema v2 | `OBSERVABLE_COMPONENT_SUM` | Same-currency securities market value plus cash buying power | Not labelled total assets or settled cash. No cross-currency sum. |
| Toss legacy schema v1 | `SECURITIES_VALUE` | Same-currency securities market value | Cash excluded and stated in the label. |

Every series remains source- and currency-scoped. USD and KRW are never merged
without an explicit FX observation contract. Fewer than two valid observations
suppress the chart. Values are plotted without interpolation or backfill.
The validator binds source, currency and metric: KB accepts only KRW
`TOTAL_ASSETS`; Toss accepts only KRW/USD `OBSERVABLE_COMPONENT_SUM` or legacy
`SECURITIES_VALUE`, and all currencies in one Toss observation must use the
same schema-generation metric. The GUI suppresses every persisted series for a source if
any point is later than that source's currently accepted snapshot `as_of`.
Current account values remain visible; only the PIT-invalid history is blocked
with an explicit reason.

## Performance boundary

The chart title and tooltip state: `account scale`, `external flows
unseparated`, and `not a return`. Provider position return, after-cost return,
daily P/L and account-scale history remain separate facts. A future
flow-adjusted performance series requires an accepted deposit/withdrawal and
transfer ledger with timestamps, currency, corrections and ownership rules.
