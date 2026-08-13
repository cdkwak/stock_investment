# KRX free-source backlog

Status: **RANKED / AUTOMATION_PERMISSION_GATE**

Current KRX Data Marketplace terms prohibit unauthorized automated collection and
copying. The following official free candidates are discovery priorities only; no
authenticated pilot or bulk run is authorized without retained KRX permission or
an explicitly authorized official interface.

1. Pre-2010 futures basis `[15010]` and option P/C ratio `[15012]`: high value,
   already verified from inception, about seven two-year chunks each.
2. Dedicated option implied-volatility trend: potentially efficient, but exact
   grain/range/fields remain unverified. Do not duplicate contract IV already in
   option rows.
3. V-KOSPI 200 daily index: high value and likely low cost; code, start, and schema
   still require an official bounded check.
4. Program trading (`MDCSTAT02601`): high value and no accepted artifact; exact
   grain/fields/units remain unverified.
5. Equity valuation/fundamentals and foreign ownership: useful PIT candidates but
   historically expensive, with availability/revision semantics unresolved.
6. ETF OHLCV/NAV: full-market/date route is survivorship-safe, but historical
   boundary and PIT semantics remain unverified.

Do not create separate open-interest or contract-implied-volatility datasets: those
fields already belong to the futures/options contract datasets. Lower-value aggregate
participation/liquidity and KRX corporate-action statistics remain behind the above
gaps and OpenDART/data.go.kr corporate-action evidence.

- [KRX Data Marketplace terms](https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd)
- [Data status](../project/DATA_STATUS.md)
