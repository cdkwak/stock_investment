# LS OpenAPI Derivatives Investor Pilot

Date: 2026-08-15 KST
Scope: official LS OpenAPI documentation plus bounded, retry-free, read-only
`t8462` calls. No account, order, private-web, or scraping endpoint was used.

| Control field | Status |
|---|---|
| `audit_status` | `CLOSED` |
| `pilot_status` | `COMPLETE_WITH_SEMANTIC_LIMITS` |
| `daily_collection_status` | `DAILY_COLLECTION_READY` |
| `normalized_status` | `FORBIDDEN_PENDING_SEMANTICS` |

The pilot and historical semantic audit are closed and must not be rerun by
default. Ongoing work routes to the separate
[daily Raw runbook](../runbooks/active/LS_T8462_DAILY_RAW_COLLECTION.md).

## Decision

LS `t8462` returns real dated investor rows for KOSPI200 and mini-KOSPI200
futures, calls, and puts. It is not yet adopted as a production source because
the official schema does not define the `sa_*` monetary multiplier or the final
meanings of `D/N/U` precisely enough for safe normalization. A bounded Raw
backfill supplies strong multi-date evidence for the `U` amount unit/session, but
`D/N` semantics remain unresolved.

| Product/scope | Verified result | Classification |
|---|---|---|
| KOSPI200 futures, `D` | 2025-07-18..2026-08-14 returned in one range call | `LIMITED_HISTORY` + `UNIT_OR_SESSION_UNRESOLVED` |
| KOSPI200 futures, `N` | One 2026-08-14 row | `UNIT_OR_SESSION_UNRESOLVED` |
| KOSPI200 call/put, `D` | One 2026-08-14 row each | `UNIT_OR_SESSION_UNRESOLVED` |
| Mini-KOSPI200 futures/call/put, `D` | One 2026-08-14 row each | `UNIT_OR_SESSION_UNRESOLVED` |
| Stock futures | No selector in the documented `t8462` underlying list | `PRODUCT_NOT_PROVIDED` |

No Dataset Contract or Normalized artifact was created.

## Daily collection handoff

The operational candidate is Raw-only and append-only. One post-close invocation
captures the same 18 scopes with one OAuth token, retry zero, and the existing
one-request-per-second throttle. It records the earliest and second returned date
per scope so a one-observed-trading-day boundary shift can promote retention from
`OBSERVED_EARLIEST_ONLY` to `ROLLING_RETENTION`.

Provider `sv_18` is authoritative. Provenance may record
`institution_components_sum`, `institution_aggregate_difference`, and
`institution_aggregate_status`, but never recalculates or overwrites the source
aggregate. Current semantic classifications remain unchanged. No scheduler was
installed during readiness work.

## Bounded Raw Backfill

Run `20260814T165922Z_da488bc5fd024f559b0ef70f6d340e1f` captured the exact
requested matrix for `2025-07-18..2026-08-14`. It made one OAuth call and exactly
18 sequential `t8462` calls, retry zero. All returned HTTP 200, `rsp_cd=00000`,
exact request echoes, complete source fields, and `tr_cont=N`.

| Product | D rows | N rows | U rows | Total rows | Raw response bytes |
|---|---:|---:|---:|---:|---:|
| KOSPI200 futures | 263 | 263 | 263 | 789 | 257,982 |
| KOSPI200 call | 263 | 263 | 263 | 789 | 239,813 |
| KOSPI200 put | 263 | 263 | 263 | 789 | 240,219 |
| Mini-KOSPI200 futures | 263 | 263 | 263 | 789 | 245,265 |
| Mini-KOSPI200 call | 263 | 263 | 263 | 789 | 234,359 |
| Mini-KOSPI200 put | 263 | 263 | 263 | 789 | 233,877 |
| **Total** | **1,578** | **1,578** | **1,578** | **4,734** | **1,451,515** |

