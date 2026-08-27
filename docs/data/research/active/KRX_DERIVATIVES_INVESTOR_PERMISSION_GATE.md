# KRX derivatives investor collection queue

Status: **BLOCKED_PERMISSION**  
Priority: **LOW**

This queue applies only to the broader
`kr_kospi200_futures_investor_trading_daily` and
`kr_kospi200_options_investor_trading_daily` targets from authenticated free KRX
Basic Statistics screen `[15007]`. It is not an executable operation.

The narrower `kr_kospi200_futures_investor_net_purchase_daily` dataset is already
complete for 1999-04-26..2026-08-13. Do not duplicate it or infer the broader
sell/buy/volume fields from that artifact.

## Permission gate

- KRX Terms Articles 10(2) and 12(2) do not provide retained authorization for
  automated collection/copying.
- Transport must fail before creating request artifacts or making a request unless
  an explicit KRX permission-evidence document is retained and its SHA-256 is
  supplied to the checkpoint.
- Reopen only after permission evidence and a concrete research requirement both
  exist.

## Preserved target scope

- Lower bound: **1999-04-26**. Never request or synthesize earlier observations.
- Grain: market date × product × option right × session × exact source investor
  label.
- Measures: source sell, buy, and net buy for both volume and trading value.
- Futures use option right `NA`; options preserve `ALL`, `CALL`, and `PUT`.
- Sessions preserve `ALL`, `REGULAR`, and `NIGHT` without collapsing source values.
- Source units and investor labels must be retained exactly.

The inspected daily-trend query exposes one measure/side at a time and allows less
than two years per call. Through 2009-12-31 the frozen plan is:

| Product | Requests | Basis |
|---|---:|---|
| KOSPI200 futures | 108 | six date chunks × three sessions × two measures × three sides |
| KOSPI200 options | 324 | futures dimensions × three option-right selections |
| Total | 432 | retry zero; one KRX stream |

## Preserved execution safeguards

- One shared KRX stream and lock; no parallel requests.
- Retry zero and at least five seconds between business requests.
- Stop immediately on HTTP 403/429, restriction HTML, authentication anomaly,
  non-JSON, or schema change.
- Retain response bytes before validation, then append request ID, scope, status,
  hash, classification, and row count to the ledger.
- Resume only after verifying every completed Landing hash; orphan artifacts require
  manual audit.
- Validate exact fields, units, investor labels, date continuity, and
  `net_buy = buy - sell` before any Normalized plan.
- Published and Normalized writes remain disabled until permission and a bounded
  pilot both pass.

Source evidence: [archived investor-statistics audit](../../../archive/data/audits/2026-08-data-phase/KRX_DERIVATIVES_INVESTOR_STATS_AUDIT.md).
