# pykrx High-Value Acquisition Optimization

Current evidence record. It is not an instruction to rerun retained pilots.

| Dataset | Best verified request shape | Historical disposition |
|---|---|---|
| Index fundamentals | `MDCSTAT00702`, one index and <=730-day range | Raw backfill complete in 70 calls instead of ~12,267 daily group calls |
| Foreign ownership | `MDCSTAT03701`, `mktId=ALL`, one market date | Ready plan: ~6,559 calls from complete-field 2000 floor; per-symbol range is rejected as higher-call and survivorship-sensitive |
| Equity fundamentals | `MDCSTAT03501`, `mktId=ALL`, one market date | Ready plan: ~4,590 calls from observed 2008 floor; per-symbol range is higher-call and identity-sensitive |
| ETF universe/OHLCV/NAV | `MDCSTAT04301`, one full-market date | Ready plan: ~4,590 calls from observed 2008 floor; per-ETF ranges cannot reconstruct a PIT universe cheaply |
| Index constituents | `MDCSTAT00601`, one index/date | PIT blocked until exact rebalance/effective dates are retained; daily brute force prohibited |
| Sector classification | `MDCSTAT03901`, one market/date | Historical values differ from current values, but effective dates remain unresolved; no backfill |

## Retained evidence

- Index range Raw: `data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T015855Z_5c6ec1d853f445e4aabdb076e2700a73/`.
  KOSPI, KOSPI200 and KOSDAQ each contain 6,559 unique dates from 2000-01-04;
  KOSDAQ150 and KRX300 each contain 4,089 unique dates from 2010-01-04. Total
  27,855 rows, 70 business calls, 75 raw HTTP responses, retry zero. KOSDAQ150
  and KRX300 pre-launch-looking histories are source backcasts and are not PIT-safe.
- ALL-market and sector pilot: `data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85/`.
  Foreign ALL returned 2,871 / 2,475 / 1,381 rows on 2026-08-12,
  2020-01-02 and 2000-01-05. Equity fundamentals ALL returned 2,715 / 2,283 /
  1,879 rows on 2026-08-12, 2020-01-02 and 2008-01-03. Sector responses
  returned KOSPI 2020=916, KOSDAQ 2020=1,399 and KOSDAQ recent=1,820 rows.
  Among 842 symbols common to KOSPI 2020 and the retained recent KOSPI response,
  96 source classifications differ, proving the endpoint does not simply return
  one current classification for every historical date. Exact change dates remain
  unresolved.

All captures used one KRX stream, the shared lock, retry zero, Landing-first raw
bodies, append-only ledger/checkpoint hashes, and 3.0--4.0 second request jitter.
No Normalized dataset was written.
