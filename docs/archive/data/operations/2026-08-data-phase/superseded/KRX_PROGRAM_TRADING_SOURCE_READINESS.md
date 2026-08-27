# C012 KRX program-trading source readiness

Status: official source candidate confirmed; executable pilot blocked on the
unverified request/response contract. No network request was made.

## What is verified locally

Installed pykrx 1.2.8 ships KRX's menu metadata in
`pykrx/website/path_bld_information.json`. The UTF-8 record is:

- menu ID: `MDC0201020305`;
- menu/screen: `프로그램매매`, `MDCSTAT026`, screen number `12012`;
- official menu path: `통계 > 기본 통계 > 주식 > 거래실적 > 프로그램매매`;
- business operation: `dbms/MDC/STAT/standard/MDCSTAT02601`;
- response data key: `output`.

This confirms that an official KRX program-trading operation exists. It is not
wrapped by pykrx 1.2.8: neither `website/krx/market/core.py` nor
`website/krx/market/wrap.py` defines `MDCSTAT02601` or a program-trading
function. No retained response or fixture contains this operation.

## What is not verified

The installed metadata does not document the request parameters, market/date
grain, row key, response fields, units, chunk/range limit, or empty behavior.
The generic menu name does not prove whether the response is:

1. one market aggregate per date;
2. all symbols for one market/date;
3. another market/range aggregate; or
4. a symbol-filtered result.

Consequently no direct `KrxWebIo` class, manual live runner, parser, or
DatasetContract is safe to implement yet. Guessing familiar parameters such as
`mktId`, `strtDd`, or `endDd` would violate the source-schema rule.

## Relationship to the Toss blocker

The Toss draft contract `kr_equity_program_trading_daily` has per-symbol/date
grain and four source-documented volume fields: arbitrage buy/sell and
non-arbitrage buy/sell. Those Toss semantics cannot be assigned to
`MDCSTAT02601` without an official response.

If the KRX operation is market aggregate by date, the correct output would be a
new provider-bounded candidate such as `kr_market_program_trading_daily`, not a
silent replacement of the per-symbol Toss contract. If it returns all symbols
for a market/date, it may safely replace the Toss fan-out path after schema and
unit verification. Either shape would materially reduce the blocker; only a
current-symbol-filtered shape would retain the survivorship problem.

## Required post-A007 discovery gate

After D confirms A007 stopped, no KRX process exists, and the shared D-owned KRX
lock is absent:

1. Inspect the official `MDCSTAT026` screen/form or its locally captured loader
   definition and record the exact parameter names, allowed market values, date
   controls, and response labels. Do not submit guessed parameters.
2. Define an immutable diagnostic matrix only after step 1. The intended upper
   bound is four business calls: recent KOSPI, recent KOSDAQ, 2008-01-02 KOSPI,
   and 2008-01-02 KOSDAQ, reduced if the verified operation is market-agnostic.
3. Use one authenticated process, the shared D-owned KRX lock, retry=0,
   parallelism=1, 20-second timeout, 8–10 second business throttle, business
   cap 4, and raw HTTP cap 12 including authentication and screen-contract
   discovery overhead.
4. Retain exact non-auth response bodies, SHA-256, sanitized scopes, redacted
   append-only call ledger, manifest, and atomic checkpoint under diagnostic
   Landing. Never retain login bodies, cookies, credentials, or auth headers.
5. Stop immediately on 403/429, any other non-200 response, HTML/restriction
   content, non-JSON, source error payload, missing `output`, unapproved endpoint,
   raw-cap exhaustion, or field drift.

The recent canonical date is 2026-08-10. The historical canonical trading date
is 2008-01-02 and is only a source-coverage sentinel, not an earliest-date
claim.

## Empty, survivorship, and PIT policy

A recent successful request returning `output=[]` is anomalous until the
official form proves that the chosen scope legitimately has no records. A
historical successful JSON response with an empty `output` is
`COVERAGE_EMPTY`; it is not an HTTP/parser failure and must not create zero
rows. Weekend behavior is deferred until the operation's date controls are
known.

A market/date aggregate has no symbol survivorship dependency. An all-symbol
market/date response is also survivorship-safe because it preserves that day's
source universe. A symbol endpoint or present-day symbol fan-out is rejected.

The source date must not be treated as the original availability date. KRX
publication timing, revisions, and whether historical responses are as-revised
remain unknown. Predictive use is blocked until those semantics are verified.

## Promotion gate

Only a completed bounded diagnostic may establish field names and units.
Source labels for quantities, monetary amounts, ratios, arbitrage, and
non-arbitrage must be preserved exactly before English mappings are proposed.
Pilot success permits a separate contract/parser review; it does not authorize
historical automation.
