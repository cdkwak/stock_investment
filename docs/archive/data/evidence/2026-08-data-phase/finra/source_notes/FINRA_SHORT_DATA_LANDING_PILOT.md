# FINRA Short Data Landing-Only Pilot

## Scope and boundary

This is a bounded Landing-only source acceptance pilot.  It keeps FINRA Daily
Short Sale Volume and FINRA Equity Short Interest as separate source families;
it does not derive short interest from daily volume, transform participant
meaning, or create Normalized, Canonical, Derived, or Published data.

The intended target symbols were `SPY`, `QQQ`, `TQQQ`, `AAPL`, and `NVDA`.
The requested daily trade date was `2026-08-11`; the intended short-interest
settlement date was `2026-07-31`.

## Official source assessment

| Family | Official delivery | Coverage/publication evidence | Meaning and PIT handling | Access and license finding |
|---|---|---|---|---|
| Daily Short Sale Volume | Daily text file, including the consolidated NMS `CNMS` facility | FINRA says files are posted no later than 6:00 p.m. ET on the trade date and may be replaced with an updated file.  FINRA's historical notice says dissemination began 2009-11-09 and included files from 2009-08-03; the current Daily page identifies the consolidated NMS series beginning 2018-08-01. | This is FINRA-reported off-exchange short-sale transaction volume, not short interest and not a whole-market exchange-consolidated volume measure.  A future ingestion needs a version policy and must retain the observed publication/capture time.  `PIT_BLOCKED` pending that contract. | The data is publicly downloadable.  No authoritative rate-limit or storage/redistribution grant was established in this audit; `LICENSE_REVIEW_REQUIRED`. |
| Equity Short Interest | FINRA download/API documentation describes a POST download endpoint | FINRA describes twice-monthly reporting: members report by 6:00 p.m. ET on the second business day after settlement, and FINRA publishes on the seventh business day after settlement.  Historic files are available from 2014; the archive notes that before June 2021 its historical files covered OTC securities only. | This is a position report and must use the actual settlement/reference date and the official public-availability date.  The documented API field `averageShortShareNumber` is FINRA's average daily volume measure; it is required by the parser and is never inferred from Daily Short Sale Volume.  `PIT_BLOCKED` pending a verified availability-date capture policy. | The catalog, downloads, and documented API are public.  Rate, retention, and redistribution terms were not conclusively verified; `LICENSE_REVIEW_REQUIRED`. |

