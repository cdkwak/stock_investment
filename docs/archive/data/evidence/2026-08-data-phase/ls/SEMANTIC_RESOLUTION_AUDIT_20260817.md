# Semantic Resolution Audit — 2026-08-17

## Scope and controls

This is a semantic audit. Its initial work used provider documentation and
retained Landing/audit evidence only. The later, separately documented KRX
[12012] replication used three authenticated official **web-screen** views;
it made no KRX API or pykrx call. No Landing, state, Raw, Normalized, or
Canonical data was changed. In particular, the active KRX Fundamental
collector, its lock, and its state/Landing namespace were not inspected or
touched.

`CONFIRMED_OFFICIAL` below means a direct provider documentation statement.
Cross-source agreement, even over many dates, is never promoted to that status.

## Results

| Item | Current status | Official evidence | Retained repository evidence / cross-source evidence | Resolved meaning | Confidence / status | Remaining blocker | Backtest allowed | Normalized allowed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LS `t1633` selector and categories | `INFERRED` | The LS OpenAPI guide documents `gubun1=0` as amount and `gubun1=1` as quantity, and labels total/arbitrage/non-arbitrage buy, sell, and net fields. | 54 lossless response files give 6,174 KOSPI and 5,280 KOSDAQ unique dates for each measure.  Re-reading all pages found no conflicting duplicate date; `buy-sell=net`, and `arbitrage+non-arbitrage=total`, each within one source unit on every scope/date. | Field families are amount versus quantity and the three programme-trading categories reconcile subject to integer display rounding. | Field/category labels: `CONFIRMED_OFFICIAL`; reconciliation: `INFERRED_MULTI_DATE`. | The guide does not state field units, multipliers, or whether a multiplier is invariant by market/time. | No for unit-sensitive use. | No. |
| LS `t1633` amount unit / multiplier | `CONFIRMED_EMPIRICAL_MULTI_DATE` | No LS guide unit or multiplier was found for `tot*`, `cha*`, `bcha*`, or `volume`. Official KB IVSA0070 documentation labels only `mprft_nt_b` (arbitrage net purchase) and `nmp_nt_b` (non-arbitrage net purchase), both `String(15)` with blank unit/description columns. | The KRX [12012] multi-date replication below compares `All` market with LS KOSPI+KOSDAQ for 2026-08-14, 2026-08-13, and 2026-07-31. Every amount field is exact or within one displayed source unit; no field differs by two or more. | LS amount is million KRW per source unit. | `CONFIRMED_EMPIRICAL_MULTI_DATE`; not an LS-official statement. | Direct LS documentation/support is still required for `CONFIRMED_OFFICIAL_LS`; PIT/contract promotion gates are independent. | No. | No. |
| Toss program-trading quantity semantics | `CONFIRMED_OFFICIAL` | Toss officially documents daily per-KRX-symbol program trading; arbitrage/non-arbitrage buy/sell/net volumes are integer shares, `netBuyVolume=buyVolume-sellVolume`, and the source excludes NXT.  The history cursor is `until`; no amount field exists. | The bounded run below independently checked the net invariant on every exact-date returned record. | Toss is an exact per-symbol KRX quantity comparator when an exact-date record is available. | `CONFIRMED_OFFICIAL` for Toss fields only. | It has no historical PIT universe and is not a canonical historical source. | No as a standalone historical universe. | No. |
| LS `t1633` quantity unit / multiplier | `CONFIRMED_EMPIRICAL_MULTI_DATE` | LS calls the selected family quantity but gives no share/unit multiplier. | The KRX [12012] multi-date replication below compares `All` market with LS KOSPI+KOSDAQ for 2026-08-14, 2026-08-13, and 2026-07-31. Every quantity field is exact or within one displayed source unit; no field differs by two or more. | LS quantity is thousand shares per source unit. | `CONFIRMED_EMPIRICAL_MULTI_DATE`; not an LS-official statement. | Direct LS documentation/support is still required for `CONFIRMED_OFFICIAL_LS`; PIT/contract promotion gates are independent. | No. | No. |
| LS `t8462` D/N/U session semantics | `UNRESOLVED` | The LS guide defines `tm_rng` only as the code set `D/N/U`; it does not expand the codes. | The retained 18-response pilot covers K2I/MKI × futures/call/put × D/N/U over 263 dates.  Fresh offline recomputation shows `U=D+N` is not a global identity: depending on product/field only 22–93 of 263 dates have all fields exact.  `sv_08` happens to reconcile on all 263 dates, which cannot define the session codes. | `U` is *all-session-like* only for the retained amount cross-check; `D` and `N` have no resolved expansion, and `U` must not be treated as a calculated `D+N`. | `U` all-session-like: `INFERRED_MULTI_SOURCE`; D/N and universal U rule: `UNRESOLVED`. | LS documentation or support explicitly expanding D/N/U, including product-specific exceptions. | No for session-dependent/PIT use. | No. |
| LS `t8462` amount / quantity unit and institution aggregate | `INFERRED` | The LS guide labels `sv_*` as quantity, `sa_*` as amount, and identifies investor codes including individual, foreign, and institution total; it does not give unit/multiplier or signed-net convention. | Retained audit compared `U` `sa_* × 100` with retained official KRX all-session million-KRW values on 2026-01-02, 2026-07-31, and 2026-08-13: 12 points, max residual 45 million KRW (display rounding).  Raw arithmetic validates institution detail aggregation on most rows, but retained U option rows have 202 non-reconciling `sv_18` rows (2025-07-18..2025-12-23); this is preserved as `OPTION_SPECIFIC_SEMANTICS`, not repaired. | `sa_*` is plausibly 100 million KRW per source unit; `sv_*` is quantity with an unresolved contract/unit.  Institution total is provider-supplied and must not be reconstructed. | `sa_*`: `INFERRED_MULTI_DATE` (with cross-source support); `sv_*` unit: `UNRESOLVED`; labels: `CONFIRMED_OFFICIAL`. | LS direct unit and sign documentation; explanation for option-specific aggregate behaviour. | No. | No. |
| FINRA Daily Short Sale Volume decimals/footer | `UNRESOLVED` | FINRA says the Daily File contains aggregate daily total volume, total short-sale volume, and total short-exempt volume by security; its developer page describes the dataset as aggregate daily volume.  Neither source defines the historical file columns' data type/unit, whether fractional literals are valid, or a one-column footer. | Retained response `daily_short_sale_volume/.../response.body` (SHA-256 `a095212f…`) has 12,181 six-field rows plus one literal `12181`.  Decimal lexical forms occur in all three volume columns (ShortVolume 7,744 rows; ShortExemptVolume 144; TotalVolume 11,412). | The fields are daily aggregated volume categories; the retained decimal and footer representations cannot be coerced or reclassified without a provider schema. | `UNRESOLVED` / documentation audit status `DOCUMENTATION_INSUFFICIENT`. | FINRA file-format/schema statement or support confirmation of decimal representation, units, footer meaning, and any 2026 change. | No. | No. |
| CFTC historical `release_date` | `UNRESOLVED` | CFTC confirms COT records represent Tuesday positions and are generally released Friday 15:30 ET, subject to holidays.  Crucially, CFTC expressly says no historical release-date list exists; only the 13 months of reports published on its website have release dates. | Retained annual Historical Compressed archives retain native report/position date but no publication timestamp; established CFTC backfill policy keeps `release_date=null` and blocks predictive use.  CFTC historical announcements document exceptions and revisions, reinforcing that `position_date + 3 days` is unsafe. | The general schedule is known, but an annual-file row cannot receive a historical `release_date` by inference.  A record can become PIT-safe only if an authoritative, dated publication artifact is retained for that record. | General convention: `CONFIRMED_OFFICIAL`; every historical-row release timestamp: `UNRESOLVED`. | Official timestamped report/archive per record plus holiday/revision handling; the current 13-month schedule cannot reconstruct 1986–present. | No for retained history. | No. |

