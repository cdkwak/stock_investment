# Yahoo current direct-30m switch gate — 2026-08-27

## Terminal decision

`PARTIAL_KEEP_NATIVE_15M`

Exactly four public Yahoo Chart requests were made: one each for `^VIX`,
`^FVX`, `^TNX`, and `^TYX`, with `interval=30m`, retry count zero, redirects
disabled, and exact responses retained in Landing. The all-or-nothing switch
condition did not pass, so no provider, contract, validator, orchestration,
receipt, Health, release-readiness, GUI, or scheduler implementation was
changed and no mixed-interval system was created.

## Per-symbol result

| Symbol | Yahoo identity | Observed start-minute offsets | Latest safe requested-grid bar | Retained 15m same-end close | Semantic check | Result |
|---|---|---|---|---|---|---|
| `^VIX` | `CXI / Cboe Indices / America/Chicago / INDEX / 30m` | `:00/:30`; one `:45` as-retrieved quote row excluded | `2026-08-26T16:00Z..16:30Z`, OHLC `15.55 / 15.64 / 15.53 / 15.62`, finite and internally valid | latest overlap `2026-08-21T20:00Z`: direct 30m `15.170000076293945` = retained 15m `15.170000076293945`; 39 overlaps all equal within `1e-9` | Cboe volatility quote index, index points | `PASS` |
| `^FVX` | `CGI / Cboe Indices / America/Chicago / INDEX / 30m` | `:20/:50`; one `:45` as-retrieved quote row | none on required `:00/:30` grid | no identical end timestamp under the requested grid | Yahoo indicative quote index, provider-native quote-index points; not official Treasury yield | `FAIL_GRID` |
| `^TNX` | `CGI / Cboe Indices / America/Chicago / INDEX / 30m` | `:20/:50`; one `:45` as-retrieved quote row | none on required `:00/:30` grid | no identical end timestamp under the requested grid | Yahoo indicative quote index, provider-native quote-index points; not official Treasury yield | `FAIL_GRID` |
| `^TYX` | `CGI / Cboe Indices / America/Chicago / INDEX / 30m` | `:20/:50`; one `:45` as-retrieved quote row | none on required `:00/:30` grid | no identical end timestamp under the requested grid | Yahoo indicative quote index, provider-native quote-index points; not official Treasury yield | `FAIL_GRID` |

At the validation boundary `2026-08-26T16:32:00Z`, `^VIX` could safely select
the completed bar ending at `16:30Z`. The three Treasury quote-index routes
could not select any completed provider-native 30-minute bar on the required
`:00/:30` grid. Treating their `:20/:50` timestamps as `:00/:30`, or locally
resampling them, would violate the requested contract.

## Preservation and scheduler evidence

- Retained 15-minute history status: `STATIC_COMPLETE / NO_REFRESH`.
- Retained 15-minute writes: `0`.
- Historical global-market writes: `0`.
- Published/Backtest writes: `0`.
- Protected-root SHA-256 identities match before and after all four calls; see
  `validation_20260827.json`.
- `STOCK_DATA_YAHOO_MARKET_30M` XML SHA-256 before and after:
  `f2c4edb0c77b335d90fcfef2786c6169b4d906e86f171623935b0c713a459edb`.
- `STOCK_DATA_*` task count before and after: `12`.
- Actual scheduler definition changes: `0`.
- Every JSON field whose name ends in `_utc` uses canonical RFC 3339 UTC `Z`
  notation; the machine-readable artifact declares
  `timestamp_format=RFC3339_UTC_Z`.

## Offline regression result

- Yahoo current orchestration, release-readiness, and GUI Health selection:
  `157 passed`. The formerly stale GUI test now derives the exact `BLOCKED`
  dataset set from the typed universe instead of hard-coding the superseded
  count nine; both the typed universe and retained artifact currently contain
  eight operationally blocked datasets.
- Focused retained native-15m VIX/Treasury GUI service tests: `4 passed`.
- Conditional 30-minute implementation tests were not added or run because
  the all-four validation gate failed and implementation was forbidden.

The machine-readable evidence is `validation_20260827.json`; the four exact
response bodies and call records are under
`data/landing/yahoo_market_30m_validation/20260827/`.
