# Market Session Rule Evidence

역할: 이 문서는 교차시장 session evidence이며, dataset별 bar/date 의미의 권위는 선택된 Dataset Contract와 source policy다.

This bounded source record supports the versioned contracts in
`src/stock_data/orchestration/exchange_calendar.py`. It is evidence, not
authorization to collect prices, promote data, or install a scheduler.

Observed on `2026-08-20` (Asia/Seoul). Only official venue documentation is
cited. A documented exchange schedule does not establish that a Yahoo
continuous symbol uses the exchange's trade-date, settlement, or bar boundary.

## Current-route conclusions

| Route identity | Official evidence established | Contract result | Remaining fail-closed gate |
|---|---|---|---|
| Yahoo `NQ=F` continuous | CME Chapter 359 identifies Chicago time, says the trading day generally begins at 17:00 CT on the prior evening, and fixes listed-contract termination at the Nasdaq regular open on the final-settlement business day; final settlement is normally the third Friday. CME's current equity-index page gives normal 17:00-16:00 CT hours. | `CME_EQUITY_INDEX_FUTURES` retains the normal hours and listed-contract expiration rule, but is not executable. | The exact current intraday halt and each finalized product holiday/early close remain dynamic. More importantly, no official evidence maps Yahoo's continuous symbol, selected month, roll, provider date, or completed bars to listed NQ. |
| Yahoo `CL=F` continuous | The current official CL page states Sunday-Friday 17:00-16:00 CT with a daily 16:00-17:00 CT closure. NYMEX Chapter 200 fixes listed CL termination at the third business day before the 25th calendar day of the month before delivery, with explicit non-business-day and later-holiday-change handling. | `NYMEX_WTI_FUTURES` retains the normal hours, daily closure, and listed-contract expiration rule, but is not executable. | Exact-date product holiday/early-close schedules remain dynamic. No official evidence maps Yahoo's continuous month selection, roll, provider date, or completed bars to listed CL. |
| CFE VX futures | CFE states Extended 17:00 previous day-08:30 CT, Regular 08:30-15:00 CT, Extended 15:00-16:00 CT. Its 2026 table gives dated closures and 12:15 CT RTH early closes on Nov 27 and Dec 24; expiring VX closes at 08:00 CT and holiday-adjusted expirations move to the prior business day. | Versioned as 2026 official evidence, but intentionally unavailable because the repository has no accepted provider identity. | No current repository provider route, no completed-provider-bar mapping, and no post-2026 holiday version. The venue schedule alone cannot create a numeric route. |
| Cboe spot VIX / Yahoo `^VIX` | Cboe VIX Methodology v6.0 states 15-second dissemination, GTH 02:15-08:25 CT and RTH 08:31-15:15 CT; times may change on shortened sessions. It also distinguishes spot mid-quote calculation from the derivative-settlement SOQ. | `CBOE_SPOT_VIX` policy v3 corrects the former 08:30 CT RTH label and explicitly classifies Yahoo `^VIX` as a provider subset, not the official 15-second series. The official service remains unavailable. | Exact shortened-session calculation windows, official observation-date mapping, and Yahoo 15m aggregation equivalence are not published. The retained XNYS-aligned Yahoo scope remains unchanged. |

## Official sources