## Source and evidence references

- LS OpenAPI source inventory: `docs/data/sources/ls/LS_OPENAPI_SOURCE_INVENTORY.md`.
- LS derivative pilot and immutable audit: `docs/data/sources/ls/LS_OPENAPI_DERIVATIVES_PILOT.md`; `data/landing/ls_openapi/t8462_raw/20260814T165922Z_da488bc5fd024f559b0ef70f6d340e1f/audit.json`.
- FINRA offline audit: `docs/data/sources/finra/FINRA_OFFLINE_DOCUMENTATION_AUDIT_20260817.md`.
- CFTC historical policy: `docs/data/sources/cftc/CFTC_COT_HISTORICAL_PILOT.md` and `docs/data/sources/cftc/CFTC_LEGACY_COT_HISTORICAL_PILOT.md`.
- Official LS entrypoint: <https://openapi.ls-sec.co.kr/apiservice>.
- Official FINRA explanation: <https://www.finra.org/rules-guidance/notices/information-notice-051019>.
- Official FINRA developer dataset page: <https://developer.finra.org/docs/api-explorer/query_api-equity-reg_sho_daily_short_sale_volume>.
- Official CFTC COT FAQ and release policy: <https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm>.
- Official CFTC historical special announcements: <https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm>.

## Resolution routing

