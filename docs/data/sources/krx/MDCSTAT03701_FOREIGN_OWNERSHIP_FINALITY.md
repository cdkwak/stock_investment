# KRX MDCSTAT03701 Foreign Ownership Source Policy

Status: `OFFICIAL_SCREEN_CONFIRMED / PUBLICATION_FINALITY_UNDOCUMENTED`

Scope: KRX `MDCSTAT03701` ALL-market daily foreign-ownership Raw response via
the repository's pinned pykrx adapter.

This source note defines what primary official evidence supports. It is not an
active operation and does not authorize a provider call, Landing mutation,
promotion, or scheduler change.

## Confirmed official boundary

- KRX Data Marketplace lists **Foreign ownership** and **Foreign ownership by
  issue**. The latter is the economic subject represented by this repository's
  `MDCSTAT03701` adapter and ALL-market exact-date request.
- KRX documents the regular securities market at 09:00-15:30 and post-market
  sessions extending through 18:00.
- Those market hours do not establish when the `MDCSTAT03701` response is first
  published, whether it can be corrected, or when its bytes become final.

Primary official references:

- KRX Data Marketplace, Foreign ownership menus:
  <https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd?locale=en>
- KRX Data Marketplace, Foreign ownership by issue:
  <https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020504>
- KRX securities-market trading hours:
  <https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T1.jsp>

## Unresolved semantics

No primary official material reviewed for this boundary specifies:

- the exact endpoint publication timestamp;
- a correction or revision window;
- a revision identifier or finality flag; or
- a time after which the exact-date response is guaranteed frozen.

Therefore `15:30`, `18:00`, or the next XKRX session must not be converted into
an endpoint finality rule. Provider field names are retained as lossless Raw
text; this source note does not infer their numeric unit, multiplier, or
Normalized meaning. The LS cross-check is not equivalent and is not a fallback.

## Bounded observation plan

The next evidence-producing step is a separately authorized observation
campaign, not a daily operation:

1. Select three completed XKRX sessions prospectively.
2. For each session, capture one immutable response in a fixed window after the
   official 18:00 post-market boundary and one before the next regular session.
3. Each observation is one ALL-market exact-date business call, retry zero.
   The total campaign budget is six calls.
4. Hash and compare the two responses for every session; record first
   availability, row/schema validity, and any byte or row revision.
5. Retain the observations in Landing only. Do not update accepted incremental
   state, Health, Normalized, Canonical, GUI, or scheduler state.

The windows are experimental comparison points. They are not claims that the
provider is available or final at those times. Any empty, restriction, source
error, malformed, duplicate, null, or changed response remains evidence and
cannot be promoted.

## Closure requirement

The lead must review all six immutable observations and adopt a typed
publication/finality evidence identifier before `execution_authorized` can
become true. `DATA_STATUS` must then select one exact completed date under an
active runbook. Until then the default source policy remains fail-closed while
verified retained-date replay remains API zero.