| Venue | Official document | Source metadata and used fields |
|---|---|---|
| CME NQ | [E-mini Nasdaq-100 Futures Contract Specs](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html) | Current product page observed 2026-08-20; identifies NQ and nearly 24-hour access, but the rendered page does not provide an exact current product schedule. |
| CME NQ rule | [CME Rulebook Chapter 359](https://www.cmegroup.com/content/dam/cmegroup/rulebook/CME/IV/350/359/359.pdf) | Current chapter observed 2026-08-20; Chicago-time convention, general 17:00 CT trading-day start, listed-contract termination, third-Friday final settlement, and scheduled/unscheduled early-close dependencies. |
| CME equity-index hours | [Equity Index Futures Hours](https://www.cmegroup.com/trading/equity-index/futures-and-etfs-myths-vs-facts.html) | Current page observed 2026-08-20; normal Sunday-Friday 17:00-16:00 CT and daily 16:00-17:00 CT closure for the equity-index family. This does not supply Yahoo mapping or a durable holiday calendar. |
| CME platform | [CME Globex Reference Guide](https://www.cmegroup.com/content/dam/cmegroup/globex/files/GlobexRefGd.pdf) | Official guide observed 2026-08-20; product hours vary; evening opening begins the next trade date; general maintenance state 16:00-16:45 CT Mon-Thu. It is not an NQ- or CL-specific holiday calendar. |
| CME holidays | [CME Group Holiday and Trading Hours](https://www.cmegroup.com/trading-hours.html) | Current dynamic page observed 2026-08-20; hours are CT unless stated and product-specific holiday schedules may change. No single generic holiday row is promoted into NQ or CL. |
| NYMEX CL | [WTI Crude Oil Futures Contract Specs](https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.contractSpecs.html) | Current official product page observed 2026-08-20; CL Globex Sunday-Friday 17:00-16:00 CT with a 60-minute break beginning 16:00 CT. |
| NYMEX CL rule | [NYMEX Rulebook Chapter 200](https://www.cmegroup.com/rulebook/NYMEX/2/200.pdf) | Current chapter observed 2026-08-20; Rule 200102.F provides the exact listed-contract termination formula and treatment of non-business days and later holiday-calendar changes. |
| CFE VX | [VIX Futures Contract Specifications](https://www.cboe.com/tradable-products/vix/vix-futures/specifications) | Contract snapshot as of 2025-06-13, page observed 2026-08-20; explicit Extended/RTH/Extended segments and market-order restriction. |
| CFE calendar | [CFE Hours & Holidays](https://www.cboe.com/about/hours/us-futures) | Current 2026 page observed 2026-08-20; all times CT, weekly VX/VXM open/close states and dated 2026 holiday/early-close table. |
| Spot VIX | [VIX Options Product Specifications](https://www.cboe.com/tradable-products/vix/vix-options/specifications) | Page observed 2026-08-20, snapshot label 2024-03-19; current page explicitly states spot VIX calculation windows 02:15-08:25 CT and 08:30-15:15 CT. |
| Spot VIX method | [Cboe VIX Methodology v6.0](https://cdn.cboe.com/resources/indices/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf) | Revised 2026-02-26 and observed 2026-08-20; official 15-second dissemination, GTH 03:15-09:25 ET, RTH 09:31-16:15 ET, shortened-session caveat, and spot-versus-SOQ distinction. |
| VIX derivative FAQ | [Cboe VIX FAQ](https://www.cboe.com/tradable_products/vix/faqs) | Official expiration and holiday-adjustment rule; expiring VX trades only through 09:00 ET/08:00 CT, and spot VIX dissemination on expiration days remains distinct from the SOQ. |
| Cboe options calendar | [Cboe Options Hours & Holidays](https://www.cboe.com/about/hours/us-options) | Current 2026 page observed 2026-08-20; options-input GTH/RTH and dated holidays. It does not explicitly state the spot-index calculation end on every modified session, so those values are not imported into the spot-index service. |

## Versioning and migration rule

- Official-document metadata is tagged `bounded-2026-08-20`; CFE is bounded to
  calendar year 2026 and spot-VIX methodology v6.0 begins at its documented
  2026-02-26 revision boundary.
- XKRX/XNYS remain executable through the installed, versioned
  `exchange-calendars` schedule. The four official-document contracts remain
  `EVIDENCE_REQUIRED`, even where individual fields are known.
- NQ and CL listed-contract expiration is now evidenced, and 2026 VX expiration
  hours are evidenced. These facts do not prove a Yahoo continuous-series roll
  or bar mapping. A future worker must obtain that exact provider binding and
  an effective-dated exact-date holiday schedule before enabling NQ or CL.
  CFE VX remains intentionally unavailable until an exact provider route exists;
  official spot VIX remains intentionally unavailable because Yahoo 15-minute
  aggregation is not equivalent to Cboe's official 15-second dissemination.
  Existing Yahoo bars and checkpoints retain their provider dates; this
  evidence does not rewrite them.
- KST 09:00 remains a UI-only visual anchor and is never an exchange open,
  maintenance boundary, or trade-date roll.
