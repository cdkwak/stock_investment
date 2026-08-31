# BOK ECOS 817Y002 publication and finality evidence

Status: `CURRENT SOURCE EVIDENCE / THREE_BATCH_GATE_REVIEWED`

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
- Canonical daily-route scheduler eligibility and unattended promotion remain
  disabled; the separate evidence observer is installed and API-zero idle at
  the reached gate.

## Reviewed three-batch observation

The predeclared 17:00-18:00 KST observations completed on 2026-08-26,
2026-08-27, and 2026-08-28. Each retained six exact-tenor `StatisticSearch`
responses with the same-day provider-native date common to all six tenors. Each
batch used six data calls and one separate UI call, retry zero, immutable
diagnostic Landing first, and no Normalized write. All three separately retained
`OSUUA02R03` responses reported `prvsMrkYn=N` and `brknwsMrkYn=N`; these remain
table-level UI evidence rather than row-level API finality fields.

The second batch retrieved the 2026-08-26 rows again and the third retrieved the
2026-08-27 rows again. In both comparisons every retained source field and every
canonical row SHA-256 matched. The version-1 state binds all three batch
identities, response hashes, selected rows, call counts, UI evidence, and
comparison results. Every checkpoint is `COMPLETE`; the latest checkpoint
hash-binds the current state. The 2026-08-29 scheduler receipt then stopped at
the reached review gate with batch count 3 to 3, API calls zero, and Normalized
writes zero.

This supports `BOUNDED_THREE_BATCH_AVAILABILITY_CONFIRMED` and the exact
provider-native fail-closed route gate documented in the active runbook. It does
not establish the earliest publication time, an official revision window, a
permanent lag, or predictive vintage, so `PERMANENT_FINALITY_UNKNOWN` and an
unset automatic expected latest remain the supported boundary. No historical,
Normalized, Canonical, or Dashboard promotion was performed.

No exact automatic reviewed date can be selected from the currently documented
official rules. The bounded observation plan is owned by
[`BOK_ECOS_TREASURY_DAILY.md`](../../operations/BOK_ECOS_TREASURY_DAILY.md).
