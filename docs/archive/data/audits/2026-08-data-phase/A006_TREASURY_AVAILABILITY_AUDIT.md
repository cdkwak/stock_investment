# A006 Korean Treasury availability audit

Reviewed: 2026-08-12 KST

Scope: determine whether the retained Toss `kr_treasury_yield_daily` artifact
can obtain a defensible knowledge-time rule, and whether an official replacement
source exists. This review used retained files and official documentation pages
only. It made no Toss, KRX, pykrx, BOK ECOS, KOFIA data, or other data-API call.

## Eligibility decision

The retained Toss artifact remains `ARTIFACT_COMPLETE /
PREDICTIVE_USE_BLOCKED`.

`source_date` can never make a daily OHLC candle usable earlier on that same
trading day: its high, low, close, and volume necessarily incorporate activity
through the observation period. It also cannot establish after-close knowledge
time. Toss supplies a midnight date label but no source update or publication
timestamp, and the historical collection timestamp is only when the backfill
was performed.

KOFIA documentation establishes a possible official *different* observation:
its afternoon final quotation yield is published at 16:30 KST. That value could
be eligible for a decision made after 16:30 on the same calendar date if an
immutable contemporaneous observation proves exact KOFIA identity and capture
time. It is not eligible at the 15:30 equity/bond close, and a date-only feature
contract should conservatively align it to the next trading session.

This official timing cannot be assigned to the Toss OHLC candle. The retained
Toss `close` differs from Bank of Korea published market-close values on several
checked dates, and the official final quotation product is a single calculated
yield, not an OHLC/volume candle.

Do not restore `availability_date = source_date`.

## Retained evidence

- Dataset: 11,162 rows, 2019-01-02 through 2026-08-10, instruments
  `KR_BOND_{2,3,5,10,20,30}Y`; PK `(date, instrument)`.
- Every source record contains a timestamp fixed at `00:00:00+09:00`. It is a
  daily bucket label, not a publication timestamp.
- The 60 immutable Landing envelopes have collection timestamps from the 2026
  historical build, so they do not reveal when old observations first became
  public or whether they were later revised.
- All 11,162 source records omit `updatedAt`; normalized `updated_at` and
  `availability_date` are therefore null.
- Yield OHLC values are decimal percentages. `volume` is a very large integer,
  but no retained or official Toss documentation defines whether it is face
  value, traded value, another aggregation, or its currency multiplier.

### Local-to-official close comparison

The comparison below is diagnostic identity evidence only. BOK pages were
published the next day and describe the preceding date's 3-year government-bond
yield as a market close. They do not prove when ECOS first exposed the value.

| Source date | Toss `KR_BOND_3Y close` | BOK published close | Difference (percentage points) | BOK page registration |
|---|---:|---:|---:|---:|
| 2026-01-12 | 2.982 | 2.980 | +0.002 | 2026-01-13 |
| 2026-02-11 | 3.200 | 3.200 | 0.000 | 2026-02-12 |
| 2026-02-24 | 3.171 | 3.158 | +0.013 | 2026-02-25 |
| 2026-03-30 | 3.541 | 3.542 | -0.001 | 2026-03-31 |
| 2026-04-07 | 3.457 | 3.451 | +0.006 | 2026-04-08 |
| 2026-04-15 | 3.321 | 3.328 | -0.007 | 2026-04-16 |
| 2026-06-11 | 3.903 | 3.904 | -0.001 | 2026-06-12 |

Exact matches on isolated dates do not override the mismatches. The Toss close
must remain provider-native unless its upstream methodology is documented.

## Official source matrix

| Candidate | Verified documentation | Time semantics | Tenor/field fit | History/revision status | Decision |
|---|---|---|---|---|---|
| KOFIA final quotation yields | KOFIA rules require reporting at 11:30 and 15:30 and publication at 12:00 and 16:30. The final yield is the trimmed arithmetic mean of designated firms' final transaction or quotation yields. | Afternoon value is public by 16:30 KST under the documented rule. It is not known at the 15:30 market close. | Current rules cover 2Y, 3Y, 5Y, 10Y, 20Y, 30Y (and other tenors). A single percent yield rounded to three decimals; no OHLC or Toss-volume equivalent. | Historical download availability and observation-level correction/supersession timestamps were not established by documentation. | Best official replacement candidate for **daily close yield only**; not a lossless replacement for Toss candles. |
| BOK ECOS market-interest-rate series | BOK describes ECOS as its official statistics portal, including external agencies' statistics, downloadable tables, a publication calendar, and Open API access. BOK daily market-indicator pages publish prior-day government-bond closing yields. | Checked daily BOK pages are registered T+1. ECOS first-availability time for the corresponding daily table remains unverified. | Likely close-yield series, not OHLC/volume. Exact table/item codes, six-tenor coverage, units, source agency, and start dates require metadata verification. | Revision flags, vintage access, and value correction history remain unverified. | Strong distribution/replacement candidate; requires a bounded non-KRX metadata/value pilot. |
| MOEF KTB portal | MOEF defines KTB issuance tenors as 2Y, 3Y, 5Y, 10Y, 20Y, 30Y, and 50Y and exposes a KTB yield-trend area. It describes exchange trading as 09:00-15:30 and ordinary OTC business as 08:30-15:30. | Market hours bound when a close can exist; the portal documentation does not state yield publication timestamps. | Confirms nominal issuance tenor identity, not the exact Toss synthetic/benchmark candle method. | Historical download and revision semantics not documented in the reviewed pages. | Authoritative market/tenor context; not yet a machine-source replacement. |
| KRX bond market documentation | KRX documentation describes the exchange bond market and 09:00-15:30 trading. Separate KTB-futures documentation says KOFIA distributes basket-bond yields to KRX/KOSCOM/vendors at specified times. | Establishes market/session boundaries, not the Toss candle's publication time. | Instrument-level exchange prices/yields are not the same object as a generic nominal-tenor OHLC candle without a verified selection/roll method. | No reviewed documentation mapped revisions or a historical nominal-tenor OHLC product. | Context only. No KRX request may be made while A007 owns the stream. |

