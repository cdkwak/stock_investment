# KRX MDCSTAT03501 Equity Fundamental Source Policy

역할: 이 문서는 source 의미·finality 권위이며, 동명 Raw queue 문서는 비실행 후보 경계만 소유한다.

Status: `OFFICIAL_SCREEN_CONFIRMED / CURRENT_DESCRIPTIVE_OBSERVATION_CAPTURED / PUBLICATION_REVISION_FINALITY_UNDOCUMENTED`

Scope: KRX `MDCSTAT03501` ALL-market daily equity-fundamental Raw response via
the repository's pinned pykrx adapter.

This source note records the boundary supported by primary official evidence.
It is not an active operation and authorizes no provider call, Landing mutation,
promotion, or scheduler change.

## Confirmed official boundary

- KRX Data Marketplace lists stock **PER/PBR/Dividend yield** information for
  all issues and individual issues. This establishes the economic subject of
  the repository's `MDCSTAT03501` adapter.
- KRX documents the regular securities market at 09:00-15:30 and post-market
  sessions extending through 18:00.
- Those market hours do not establish when the fundamental response is first
  available or when its source inputs stop changing.

Primary official references:

- KRX Data Marketplace, stock PER/PBR/Dividend yield menus:
  <https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd?locale=en>
- KRX Data Marketplace mobile stock menu:
  <https://data.krx.co.kr/contents/MMC/MAIN/main/index.cmd>
- KRX securities-market trading hours:
  <https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T1.jsp>

## Unresolved semantics

No reviewed primary official material specifies:

- the exact `MDCSTAT03501` publication timestamp;
- whether same-date or historical rows can be revised after filings or source
  corrections;
- a revision identifier, correction window, or freeze; or
- the meaning of duplicate issue codes in one response.

Therefore `15:30`, `18:00`, or next-session availability cannot be treated as
endpoint finality. PER/PBR/dividend-yield menu labels do not establish the unit,
multiplier, accounting-period alignment, or PIT availability of every provider
field in the retained payload. Raw numeric text and literal `-` missing tokens
remain lossless and unnormalized.

## Duplicate row policy

Retained bounded evidence already demonstrates that provider responses may
contain repeated issue codes, including a 2008-03-28 `020560` pair whose BPS
text differs. This is local provider evidence, not an official duplicate rule.

- Never collapse, select, average, fill, or overwrite duplicate rows.
- `source_row_ordinal` is the 1-based identity within one immutable response.
- The ordinal is not assumed stable across separate captures.
- Record every duplicate ordinal, exact/conflicting classification, and
  differing provider field.
- The contract key is `(market_date, source_row_ordinal)`, not date-symbol.

## Bounded observation plan

The next evidence-producing step is a separately authorized campaign:

1. Select three completed XKRX sessions prospectively.
2. For each exact session, retain one response after the official 18:00
   post-market boundary, one before the next regular session, and one after five
   additional completed XKRX sessions.
3. Each observation is one ALL-market exact-date business call with retry zero;
   the total campaign budget is nine calls.
4. Compare immutable hashes, provider rows, literal missing tokens, and
   duplicate groups. Ordinals are compared only within each response.
5. Retain observations in Landing only. Do not update accepted incremental
   state, Health, Normalized, Canonical, valuation, Backtest, or scheduler state.

These are experimental comparison windows, not provider-availability or freeze
claims. A stable nine-observation result remains bounded empirical evidence and
does not become an official revision guarantee.

## Current descriptive observation boundary

On 2026-08-27 KST, the retry-zero current-observation provider retained one
immutable ALL-market response for market date 2026-08-25: 2,719 rows, 2,719
distinct source symbols, zero duplicate groups, 1,174 missing PER/EPS values
and 202 missing PBR/BPS values. The response and sanitized provenance are under
`data/landing/kr_equity_fundamental_current_observation/date=2026-08-25/`.

This first-observed artifact may support exact-date descriptive PER/PBR display
only. It has `finality=UNKNOWN`,
`pit_status=PIT_LIMITED_FIRST_OBSERVED_ONLY`, `predictive_use=false` and no
Normalized write. Bundle contract v4 schedules the same Landing-only boundary
at 09:10, one missing completed session per natural occurrence with retry zero;
valid-empty preserves prior evidence and waits until the next natural
occurrence. It does not close the prospective
revision campaign, historical PIT, Forward EPS, relative-value, value-trap or
Backtest gates.

## Closure requirement

The lead must review the complete campaign, select an explicit operational
revision policy, and adopt a typed finality evidence identifier. `DATA_STATUS`
must then select one exact completed date under an active runbook. Until that
point the default source policy remains fail-closed while verified retained-date
replay remains API zero.
