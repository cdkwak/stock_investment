# Toss Historical Candidate Audit

Audit date: 2026-08-14 KST  
Mode: read-only/offline repository inspection plus official metadata review; no
OAuth token or market-data request was issued in this audit.

## Decision

Toss does not expose a new survivorship-safe, market-level historical source for
the current high-priority gaps. No new backfill is authorized from this audit.

The canonical Toss OpenAPI document was version `1.2.14` when reviewed. Its
market-indicator catalog is limited to KOSPI, KOSDAQ, and six Korean government
bond tenors. It contains no futures, options, derivative-investor, valuation, or
fundamentals endpoint. Program trading, stock investor trading/foreign ownership,
short selling, credit, and securities lending are historical cursor endpoints,
but their grain is one currently resolvable stock symbol rather than the market.

Official metadata:

- <https://developers.tossinvest.com/llms.txt>
- <https://openapi.tossinvest.com/openapi-docs/latest/openapi.json>

## Candidate classification

| Target | Toss operation | Historical support | PIT / survivorship result | Decision |
|---|---|---|---|---|
| Futures/options prices | None | None | Not applicable | `UNAVAILABLE` |
| Derivatives investor flow | None | None | Not applicable | `UNAVAILABLE` |
| Valuation/fundamentals | None | None | Not applicable | `UNAVAILABLE` |
| Historical equity universe | `listStocks` (`GET /api/v1/stocks/all`) | No as-of date or range. This is one current, unpaginated market snapshot; `status=DELISTED` is accepted, but rows contain only symbol, name, security type, common-share flag, and ISIN | Useful future discovery evidence, but it supplies neither listing/delisting dates nor a claim of complete historical membership. The related detail call exposes current `listDate`/`delistDate`, not point-in-time vintages | `SNAPSHOT_ONLY_NOT_PIT_UNIVERSE` |
| Market program trading | `getStockProgramTrades` | True `until` cursor, maximum 100 rows, but only per symbol | Retained delisted-symbol sentinel `003410` returned HTTP 404 `stock-not-found` | `NOT_SURVIVORSHIP_SAFE` |
| Foreign ownership | `getStockInvestorTrading.foreignerHolding` | True `until` cursor, but only per symbol | No historical market/universe endpoint; the stock endpoint uses the same current-symbol resolver boundary already demonstrated by the other stock-trend operations | `SURVIVORSHIP_BLOCKED` |
| Stock investor flow | `getStockInvestorTrading` | True `until` cursor, but only per symbol | Same boundary; registered foreigner flow is KRX+NXT and is not a replacement for market-level KRX derivatives statistics | `SURVIVORSHIP_BLOCKED` |
| Short selling | `getStockShortSelling` | Representative-symbol evidence begins in 2019 | Delisted-symbol sentinel returned HTTP 404 `stock-not-found`; superior accepted KRX Trading/Balance artifacts already exist | `NOT_SURVIVORSHIP_SAFE` |
| Credit | `getStockCreditTrades` | Representative-symbol evidence begins in 2023 | Delisted-symbol sentinel returned HTTP 404 `stock-not-found`; accepted official market credit history already exists | `NOT_SURVIVORSHIP_SAFE` |
| Securities lending | `getStockSecuritiesLending` | Representative-symbol evidence begins in 2021 | Delisted-symbol sentinel returned HTTP 404 `stock-not-found`; accepted official detail/market/participant histories already exist | `NOT_SURVIVORSHIP_SAFE` |
| KOSPI/KOSDAQ investor trading | `getMarketIndicatorInvestorTrading` | Market-level cursor history | Survivorship-safe at market grain; already fully collected and integrated | `COMPLETE_EXISTING` |
| KOSPI/KOSDAQ index candles | `getMarketIndicatorCandles` | Market-level cursor history; retained probe first finds data in 2014 | Safe grain, but duplicates longer retained domestic-index/equity coverage | `DUPLICATE_INFERIOR` |
| Korean Treasury yields | `getMarketIndicatorCandles` | Six market-level tenor histories | Already collected; BOK ECOS has longer coverage and clearer official-source authority | `COMPLETE_EXISTING_LIMITED` |

## Retained evidence

- `data/state/toss_survivorship.json` records four independent stock-trend
  operations failing on the delisted 003410 sentinel with HTTP 404
  `stock-not-found`. None passed the required delisted-symbol gate.
- `tests/fixtures/tossinvest_historical_probe.json` retains bounded historical
  anchor and refinement responses. The first observed data years are 2019 for
  Samsung Electronics program/short history, 2021 for securities lending, and
  2023 for credit. A representative surviving symbol does not establish a
  historical universe.
- `kr_market_investor_trading_daily` is complete locally with 5,946 rows,
  2014-07-01..2026-08-11, two market targets, 60 market calls, Landing capture,
  checkpoint, and Normalized output.
- `kr_treasury_yield_daily` is complete locally with 11,162 rows,
  2019-01-02..2026-08-10, six instruments, 60 market calls, Landing capture,
  checkpoint, and Normalized output.
- The four blocked per-symbol contracts have no Normalized artifact, as required.

An exhaustive schema-level review found 33 paths in canonical OpenAPI `1.2.14`.
Outside account/order history, the complete set of backward navigation modes is:

- `before`: per-symbol stock candles and KOSPI/KOSDAQ/government-bond candles;
- `until`: the five per-symbol stock-trend operations and KOSPI/KOSDAQ market
  investor trading;
- point lookup: exchange rate `dateTime` and KR/US market-calendar `date`;
- lookback window without an as-of parameter: rankings (`1d` through `1y`), which
  only returns a current top-100 result and is not a historical universe.

The generated official Markdown reference and overview contain the same operation
set as the canonical JSON. No extra documented route or pagination mode was found.
Legacy `Stock Investment` contains no Toss client, endpoint metadata, or retained
Toss behavior to import; this audit did not modify or depend on legacy runtime code.

## Request-volume consequence

The canonical daily universe contains 3,137 distinct symbols from 2019 onward,
3,082 from 2021 onward, and 2,999 from 2023 onward. With a maximum of 100 records
per response, even a current-survivor-only collection would require roughly tens
of thousands of calls per dataset before validation. More importantly, increasing
request volume cannot repair the demonstrated inability to retrieve delisted
symbols. No call/runtime/storage estimate can turn those endpoints into a valid
market-history backfill.

## Date and availability contracts

The blocked stock operations must not share one common availability rule if they
are ever reconsidered:

- program trading: the current-day row can change until market close;
- short selling and securities lending: daily confirmed values appear in the
  evening of the source date;
- credit: source date values appear on the next business day (`T+1`);
- stock investor trading: intraday partial values become complete in stages;
  investor flow and foreign holdings update in the evening, CFD at `T+1`, and
  foreign holdings may receive another confirmed update the next morning.

`updatedAt` must therefore be retained as source provenance. A capture date must
not be substituted for `source_date` or for a field-specific availability rule.

## Integration action

Keep the existing market investor and Treasury artifacts unchanged. Do not start
per-symbol Toss backfills, do not synthesize market aggregates from current
survivors, and do not use the ranking endpoint or `stocks/all` snapshot as a
historical universe. `status=DELISTED` materially improves forward discovery, but
does not cure the retained `stock-not-found` history failure or prove complete PIT
membership. Revisit only if Toss publishes a historical market-level endpoint or
demonstrates access to delisted histories with an authoritative point-in-time
universe contract.
