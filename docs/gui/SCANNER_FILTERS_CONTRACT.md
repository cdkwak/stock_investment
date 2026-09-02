# Oversold scanner liquidity and financial-health contract

Status: `DOCUMENTATION_ONLY / LIQUIDITY_INPUT_READY / FUNDAMENTALS_UNAVAILABLE`

Contract ID: `stock-exploratory-scanner-filters/v1`

## Boundary

This contract extends the practical scanner described by
[the existing scanner contract](STOCK_EXPLORATORY_SCANNER_CONTRACT.md). It does
not authorize provider calls or change `src/stock_web/`. The existing technical
candidate rule remains RSI14 <= 30 or close/SMA60 <= 0.8. Liquidity and
financial health are explanatory/filter axes over those candidates; they do
not alter source facts or silently manufacture missing values.

## Consumer columns

| Column | Type / unit | Required meaning |
|---|---|---|
| `avg_value_20d` | nullable number, KRW/session | Mean `trading_value` over the latest 20 distinct retained Korean-equity sessions at or before scanner `as_of`; null unless that symbol has all 20 observations |
| `market_cap` | nullable integer, KRW | Value on the latest retained market-cap session at or before scanner `as_of` |
| `debt_ratio` | nullable number, percent | `total_liabilities / total_equity * 100` for the selected consolidated fiscal quarter; null when equity is zero/negative or inputs/scope are unavailable |
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

## Value-trap label

The Korean label is `가치 함정 후보`. It is flagged exactly when:

```text
op_income_positive_4q is False OR debt_ratio > 200.0
```

The label is tri-state: `FLAGGED`, `NOT_FLAGGED`, or `UNAVAILABLE`. A null
operand does not become `False` or zero; the available operand may still decide
`FLAGGED`, otherwise the result is `UNAVAILABLE`. The UI must always show the
label/status together with the underlying debt ratio and four-quarter
operating-income status. It must never hide, drop, or silently reorder a
technical candidate. Net-income status and revenue trend remain visible
supporting facts but do not change this exact flag rule.

## Current availability

`avg_value_20d` and `market_cap` are available from accepted local Normalized
datasets with no new collection. Financial columns remain unavailable because
[the source review](../data/sources/FUNDAMENTALS_SOURCE_OPTIONS.md) found no
currently compliant Normalized source. The scanner must render that state and
must not fall back to KRX PER/PBR/EPS/BPS/DIV, Naver, FnGuide, or scraped KIND
pages.
