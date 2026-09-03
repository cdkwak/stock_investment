# Oversold scanner liquidity and financial-health contract

Status: `IMPLEMENTED_PROVIDER_FREE / LIQUIDITY_DEFAULT_FILTER / FUNDAMENTALS_PARTIAL`

Contract ID: `stock-exploratory-scanner-filters/v2`

## Boundary

This contract extends the practical scanner described by
[the existing scanner contract](STOCK_EXPLORATORY_SCANNER_CONTRACT.md). It does
not authorize provider calls. The existing technical candidate rule remains
RSI14 <= 30 or close/SMA60 <= 0.8. Liquidity and financial health are
explanatory/filter axes over those candidates; they do not alter source facts
or silently manufacture missing values. The resulting surface is an
`설명용 관찰 목록`; it is not a recommendation, suitability judgment, expected
return, portfolio weight, or order signal (`추천/주문 신호 아님`).

Saved `watchlist`-scope conditions remain scoped to watchlist rows. Only saved
`universe`-scope conditions participate in the scanner's existing additional
candidate rule; neither scope changes the liquidity or financial-health meaning.

## Consumer columns

| Column | Type / unit | Required meaning |
|---|---|---|
| `avg_value_20d` | nullable number, KRW/session | Mean `trading_value` over the latest 20 distinct retained Korean-equity sessions at or before scanner `as_of`; null unless that symbol has all 20 observations |
| `market_cap` | nullable integer, KRW | Value on the latest retained market-cap session at or before scanner `as_of` |
| `debt_ratio_pct` | nullable number, percent | `total_liabilities / total_equity * 100` for the selected consolidated fiscal quarter; null when equity is zero/negative or inputs/scope are unavailable |
| `op_income_positive_4q` | nullable boolean | `True` only when each of the four latest discrete quarterly operating-income values is > 0; `False` when all four are known and at least one is <= 0 |
| `net_income_positive_4q` | nullable boolean | Same four-discrete-quarter rule for net income attributable to the selected statement scope |
| `revenue_trend` | nullable enum | `INCREASING`, `DECLINING`, `FLAT`, `MIXED`, or `UNAVAILABLE`, using the four latest discrete quarterly revenue values in chronological order |
| `fundamentals_as_of` | nullable date or aware timestamp | Latest instant/date when the exact filing set and revisions used by all financial fields were available; never the fiscal-period end or local file mtime |

`revenue_trend` is `INCREASING` for non-decreasing values with at least one
increase, `DECLINING` for non-increasing values with at least one decrease,
`FLAT` when all four values are equal, and `MIXED` otherwise. Fewer than four
valid comparable discrete quarters yields `UNAVAILABLE`.

## As-of and join rules

- Join on exact normalized Korean `symbol`; never join issuer name text.
- All source observations must be at or before the requested scanner `as_of`.
- The liquidity helper is `stock_data.features.liquidity.liquidity_snapshot`.
- Missing liquidity or fundamental facts stay null with a visible unavailable
  state. They are never zero, `False`, or a shortened-window statistic.
- Consolidated (`CFS`) statements are preferred only under a future Data
  contract. Separate statements, currencies, units, fiscal calendars, amended
  filings, and cumulative interim values may not be mixed silently.
- `fundamentals_as_of` must accompany every non-null financial-health result;
  a future `fundamentals_as_of` or unknown revision/availability state clears
  all four financial-health outputs.

## Liquidity filter and API

The server annotates technical candidates first, then applies the default
liquidity gate before `count`, `top`, and `candidates` are returned:

```text
avg_value_20d >= 1,000,000,000 KRW
AND market_cap >= 100,000,000,000 KRW
```

`/api/scanner` accepts `min_value` and `min_cap` overrides in KRW. `all=1`
disables the liquidity gate without disabling annotations. The payload exposes
the effective KRW thresholds and decision as
`filters.avg_value_20d_min`, `filters.market_cap_min`, and `filters.applied`.
These inputs are part of the cache key. Cache schema v2 retains the existing
envelope shape while invalidating v1 results.

If `liquidity_snapshot` raises or returns an empty frame, the scanner remains
available, every liquidity value is null, `filters.applied` is false, and the
visible note says `유동성 데이터 없음 · 필터 미적용`. A non-empty partial
snapshot is not an inner join: missing symbol facts remain null and therefore
do not satisfy an enabled threshold.

## Financial-health presentation

Financial health is not a server-side hard filter. The scanner always emits
the five nullable source fields and displays debt ratio, four-quarter operating
income status, four-quarter net-income status, and revenue trend. A candidate
without a retained helper row displays `미수집`, not fail or zero. The
`재무 수집됨만` control is a reversible client-side view filter and does not
change the server `count` or candidate order.

`fundamentals_coverage` reports `available`, `total`, and the latest retained
`as_of`. Availability means the candidate has a valid `fundamentals_as_of`;
`total` is the post-liquidity server candidate count.

## Value-trap label

The Korean label is `가치 함정 후보`. It is flagged exactly when:

```text
op_income_positive_4q is False OR debt_ratio_pct > 200.0
```

The label is tri-state: `FLAGGED`, `NOT_FLAGGED`, or `UNAVAILABLE`. A null
operand does not become `False` or zero; the available operand may still decide
`FLAGGED`, otherwise the result is `UNAVAILABLE`. The UI always shows the
label/status together with the underlying debt ratio and four-quarter
operating-income status. It never uses the label to hide, drop, or reorder a
technical candidate. Net-income status and revenue trend remain visible
supporting facts but do not change this exact flag rule.

## Current availability

`avg_value_20d` and `market_cap` are provided by
`stock_data.features.liquidity.liquidity_snapshot` from accepted local
Normalized datasets with no new collection. Financial columns are provided by
`stock_data.orchestration.kr_fundamentals_quarterly.fundamental_health` from
retained OpenDART rows. Coverage is currently limited to collected watchlist
symbols; all other symbols remain `미수집`. The scanner must not fall back to
KRX PER/PBR/EPS/BPS/DIV, Naver, FnGuide, or scraped KIND pages for these
financial-health facts.