Raw response bytes are preserved unchanged, with a separate provenance record
per scope containing the raw product/session codes, requested range, capture
timestamp, source, TR code, response code, row/date coverage, bytes, and hash.
The append-style ledger, checkpoint, and zero-network audit reconcile all 18
responses. No Normalized write occurred.

Every scope returned the identical observed coverage `2025-07-18..2026-08-14`.
This is classified `OBSERVED_EARLIEST_ONLY`: the common 263-row result is
consistent with a rolling retention window or row ceiling, but neither is proven
by the official documentation. No nineteenth boundary request was made because
the authorized data-call cap was 18.

## Authentication and Calls

The exact official OAuth request is `POST
https://openapi.ls-sec.co.kr:8080/oauth2/token`, using Requests `params=` with
`grant_type=client_credentials`, `appkey`, `appsecretkey`, and `scope=oob`.
Credentials and tokens remained in process memory and passed post-run secret
scans.

| Run | OAuth | Investor calls | Retry | Result |
|---|---:|---:|---:|---|
| Initial invalid composition | 1 | 0 | 0 | HTTP 403; wrong base-path/transport evidence retained |
| Corrected single-day proof | 1 | 1 | 0 | OAuth PASS; K2I futures `D` row PASS |
| Follow-up | 1 | 13 | 0 | OAuth PASS; all 13 `t8462` calls PASS |

The follow-up reused the retained corrected K2I futures `D` response rather than
calling it again. Its own call budget was exactly one OAuth plus 13 serial
investor calls, with at least 1.05 seconds between investor requests. HTTP errors,
provider errors, response-shape anomalies, and access restrictions were zero.

## Official Contract Evidence

