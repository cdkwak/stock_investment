# LS t8462 official-KRX multi-product validation — 2026-08-17

Status: **COMPLETE_WITH_REMAINING_OFFICIAL_FINALIZATION_CATEGORY_AND_PIT_LIMITS**

This was a bounded, authenticated, visible-UI comparison against official KRX
Data Marketplace screen `[15007] 투자자별 거래실적`. No KRX file was downloaded,
no collector or backfill was run, and no Raw/Normalized/Canonical artifact was
changed.

## Scope

The KRX screen was fixed to `기간합계`, market `전체`, volume unit `계약`, and
amount unit `백만원`. Three retained LS dates were used: 2026-01-02,
2026-07-31, and the 2026-08-13 expiry-relevant date. The direct comparison used
KOSPI200 futures on all three dates and KOSPI200 CALL, KOSPI200 PUT, and
mini-KOSPI200 futures on 2026-08-13.

Investor mappings were literal: LS individual, foreign, institution total, and
other matched KRX 개인, 외국인, 기관합계, and 기타법인. KRX net purchase was the
displayed source field, not a locally reconstructed value.

## Direct comparison

Across 24 date/product/investor points, every LS `sv_*` value exactly equalled
the KRX volume net-purchase value in contracts. This establishes the LS
quantity unit empirically as one contract and establishes the observed sign as
net purchase for these `U` scopes.

For the same 24 points, `LS sa_* × 100` matched the KRX amount net-purchase
value in million KRW. Residuals were -47..+45 million KRW, consistent with LS
100-million-KRW source rounding. The result extends the earlier futures-only
evidence to CALL, PUT, and mini futures.

| Evidence | Result |
|---|---:|
| Direct product/date/investor points | 24 |
| Exact quantity points | 24 |
| Maximum quantity difference | 0 contracts |
| Amount residual range after `sa × 100` | -47..+45 million KRW |
| Maximum absolute amount residual | 47 million KRW |

## Option aggregate

For 2026-08-13, the official KRX option `전체` volume net purchase exactly
equalled KRX `CALL + PUT` for all four compared investor groups. Amount net
purchase was exact for foreign, institution total, and other corporation; the
individual row differed by one displayed million-KRW unit (`-16,169` versus
`-16,170`). This is a display-rounding boundary, not a transformation rule.

The retained LS CALL and PUT `U` rows reproduce the same component values:
their quantities sum exactly to the KRX option `전체` quantities, and their
amounts at the inferred `×100` scale remain within source rounding.

## D/N session mapping

The same three KOSPI200-futures dates were queried with KRX market `정규` and
`야간`. LS `D` matched KRX `정규`, and LS `N` matched KRX `야간`:

- individual and foreign contract net purchase: 12/12 exact points;
- institution total plus other corporation contract net purchase: 6/6 exact
  combined points;
- amount direction and 100-million-KRW scale: consistent after combining
  institution total and other corporation.

KRX and LS allocated a small offset differently between institution total and
other corporation: 3 contracts on 2026-01-02, 34 on 2026-07-31, and 53 on
2026-08-13, with equal and opposite differences so the combined value remained
exact. Therefore the session mapping is
`CONFIRMED_EMPIRICAL_MULTI_DATE_WITH_INSTITUTION_OTHER_CATEGORY_BOUNDARY`, not
an assertion that every investor category is source-equivalent.

## Classification

- `sv_*` unit: `CONFIRMED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE_CONTRACT`.
- `sa_*` unit: `INFERRED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE_100_MILLION_KRW`.
- positive/negative direction: `CONFIRMED_EMPIRICAL_NET_PURCHASE`.
- `U`: `CONFIRMED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE_ALL`.
- `D`: `CONFIRMED_EMPIRICAL_MULTI_DATE_REGULAR_WITH_CATEGORY_BOUNDARY`.
- `N`: `CONFIRMED_EMPIRICAL_MULTI_DATE_NIGHT_WITH_CATEGORY_BOUNDARY`.

These are empirical cross-provider conclusions, not official LS definitions.
They do not establish LS session cut-off/finalization times, remove the
institution/other category boundary, authorize a universal `U=D+N` repair rule,
create a Dataset Contract, permit Normalized/Canonical writes, or support a
PIT-safe declaration.

Machine-readable evidence:

