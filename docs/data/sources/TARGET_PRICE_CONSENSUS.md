# Target Price Consensus

Status: `RESEARCH_ONLY / MANUAL / AUTOMATION_DISABLED / NON_PREDICTIVE`

This dataset is dated reference material for the user's local watchlist. It is
not advice, a valuation model, a trading signal, or a Backtest/ML input. Raw
responses are retained before parsing under
`data/landing/research/target_prices/<run_id>/`; accepted rows are stored in
`data/normalized/research_target_price_consensus/`.

## Yahoo Finance (US)

For U.S. stocks and ETFs the job uses the empirical, undocumented Yahoo Finance
route on `query2`. A non-empty run performs one bounded session handshake, then
one data request per selected security:

1. `GET https://fc.yahoo.com` to obtain the session's `A3` cookie.
2. `GET https://query2.finance.yahoo.com/v1/test/getcrumb` to obtain the crumb.
3. `GET https://query2.finance.yahoo.com/v10/finance/quoteSummary/<SYMBOL>?modules=financialData&crumb=<session-crumb>`.

The two handshake responses and every data response are captured before
inspection. Landing call records do not retain `Set-Cookie` or the crumb request
parameter; the exact crumb response body may remain only inside that run's raw
immutable Landing capture. Neither cookie nor crumb is written to Normalized
data, logs, or CLI output.

The collector retains and parses only these `financialData` values:

| Provider field | Dataset field | Meaning |
|---|---|---|
| `targetMeanPrice.raw` | `target_mean` | Provider consensus mean target price |
| `targetHighPrice.raw` | `target_high` | Provider consensus high target price |
| `targetLowPrice.raw` | `target_low` | Provider consensus low target price |
| `numberOfAnalystOpinions.raw` | `analyst_count` | Provider-reported sample count |
| `recommendationMean.raw` | `recommendation_mean` | Yahoo 1 (strong buy) to 5 (sell) mean |

Terms basis reviewed on 2026-09-03 and applied unchanged to the verified Korean
path on 2026-09-05:

- [Yahoo Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html),
  sections 2.5, 2.8, and 2.9: personal, non-commercial, revocable use of the
  service/software/API is the basis for this user's private local reference
  copy; commercial reuse, public exploitation, and redistribution are outside
  this job.