### Confirmed now

- LS `t1633` amount-versus-quantity selector and its documented programme category fields.
- LS `t1633` quantity = thousand shares and amount = million KRW as
  `CONFIRMED_EMPIRICAL_MULTI_DATE` from the separate official-KRX display
  comparison below. This is not `CONFIRMED_OFFICIAL_LS`.
- LS `t8462` field families (`sa_*` amount, `sv_*` quantity) and documented investor code labels.
- CFTC's general Tuesday observation / Friday 15:30 ET release convention, with holiday exceptions.
- FINRA Daily File's role as daily aggregated short-sale volume, distinct from short interest.

### Multi-date or multi-source inference only

- `t8462` `sa_*` tentative 100-million-KRW scaling (three dates/12 cross-source points) and `U`'s all-session-like behaviour.  None determines D/N semantics or authorizes `U=D+N`.

### Provider support questions

- LS: direct published unit/multiplier and sign convention for `t1633` amount/quantity are still needed only to elevate the empirical conclusion to `CONFIRMED_OFFICIAL_LS`; exact D/N/U expansion, `t8462` `sa_*`/`sv_*` units, and option aggregate behaviour.
- FINRA: authoritative CDN/Daily File schema covering the three volume columns, fractional lexical values, footer record count, and 2026 format history.
- CFTC: only a provider-held historical release-timestamp archive could improve individual historical records; the public FAQ says the aggregate list does not exist.

### Low-return follow-up

- Trying further arithmetic identities cannot establish LS units or session definitions; the existing broad retained samples already falsify a universal `U=D+N` rule.
- Recreating CFTC dates from the weekly convention would add synthetic, holiday-sensitive timestamps and is prohibited by the retained PIT policy.

## Addendum — bounded KB / Toss cross-provider validation

### Comparator eligibility

The official retained KB workbook (`current_b2c_all_api.xlsx`, worksheet 47,
SHA-256 `1a229821ccd6491096ea09476a779760df1988d5f112ac30120439b926496a24`)
defines IVSA0070 `mprft_nt_b` as *arbitrage net purchase* and `nmp_nt_b` as
*non-arbitrage net purchase*.  Both are `String(15)`; its unit and whether the
figure is amount or quantity are blank.  No total-program, buy, sell, quantity,
or amount field exists in this slice.

The only post-close retained IVSA0070 observation is the 2026-08-14 run.  Its
source `inq_dy_tm` is 20260814 and it differs from the same-day pre-open body,
but retained `slice_date_comparison.json` explicitly marks `program_trading` as
`CURRENT_DAY_CLOSE` **candidate pending offline review**.  It therefore cannot
be accepted as a same-`market_date` comparator under this audit.  The otherwise
notable, excluded values are: LS KOSPI amount arbitrage net `-113526` versus KB
`-113525`, and LS non-arbitrage net `160437` versus KB `160437`.  This is
recorded as a date-semantics lead only, not multiplier evidence.

