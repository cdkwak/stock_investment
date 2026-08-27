# UR-240 Toss 000660 retained NXT-close recovery

Status: `COMPLETED_API0_RETAINED_RECOVERY / DISPLAY_ONLY_POST_CLOSE / NO_REPEAT`.

This is a recovery-only operation over the immutable UR-239 Landing, never a
provider request.  OAuth, business GET, retry, redirect, fallback, cookie,
environment, GUI, History, Canonical, Backtest, and scheduler actions are zero.

## Exact evidence gate

- Landing: `data/landing/tossinvest/stock_current_quote_ur239/000660_2026-08-21_20260821T131225785444Z.json`
- Exact bytes: `498`; SHA-256:
  `576019ac260bf2e6ce97f6683bb60fb5f1fb39beaaa1785abdd21d806bad78d5`.
- Route/identity: Toss `/api/v1/prices?symbols=000660`,
  `KR_EQUITY_CURRENT/XKRX/000660`.
- Retained row proves `currency=KRW`, `lastPrice`, and aware provider timestamp
  `2026-08-21T19:59:59+09:00`.

The row contains no `venue` or `session` field.  The current user expressly
authorizes a narrow exception only for this exact Toss domestic `000660` route:
the same-date, aware timestamp in `[19:55:00,20:00:00]` KST is classified
`TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW` with
`venue_inferred=true`.  This is route-local inference, **not** provider-declared
`XKRX`/`NXT`, and it is never a live tick.  A partial or contradictory supplied
venue/session remains fail-closed.

## Conditional projection contract

`recover_ur239_nxt_session_close()` is transport-free and may only project an
otherwise hash-verified retained record when it includes all of:

1. exact Toss route, `000660`, `currency=KRW`, and a finite price;
2. either explicit consistent `venue=XKRX` and `session=NXT`, or both fields
   absent and the one current-user-authorized inference rule;
3. a timezone-aware timestamp on 2026-08-21 KST in `[19:55:00,20:00:00]`;
4. atomic readback to the isolated display-only/PIT-blocked observation with
   `unit=KRW per share` and `finality=POST_CLOSE_SNAPSHOT`.

That observation is `TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW`, never
a live tick.  It has no
canonical/history/Backtest meaning.  Successful recovery would immediately
perform API-zero replay; rejected/tampered/missing evidence preserves prior
projection bytes.

## Completed local result

The actual UR-239 bytes pass every revised gate.  They atomically created and
read back `data/state/current_observations/toss_000660_nxt_session_close_ur240.json`
with `unit=KRW per share`, `finality=POST_CLOSE_SNAPSHOT`, route ID
`toss-stock-price:000660:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW`,
and exact provider timestamp `2026-08-21T10:59:59+00:00`.  The state SHA-256 is
`2c12c1b737ed103aef42bb08ea2199adb2d109bf2399c2e88cc7d94dc6ae4ebb`.
Immediate replay is provider API `0`.  Neither UR-239 OAuth nor its business
route may be repeated.
