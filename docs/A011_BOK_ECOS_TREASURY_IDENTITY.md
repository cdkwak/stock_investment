# A011 BOK ECOS Treasury identity review

Reviewed: 2026-08-12 KST

## Decision

The A010 metadata phase is `READY` with the reviewed config candidate in
`docs/examples/bok_ecos_treasury_pilot.reviewed.json`.  The A010 value phase
remains `BLOCKED` until its immutable one-call `StatisticItemList` response is
captured and independently approved by SHA-256, exactly as A010 designed.

No ECOS value request or OpenAPI request was made in A011.  This review used
official documentation, the public ECOS UI metadata used to render the table
selector and statistical-description panes, and retained local A006 artifacts.

## Exact official identity

ECOS UI metadata identifies table `817Y002` as
`시장금리(일별)` / `Market Interest Rates(Daily)`, frequency `D`.  Its table-level
UI coverage is `1995-01-03` through `2026-08-11` as observed on the review date.

| Canonical tenor | ECOS item code | Official item name | Unit |
|---|---|---|---|
| 2Y | `010195000` | `국고채(2년)` | `연%` |
| 3Y | `010200000` | `국고채(3년)` | `연%` |
| 5Y | `010200001` | `국고채(5년)` | `연%` |
| 10Y | `010210000` | `국고채(10년)` | `연%` |
| 20Y | `010220000` | `국고채(20년)` | `연%` |
| 30Y | `010230000` | `국고채(30년)` | `연%` |

The BOK Financial and Economic Snapshot labels government-bond maturity yields
as final quotation yields and names KOFIA and ECOS as sources.  Therefore the
source identity must retain both roles: KOFIA is the upstream final-quotation
yield source and BOK ECOS is the official distributor.  It is a single annual
percent yield, not a Toss OHLC/volume candle.

## Historical-start result

The UI metadata establishes only the table-level start, `1995-01-03`.  It does
not publish per-item `START_TIME` in the reviewed table/item panes.  Per-item
historical starts must therefore not be guessed.

One tenor boundary is independently documented: KOFIA added the 2-year
government bond to the final-quotation reporting set by the amendment effective
`2021-03-10`.  This is the defensible 2Y boundary pilot date, but it is not
silently promoted to an asserted ECOS first observation date.  A010's bounded
one-call `StatisticItemList` metadata phase is the next approved mechanism to
capture exact per-item `START_TIME` and `END_TIME`; no data values are required
for that gate.

## Four reviewed pilot dates

| Role | Date | Reason |
|---|---|---|
| recent normal | `2026-08-10` | Latest retained Toss date common to all six tenors; permits an overlapping comparison. |
| 2Y introduction boundary | `2021-03-10` | KOFIA reporting-standard amendment effective date for the 2Y final quotation yield. |
| retained source gap | `2021-12-30` | Retained Toss has 3Y but no 2Y on this post-introduction date; tests whether official history fills the provider-specific gap. |
| early 2019 | `2019-01-02` | First retained Toss date; expected to distinguish the pre-2Y-official-boundary Toss label from the official item without forcing equivalence. |

The local audit found 40 retained Toss dates present for 3Y but absent for 2Y;
`2021-12-30` is the only such date after the documented 2Y reporting boundary.

## A010 schema cross-check

The reviewed candidate has exactly A010's required keys, canonical tenor order,
distinct item codes, cycle `D`, and four date roles.  It contains no credential
or placeholder.  A010 still must verify the response's exact `STAT_CODE`,
`STAT_NAME`, `ITEM_CODE`, `ITEM_NAME`, `UNIT_NAME`, and per-item start/end fields
before the value phase can be approved.

## Remaining unknowns

1. Exact per-item ECOS `START_TIME`/`END_TIME` for all six tenors.  Resolve only
   through A010's already bounded metadata phase when D separately authorizes it.
2. Original ECOS first-publication timestamps, observation-level correction
   identifiers, and vintage/supersession access.  Date labels do not establish
   predictive availability.
3. Whether ECOS history and retained Toss `close` ever represent the same value
   construction.  Existing A009 comparisons reject assumed equality.

## Primary official evidence

- BOK ECOS UI: <https://ecos.bok.or.kr/#/SearchStat>
- BOK Financial and Economic Snapshot, bond market: <https://snapshot.bok.or.kr/dashboard/A2/>
- KOFIA final quotation yield reporting standard, including the 2Y amendment
  and effective date: <https://law.kofia.or.kr/service/law/lawFullScreenContent.do?historySeq=1577&seq=178>
- MOEF KTB tenor and fungible-issue description: <https://ktb.moef.go.kr/ntndbtUnityIsu.do>
- MOEF government-bond definition: <https://ktb.moef.go.kr/ntpbnd.do>