Toss supplies official per-symbol KRX-only program-trading **share** quantities,
with arbitrage/non-arbitrage buy, sell, and net fields and `net=buy-sell`.
There is no amount field.  The historical universe limitation remains: Toss is
only a comparator, never a canonical historical source.

### Result

| Comparison target | Same-date eligible rows | Exact multiplier evidence | Verdict |
| --- | ---: | --- | --- |
| LS amount ↔ KB IVSA0070 | 0 | None; KB only exposes two net fields and their unit is undocumented. | `UNRESOLVED` |
| LS quantity ↔ Toss per-symbol | 4 source-date matches, but 0 market aggregates | None; per-symbol values cannot equal a market aggregate and Toss is survivorship-unsafe. | `UNRESOLVED` |
| LS ↔ KB ↔ Toss three-way | 0 | None; no common retained date and no qualified KB market date. | `UNRESOLVED` |

No retained official KRX `MDCSTAT02601` program-trading response exists in this
repository; only screen/operation metadata is retained.  No KRX request was
made.  Toss remains `CROSS_CHECK / SURVIVORSHIP_UNSAFE`, never the canonical
historical primary.

## Addendum — canonical-universe Toss quantity validation (2026-08-16)

The retained PIT-safe `kr_equity_canonical_universe_daily` was used directly;
no current-symbol discovery/fan-out was used.  The selected market date was
2025-01-02, before NXT trading, so the official Toss KRX-only scope does not
have an NXT overlap for this date.  The run used one OAuth request, 2,745
read-only Toss requests at a maximum 8.3 requests/second, and retry zero.  Each
response body was persisted before parsing with per-response SHA-256 and request
provenance under:

`data/landing/tossinvest/ls_t1633_quantity_validation/run=20260816T153912Z_c604e7210367403bbc2fbf2ee06ec183/`

All 2,745 retained body hashes match their provenance.  There were 2,613 HTTP
200 responses and 132 HTTP 404 responses.  Exact-date records were validated
for integer representation and both arbitrage/non-arbitrage `buy-sell=net`.

| Market | Canonical symbols | Exact-date, invariant-valid Toss records | HTTP 404 | 200 with no 2025-01-02 record | Accepted market aggregate / multiplier |
| --- | ---: | ---: | ---: | ---: | --- |
| KOSPI | 961 | 812 | 28 | 121 | None — 149 symbols unavailable. |
| KOSDAQ | 1,784 | 1,622 | 104 | 58 | None — 162 symbols unavailable. |

The partial sums are retained only as non-comparator diagnostics.  They were not
treated as market totals, and the exact-multiplier routine was intentionally not
called for either market.  There was no `×1`, `×10`, `×100`, `×1,000`,
`×10,000`, or `×1,000,000` trial.  Because the first date is incomplete, the
planned 3–5-date extension was not started. Final LS quantity status is
`UNRESOLVED` **for the Toss-comparator route**; LS amount is outside Toss
scope because Toss has no amount field. This limited result is superseded for
the LS unit question by the official-KRX multi-date comparison below.
`CONFIRMED_OFFICIAL` applies only to Toss quantity semantics, not to LS units.

## Addendum — 2025-01-02 Toss missing-symbol offline forensic audit

This follow-up made **zero network requests**. It re-read only the retained
Toss bodies/provenance from the canonical-universe run above, the 2025-01-02
PIT-safe canonical-universe partitions, and the matching retained daily-price
partitions. All 311 response-body SHA-256 values were recomputed and matched
their retained provenance. The full per-symbol evidence is retained at:

`data/state/audits/toss_ls_t1633_quantity_validation/20260816T153912Z_c604e7210367403bbc2fbf2ee06ec183_missing_forensic.json`

