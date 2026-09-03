# Stock Detail Page

## Scope

`/stocks` is a provider-free, read-only market-data view with loopback-only
watchlist and condition preferences. Desktop uses a sticky 250 px watchlist
sidebar and a stock-detail column. At phone width, the sidebar rows become a
horizontal card scroller above the detail.

The page does not collect, promote, or repair data. It never submits an order.
Relayed private-network clients may read both detail endpoints, while the
existing watchlist and condition mutations continue to enforce direct-loopback
access in `stock_web.api.router`.

## API

- `GET /api/stock-detail?symbol=&market=` returns `identity`, `headline`,
  `stats`, `company`, `fundamentals`, `dividends`, `target_price`, `basis`, and
  `conditions`. Results are cached in memory for 60 seconds per project root,
  symbol, and market.
- `GET /api/stock-sparklines?symbols=005930,QQQ` returns at most the latest 30
  retained closes for each requested identity, oldest to newest.
- `GET /api/stocks/search?q=` uses a process-local catalog index invalidated by
  the retained master-dataset signature. Results rank exact symbol, exact name,
  name prefix, then name containment; ties prefer the latest retained market
  capitalization, fall back to issued shares times the latest retained price,
  and otherwise prefer the shorter name.

Korean price lookup is `kr_equity_price_daily` (plus a strictly newer retained
provisional row when present), then `kr_etf_price_daily` with
`partitioning=None`. U.S. tickers use `global_etf_price_daily`. The full chart
continues to load through `/api/chart` and the existing Lightweight Charts and
`window.SIChart` paths.

## Retained projections

- Company fields come only from `kr_equity_master`. Market capitalization is
  the latest retained close multiplied by `issued_shares`; it is unavailable
  for U.S. ETFs.
- Korean fundamentals come from `kr_fundamentals_quarterly`. For each period,
  the newest `rcept_no` is retained within a scope and CFS is preferred over
  OFS. The newest six periods are displayed. Operating margin is
  `operating_income / revenue * 100`; zero or missing revenue suppresses it.
  Scanner availability is cut off by the disclosure receipt date (`rcept_date`
  or the first eight digits of `rcept_no`), never by the later local
  `retrieved_at`; its as-of label is the latest eligible receipt date. A detail
  row whose operating income or net income exceeds revenue carries a
  `확인 필요` badge without changing the retained values.
- Korean cash dividends join `kr_equity_dividend` to the master ISIN. The most
  recent four events determine the trailing sum and displayed yield. When the
  newest record date has no payment date, the summary says `지급 예정` with
  the record date and `지급일 미공시`. Otherwise it shows the next quarter-end
  record date as `다음 기준일 (예상)` and does not repeat a paid quarter.
- Korean target consensus always displays: `국내 종목 컨센서스는 보관 가능한
  공개 출처가 없어 표시하지 않습니다.` U.S. target consensus uses the newest
  retained `research_target_price_consensus` vintage and otherwise says
  `미수집`.

Missing Korean fundamentals display `OpenDART 미수집 · 수집 후 표시`. U.S.
ETF fundamentals display `미국 ETF 재무 데이터 미보존`; U.S. dividends display
`배당 데이터 미보존`. Missing values remain `—` and are never inferred from a
different provider or security.

## Interaction

The first watchlist item is selected by default. `/stocks?symbol=005930`
selects that identity directly, including a catalog search result not already
in the watchlist. Sidebar selection fetches the detail without navigation and
updates the URL with `history.replaceState`. Sidebar sparklines are fetched once
after each `/api/stocks` refresh and use red for a 30-session rise, blue for a
decline, and neutral only when flat. Sidebar search waits 350 ms, requires two
characters, and ignores responses older than the latest input. Every U.S. ETF
uses its ticker as the row/headline title and its full fund name as subtitle.

Daily candles are aggregated client-side for weekly and monthly display.
MA5/20/60/120 and RSI14 are computed from the loaded candle window; RSI is a
separate pane with 30 and 70 guide lines and trading-index x positions aligned
to the candle series. Weekly/monthly labels state their bar unit. Monthly view
disables 3M/6M and forces at least 1Y. Candle and moving-average axes display
KRW as grouped integers and USD with two decimals. When the matching
`/api/stocks` row has `price_basis=provisional`, headline and chart basis labels
append `· 잠정`. The scanner remains canonical-only and explicitly labels
`정식 종가 기준 (MM-DD) · 잠정 미포함`.

The existing 관심종목 관리, 조건 설정, and 과매도 스캐너 controls remain below
the detail in collapsible sections with their original element IDs and API
routes.
