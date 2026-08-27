# B008 official credit-balance source audit

## Decision

**Overall A005 replacement classification: SOURCE_BLOCKED.** No publicly
documented official source was found that supplies survivorship-safe,
full-universe, per-symbol daily margin-loan and stock-loan quantities/rates.
This audit made zero data/API calls and does not prove that such records do not
exist internally; it proves that no executable public product is currently
documented in the inspected official sources.

Two narrower results must remain separate:

- Existing market aggregate `kr_credit_balance_daily`: **DATA_COMPLETE remains
  unchanged** for its retained scope. It is not A005 and cannot be joined or
  renamed into A005.
- Potential pre-2021 market-aggregate extension through KOFIA FreeSIS:
  **PILOT_READY**, because the official site exposes credit-balance and executed
  share trend reports, but exact historical start, export schema and cutoff are
  not documented.

## Existing retained artifact

The local FSC/KOFIA artifact is one daily market-level row, not a symbol table:

| Measure | Verified local result |
|---|---:|
| Rows / PK | 1,158 / `(date)` |
| Coverage | 2021-11-09 through 2026-08-05 |
| Landing | one 302,043-byte full-history JSON response |
| State | coarse `full_history` completion marker |
| PK duplicates / nulls | 0 / 0 |
| Aggregate identities | financing total = KOSPI + KOSDAQ and stock-loan total = KOSPI + KOSDAQ on all 1,158 rows |
| Subscription-loan values | all 1,158 are source zero; zero must not be rewritten as missing |
| Weekday differences | 79 Monday-Friday dates absent; this count includes exchange holidays and possibly source gaps, so it is not labeled 79 missing observations |

The Landing body has no capture timestamp or call ledger, and the state does
not version source revisions. The artifact is reproducible from the retained
response but does not establish historical knowledge time.

## Product and category boundary

The official local OpenAPI guide defines `getGrantingOfCreditBalanceInfo` as
**daily credit-provision balance trend** and documents only monetary amount
fields:

| Concept | API fields | Grain |
|---|---|---|
| Credit transaction financing (`신용거래융자`) | total, KOSPI, KOSDAQ | date-level market amount |
| Credit transaction stock loan (`신용거래대주`) | total, KOSPI, KOSDAQ | date-level market amount |
| Subscription-fund loan (`청약자금대출`) | total only | date-level amount |
| Deposited-securities collateral loan (`예탁증권담보융자`) | total only | date-level amount |

KOFIA's official compliance material defines these as distinct credit-provision
methods. A stock loan here is credit transaction stock lending to support a
customer sale; it is not the broader securities-lending dataset and not short
selling itself. Collateral lending is a cash loan secured by deposited
securities, not a per-symbol financed-position balance.

Toss A005 instead targets `(date, symbol)` with margin-loan and stock-loan new,
return and balance **share quantities**, plus balance/trading ratios. The KOFIA
aggregate has no symbol, security class, new/return quantities or ratios.
Market aggregation removes the need for a current-symbol universe at the
aggregate level, but cannot repair or stand in for missing delisted-symbol rows.

## Unit finding

The official OpenAPI guide calls all eight values `금액` (amount) but does not
state won, thousand won or million won. The current local Dataset Contract
therefore has no declared unit for these columns.

There is strong value-level evidence for the retained current raw scale:

- retained 2026-07-24 financing total: `32,671,669,500,007`;
- the official KOFIA FreeSIS headline for that date displays `32,671,670` with
  unit `백만원`;
- dividing the retained value by 1,000,000 and rounding to the displayed whole
  million yields exactly `32,671,670`.

This supports interpreting the retained current value as won, but it is an
evidence-based cross-display inference, not an explicit field-unit declaration.
The local official guide's older 2022-09-29 example is `17,461,184`, a
million-style magnitude, whereas current retained history uses raw values near
10^13. Until KOFIA/FSC confirms the API's unit and any past scaling migration,
the source contract must not silently add `KRW` or rescale stored values.

## Ranked official-source matrix

