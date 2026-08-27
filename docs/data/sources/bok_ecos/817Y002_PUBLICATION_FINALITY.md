# BOK ECOS 817Y002 publication and finality evidence

Status: `CURRENT SOURCE EVIDENCE / FINALITY UNDOCUMENTED`

Scope: ECOS `StatisticSearch`, table `817Y002`, daily 2Y, 3Y, 5Y, 10Y, 20Y,
and 30Y Korean Treasury yield observations. This note is evidence, not execution
authority.

## Official documentation

- The [ECOS Open API StatisticSearch specification](https://ecos.bok.or.kr/api/#/DevGuide/DevSpeciflcation)
  documents table/item identities, unit, cycle, observation period (`TIME`),
  and value (`DATA_VALUE`). It does not document a publication timestamp,
  preliminary/final flag, revision identifier, or revision window in the
  response.
- The BOK announcement
  [ECOS 속보·잠정치 식별 표시 개선](https://www.bok.or.kr/portal/bbs/B0000501/view.do?depth=201264&menuNo=201264&nttId=11063460&programType=newsData&relate=Y)
  states that ECOS single-statistic screens can display `p` beside periods that
  contain preliminary/provisional values for applicable BOK-produced
  statistics. It does not specify a publication clock, a revision deadline, or
  a table-specific rule for `817Y002`, and the documented `StatisticSearch`
  output has no corresponding marker.
- The [BOK statistical release schedule](https://www.bok.or.kr/portal/stats/statsPublictSchdul/listKnd.do?menuNo=200776)
  lists named scheduled statistical releases, but it does not define daily
  `817Y002` availability or finality timing.

## Retained local evidence

The retained run captured all six tenors through observation date 2026-08-13
between approximately 21:37 and 21:45 KST on that same date. Its checkpoint
records 29,674 normalized rows, five new provider calls, and six retained source
responses including the adopted 3Y response. Retained item metadata explicitly
records publication and revision semantics as not supplied.

This establishes only a bounded fact: the 2026-08-13 values were available by
the capture window. One same-date capture cannot establish the earliest
availability time, a stable publication lag, whether the values were provisional,
or whether later revisions can occur.

## Policy consequence

- Observation calendar: `PROVIDER_PUBLICATION`.
- Automatic expected latest: unset.
- Availability/finality: `UNKNOWN` until an explicit reviewed date is selected.
- XKRX sessions, Treasury-futures sessions, and the local business date are not
  valid proxies.
- Scheduler eligibility and unattended promotion remain disabled.

## Prospective observation checkpoint

The active finality observation captured its first of three required batches in
the predeclared 2026-08-26 17:00-18:00 KST window. Six exact-tenor
`StatisticSearch` responses had provider-native date 2026-08-26 in common. The
official single-table information response was captured separately and reported
`prvsMrkYn=N` and `brknwsMrkYn=N`; this table-level UI flag is not treated as an
API row-level finality field. The batch used six data calls and one separate UI
call, retry zero, immutable diagnostic Landing first, and no Normalized write.
Retained readback and same-window replay both required API zero, and the runtime
credential was absent from every retained run file. Both complete-Landing state
recovery and the inverse state-first/checkpoint-second interruption reconcile
the exact retained evidence with API zero; a partial retry-zero capture remains
non-resumable.

Because this is the first batch, its next-provider-day field and canonical-row
byte comparison is pending. It establishes same-window availability only. It
does not establish earliest publication time, a permanent lag, or finality, so
automatic expected latest remains unset and `UNKNOWN`.

No exact automatic reviewed date can be selected from the currently documented
official rules. The bounded observation plan is owned by
[`BOK_ECOS_TREASURY_DAILY.md`](../../operations/BOK_ECOS_TREASURY_DAILY.md).