- [Yahoo Finance data-provider notice](https://help.yahoo.com/kb/yahoo-finance-plus/partnerships-sln2310.html):
  analyst recommendations and price-target data are attributed to S&P Global
  Market Intelligence; Yahoo data is informational and must not be
  redistributed. Consequently this dataset stays local, personal, attributed,
  and display-only.
- [Yahoo API terms](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html):
  Yahoo may impose rate limits. This job performs one request per selected
  security per day, waits at least one second between all request starts
  (including the handshake), uses a 30-second timeout, and never retries in the
  same run. HTTP 429 and every other HTTP/schema error fail closed after Landing
  capture. A fund-product 404 is the reviewed `NOT_APPLICABLE_ETF` outcome, not
  a transport failure.

This is the same `UNOFFICIAL / EMPIRICAL`, research-only classification used by
[`docs/data/sources/yahoo/README.md`](yahoo/README.md). The route has no stable
developer contract. A changed response or changed terms must be reviewed before
continued collection. The retained data must not leave the user's private
workspace.

## Korean markets

Decision dated 2026-09-05: **Yahoo `quoteSummary/financialData` is verified for
exchange-qualified Korean securities and uses the same personal,
non-commercial, display-only, no-redistribution terms basis as the U.S. path.**

The retained market identity determines the provider symbol without guessing:

- `KOSPI` six-digit code -> `<code>.KS`;
- `KOSDAQ` six-digit code -> `<code>.KQ`;
- a generic `KR`/`KRX` identity with no retained KOSPI/KOSDAQ classification is
  not requested and remains the legacy `UNAVAILABLE_SOURCE` fallback.

The standard A3-cookie/crumb handshake above returned full `financialData` on
2026-09-05 for `005930.KS` (37 analyst opinions) and `000660.KS` (38 analyst
opinions), including `financialCurrency=KRW`. Accepted Korean values must carry
`KRW`; a missing or different provider currency fails closed. Collection stays
bounded to one request per selected security per day after the single run-level
handshake.

The earlier “출처 없음 — 표시 불가” decision missed this already-validated
`.KS`/`.KQ` Yahoo path. It is superseded for exchange-resolved Korean
securities. The following exclusions remain unchanged:

- no KRX, KIND, OpenDART, FnGuide/FnSpace, or Naver page/PDF scraping;
- no redistribution, public exploitation, commercial reuse, or non-display use;
- no substitution of a scraped value, trailing fundamental, or another
  provider when Yahoo has no consensus.

KRX, KIND, and OpenDART still do not supply the aggregate consensus field for
this dataset. Their absence is no longer used to suppress the verified Yahoo
path. The detailed research-data rights boundary remains consistent with
[`KR_FORWARD_EARNINGS_PIT_CONTRACT.md`](../research/active/KR_FORWARD_EARNINGS_PIT_CONTRACT.md).

## Dataset contract and vintage semantics

The v1 contract declaration is `src/stock_data/contracts/research_target_prices.py`.
The additive v2 storage view, including typed `status`, is currently defined in
`src/stock_data/research/target_prices.py`; the collector backward-reads v1 rows
and writes validated v2 rows without overwriting an existing vintage.

| Field | Rule |
|---|---|
| `date` | Local research run date (`YYYY-MM-DD`) |
| `symbol`, `market` | Exact de-duplicated watchlist identity |
| `source` | Yahoo, or the legacy unresolved-Korean-exchange marker |
| `status` | One exact typed outcome from the table below |
| `target_mean`, `target_high`, `target_low` | Nullable non-negative values in `currency` per share |
| `analyst_count` | Nullable non-negative provider sample count |
| `recommendation_mean` | Nullable Yahoo 1-to-5 mean |
| `currency` | Watchlist currency, verified against `financialCurrency` for Korean rows |
| `retrieved_at` | UTC Landing-capture time (or run time for an unresolved-exchange fallback) |
| `terms_ref` | This document's applicable source section |

The primary key is `(date, symbol)`. A run appends at most one row for each
watchlist identity on that date. An existing identity/date is an API-zero no-op
and can never be replaced; later consensus is a new dated vintage. The dataset
records first observation by this project, not the provider's original
publication time, so predictive and Backtest eligibility remain blocked.

### Status and card wording

| Dataset status | Row meaning | Intended Korean card text |
|---|---|---|
| `AVAILABLE` | Positive analyst count and validated target values | `참고 · 출처 · 기준일 · 표본 n명 · 현재가 대비 괴리율` |
| `NOT_APPLICABLE_ETF` | Fund/ETN/leveraged product returned HTTP 404 or empty `financialData` | `애널리스트 목표가 없음 (ETF)` |
| `NO_COVERAGE` | HTTP 200 with `numberOfAnalystOpinions` equal to zero or null | `커버리지 없음` |
| `NOT_COLLECTED` | Security has not been fetched; dry-run requests carry this planned state | `미수집 · 수집기 미실행` |
| `UNAVAILABLE_SOURCE` | Legacy Korean fallback because KOSPI/KOSDAQ identity cannot be resolved | `거래소 확인 불가 · 수집 불가` |

## Operation

Dry-run (no request and no write):

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\research\collect_target_prices.py --project-root . --dry-run
```

One-line manual live run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\research\collect_target_prices.py --project-root .
```

The live run captures every response before status/JSON/schema inspection and
atomically promotes only a complete validated run. The dry-run call count
includes the two handshake requests whenever at least one security is planned.
No scheduler owns this job.

## Display rule

Render `AVAILABLE` values exactly as:

`참고 · 출처 · 기준일 · 표본 n명 · 현재가 대비 괴리율`

The current price is not part of this contract. A consumer may calculate the
gap only from a separately sourced, explicitly dated current price, using
`(target_mean / current_price - 1) * 100`; otherwise suppress the gap. Always
show the source and consensus date, never promote the value to a signal, and
render every non-`AVAILABLE` row from its exact typed status.
