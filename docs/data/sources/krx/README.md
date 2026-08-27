# KRX and Korean official-source notes

This project retains several Korean-market routes that must remain visibly
distinct even when they describe related variables.

## Official reference

- [KRX Data Marketplace](https://data.krx.co.kr/)
- [KRX Open API service list](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)
- [KRX data usage terms](https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd)
- [pykrx project](https://github.com/sharebook-kr/pykrx)

pykrx is a library adapter that retrieves KRX/Naver data; it is not itself an
official KRX contract. Pin and test library behavior, respect source terms and
rate limits, and do not assume a screen field is stable because a wrapper name
stayed the same.

## Source identities

| Identity | Accepted examples | Boundary |
|---|---|---|
| KRX via pykrx | KOSPI/KOSDAQ/KOSPI200 indices, short-selling artifacts, retained high-value Raw | pykrx is a library route to KRX data; library calls and exact KRX screens retain their provenance |
| Direct KRX screen/API | VKOSPI and reviewed screen-specific evidence | Screen code, trading date, capture time, and publication rule must be explicit |
| Reviewed manual KRX CSV | KOSPI200 futures investor net purchase | Zero-network promotion only after the exact file and scope are reviewed |
| data.go.kr official open data | KOSPI200 futures/options, lending, liquidity, credit, corporate-action observations | Separate provider operation and contract; never relabel as a direct KRX screen response |

## Active and important datasets

- `kr_index_daily`: KOSPI/KOSDAQ daily OHLC, volume, trading value, and market
  cap through the Landing-first pykrx index lane.
- `kr_kospi200_index_daily`: official KOSPI200 spot input, including the
  same-date EOD T+1 option-wall join.
- `kr_vkospi_daily`: direct KRX VKOSPI daily route with its own finality policy.
- KOSPI200 futures/options source, provider bridge, nearest-listed Basis, volume
  PCR, and maximum-OI strike analysis: official data.go.kr inputs with preserved
  provider/session and unit limits.
- Short selling: date-regime-aware official artifacts. Do not infer an intraday
  source or substitute broker snapshots.

## Manual inbox

`manual_inbox/` is acquisition evidence, not a general runtime dependency.
Agents must not bulk-read or reinterpret every CSV. Use only the exact file
named by an active operation, verify its hash/scope/encoding, and promote through
the existing zero-network reviewer. Do not edit or overwrite retained source
files.

## Semantic and timing rules

- A completed XKRX trading date and the source's publication/finality gate are
  separate conditions.
- KOSPI200 Basis keeps the label `source-native difference` until price units
  are officially verified.
- Volume PCR is `put_volume / call_volume`; valid-empty input produces null,
  never zero. OI PCR cannot substitute for price PCR.
- The displayed Call/Put value is a front retained-maturity maximum-OI strike,
  not an active/gamma wall or forecast.
- KRX, data.go.kr, Toss, KB, and LS rows are never silently joined or used as
  fallbacks for one another.

## Runtime route

- Dataset map: [Dataset Index](../../DATASET_INDEX.md)
- Core daily operations: [Dashboard Core Daily Incremental](../../operations/DASHBOARD_CORE_DAILY_INCREMENTAL.md)
- VKOSPI operation: [KRX VKOSPI Daily Incremental](../../operations/KRX_VKOSPI_DAILY_INCREMENTAL.md)
- Market daily operation: [Market Daily Incremental](../../operations/MARKET_DAILY_INCREMENTAL.md)
- Dashboard ownership: [Dashboard Daily Source Routing](../../../gui/DASHBOARD_DAILY_SOURCE_ROUTING.md)

## Safe read examples

Use checked-in collectors so Landing, validation, checkpoint, and rollback
rules remain intact:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_krx_vkospi_daily.py --help
.\.venv\Scripts\python.exe .\scripts\manual\pilot\pilot_pykrx_short_selling.py --help
```

Do not derive BLD/screen codes by browser trial-and-error in production. Exact
screen code, request fields, date scope, response header, empty-result meaning,
and usage permission must be recorded before adding a direct route.
