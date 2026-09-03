# Korean watchlist ETF daily operation

Status: `MANUAL_ONLY / AUTOMATION_DISABLED / NOT_YET_COLLECTED / PIT_BLOCKED`.

This operation retains current-list identity and daily prices for explicitly
requested Korean ETFs. It does not replace or promote the retained full-market
Raw `kr_etf_universe_daily` and `kr_etf_ohlcv_daily` research artifacts.

## Contracts

- `kr_etf_master`: `symbol`, `name`, provider-level market `KRX`, constant
  `security_type=ETF`, current `listing_status`, nullable `listing_date`,
  `leverage_multiple`, and source provenance.
- `kr_etf_price_daily`: `date`, `symbol`, provider-native OHLC, volume,
  trading value, nullable NAV, and source provenance. Valid zero no-trade values
  are retained.

The selected pykrx calls do not expose a KOSPI/KOSDAQ board or listing date, so
the master records `market=KRX` and null listing date. Exposure is the only
name-derived field: a whitespace-insensitive `인버스2X` token maps to `-2`;
otherwise `레버리지` or `2X` maps to `2`; every other name maps to `1`. No
other wording is interpreted.

## Bounded Landing-first flow

`scripts/manual/collect/refresh_kr_etf_daily.py` requires explicit `--symbols`,
`--start`, and `--end`. One occurrence permits at most 10 symbols and 10
calendar days, uses no retries, and performs exactly `1 + 2 × symbol_count`
provider calls:

1. `get_etf_ticker_list(end)` proves exact-date current membership;
2. `get_etf_ticker_name(symbol)` captures each identity;
3. `get_etf_ohlcv_by_date(start, end, symbol)` captures each price frame,
   including NAV when provided.

Every provider-returned list, name, and frame is written to immutable Landing,
read back, hashed, and recorded in the run checkpoint before normalization.
Validated master and price tables use atomic Parquet generations followed by
contract read-back. Durable request-key state makes an identical successful
replay a pre-network no-op. Overlapping identity or price conflicts fail closed
without overwriting prior valid data.

`get_etf_portfolio_deposit_file` is not called: portfolio holdings are not part
of either contract. Current-list membership must never be backprojected into a
historical universe, and as-retrieved values do not establish revision finality
or predictive PIT safety.

No Windows task is installed. Automation remains disabled until the first live
receipt and normalized outputs are reviewed.
