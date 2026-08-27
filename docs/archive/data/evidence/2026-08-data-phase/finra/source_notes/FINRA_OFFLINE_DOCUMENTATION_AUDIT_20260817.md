# FINRA Offline Documentation Audit — 2026-08-17

No FINRA API, CDN, or other FINRA data request was made for this audit.  The
only source observations used are the already-preserved 2026-08-16 Landing
receipts and FINRA's public documentation.

## Status summary

| Family / question | Status | Evidence-based conclusion |
|---|---|---|
| Daily CDN-file `ShortVolume`, `ShortExemptVolume`, `TotalVolume` exact type, unit, fraction policy, and footer | `DOCUMENTATION_INSUFFICIENT` | FINRA documents Daily short-sale data as aggregated volume by security, and current API examples show the parallel `shortParQuantity`, `shortExemptParQuantity`, and `totalParQuantity` as whole-number examples.  It does not publish an authoritative type/unit schema for the historical CDN file fields, permission for fractions, or a footer specification. |
| Daily 2026 CDN-file format change | `DOCUMENTATION_INSUFFICIENT` | FINRA documents versioning for its Query API, but no official change notice or CDN-file version/schema history was found.  The captured file's decimal literals and final `12181` line cannot be treated as a documented new format. |
| Legacy `EquityShortInterest` POST body and `settlementDate` equality filter | `CONFIRMED_SCHEMA` | FINRA's still-published guide explicitly shows `compareFilters`, `compareType: EQUAL`, `fieldName: settlementDate`, ISO `YYYY-MM-DD` `fieldValue`, and the `EquityShortInterest` POST endpoint. |
| Current short-interest API route and field schema | `CONFIRMED_SCHEMA` | FINRA's current Developer Center identifies `otcMarket/consolidatedShortInterest`, with fields including `symbolCode`, `settlementDate`, `currentShortPositionQuantity`, `averageDailyVolumeQuantity`, and `revisionFlag`; it states availability by 4:40 p.m. ET on publication date. |
| How to discover available settlement values before a data request | `REQUEST_SEMANTICS_RESOLVED` | FINRA documents `GET /partitions/group/{group}/name/{dataset}` as returning partition fields and available partition values.  Metadata identifies a dataset's partition fields.  Calling it is deferred because this audit prohibits extra API requests. |
| 2026-08-16 HTTP 204 with `record-total: 0` | `DOCUMENTATION_INSUFFICIENT` | FINRA defines `Record-Total` as total records found at request time and defines successful synchronous responses as HTTP 200, but it does not define HTTP 204 for this endpoint.  The receipt proves the server was reached and reported zero records, not that access was impossible. |
| Historical files versus API | `CONFIRMED_SCHEMA` | FINRA says the interactive grid has five rolling years, the Equity API has up to five rolling years, and historical download files are available; archive files reach 2014 and pre-June-2021 files cover OTC only. |

## Daily Short Sale Volume

The existing receipt has the header
`Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`, 12,181
six-field rows, and a final single literal `12181`.  It also has decimal
literals in all three volume columns.  Those facts are preserved observations,
not a permission to parse the columns as decimal numbers or to drop the final
line.

FINRA's current Query API calls the analogous fields
`shortParQuantity`, `shortExemptParQuantity`, and `totalParQuantity`; its
public examples are integers.  The documentation establishes that the data is
daily aggregated short-sale volume reported to FINRA trade-reporting
facilities, but it does not define whether the CDN fields have identical types,
whether `ParQuantity` applies to all records, or whether the file contains a
record-count footer.  The current parser must remain fail-closed.

## Equity Short Interest

The legacy guide is internally clear: a POST `compareFilters` object with
`EQUAL` `settlementDate` and an ISO date is valid for
`EquityShortInterest`; its field is `averageShortShareNumber`.  The prior
HTTP-204 receipt therefore is not evidence of denied access.

However, official FINRA documentation is not fully reconciled.  A 2021 FINRA
notice says the prior `equityShortInterest` dataset would cease publication
after 2021-04-30 and be replaced by a standardized dataset, while the current
Developer Center presents `consolidatedShortInterest` and different field
names.  The legacy guide remains published and names the former route.  No
offline documentation establishes an equivalence map, transition date, or
whether the old endpoint is expected to return HTTP 204 for a valid 2026 date.
No parser or request route is changed on that basis.

For a future, separately authorized pilot, `GET /metadata/...` and
`GET /partitions/...` are the documented preflight sequence.  The first
identifies the current partition field and data types; the second supplies the
available settlement values, avoiding date guessing.  That future operation
must preserve its response before parsing and must not rely on the legacy
endpoint until the official route/field transition is resolved.

## Consequences

Both families remain `PIT_BLOCKED`; neither is ready for historical Raw
backfill.  `SOURCE_BLOCKED` is not used: both existing receipts show that a
FINRA server responded.  This audit does not alter the preserved raw bodies,
their SHA-256 values, or their provenance records.

## Official references

- [FINRA Daily Short Sale Volume](https://www.finra.org/finra-data/daily-short-sale-volume-transaction-data)
- [FINRA Developer Center — Reg SHO Daily](https://developer.finra.org/docs/api-explorer/query_api-equity-reg_sho_daily_short_sale_volume)
- [FINRA Query API documentation](https://developer.finra.org/docs)
- [Legacy Equity Short Interest download API guide](https://www.finra.org/sites/default/files/Equity_Short_Interest_Data_File_Download_API.pdf)
- [Current Equity Short Interest catalog](https://www.finra.org/finra-data/browse-catalog/equity-short-interest)
- [Equity Short Interest files](https://www.finra.org/finra-data/browse-catalog/equity-short-interest/files)
- [2021 FINRA API transition notice](https://www.finra.org/filing-reporting/otc-transparency/otc-transparency-api-changes)