Primary references: [Daily Short Sale Volume](https://www.finra.org/finra-data/daily-short-sale-volume-transaction-data), [FINRA historical dissemination notice](https://www.finra.org/rules-guidance/notices/information-notice-051019), [Equity Short Interest catalog](https://www.finra.org/finra-data/browse-catalog/equity-short-interest), [data glossary](https://www.finra.org/finra-data/browse-catalog/equity-short-interest/glossary), and [Equity Short Interest download API guide](https://www.finra.org/sites/default/files/Equity_Short_Interest_Data_File_Download_API.pdf).

Symbol fields are source symbols, not a stable cross-provider security identifier.  Delisted retrieval and ticker/identifier continuity remain unverified.  Amendments/replacements are explicit for Daily Short Sale Volume; short-interest amendment/version semantics remain a contract question.

## Bounded live pilot result

Run ID: `20260816T144327Z_326aae2007c340b181f062bbc1bf876b`.

One official Daily Short Sale Volume request was made for `CNMSshvol20260811.txt`.
The strict parser rejected row 2 because `ShortVolume` was not an integer.  Per
the fail-closed rule, the run stopped immediately with `SOURCE_BLOCKED`; it
made no retry, performed no target-row extraction, and did not call the Equity
Short Interest API.  The resulting state and manifest record `captures: []`,
`retry_count: 0`, and `normalized_or_canonical_created: false`.

### Capture incident

The first pilot implementation parsed the response before committing its raw
bytes.  Consequently, the failed response body, SHA-256, headers, and exact
request provenance were not retained.  This is an implementation failure, not
a conclusion about the provider.  The failed run is therefore not an accepted
Landing receipt, and its content must not be reconstructed, inferred, or
redownloaded under this pilot.

The collector has been corrected so that any future validation failure first
creates an `unvalidated_response` Landing receipt containing the original
bytes, SHA-256, URL, request metadata, response headers, collection timestamp,
and validation failure; it then stops without a follow-on source call.  A
fixture test covers this ordering.  No additional live request was made after
the correction.

## Verdict and blockers

| Family | Pilot verdict | Backfill readiness | Blocking condition |
|---|---|---|---|
| Daily Short Sale Volume | `PILOT_STOPPED_SCHEMA_ANOMALY / SOURCE_SEMANTICS_UNRESOLVED` | Not ready | New raw-first receipt has mixed integer/decimal volume literals plus a trailing count literal; no official type/footer contract was located. |
| Equity Short Interest | `SOURCE_BLOCKED` | Not ready | Independent documented POST received HTTP 204 with zero records; no fallback date or retry was used. |

Neither family is approved for Raw backfill.

> Superseded interpretation: the 2026-08-16 HTTP-204 response is not
> `SOURCE_BLOCKED`, because it proves a FINRA server responded.  The offline
> documentation reconciliation classifies the current Short Interest route and
> response meaning as `DOCUMENTATION_INSUFFICIENT`.  See
> [offline audit](FINRA_OFFLINE_DOCUMENTATION_AUDIT_20260817.md).

## Family-scoped revalidation (2026-08-16)

The source families were split into independent pilots. Each request below was
made once only; no retry, fallback date, alternative facility, or symbol-level
follow-up was made.

### Daily Short Sale Volume

- Raw receipt: `data/landing/finra/daily_short_sale_volume_pilot/20260816T145124Z_3b66d5afc5ca45ca93b093e97e13a6c6/daily_short_sale_volume/response.body`
- Provenance: corresponding `provenance.json`; HTTP `200`, GET,
  `2026-08-16T14:51:25.666156Z`, 536,672 bytes, SHA-256
  `a095212f3b519e12972d7026e9620b4044cb91f88712ab0bb7c09af89af3f87c`.
- The raw body was committed before parsing. Its declared six-column header is
  `Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`.
- Offline-only inspection found 12,181 six-field rows and a final one-field
  literal `12181`. `ShortVolume` literals were 4,437 integer and 7,744
  decimal; `ShortExemptVolume` was 12,037 integer and 144 decimal;
  `TotalVolume` was 769 integer and 11,412 decimal. The five target rows all
  contain decimal `ShortVolume` and `TotalVolume` values.

The collector stopped at the first decimal value without coercion. FINRA's
public Daily documentation explains field meaning, but this audit did not
locate an official type definition that authorizes decimal quantities or a
trailing record-count footer. The observation is `UNRESOLVED`, not a malformed
provider row or a parser defect. The parser remains unchanged: no
`int(float())`, rounding, filtering, or missing-value policy is permitted
without official schema evidence. Historical Raw backfill is not permitted;
PIT remains `PIT_BLOCKED`.

### Equity Short Interest

- Raw receipt: `data/landing/finra/short_interest_pilot/20260816T145223Z_d3d64ef4a0464ec693e90de4bc5652c1/short_interest/response.body`
- Provenance: corresponding `provenance.json`; POST with documented
  `settlementDate=2026-07-31`, captured at `2026-08-16T14:52:24.586717Z`.
- The official endpoint returned HTTP `204`, zero bytes (SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`) and
  `record-total: 0`.

No payload means no independently observed symbol, identifier, short-interest
value, `averageShortShareNumber`, duplicate/null-key behavior, or confirmed
public-availability field. No different settlement date was requested to
avoid implicit retry or date-selection policy. FINRA's documented archive
coverage remains evidence only (historic files from 2014, and pre-June-2021
history limited to OTC), not a verified retrieval result. Historical Raw
backfill is not permitted, and this family remains `PIT_BLOCKED` pending a
successful, separately authorized receipt and a verified availability-date
contract.
