# B006 V-KOSPI 200 source and definition audit

## Decision

**Classification: PILOT_READY.** The official economic identity and a plausible
authenticated KRX daily-index route are known, but this audit did not verify the
source index code, public historical start, returned field set, correction policy,
or full calculation methodology. Those facts require a bounded pilot after A007
releases the single KRX request stream. No KRX, pykrx, or data API request was made
for this audit.

Formula reproduction from retained KOSPI 200 options is **SOURCE_BLOCKED**. The
official material located here does not specify enough calculation detail to
reproduce the index without guessing.

## Verified identity and definition

- The official name is **V-KOSPI 200**; KRX also calls it the **KOSPI 200
  Volatility Index**. `VKOSPI` should be treated only as
  an alias unless the source exposes a separately identified series.
- KRX says the index uses KOSPI 200 option prices and represents the expected
  volatility of the KOSPI 200 over the next **30 calendar days**.
- KRX announced/launched V-KOSPI 200 in **2009**. This establishes the
  publication milestone, not the first downloadable observation. No claim of
  backfilled coverage is made.
- KRX displays the related futures price in **points**. The futures product was
  listed on 2014-11-17 and is not the spot index dataset.
- The volatility-index futures final settlement price is the final V-KOSPI 200
  value on its last trading day. The last trading day is tied to 30 calendar days
  before the following month's regular KOSPI 200 option expiry, subject to the
  exchange holiday rule.

The official pages located did **not** verify the strike-selection rule,
zero-bid cutoff, forward extraction, interpolation, risk-free-rate input,
annualization constant, weekly-versus-monthly option treatment, rounding,
intraday calculation frequency, or methodology-revision history. `index_points`
is therefore the safe storage unit; this audit does not relabel the value as a
percent or reconstruct it from options.

## Evidence-backed source matrix

