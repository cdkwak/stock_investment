# Target Price Consensus

Status: `RESEARCH_ONLY / MANUAL / AUTOMATION_DISABLED / NON_PREDICTIVE`

This dataset is dated reference material for the user's local watchlist. It is
not advice, a valuation model, a trading signal, or a Backtest/ML input. Raw
responses are retained before parsing under
`data/landing/research/target_prices/<run_id>/`; accepted rows are stored in
`data/normalized/research_target_price_consensus/`.

## Yahoo Finance (US)

For U.S. stocks and ETFs the job makes one bounded request per ticker to the
empirical, undocumented Yahoo Finance route:

`GET https://query1.finance.yahoo.com/v10/finance/quoteSummary/<TICKER>?modules=financialData`

It retains and parses only these `financialData` values:

| Provider field | Dataset field | Meaning |
|---|---|---|
| `targetMeanPrice.raw` | `target_mean` | Provider consensus mean target price |
| `targetHighPrice.raw` | `target_high` | Provider consensus high target price |
| `targetLowPrice.raw` | `target_low` | Provider consensus low target price |
| `numberOfAnalystOpinions.raw` | `analyst_count` | Provider-reported sample count |
| `recommendationMean.raw` | `recommendation_mean` | Yahoo 1 (strong buy) to 5 (sell) mean |

Terms basis reviewed on 2026-09-03:

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
  ticker, waits at least one second between starts, uses a 30-second timeout,
  and never retries in the same run. HTTP 429 and every other HTTP/schema error
  fail closed after Landing capture.

This is the same `UNOFFICIAL / EMPIRICAL`, research-only classification used by
[`docs/data/sources/yahoo/README.md`](yahoo/README.md). The route has no stable
developer contract. A changed response or changed terms must be reviewed before
continued collection. The retained data must not leave the user's private
workspace.

## Korean markets

Decision: **출처 없음 — 표시 불가**.

No compliant, already-approved consensus source was found:

- KRX Data Marketplace/Open API supplies exchange and issuer observations, not
  an analyst target-price consensus API. Its [market-data usage policy](https://data.krx.co.kr/inc/datasale/Market%20Data%20Usage%20Polices_ko.pdf)
  also distinguishes private end use from redistribution and non-display use;
  it does not create a missing consensus field.
- KIND publishes disclosures and some individual research-report documents, but
  it exposes no approved aggregate target-price consensus API. Extracting KIND
  pages or report PDFs for this dataset would be scraping and is excluded.
- [OpenDART's official API catalog](https://opendart.fss.or.kr/guide/main.do)
  exposes filings, company identity, financial statements, ownership, and
  report facts. It does not expose analyst opinions or target-price consensus.
- The repository's approved KRX/pykrx, OpenDART, KB, LS, Toss, FRED, and Yahoo
  routes contain no contract-valid Korean target-price consensus source.
  FnGuide/FnSpace and Naver Finance page extraction are not used. The detailed
  rights boundary is consistent with
  [`KR_FORWARD_EARNINGS_PIT_CONTRACT.md`](../research/active/KR_FORWARD_EARNINGS_PIT_CONTRACT.md).

Each Korean watchlist symbol is nevertheless represented for the run date with
source `NONE_COMPLIANT_KR_CONSENSUS_SOURCE` and null consensus fields. Consumers
must render that state as `출처 없음 — 표시 불가`; they must not substitute a
scraped value, trailing fundamental, or another provider.

## Dataset contract and vintage semantics

Contract: `src/stock_data/contracts/research_target_prices.py`

| Field | Rule |
|---|---|
| `date` | Local research run date (`YYYY-MM-DD`) |
| `symbol`, `market` | Exact de-duplicated watchlist identity |
| `source` | Yahoo or the explicit no-compliant-Korean-source marker |
| `target_mean`, `target_high`, `target_low` | Nullable non-negative values in `currency` per share |
| `analyst_count` | Nullable non-negative provider sample count |
| `recommendation_mean` | Nullable Yahoo 1-to-5 mean |
| `currency` | Watchlist currency, defaulting only by validated US/KR region |
| `retrieved_at` | UTC Landing-capture time (or run time for the no-source KR row) |
| `terms_ref` | This document's applicable source section |

The primary key is `(date, symbol)`. A run appends at most one row for
each watchlist identity on that date. An existing identity/date is an API-zero
no-op and can never be replaced; later consensus is a new dated vintage. The
dataset records first observation by this project, not the provider's original
publication time, so predictive and Backtest eligibility remain blocked.

## Operation

Dry-run (no request and no write):

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\research\collect_target_prices.py --dry-run
```

One-line manual live run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\research\collect_target_prices.py
```

The live run captures every response before status/JSON/schema inspection and
atomically promotes only a complete validated run. No scheduler owns this job.

## Display rule

Render available values exactly as:

`참고 · 출처 · 기준일 · 표본 n명 · 현재가 대비 괴리율`

The current price is not part of this contract. A consumer may calculate the
gap only from a separately sourced, explicitly dated current price, using
`(target_mean / current_price - 1) * 100`; otherwise suppress the gap. Always
show the source and consensus date, never promote the value to a signal, and
render Korean/no-coverage rows as unavailable.