- `artifacts/semantic_validation/ls_t8462_krx_multi_product_20260817.csv`
- `artifacts/semantic_validation/ls_t8462_krx_sessions_20260817.csv`
- `artifacts/semantic_validation/ls_t8462_krx_multi_product_20260817.json`

The earlier authentication-gate observation is superseded for access only by
this successful run; it remains preserved as historical evidence.

## Option and mini session follow-up

The authenticated `[15007]` screen was revisited without any provider API or
download. KOSPI200 CALL and mini-KOSPI200 futures were compared for `D/N/U` on
2026-01-02, 2026-07-31, and 2026-08-13. The screen explicitly displayed volume
in contracts and amount in million KRW.

| Product | Dates × sessions | Individual/foreign volume | Institution/other | `institutional_complex` | Session result |
|---|---:|---|---|---|---|
| KOSPI200 CALL | 3 × 3 | exact | individual categories exact | exact | `D=REGULAR`, `N=NIGHT`, `U=ALL`; `U=D+N` exact in contracts |
| Mini-KOSPI200 futures | 3 × 3 | exact | 2026-01-02 exact; 2026-07-31 and 2026-08-13 `D/N` category allocation offsets; all `U` exact | 9/9 exact | `D=REGULAR`, `N=NIGHT`, `U=ALL`; `U=D+N` exact in contracts |

Across the 90 comparison rows (four source categories plus
`institutional_complex`), 82 contract rows matched directly. The eight
non-exact rows are the equal-and-opposite mini-futures institution/other rows
for `D/N` on 2026-07-31 (170 contracts) and 2026-08-13 (265 contracts). Their
combined values are exact. This is the same `PROVIDER_CATEGORY_BOUNDARY`
pattern already observed for KOSPI200 futures.

For source-equivalent categories and all combined institution/other rows, KRX
million-KRW amount minus `LS sa × 100` ranged from -49 to +66 million KRW. The
direct ratio is unstable when a rounded LS value is zero or near zero, while
the absolute residual remains bounded by the expected rounding of a
100-million-KRW LS source unit. The sign matches KRX net purchase, including
negative observations, across both products, all three sessions, and all three
dates. Classification: LS `sa_*` is
`CONFIRMED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE` at 100 million KRW per raw unit,
with signed net-purchase direction. This is not `CONFIRMED_OFFICIAL_LS`.

Full comparison table:
`artifacts/semantic_validation/ls_t8462_krx_option_mini_20260817.csv`.

## Historical Option U aggregate/detail follow-up

Three previously mismatching Option-U dates and two normal dates were checked.
No LS value was repaired or selected as canonical.

| Product/date | LS institution `sv_18` | LS institution detail sum | KRX institution | LS other | KRX other | LS aggregate + other | KRX institution + other | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| CALL 2025-07-31 | 2,199 | 2,192 | 2,192 | -336 | -336 | 1,863 | 1,856 | KRX matches detail; aggregate residual +7 |
| CALL 2025-08-05 | -530 | -175 | -175 | -868 | -868 | -1,398 | -1,043 | KRX matches detail; aggregate residual -355 |
| PUT 2025-12-11 | -24,616 | -20,612 | -20,612 | 1,535 | 1,535 | -23,081 | -19,077 | KRX matches detail; aggregate residual -4,004 |
| CALL 2026-01-02 | -302 | -302 | -302 | 575 | 575 | 273 | 273 | normal exact |
| CALL 2026-08-13 | -32,714 | -32,714 | -32,714 | 629 | 629 | -32,085 | -32,085 | normal exact |

KRX institution matched the LS institution-component sum on all three problem
dates, while KRX other corporation matched LS other corporation. On the normal
dates, LS `sv_18`, its component sum, and KRX institution all matched. The
bounded conclusion is `AGGREGATE_FIELD_SEMANTICS` within historical KOSPI200
Option `U`, not a general provider category boundary and not proof of a source
revision. Retain both LS aggregate and component fields unchanged; do not use a
universal repair rule.

## Finality observation

The official KRX screen confirms its own labels `REGULAR`, `NIGHT`, and `ALL`,
and its displayed units `contracts` and `million KRW`. It did not display a
regular close time, night close time, ALL-value publication time, revision
schedule, or LS `t8462` availability timestamp. LS publication/finalization,
revision behavior, and PIT safety therefore remain unresolved.

## Semantic closure sprint