| Rank | Source / route | Verified evidence | Coverage / frequency | Use and remaining gate |
|---:|---|---|---|---|
| 1 | KRX individual-index daily history; pykrx candidate operation `stock.get_index_ohlcv_by_date`, internally associated locally with KRX BLD `MDCSTAT00301` | KRX is the index owner; local pykrx 1.2.8 code exposes an authenticated daily individual-index operation | Exact V-KOSPI 200 group/code, earliest date, range limits, and returned fields are not locally verified | Primary candidate for Normalized daily observations. Run a bounded authenticated pilot only after A007. |
| 2 | FSC derivatives `getStockFuturesPriceInfo` | A retained 2022-09-19 response has 11 `?뚯깮 ?좊Ъ 蹂?숈꽦吏?? rows whose `sptPrc` is consistently 19.64 across six outright and five spread contracts | One retained source date only; this is a futures-market response, not a dedicated index history | Useful as an independent same-date cross-check, not as the canonical spot-index source. Its historical coverage remains unknown. |
| 3 | KRX historical market-data / index-business request | KRX states that it distributes real-time and historical market data and publishes index-business and market-data contacts | Availability, delivery format, charge, permitted use, and history are request-specific | Escalation route if the authenticated public history is absent or insufficient; also request the official methodology/revision record. |
| 4 | Offline reconstruction from retained KOSPI 200 options | The repository has retained official/legacy option contracts | Option coverage alone does not supply the unverified methodology inputs and rules above | Do not implement. Reconsider only after obtaining an authoritative methodology and validating every required input. |

Official evidence:

- [KRX product description: future 30-day volatility from KOSPI 200 option prices](https://open.krx.co.kr/contents/OPN/01/01040301/OPN01040301T1.jsp)
- [KRX index-business history: V-KOSPI 200 launched in 2009](https://open.krx.co.kr/contents/OPN/02/02010100/OPN02010100.jsp)
- [KRX global futures specification and 2014-11-17 listing date](https://global.krx.co.kr/contents/GLB/02/0201/0201040301/GLB0201040301T1.jsp)
- [KRX rule: underlying definition and final-settlement semantics](https://law.krx.co.kr/las/RefBon.jsp?lawid=000114&lawkd=B&pubdt=20241101&pubno=0000022630&reflinkchk=Y)
- [KRX market-data distribution products](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp)
- [KRX organization and index/data contacts](https://global.krx.co.kr/contents/GLB/01/0103/0103010000/GLB0103010000.jsp)

An index or market-data license may be required for redistribution or product
use. The public pages do not establish that an internal bounded historical
download itself requires a paid license, so licensing and technical access must
remain separate decisions.

## Retained local evidence

There is no dedicated V-KOSPI 200 contract, state, checkpoint, Landing tree, or
Normalized/Derived/Published dataset in the repository.

The only retained observation found is indirect:

| Artifact | Measured result |
|---|---|
| FSC general derivatives Landing, 2022-09-19 | 3,591 total rows; 11 volatility-index futures rows |
| Volatility rows | 6 outright codes and 5 spread codes |
| Repeated spot field | `sptPrc = 19.64` on all 11 rows |
| Futures-specific fields | outright settlement 19.65; one outright contract has open interest 117; most other values are zero |

This proves only that an official derivatives response may carry an indirect
same-day spot value. It does not prove daily historical coverage, unique index
identity, or an index OHLC schema. Futures contract rows must never be mixed
into the spot-index dataset.

## Draft contract boundary

Dataset name: `kr_vkospi200_daily`  
Layer: Normalized  
Frequency: exchange trading day, end of day  
Timezone: `Asia/Seoul`

Use one of the following only after the pilot establishes the response shape:

1. If KRX returns daily OHLC, primary key `(date, index_code)` with source-native
   `open`, `high`, `low`, `close` as nullable `float64`, unit `index_points`.
2. If KRX returns only the final daily index, primary key `(date, index_code)`
   with a single `value` field. Do not fabricate OHLC aliases.

Required provenance fields are `source`, `source_operation`, `source_date`,
`captured_at`, immutable Landing body hash, and the source-returned index name.
`index_code` must be resolved from official source metadata; it must not be
hard-coded from memory. Preserve source-supplied changes or rates as separate
fields with their source unit; do not recompute or relabel them silently.

## Point-in-time and revision policy

- A final daily observation is predictive-use eligible no earlier than the next
  trading decision after the official final value is available. Treat same-day
  use as blocked unless a separately timestamped intraday contract is created.
- `source_date` is not automatically a knowledge timestamp. Record `captured_at`
  and verify any source publication/cutoff time during the pilot.
- The correction and historical-revision policy is unknown. Keep immutable
  Landing captures and append observation versions until source behavior is
  established; do not silently overwrite a prior body.
- A volatility-futures final settlement value has special contract semantics and
  is only a validation observation. It does not define the daily index series.

## Post-A007 bounded pilot

Run sequentially, with zero retries and a hard cap of **3 business requests**
(authentication traffic separately ledgered; hard raw-call cap **8**):

1. Resolve the current official V-KOSPI 200 index group/code from authenticated
   KRX metadata.
2. Request one recent trading date through the individual-index daily route.
3. Request one trading date in the 2009 launch year to test historical reach;
   an empty result is evidence, not a retry trigger.

Landing must precede parsing. The ledger must record request identity, response
hash, HTTP/content classification, row count, timestamps, and checkpoint
position. Stop on HTML, authentication anomaly, non-200, or ambiguous index
identity. Verify source fields, units, PK uniqueness, dates, null/zero handling,
and whether OHLC or final-only values are provided. Do not infer the earliest
date from the two probes and do not enable bulk history until a separately
budgeted coverage pilot and checkpoint plan pass.

An optional FSC same-date cross-check is a separate later approval; it must not
run in parallel with KRX and is unnecessary if an already retained matching date
becomes available.

## Remaining blockers

- Official V-KOSPI 200 index group/code on the authenticated historical route.
- First available date and whether the source supplies backfilled history.
- Exact response fields, units, missing-day behavior, and maximum request span.
- End-of-day publication/cutoff semantics and historical correction behavior.
- Authoritative detailed methodology and revision history.
- Redistribution/licensing terms if the Published layer will leave internal use.
