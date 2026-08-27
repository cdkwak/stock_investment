# Cboe Put/Call and VIX Source Audit

> Official Cboe source audit only. No market-statistics, VIX, or options-chain data was collected.

## 2026-08-17 Landing-only bounded-pilot decision

**No Cboe data pilot was run.**  Cboe's website terms allow viewing, printing,
and downloading one copy of website materials only for personal non-commercial
use in connection with Cboe products and services, while prohibiting copying,
storing in an electronic retrieval system, derivative works, and distribution
without prior written consent (except applicable fair use).  A project
Landing namespace is a retained electronic retrieval system and its research
purpose is not expressly within the limited exception.  The requested
lossless, hash-retained source capture is therefore not licensed by the
published terms.

| source family | Pilot status | Reason | PIT / backfill |
|---|---|---|---|
| `CBOE_OPTION_MARKET_STATISTICS` | `LICENSE_BLOCKED` | No explicit right to retain a Landing capture; do not infer permission from the public daily page or downloadable archives. | `PIT_BLOCKED`; no Raw backfill. |
| `CBOE_VIX_HISTORY` | `LICENSE_BLOCKED` | Same website-terms restriction applies to the public VIX historical download. | `PIT_BLOCKED`; no Raw backfill. |

This decision creates no `data/landing/cboe/` or `data/state/us_cboe_*`
artifact, no parser, no fixture, no Normalized/Canonical dataset, and no
contract.  It also does not establish source access failure: Cboe's public
pages are reachable, but retained Landing use is not explicitly permitted.

### Phase-1 source findings

| topic | official finding | operational interpretation |
|---|---|---|
| Daily put/call source | The Daily Market Statistics page exposes Total, Index, Exchange Traded Products, Equity, VIX, and `SPX + SPXW` put/call ratios.  Its tables expose call, put, total volume, and open interest where shown. | Only page-present fields could be retained in a future authorized pilot; no absent category or ratio may be calculated. |
| Put/call history | The historical page links recent Total/Index/Equity/ETP 2006-11-01..2019-10-04, VIX 2006-02-24..2019-10-04, older Total/Index/Equity archives, and an SPX archive.  It also offers a historical-options download form. | Public links do not grant a bulk-retention license or a continuous series guarantee. |
| VIX history | Cboe links VIX daily closing values for 1990-present (updated daily) and 1990-2003.  The page itself describes daily closing values, not an OHLC contract. | Do not presume Open/High/Low are present; no file was downloaded. |
| Public API | No public REST/API route for Daily put/call or VIX history was identified in the cited Cboe material. | This is an absence of located documentation, not proof no API exists. |
| Publication / revisions | The public pages establish value/session date content but no historical per-record publication timestamp.  Website terms permit Cboe to modify or discontinue site features without notice. | `observation_date` is distinct from `publication_date` and `available_at`; set the latter two to null if ever retained.  `PIT_BLOCKED`. |

The existing archive ranges and page-present fields are source-discovery
evidence only.  They must not be treated as a request authorization, a
publication-time schedule, or evidence that historical file revisions are
versioned.

### Required gate before any future pilot

Obtain Cboe's written consent or an applicable data license that expressly
permits local raw retention, the intended research/backtest use, and any
required storage and redistribution restrictions.  It must also identify the
permitted source endpoint/file and date range.  After that gate, a new
Landing-first pilot may separately validate Put/Call and VIX schemas; it must
still preserve `publication_date = null`, `available_at = null`, and
`PIT_BLOCKED` unless Cboe supplies historical availability evidence.

## Coverage and acquisition finding

| measure | verified free historical coverage | date lookup / bulk / API | value date vs availability | storage/license conclusion |
|---|---|---|---|---|
| Total P/C | Archive: 1995-09-27..2003-12-31; recent archive: 2006-11-01..2019-10-04 | Historical archive page provides files; current daily page supports a date field. No documented public API found. | Archive/data page does not furnish a verified historical publication timestamp for every record. | Cboe calls it convenience data, disclaims accuracy and subjects use to website terms. Retention/redistribution requires terms review; predictive use blocked. |
| Equity P/C | Archive: 2003-10-21..2003-12-31; recent archive: 2006-11-01..2019-10-04 | Same | Same | Same |
| Index P/C | Archive: 2003-10-21..2003-12-31; recent archive: 2006-11-01..2019-10-04 | Same | Same | Same |
| SPX / SPXW P/C | Cboe exposes SPX archive and current daily page publishes combined `SPX + SPXW`; exact free start for current combined series was not documented | Archive files and individual-date page; no official API found | No record-level historical release evidence found | Keep `PREDICTIVE_USE_BLOCKED` pending a retained release schedule or dated source version evidence |
| VIX option P/C | Recent archive: 2006-02-24..2019-10-04; current page publishes VIX P/C | Archive files and individual-date page; custom detailed data points to DataShop | No historical release timing evidence found | Same website terms gate; no production bulk authorization |
| VIX daily close | Cboe downloadable series: 1990-present, updated daily; separate 1990-2003 file | Downloadable file; no official REST API found | Value date is a trading date. The page does not establish historical cut-off/version schedule for predictive use. | `LICENSE_BLOCKED` for project Landing retention under the current website terms; predictive use blocked. |

## Semantics and PIT policy

`value_date` is the session/trading date associated with the ratio or VIX close. It is not evidence of when the value was disseminated or when a strategy could consume it. Neither a currently downloaded archive file nor project `retrieved_at` substitutes for an official historical release time.

All future observations should initially carry:

```text
value_date
source_published_at = null
retrieved_at
source_url
source_file_hash
availability_state = PIT_BLOCKED
predictive_use = blocked
```

The daily page supplies Total, Index, Equity, Exchange Traded Product, VIX, and `SPX + SPXW` ratios with call/put volume/open interest. It is not a contract for a continuous all-history series until archive gaps and website terms are resolved. Cboe directs custom VIX options/futures history to DataShop; that is out of scope.

## Explicit conclusions

- No free API was verified.
- Bulk archives exist for several historical P/C segments, but do not establish one continuous official release stream.
- No options-chain collection is warranted.
- No predictive use is warranted without official availability/release evidence even when value date is known.

## Official sources

- Historical options volume/P-C archive: <https://www.cboe.com/us/options/market_statistics/historical_data/>
- Daily market statistics: <https://www.cboe.com/markets/us/options/market-statistics/daily>
- VIX historical data: <https://www.cboe.com/tradable_products/vix/vix_historical_data>