| Market | `SYMBOL_MAPPING_ISSUE` (HTTP 404) | `NO_EXACT_DATE_UNRESOLVED` | Total |
| --- | ---: | ---: | ---: |
| KOSPI | 28 | 121 | 149 |
| KOSDAQ | 104 | 58 | 162 |
| Total | 132 | 179 | 311 |

The official Toss guide defines the retained 404 as `stock-not-found` — the
requested stock was not found. That is direct evidence of a source-side lookup
failure only; it does not prove that a listed security was unsupported by
security type, delisted, suspended, or zero-program-volume. The audit therefore
labels these 132 rows `SYMBOL_MAPPING_ISSUE`, not zero and not an
unsupported-type conclusion.

The 179 HTTP-200 responses were split without inference: 117 had an empty
`records` result (KOSPI 113, KOSDAQ 4), while 62 returned a date earlier than
2025-01-02 (KOSPI 8, KOSDAQ 54). The latter records retain their individual
`closest_toss_date`; neither case establishes whether the target-date value was
zero, omitted, unavailable, or otherwise ineligible.

Every one of the 311 symbols was a valid canonical-universe member on the
target date and had a corresponding retained price row. Price volume was zero
for 98 rows, which is only possible suspension/no-trade evidence; it is not a
program-trading zero. The metadata contains 57 common-share flags, 116
preferred-share flags, 61 name-flagged SPACs, 2 name-flagged REITs, and no
ETF/ETN flags. Because the official program endpoint documentation inspected
does not state its security-type coverage, none is classified
`UNSUPPORTED_SECURITY_TYPE` from these observations alone.

| Classification | Count | Market-total treatment |
| --- | ---: | --- |
| `VERIFIED_ZERO_ELIGIBLE` | 0 | None — no official zero-row/omission rule. |
| `UNSUPPORTED_SECURITY_TYPE` | 0 | None — coverage is undocumented. |
| `SYMBOL_MAPPING_ISSUE` | 132 | Do not impute or exclude as zero. |
| `NO_EXACT_DATE_UNRESOLVED` | 179 | Do not impute or exclude as zero. |
| `OTHER_UNRESOLVED` | 0 | — |

Consequently no incomplete symbol is safely includable as a zero or safely
excludable from the 2025-01-02 market total. The existing partial Toss sums
remain invalid as LS `t1633` quantity-multiplier evidence, and the planned
additional 3–5-date Toss fan-out remains stopped.