| Rank | Official source | Scope and verified fields | Coverage / access / license | Decision |
|---:|---|---|---|---|
| 1 | FSC/data.go.kr `GetKofiaStatisticsInfoService/getGrantingOfCreditBalanceInfo` | Daily monetary balances for financing and stock loan by total/KOSPI/KOSDAQ; subscription and collateral loans total only | Local retained 2021-11-09..2026-08-05. Official service began 2021-11-16, but portal temporal coverage is blank. Free, auto-approved, JSON/XML, development quota 10,000, use permission unrestricted. Local guide says daily update once; portal metadata says real time | Existing market aggregate only; DATA_COMPLETE retained scope. Not A005 replacement |
| 2 | KOFIA FreeSIS: `신용공여 잔고 추이` and `신용거래 체결주수 추이` | Official site confirms two aggregate trend reports; home headline labels financing balance in million won | Exact first date, report dimensions, downloadable file format, publication cutoff, correction behavior and access terms not documented in inspected pages | PILOT_READY for older aggregate/volume history only |
| 3 | KOFIA/FSC formal data-definition or provision request | Source authority can confirm unit changes, daily cutoff, revision policy and whether member-reported symbol history is releasable | Documentation/contact route; no public symbol endpoint identified | Highest-value blocker-resolution step before any symbol collector |
| 4 | KRX public data / authenticated pykrx 1.2.8 | Local pykrx public inventory has no credit-balance function; the cached KRX menu inventory inspected here exposes no verified credit screen | No endpoint, field list, historical start, license or delisted coverage verified | SOURCE_BLOCKED; do not probe KRX merely on a guessed BLD |
| 5 | Bank of Korea publications | Official macro analysis uses aggregate credit-financing measures and defines credit provision | Publication/analysis, not a daily full-universe security-level source; no independent ECOS series was verified | Cross-context only, not a collection source |

Primary documentation:

- [FSC/data.go.kr KOFIA statistics OpenAPI product](https://www.data.go.kr/data/15094809/openapi.do)
- [KOFIA FreeSIS main statistics page](https://freesis.kofia.or.kr/stat/main.do)
- [KOFIA FreeSIS sitemap showing the two credit reports](https://freesis.kofia.or.kr/stats/siteMap.do)
- [KOFIA official definitions of credit provision, financing, stock loan and collateral loan](https://law.kofia.or.kr/service/law/detailArticlePrint.do?contentSeq=136609&historySeq=1097&seq=284)
- [BOK credit-provision glossary context](https://file-cdn.bok.or.kr/portal/7917169798ec4b4695ca912e251c1cd5/9/tempFile.pdf)

## Publication, revision and PIT boundary

- The local official guide says `일 1회`; the data.go.kr page currently labels
  the product `실시간`. These statements are not enough to establish a time-of-day
  cutoff.
- The KOFIA headline can lag the page's market date. No fixed T, T+1 or T+2
  rule is documented by the sources inspected.
- Neither the API fields nor the retained state provides `published_at`,
  `revised_at`, observation version or supersession identity.
- A later full-history query may return revised history. Future collections
  must retain immutable Landing bodies, capture timestamps and body hashes.
- Until the cutoff is confirmed, daily predictive use is no earlier than T+1
  after an independently observed publication. A historical `basDt` must not be
  treated as a knowledge date.

## Minimal defensible schema boundary

For the existing official aggregate only:

- dataset: `kr_credit_balance_daily`;
- layer: Normalized;
- primary key: `(date)`;
- one source date plus the eight documented amount fields already present;
- retain raw integers without conversion;
- unit remains `source_amount_unit_unverified` in documentation until official
  confirmation, despite the current FreeSIS scaling match;
- future captures need `captured_at`, Landing body hash and source operation in
  an observation/manifest layer rather than silently rewriting frozen rows.

No new per-symbol schema is proposed. The existing Toss A005 draft describes
the desired consumer shape, but an official source contract cannot be defined
before an official source supplies symbol identity, units and history.

## Exact minimal future pilot

First send one documentation request to KOFIA/FSC asking:

1. raw API unit now and historically, including any scaling migration;
2. earliest date for both FreeSIS credit reports;
3. publication time/cutoff and revision/backfill policy;
4. whether issuer/security-level financing and stock-loan history exists for
   all then-listed and later-delisted securities, and its access/license terms.

After a response, D may authorize a sequential FreeSIS manual pilot with **two
data retrievals maximum, zero retries, no parallelism**:

1. 2026-07-24 current sentinel for both displayed unit and parity against the
   retained API row;
2. 2020-01-02 historical sentinel, before retained API coverage, to test only
   whether the aggregate report extends backward.

Each response/export must be captured Landing-first with URL/report identity,
query scope, capture timestamp, file hash, displayed unit and row count. Empty
or unavailable is a terminal pilot result, not a retry trigger. Do not automate
history from two sentinels.

For a per-symbol source, make **zero pilot calls** unless KOFIA/FSC/KRX first
provides exact official documentation. If such a product is identified, the
first later pilot is one historical trading date, all markets, with a hard
single-business-call cap. It must verify stable symbol/ISIN identity, both
financing and stock-loan share/amount units, current and delisted inclusion,
publication timing, revisions and full-market pagination before a contract is
written.

## Blocker conclusion

The official aggregate is useful as a market credit/leverage indicator and can
potentially be extended backward through FreeSIS. It does not reduce A005's
missing symbol history by substitution. A005 remains blocked until an official
security-level historical source is documented or the project explicitly
changes the requirement to a market aggregate, which would be a different
dataset rather than a repair.

