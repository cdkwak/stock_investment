# KB IVSA0070 2026-08-14 Derivatives Audit

## Evidence and complete-flatten check

The offline audit used
`data/landing/kbsec/daily_snapshot/20260814T080005Z_daily/market_response.json`
(provider time `20260814170006957`). Its SHA-256 is
`df4e72c8fc44535513ef276ad65a1973d90fbbe8f46e09e28db0a719878d1e95`.
Recursive JSONPath-style flattening of the complete response produced 293 scalar
path/value pairs. Exact numeric searches found no signed or unsigned match for 562,
218, 416, 83, or 78. No path matched `token`, `authorization`, `cookie`, `password`,
`secret`, or `app_key`; no credential was copied here. Landing, state, and datasets
were not changed.

## All derivative investor raw fields

These are every derivative-flow key/value in `dataBody.out5`:

| index | code | investor | `fts_nt_b` | `call_opt_nt_b` | `put_opt_nt_b` | `star_fts_nt_b` | `stk_fts_nt_b` |
|---:|---|---|---:|---:|---:|---:|---:|
| 0 | 0008 | 개인 | 0 | 0 | 0 | 0 | -819 |
| 1 | 0009 | 외국인 | 0 | 0 | 0 | 0 | -10208 |
| 2 | 0018 | 기관계 | 0 | 0 | 0 | 0 | 11155 |
| 3 | 0001 | 금융투자 | 0 | 0 | 0 | 0 | 10091 |
| 4 | 0003 | 투신 | 0 | 0 | 0 | 0 | 2310 |
| 5 | 0004 | 은행 | 0 | 0 | 0 | 0 | 0 |

There is no mini-futures key. `star_fts_nt_b` identifies STAR futures and cannot be
relabeled as KOSPI200 mini-futures without provider evidence. `stk_fts_nt_b` is the
separate stock-futures field.

`dataBody.out3` contains quotes, not investor flows:

| index | code | name | price | volume | open interest |
|---:|---|---|---:|---:|---:|
| 0 | A0169000 | F 202609 | 1098.90 | 0 | 153146 |
| 1 | B0169A41 | C 202609 1,097.5 | 60.45 | 0 | 79 |
| 2 | C0169A41 | P 202609 1,097.5 | 61.40 | 0 | 84 |

## Public-screen comparison

| Product | Foreign | Individual | Institution | Raw result |
|---|---:|---:|---:|---|
| KOSPI200 futures | -562 | +218 | +416 | every `fts_nt_b` is 0 |
| CALL | -2 | +2 | 0 | every `call_opt_nt_b` is 0 |
| PUT | +1 | -1 | 0 | every `put_opt_nt_b` is 0 |
| Mini futures | -83 | +78 | 0 | no mini key; every `star_fts_nt_b` is 0 |

## Mapping finding and disposition

The normalizer iterates each object and identifies the investor by `invstr_cd`; it
does not assign investor classes by array position. The mappings are direct:
`fts_nt_b -> futures_net_buy`, `call_opt_nt_b -> call_option_net_buy`,
`put_opt_nt_b -> put_option_net_buy`, `star_fts_nt_b -> star_futures_net_buy`, and
`stk_fts_nt_b -> stock_futures_net_buy`. Therefore this response has no field-mapping
or array-index bug.

The disposition is `UNAVAILABLE_FROM_IVSA0070`. The two current official workbooks
define no request inputs, the official sample request uses an empty `dataBody`, and
the current client therefore correctly sends an empty body. The public investor-flow
screen uses a separate non-OpenAPI web route and is not adopted as a source. Raw zero
values remain lossless in Landing. Normalized zero values in the four unavailable
fields are null with `derivatives_flow_status=UNAVAILABLE_FROM_IVSA0070`; nonzero
values and the independent stock-futures field are preserved. STAR futures and
mini-KOSPI200 futures remain distinct products.
