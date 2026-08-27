# KB Securities IVSA0070 current snapshot contract

Status: **CURRENT_SEMANTIC_BOUNDARY / NORMALIZED_PUBLICATION_BLOCKED**

This document replaces the archived provisional common-date contract. IVSA0070 is
a read-only forward snapshot and cross-check source, not a historical replacement.
The complete response is retained Landing-first before parsing.

## Time model

- `collected_at` is the exact UTC capture timestamp.
- `capture_date` is the Asia/Seoul calendar date used only for capture partitioning
  and daily-attempt identity. It is never automatically the market date.
- `source_reported_datetime` preserves each source date/time token at the narrowest
  slice or row where it is reported.
- `market_date` is assigned independently per slice only when retained source
  evidence establishes that slice's trading/reference date.
- `date_semantics_status` is one of `CURRENT_DAY_CLOSE`, `PREVIOUS_DAY_CLOSE`,
  `INTRADAY_NIGHT`, `LAGGED_SOURCE_DATE`, or `DATE_UNRESOLVED`.
- `inq_dy_tm` is retained evidence but must not impose one common `market_date` on
  breadth, program, investor, liquidity, derivatives, domestic-index, and global
  symbol slices.

Rows with `DATE_UNRESOLVED` may exist only in the provider-specific Current Snapshot
layer or quarantine. They cannot be promoted into historical `*_daily` datasets.
The Python contracts now implement nullable slice-specific `market_date`, preserve
`inq_dy_tm` as `reference_date`, and keep `capture_date` separate. A later audited
date mapping may update future snapshot availability labels; it must not rewrite Raw.

## Slice boundaries

| Slice | Date rule |
|---|---|
| Market breadth | Determine from the post-close response for that market; do not inherit liquidity or inquiry dates |
| Program trading | Determine independently from its own close/session evidence |
| Investor flow | Determine independently; zero current-session snapshots are not previous-day closes |
| Market liquidity | Preserve its explicit source date and classify lag separately |
| Derivatives summary | Use only a source-reported instrument/session date; otherwise unresolved |
| Domestic indices | Resolve from the slice's own source/session evidence |
| Global symbols | Preserve each row's `source_datetime`; different rows may have different market dates |

The primary identity remains `collected_at` plus slice-specific market/instrument
keys. Capture partitioning must not overwrite or coerce slice dates.

## IVSA0070 investor derivatives

- Preserve every raw provider value, including zero, byte-exactly in Landing.
- Official schema/sample and observed production responses return constant zero for
  KOSPI200 futures, CALL, PUT, and STAR-futures investor-flow fields with no request
  selector that can resolve them.
- Those source-unavailable/provisional zeros are **not valid numeric zero** in
  Normalized data. They become null with
  `derivatives_flow_status=UNAVAILABLE_FROM_IVSA0070`.
- A genuine nonzero source value may be retained as `SOURCE_VALUE` only after the
  same field and date semantics validate.
- Stock-futures values are independent and may remain usable when nonzero.
- STAR futures and MINI KOSPI200 futures are distinct products. IVSA0070 provides no
  verified mini-futures or mini-options investor-flow field.

## Authentication and operation routing

The canonical authentication path is the corrected official nested
`dataHeader`/`dataBody` OAuth envelope used by the
[daily snapshot operation](../../operations/KBSEC_DAILY_MARKET_SNAPSHOT.md). The
historical flat-envelope E021 pilot is superseded evidence only.

No account or order endpoint is permitted. Historical-daily publication remains
blocked until the post-close comparison resolves every promoted slice. Current GUI
views may display provider-labelled provisional rows, including `DATE_UNRESOLVED`,
but must expose their availability and value status.
