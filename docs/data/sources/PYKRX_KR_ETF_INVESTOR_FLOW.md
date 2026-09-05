# pykrx KRX ETF investor flow

`kr_etf_investor_flow_daily` uses the ETF-specific pykrx overload backed by
KRX Data Marketplace `MDCSTAT04902` (`13207`):

```python
stock.get_etf_trading_volume_and_value(
    start, end, ticker, "거래대금", "순매수",
)
```

The exact provider columns are `기관`, `기타법인`, `개인`, `외국인`, `전체`.
They must not be interpreted as the equity endpoint's `기관합계` or
`외국인합계`. Values are signed KRW net-purchase amounts and are retained as
as-retrieved descriptive observations; revision finality and predictive PIT
safety are not claimed.

Each business call is limited to at most 10 calendar days. The symbol universe
is the same bounded union used by `KR_ETF_PRICE_DAILY`: watchlist ETFs plus ETFs
held in manual accounts (and retained ETF master symbols), capped at 10. Every
response is written to immutable Landing and read back before atomic Normalized
promotion. Existing `(date, symbol)` values may not change silently.

The lane is `MANUAL_READY` with automation disabled until the coordinator's
first live run confirms availability and publication timing. `16:20 KST` on
weekdays is the proposed post-close task time supplied for this onboarding; it
is not yet an evidenced provider-finality claim. The first receipt should be
checked for the current XKRX session before enabling that schedule.
