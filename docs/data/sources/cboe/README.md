# Cboe market data candidate

## Status

- Project status: `CONTRACT_ONLY / LICENSE_BLOCKED`.
- Possible future scopes: options market statistics and volatility-related data.
- No accepted runtime endpoint, retained provider artifact, or active runbook
  exists. An unregistered contract-only schema and numeric-free local GUI view
  exist solely to preserve category identity and the current gate.

The current Dashboard candidate is U.S. options put/call activity. The public
Daily Market Statistics page exposes Cboe-scoped Total, Index, Equity, ETP,
VIX, and SPX+SPXW categories, but those labels must not be represented as a
consolidated all-U.S.-exchange series without an explicit product contract.

## Official reference

- [Cboe U.S. options market statistics](https://www.cboe.com/markets/us/options/market-statistics)
- [Cboe market data services](https://www.cboe.com/data/market-data-services)
- [Cboe Daily Market Statistics](https://www.cboe.com/markets/us/options/market-statistics/daily)
- [Cboe historical market statistics](https://www.cboe.com/us/options/market_statistics/historical_data/)
- [Cboe explanation of equity-option put/call scope](https://www.cboe.com/insights/posts/how-early-exercise-order-flow-impacts-equity-option-put-call-ratios)
- [Cboe website terms](https://www.cboe.com/terms)
- [Cboe Use of Content](https://www.cboe.com/use-of-content)
- [Cboe DataShop Option Sentiment specification](https://datashop.cboe.com/Documents/Cboe_OptionSentiment_Specs.pdf)
- [Cboe Option EOD Summary](https://datashop.cboe.com/option-eod-summary)

## Authentication and safe example

No request example is intentionally provided. Before implementation, select an
official product, verify redistribution/display rights, licensing, entitlement,
delays, rate limits, schema, and historical availability. A visible website
table or downloadable example is not automatically an approved production API.
The public statistics page is therefore not scraped or retained by this project.

## Current official category semantics

The page's published P/C ratio is a **volume** put/call ratio (put volume divided
by call volume), not a price ratio and not an open-interest ratio. Each row has
its own scope and must remain separate:

| Contract scope | Official label | Meaning |
|---|---|---|
| `CBOE_TOTAL` | TOTAL PUT/CALL RATIO | `SUM OF ALL PRODUCTS` within the Cboe-reported page scope; not the entire U.S. options market |
| `CBOE_INDEX` | INDEX PUT/CALL RATIO | index-option product group inside the reported scope |
| `CBOE_ETP` | EXCHANGE TRADED PRODUCTS PUT/CALL RATIO | ETP option group; not QQQ or SOXX specifically |
| `CBOE_EQUITY` | EQUITY PUT/CALL RATIO | Cboe Options Exchange (C1) single-stock option flow |
| `CBOE_VIX` | CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO | VIX option family; not the VIX index level |
| `CBOE_SPX_SPXW` | SPX + SPXW PUT/CALL RATIO | explicitly combined SPX and SPXW option roots |

VIX and SPX+SPXW are product-family subsets of Index, not extra disjoint totals
to add to Total. The page also displays call/put/total open interest, but does
not label its headline P/C rows as open-interest P/C.

No Cboe aggregate is an exact Nasdaq, QQQ, NDX, or SOXX ratio. Those four GUI
slots remain numeric-free until an exact licensed source exists.

## Usage, retention, automation, and finality

- Website terms allow one personal non-commercial view/print/download copy but
  otherwise prohibit copying, electronic storage, display, derivative use, or
  distribution without prior written consent.
- The Use of Content page states that approval is not effective until Cboe and
  the requester sign a license agreement. Merely submitting a request grants no
  permission.
- Consequently this repository has no permission to automate page extraction,
  retain Landing bytes, publish the figures in the local Dashboard, or install
  a scheduler.
- The Daily Market Statistics page exposes an observation date and daily
  summary, while the current-market page exposes intraday cumulative rows. The
  official pages reviewed on 2026-08-20 do not define a daily publication time,
  revision freeze, or correction/version policy. Finality and PIT remain
  unresolved.
- Legacy downloadable ratio archives have category-dependent coverage and the
  listed recent Total/Index/Equity/ETP files stop at 2019-10-04. Their presence
  does not extend current retention rights or establish a current automation
  source.

Licensed candidates remain distinct:

- Option Sentiment: call/put volume and dollar premium; a dollar-premium ratio
  must be named `premium P/C`, never price PCR.
- Option EOD Summary: contract-level EOD volume/OI from which an explicitly
  scoped SPX, QQQ, or other approved-underlying ratio could be derived.

Until one product and entitlement are selected, the GUI must show each exact
scope as unavailable with no number.

The typed handoff is `stock_data.gui.us_option_pcr_adapter`. It supplies the six
separate Cboe rows as `LICENSE_BLOCKED` and Nasdaq/QQQ/NDX/SOXX as
`SOURCE_UNAVAILABLE`. It does not read the website or a local data root.

## Required entry gate

1. Name the exact official product and endpoint.
2. Record license/display/retention permissions.
3. Define credential handling and bounded rate limits.
4. Create Landing, contract, availability/PIT rule, and focused tests.
5. Only then add a read-only project adapter.

Do not scrape Cboe pages or substitute a Yahoo symbol for an official Cboe
dataset without a separately approved contract.