Official documentation result: the meaning of `stock-not-found` is
`CONFIRMED_OFFICIAL`; the endpoint's zero-program-date behaviour, supported
Korean security-type range, and suspension/no-trade handling remain
`DOCUMENTATION_INSUFFICIENT`. See the [official Toss Open API guide](https://developers.tossinvest.com/docs).

## Addendum — KRX [12012] 2026-08-14 offline unit comparison

The user supplied an official KRX [12012] Program Trading screen for
2026-08-14 with market selection `All`. Its visible unit selectors state
quantity `thousand shares` and amount `million KRW`. No KRX request was made
for this audit. The comparison used the four retained, hash-verified LS t1633
responses for the same date (KOSPI and KOSDAQ, amount and quantity), summing
the two LS market rows only to match the KRX `All` scope:

- `001_kospi_amount_page_01.response.json` — SHA-256
  `30019c0a82787dc6c0094aa1a9da21c28687b58856560549d0c6430de1475941`
- `001_kospi_quantity_page_01.response.json` — SHA-256
  `16f0b20eca367f7bed903cb5217c799f4a2fef66d3f9e239a510d9acd3455281`
- `015_kosdaq_amount_page_01.response.json` — SHA-256
  `1f6284905083be4d6d116f62fb7c74591a345c5f39beb2a8db7620a113435c54`
- `027_kosdaq_quantity_page_01.response.json` — SHA-256
  `28efbcc4d5d40c50041a465d9f90cf541dddc79bf6e97798ba442bec0274e3c4`

Each retained LS hash exactly matches its adjacent provenance. LS fields map as
`*1=buy`, `*2=sell`, `*3=net`, with `cha*` arbitrage, `bcha*`
non-arbitrage, and `tot*` total. The following is a literal comparison; no
multiplier, rounding, or field value was changed.

| Family | Category / side | KRX screen | LS KOSPI + KOSDAQ | LS − KRX |
| --- | --- | ---: | ---: | ---: |
| Quantity | Arbitrage: sell, buy, net | 2,014; 1,285; -729 | 2,014; 1,285; -729 | 0; 0; 0 |
| Quantity | Non-arbitrage: sell, buy, net | 241,238; 244,908; 3,670 | 241,238; 244,908; 3,670 | 0; 0; 0 |
| Quantity | Total: sell, buy, net | 243,252; 246,193; 2,941 | 243,252; 246,192; 2,940 | 0; -1; -1 |
| Amount | Arbitrage: sell, buy, net | 365,458; 247,036; -118,422 | 365,458; 247,037; -118,422 | 0; +1; 0 |
| Amount | Non-arbitrage: sell, buy, net | 9,721,799; 9,825,895; 104,095 | 9,721,799; 9,825,894; 104,095 | 0; -1; 0 |
| Amount | Total: sell, buy, net | 10,087,257; 10,072,931; -14,326 | 10,087,256; 10,072,931; -14,326 | -1; 0; 0 |

Thus 13 of 18 corresponding values are exactly equal; five differ by one
displayed source unit. The screen itself has a one-unit arithmetic discrepancy
for non-arbitrage amount (`9,825,895 - 9,721,799 = 104,096`, while net is
displayed as `104,095`). The retained LS market sums also have one-unit
differences between category/total arithmetic. This is evidence of an
unresolved display or aggregation boundary, not permission to change either
provider's values.

Verdict: LS `t1633` quantity=`thousand shares` and amount=`million KRW` remain
`INFERRED_MULTI_SOURCE`, not `CONFIRMED_EMPIRICAL`, because all 18 fields did
not exactly match. They cannot be `CONFIRMED_OFFICIAL_LS`, since the unit
statement is from KRX rather than LS. No other retained official [12012]
screen/data response for a second same-date comparison was found in the
permitted repository evidence, so no multi-date claim is made. Normalized and
backtest use remain blocked.

## Addendum — KRX [12012] multi-date display-precision replication

The authenticated official KRX [12012] Program Trading **web screen** was used
for two additional one-day, `All`-market queries: 2026-08-13 and 2026-07-31.
This was visible UI use only: no KRX API or pykrx call was made, and no KRX
Landing/Raw artifact was written. For all three dates (including the
user-supplied 2026-08-14 screen), the visible selectors were quantity `thousand
shares` and amount `million KRW`. For every date, the comparison scope is KRX
`All` against the lossless LS KOSPI plus KOSDAQ rows. The two LS responses for
each family/date were re-hashed and each matched its retained provenance.

`LS − KRX` is literal source-unit subtraction. No rounding or reconciliation
was applied. `0` is exact; `+1`/`-1` is a one-unit display difference.

### 2026-08-14

| Family | Category | KRX sell / buy / net | LS sell / buy / net | LS − KRX |
| --- | --- | --- | --- | --- |
| Quantity | Arbitrage | 2,014 / 1,285 / -729 | 2,014 / 1,285 / -729 | 0 / 0 / 0 |
| Quantity | Non-arbitrage | 241,238 / 244,908 / 3,670 | 241,238 / 244,908 / 3,670 | 0 / 0 / 0 |
| Quantity | Total | 243,252 / 246,193 / 2,941 | 243,252 / 246,192 / 2,940 | 0 / -1 / -1 |
| Amount | Arbitrage | 365,458 / 247,036 / -118,422 | 365,458 / 247,037 / -118,422 | 0 / +1 / 0 |
| Amount | Non-arbitrage | 9,721,799 / 9,825,895 / 104,095 | 9,721,799 / 9,825,894 / 104,095 | 0 / -1 / 0 |
| Amount | Total | 10,087,257 / 10,072,931 / -14,326 | 10,087,256 / 10,072,931 / -14,326 | -1 / 0 / 0 |

Result: exact 13; ±1 5; ±2-or-more 0. KRX `buy-sell-net` is `0/0/0`
for quantity and `0/+1/0` for amount (arbitrage/non-arbitrage/total).

### 2026-08-13

| Family | Category | KRX sell / buy / net | LS sell / buy / net | LS − KRX |
| --- | --- | --- | --- | --- |
| Quantity | Arbitrage | 2,112 / 2,108 / -4 | 2,112 / 2,108 / -4 | 0 / 0 / 0 |
| Quantity | Non-arbitrage | 275,054 / 251,425 / -23,629 | 275,054 / 251,425 / -23,629 | 0 / 0 / 0 |
| Quantity | Total | 277,166 / 253,534 / -23,632 | 277,166 / 253,534 / -23,632 | 0 / 0 / 0 |
| Amount | Arbitrage | 374,359 / 376,377 / 2,018 | 374,359 / 376,377 / 2,018 | 0 / 0 / 0 |
| Amount | Non-arbitrage | 12,681,182 / 12,505,600 / -175,582 | 12,681,182 / 12,505,600 / -175,582 | 0 / 0 / 0 |
| Amount | Total | 13,055,541 / 12,881,977 / -173,563 | 13,055,541 / 12,881,977 / -173,563 | 0 / 0 / 0 |

Result: exact 18; ±1 0; ±2-or-more 0. KRX `buy-sell-net` is `0/0/0`
for quantity and `0/0/-1` for amount.

### 2026-07-31

| Family | Category | KRX sell / buy / net | LS sell / buy / net | LS − KRX |
| --- | --- | --- | --- | --- |
| Quantity | Arbitrage | 5,496 / 918 / -4,578 | 5,496 / 918 / -4,578 | 0 / 0 / 0 |
| Quantity | Non-arbitrage | 264,371 / 335,304 / 70,933 | 264,371 / 335,304 / 70,933 | 0 / 0 / 0 |
| Quantity | Total | 269,867 / 336,221 / 66,355 | 269,867 / 336,222 / 66,355 | 0 / +1 / 0 |
| Amount | Arbitrage | 963,289 / 152,809 / -810,480 | 963,289 / 152,809 / -810,480 | 0 / 0 / 0 |
| Amount | Non-arbitrage | 18,466,279 / 23,827,432 / 5,361,153 | 18,466,279 / 23,827,433 / 5,361,154 | 0 / +1 / +1 |
| Amount | Total | 19,429,568 / 23,980,241 / 4,550,673 | 19,429,568 / 23,980,241 / 4,550,673 | 0 / 0 / 0 |

Result: exact 15; ±1 3; ±2-or-more 0. KRX `buy-sell-net` is `0/0/-1`
for quantity and `0/0/0` for amount.

### Multi-date verdict

Across 54 fields: **46 exact, 8 within ±1 source unit, 0 at ±2 or more**.
Quantity has 24 exact and 3 ±1 fields; amount has 22 exact and 5 ±1 fields.
Every one of the three KRX screens has exactly one visible `buy-sell-net`
one-unit discrepancy, in a different category/family. This independently
reproduces the comparator display/aggregation-precision boundary observed on
2026-08-14; it is not a transformation rule.

The required condition is satisfied. LS `t1633` quantity = **thousand shares**
and amount = **million KRW** are each
`CONFIRMED_EMPIRICAL_MULTI_DATE`. This is empirical cross-provider evidence,
not `CONFIRMED_OFFICIAL_LS`: LS has not published a direct unit statement.

This resolves the unit blocker only. A future contract review may record these
provider-native units, but no Normalized/Canonical writer or data was changed
here. Predictive Backtest use remains blocked until the existing LS
publication/availability and revision/PIT gates are resolved.