The official [LS derivatives-investor guide](https://openapi.ls-sec.co.kr/apiservice?group_id=2f1eea77-5606-4512-93c6-31b21d2ece90&api_id=47005ce6-8500-4a3d-ad6c-f96ec3251669)
defines `POST /futureoption/investor`, one request per second.

| TR | Official scope | Assessment |
|---|---|---|
| `t2541` | Commodity-futures investor realtime | Bond/currency/gold/livestock products, not KOSPI200 |
| `t2545` | Commodity-futures investor chart | Same non-target product boundary |
| `t8462` | KRX night-derivatives investor by period | Dated target-product candidate used here |
| `t8463` | KRX night-derivatives investor by time | Intraday candidate; not called |

### `t8462InBlock`

| Field | Official description |
|---|---|
| `tm_rng` | Time-range code `D/N/U`; the guide does not expand these codes |
| `fot_clsf_cd` | `F` futures, `C` call, `P` put, `S` spread |
| `bsc_asts_id` | `K2I` KOSPI200, `MKI` mini-KOSPI200, and other listed underlyings |
| `gubun2` | `1` value, `2` cumulative |
| `gubun3` | `1` day, `2` week, `3` month |
| `from_date`, `to_date` | Date range in `YYYYMMDD` |

The response echoes `tm_rng`, `fot_clsf_cd`, and `bsc_asts_id`. Every pilot
response matched the exact request echo.

The live official schema describes `tm_rng` only as `시간대(D/N/U)` and does not
expand any of the three codes. Its official example uses `N` but likewise gives
no label, aggregation boundary, or finalization time. Therefore none of
`D=day`, `N=night`, or `U=unified/all` is promoted from naming intuition.

### Investor fields and arithmetic

| Suffix | Official investor label |
|---|---|
| `08` | Individual |
| `17` | Foreign |
| `18` | Institution total |
| `01` | Securities |
| `03` | Investment trust |
| `04` | Bank |
| `02` | Insurance |
| `05` | Merchant bank |
| `06` | Fund |
| `07` | Other |
| `15` | Futures, using the official label |
| `00` | Private-equity fund |

`sv_*` is labelled quantity and `sa_*` amount. Negative values prove these are
signed net balances rather than gross buy/sell fields. The official guide does
not define which direction is positive.

Across all 263 K2I-futures `D` range rows:

- `sv_08 + sv_17 + sv_18 + sv_07 = 0` exactly;
- `sv_18 = sv_01 + sv_02 + sv_03 + sv_04 + sv_05 + sv_06 + sv_00` exactly;
- `sa_08 + sa_17 + sa_18 + sa_07` differed from zero by at most one source unit;
- the `sa_18` subtotal differed from its detailed components by at most two
  source units.

The amount differences are consistent with source-side rounding but do not prove
the monetary multiplier from LS documentation alone.

### Amount-unit cross-check

The `K2I/F/U` `sa_*` values were compared with the retained official KRX Basic
Statistics KOSPI200-futures `ALL`-session net-purchase series, whose documented
unit is million KRW. The comparison used 2026-01-02, 2026-07-31, and 2026-08-13
for individual, foreign, institution total, and other corporation: 12 independent
points.

For all 12 points, `LS sa_* × 100` matched the KRX million-KRW value within
source rounding; residuals were -38..+45 million KRW. Sign direction matched KRX
net purchase. The result is therefore `UNIT_INFERRED_MULTI_DATE_MATCH`, with the
LS source unit inferred as 100 million KRW for this `U` scope. It is not labelled
`UNIT_CONFIRMED` because the multiplier is absent from the official LS schema.

The same evidence supports `U = ALL` as `SESSION_INFERRED`. It does not establish
the exact meanings or finalization rules for `D` and `N`; the overall result stays
`SESSION_UNRESOLVED`. `U` is not assumed to equal arithmetic `D + N`, and the
captured values do not support that identity consistently.

Across all 4,734 captured rows, the institution quantity equalled the documented
detail fields in 4,532 rows. The official institution-candidate detail list is
`sv_01` securities, `sv_03` investment trust, `sv_04` bank, `sv_02` insurance,
`sv_05` merchant bank, `sv_06` fund, `sv_15` futures, and `sv_00` private-equity
fund. The earlier audit formula omitted `sv_15`; the complete re-audit includes
it. `sv_15` is zero in every mismatching row, so it explains none of the residual.

The remaining 202 mismatches comprise 100 KOSPI200-call `U` rows and 102
KOSPI200-put `U` rows, all dated 2025-07-18..2025-12-23. All option-`U` rows from
2025-12-24 onward reconcile exactly, while other captured product/session scopes
do not exhibit this quantity-subtotal pattern. No additional response field is
available as an omitted institution category. The classification is therefore
`OPTION_SPECIFIC_SEMANTICS`, with `PROVIDER_AGGREGATE_DIFFERENCE` as the observed
mechanism—not `SUBCATEGORY_OMITTED`.

The full row-level report records date, `sv_18`, the exact eight-field list and
values, detail sum, and residual without changing Raw:
[option-U institution differences](LS_T8462_OPTION_U_INSTITUTION_DIFFERENCES.csv).
Residuals reach 4,004 contracts. Amount subtotals show bounded source-unit
rounding (institution residual at most 6; aggregate residual at most 47). These
raw values remain unchanged and are not repaired or derived.

No retained same-grain official KRX amount series exists for KOSPI200 call, put,
or mini futures. The authenticated KRX comparison screen was not available in
the current browser session, so no KRX data request was made merely to fill that
gap. Consequently the amount result remains
`UNIT_INFERRED_MULTI_DATE_MATCH` for KOSPI200 futures `U`; it is not upgraded to
`UNIT_INFERRED_MULTI_PRODUCT_MATCH` or `UNIT_CONFIRMED`.

## 2026-08-14 Rows

Values below are exact source-native signed quantity (`sv`) and amount (`sa`).
They are not relabelled as net purchases.

| Product | Code | Session code | Individual sv/sa | Foreign sv/sa | Institution sv/sa | Other sv/sa |
|---|---|---|---:|---:|---:|---:|
| KOSPI200 futures | K2I/F | D | 43 / 109 | -1,648 / -4,540 | 1,581 / 4,366 | 24 / 65 |
| KOSPI200 call | K2I/C | D | -150 / 10 | 281 / 4 | 192 / -15 | -323 / 0 |
| KOSPI200 put | K2I/P | D | -3,119 / -7 | 2,869 / 19 | 621 / -11 | -371 / -2 |
| Mini-KOSPI200 futures | MKI/F | D | -124 / -72 | -197 / -104 | 304 / 167 | 17 / 9 |
| Mini-KOSPI200 call | MKI/C | D | -141 / -2 | 20 / -4 | 149 / 5 | -28 / 0 |
| Mini-KOSPI200 put | MKI/P | D | -398 / -1 | 472 / 3 | -66 / -1 | -8 / 0 |
| KOSPI200 futures | K2I/F | N | -133 / -369 | 536 / 1,438 | -450 / -1,198 | 47 / 129 |

`D` and `N` produce distinct rows and are echoed independently, but this alone
does not prove final `REGULAR`, `NIGHT`, or combined-session labels. `U` was not
called. STAR futures and mini-KOSPI200 futures remain distinct products.

The retained KB public-screen benchmarks cannot be reconciled yet: their unit,
capture/finalization time, and session do not match a fully documented LS
contract. No numeric equivalence is asserted.

## Historical and Range Behavior

| Probe | Result |
|---|---|
| 2026-08-13 | One K2I/F `D` row |
| 2026-07-31 | One K2I/F `D` row |
| 2026-01-02 | One K2I/F `D` row |
| 2025-01-02 | Valid empty |
| 2021-01-04 | Valid empty |
| 2021-01-04..2026-08-14 | 263 unique, complete-field rows, descending 2026-08-14..2025-07-18 |
| 2026-08-15 holiday/weekend | Valid empty |

The broad response header reported `tr_cont=N`, so no continuation was indicated
for 263 rows. A continuation-key header was present, but its presence cannot
override `tr_cont=N`. The exact row ceiling remains undocumented. The earliest
observed row is 2025-07-18; it is not promoted to a proven source inception date
because an undocumented retention window or response ceiling has not been ruled
out.

## Provisional Backfill Cost

The 263-row K2I/F `D` response occupies 150,584 bytes in retained pretty-printed
JSON, about 0.57 KB per row. If all selectors have the same range behavior:

- six `D` selectors (`K2I/MKI × F/C/P`): about 6 calls, at least 6 seconds and
  roughly 0.9 MB of raw JSON, plus one OAuth call;
- all three `D/N/U` codes for those products: about 18 calls, at least 18 seconds
  and roughly 2.7 MB, plus one OAuth call.

These were pre-capture planning estimates. They did not authorize Normalized
publication or a historical reconstruction.

The estimate has now been measured for the bounded range: 18 data calls plus one
OAuth call produced 1.45 MB of raw response JSON in about the provider-throttled
minimum scale. This supports the separately controlled Raw-only daily collector.
Product mapping is confirmed, but full session semantics and an official amount
unit remain incomplete; `daily_raw_collection_ready=true` and
`normalized_writes=false`.

## Retained Evidence

- Corrected single-day run:
  `data/landing/diagnostics/ls_derivatives_investor_pilot/20260814T164315Z_1f6e2f359c7a436c86ee8a7a019f5b66/`
- Follow-up run:
  `data/landing/diagnostics/ls_derivatives_investor_pilot/20260814T164916Z_5b2d7097e7de4d21b683507aa1dc901a/`
- Bounded Raw backfill:
  `data/landing/ls_openapi/t8462_raw/20260814T165922Z_da488bc5fd024f559b0ef70f6d340e1f/`

Each contains sanitized response JSON, an append-style call ledger, and a final
checkpoint. Secret scans passed; no request headers, credentials, or tokens were
persisted.
