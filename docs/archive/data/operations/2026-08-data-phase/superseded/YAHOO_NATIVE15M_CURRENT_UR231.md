# Yahoo native-15m current windows — UR-231

Status: `WAITING_FIRST_COMPLETED_BAR_20260821`

UR-234 installed and hash-read the exact public activation manifest at
`data/state/yahoo_native15m_ur231_manifest.json` before either first boundary.
It did not create either lane ledger, Landing capture, or projection.

## Exact lanes and boundaries

| Independent lane | Identities | First native 15m bar | earliest completed KST window | budget |
| --- | --- | --- | --- | --- |
| `YAHOO_TREASURY_QUOTE` | `^FVX`, `^TNX`, `^TYX` | 08:20–08:35 America/Chicago | `[2026-08-21T22:35:00+09:00, 22:50:00+09:00)` | 3 GETs total, one per identity |
| `CBOE_VIX` | `^VIX` | 09:30–09:45 America/New_York (XNYS-aligned) | `[2026-08-21T22:45:00+09:00, 23:00:00+09:00)` | 1 GET total |

The boundaries are derived from the existing accepted native-15m session policy
and `BAR_END_LE_RETRIEVED_AT`: a bar is never accepted while live-forming.
Treasury values remain provider-native quote index points, not official yields.
Yahoo `^VIX` remains an indicative/delayed provider subset, not the official
Cboe 15-second service.

## Mandatory preflight and future execution

Use only the supported API-zero preflight before an expressly authorized
injected transport:

```text
python scripts/maintenance/run_yahoo_native15m_ur231_current.py \
  --lane YAHOO_TREASURY_QUOTE --as-of <timezone-aware timestamp>
```

The exact public manifest is `data/state/yahoo_native15m_ur231_manifest.json`.
Each lane keeps an independent durable ledger under
`data/state/yahoo_native15m_ur231/`, Landing root under
`data/landing/yahoo_native15m_ur231/`, and display-only/PIT-blocked projection
root under `data/state/current_observations/yahoo_native15m_ur231/`.

A future collector must, in this order: read/validate the manifest, select the
current half-open window, durably claim the lane before transport, make exactly
one timeout-10/retry-zero/no-redirect/no-fallback request per listed symbol,
retain Landing first with hash readback, validate exact identity/timezone/session
and the completed native bar, then atomically preserve-or-replace the lane's
display-only observation and perform API-zero replay. Orphaned `ATTEMPTING`,
terminal, malformed, pre-boundary, and closed-window states are API zero and
must not construct transport.

No `XNYS_MARKET_INDEX`, `NQ=F`, `^IXIC`, or `^GSPC` route is in this operation.
No scheduler cadence, canonical/history, GUI, Backtest, authentication, cookies,
or environment configuration is authorized.