## Tenor identity and units

The Toss symbols are verified only as provider labels for nominal maturities.
They must not be described as one immutable bond issue or a constant-maturity
curve without upstream methodology.

KOFIA's official final-quotation definitions are more precise: the current
residual-maturity ranges are 1y9m-2y, 2y6m-3y, 4y6m-5y, 9y6m-10y, 19y-20y,
and 29y-30y. The newly issued benchmark enters the report set on the day after
its auction. That creates a changing benchmark identity even though the tenor
label remains stable.

The official KOFIA yield unit is percent and is rounded at the fourth decimal
place to three decimals. This is compatible in scale with Toss yield values but
does not prove equality. There is no defensible official mapping for Toss
`volume`; keep its unit unknown and do not publish arithmetic based on it.

## Corrections and revisions

The reviewed KOFIA rule pages define calculation and scheduled publication but
do not define an observation-level correction timestamp, revision flag,
supersession key, or historical-vintage retrieval mechanism. The reviewed BOK,
MOEF, and KRX documentation likewise does not prove whether a previously
published daily observation can be silently replaced.

Any future official collector therefore needs immutable Landing observations
keyed by source identity plus `captured_at`, with content hashes and append-only
revision comparisons. A changed value must create a new observed version; it
must not overwrite the earlier vintage. Predictive features may use only the
version captured before their decision cutoff.

## Exact remaining unknowns

1. Toss's upstream source, benchmark-security selection, OHLC construction,
   session cutoff, holiday policy, and publication latency.
2. Whether Toss `volume` is face value, traded amount, or another measure and
   its multiplier/currency.
3. The exact ECOS table and item codes for all six desired tenors, official
   source agency, unit, first date, publication schedule, revision flags, and
   whether vintages are accessible.
4. KOFIA public historical-download/API mechanism, earliest date for each tenor,
   licensing terms, and correction/supersession policy.
5. Whether an official KOFIA/ECOS close series matches any Toss field over a
   sufficiently broad boundary sample. Current spot comparisons reject assumed
   equality.

## Bounded non-KRX pilot plan (not executed)

The useful next pilot is official close-yield identity and timing, not another
Toss backfill.

1. Use only a separately approved BOK ECOS or KOFIA channel; make no KRX call.
2. Fetch metadata first: table/item codes, labels, units, source agency,
   publication schedule, start/end coverage, and revision metadata.
3. With an explicit maximum of 16 data observations, request two tenors (2Y and
   3Y) on four fixed dates: one recent normal date, the 2021 2Y introduction
   boundary, one retained source-gap date, and one early 2019 date.
4. Record raw response, HTTP timestamp, source observation date, publication or
   update metadata, revision indicator, unit, and a hash in a diagnostic ledger.
5. Compare official values to retained Toss open/high/low/close without forcing
   a match. Classify exact match, field-only match, or distinct series.
6. If the official channel exposes only current history with no vintage or
   publication timestamp, retain a conservative T+1 availability rule and
   append future observations to measure revisions.
7. Only after identity and history pass should D define a separate Normalized
   official-close contract. It should coexist with, not overwrite, the Toss
   OHLC artifact.

## Primary documentation

- KOFIA, `금융투자회사의 영업 및 업무에 관한 규정`, current rule amended
  2026-02-26, Article 7-8:
  https://law.kofia.or.kr/service/law/detailArticlePrint.do?contentSeq=303212&historySeq=1779&seq=136
- KOFIA, implementing rules, Article 51 (12:00/16:30 publication; tenor and
  residual-maturity definitions):
  https://law.kofia.or.kr/service/law/lawFullScreenContent.do?historySeq=1645&seq=137
- KOFIA, final quotation yield reporting standard (definition and reporting
  basis):
  https://law.kofia.or.kr/service/law/lawFullScreenContent.do?historySeq=1577&seq=178
- MOEF KTB portal, distribution-market hours:
  https://ktb.moef.go.kr/distbMrktIntrcn.do
- MOEF KTB portal, government-bond tenor definitions:
  https://ktb.moef.go.kr/ntpbnd.do
- KRX Global, exchange bond-market trading hours:
  https://global.krx.co.kr/contents/GLB/06/0604/0604010100/GLB0604010100T2.jsp
- KRX Global, KTB-futures yield distribution context:
  https://global.krx.co.kr/contents/GLB/02/0201/0201040506/GLB0201040506.jsp
- BOK, ECOS service description and Open API availability:
  https://www.bok.or.kr/portal/bbs/B0000522/view.do?menuNo=201692&nttId=10070977
- BOK daily market-indicator examples used in the local comparison (page IDs):
  `10095655`, `10096462`, `10096625`, `10097248`, `10097397`, `10097548`,
  and `10098467`, under
  `https://www.bok.or.kr/portal/bbs/P0002018/view.do?menuNo=200366&nttId=<page-id>`.