### Remaining product/session matrix

The logged-in KRX `[15007]` screen and unchanged retained LS Raw were compared
for KOSPI200 PUT, mini-KOSPI200 CALL, and mini-KOSPI200 PUT on 2026-01-02,
2026-07-31, and 2026-08-13, for every `D/N/U` scope. Each scope compared
individual, foreign, institution, other corporation, and
`institutional_complex` in contracts and amount.

| Product | Scope rows | LS/KRX contract exact | `institutional_complex` | `U=D+N` contracts | Amount residual, KRX million KRW minus `LS sa × 100` |
|---|---:|---:|---:|---:|---:|
| KOSPI200 PUT | 45 | 45/45 | 9/9 | 15/15 | -80..+45 million KRW |
| Mini-KOSPI200 CALL | 45 | 45/45 | 9/9 | 15/15 | -45..+62 million KRW |
| Mini-KOSPI200 PUT | 45 | 45/45 | 9/9 | 15/15 | -49..+50 million KRW |
| **Total** | **135** | **135/135** | **27/27** | **45/45** | **-80..+62 million KRW** |

KRX and LS amount `U-(D+N)` residuals were respectively -1..+1 displayed
million KRW and -1..+1 LS raw unit, consistent with independent display/source
rounding. Each product is `CONFIRMED_EMPIRICAL_MULTI_DATE` for `D=REGULAR`,
`N=NIGHT`, and `U=ALL`. Together with the previously accepted futures, CALL,
and mini-futures results, all six retained `K2I/MKI × F/C/P` product families
now have multi-date session evidence. No untested product is inferred.

Compact evidence:
`artifacts/semantic_validation/ls_t8462_krx_put_mini_options_closure_20260817.csv`.

### Official finality and revision documentation

The live official LS guide identifies t8462 as `KRX야간파생 투자자기간별(API용)`,
documents `POST /futureoption/investor`, and states one request per second. Its
visible schema and page contain no regular-session close time, night-session
close time, ALL completion time, API availability time, final/fixed marker, or
correction/revision policy. The KRX screen establishes its own market labels
and units but not LS publication timing.

No live LS diagnostic was made during this sprint. The active runbook permits
daily capture only after the target day closes, and a mid-session observation
would not prove finality. Publication/finality and revision policy remain
`UNRESOLVED` rather than being inferred from session labels.

Existing duplicate captures provide only short-window no-change evidence. A
K2I-futures-D 263-row range captured near 16:49 KST matched the later Raw
backfill near 16:59 KST in 263/263 rows and every retained field. Six
2026-08-14 product/session samples and K2I-futures-D samples for 2026-01-02,
2026-07-31, and 2026-08-13 also matched all 25 fields. This does not prove that
historical values are never revised.

### 263-row semantics

The broad retained K2I-futures-D request for 2021-01-04..2026-08-14 returned
263 dates, 2025-07-18..2026-08-14, with `tr_cont=N`. Direct point requests for
2021-01-04 and 2025-01-02 were valid empty, while later retained dates were
present. The bounded Raw capture then showed the identical 263-date boundary
for all 18 product/session scopes.

The old-date point empties rule out pagination as the explanation for missing
earlier rows, and the common boundary is stronger evidence than a
product-specific limit. Current classification is `SOURCE_HISTORY_BOUNDARY` at
the observed 2025-07-18 boundary. Because no later accepted daily capture
exists, a future rolling boundary is not disproved; earliest/second-date
monitoring remains required.

### Availability policy

The historical Raw backfill was captured in August 2026 and has no historical
publication timestamps. It is `RESEARCH_ONLY_NON_PREDICTIVE` for observations
preceding its actual capture. Do not synthesize a historical D+1 timestamp.

A future accepted daily capture may be used only from its actual `captured_at`.
`NEXT_TRADING_DAY_ALLOWED` is conditional on capture after the full
regular/night/ALL cycle, a complete checkpoint, and no later changed snapshot
for that market date. That operation has not yet been demonstrated. Same-day
use is forbidden and no t8462 dataset is `PIT_SAFE`.

Closure status: `LS_T8462_SEMANTIC_CLOSURE=CLOSED_WITH_LIMITS`. Product,
session, unit, sign, and analysis-category semantics are closed for the retained
six-product matrix. Official finality/revision timing and prospective daily PIT
operation remain explicit limits.
