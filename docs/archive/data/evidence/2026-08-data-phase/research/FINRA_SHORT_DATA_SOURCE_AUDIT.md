# FINRA U.S. Short Data Source Audit

> Official FINRA sources only. Audit only; no file/API download was performed.

## Decision

Treat FINRA daily short-sale volume and FINRA equity short interest as separate source families. They cannot be merged into one economic variable and daily short-sale volume must never be labelled short interest.

## A. Daily Short Sale Volume

| property | verified finding |
|---|---|
| Historical start | FINRA began publication 2009-11-09; daily data initially included files from 2009-08-03. Current page documents later file-family starts, including Consolidated NMS 2018-08-01 and Chicago TRF 2018-09-10. |
| Delivery | Public daily text files, browseable by month/year. No official bulk-all-history archive/API entitlement was identified in this audit. |
| Grain / fields | Aggregate regular-hours volume by security and reporting facility. FINRA documents trade date, symbol, short volume, short-exempt volume, total volume, and market/venue context. |
| Venue meaning | Files cover trades reported to FINRA TRFs, ADF, or ORF for public dissemination; Consolidated TRF/ADF combines exchange-listed TRF/ADF reports. It excludes non-publicly-disseminated activity and is not consolidated with exchange data. |
| Publication / revisions | Posted no later than 18:00 ET on trade date. FINRA can later publish a distinct `Updated` file and says it retains original and updated files. |
| Semantic boundary | It is executed/reported short-sale **volume**, not end-of-day short position and not all-U.S.-market short volume. A complete listed-security view would require applicable exchange files as well. |
| Price / access | FINRA catalog labels this public/no-fee. No separate redistribution/storage license conclusion was found; preserve FINRA website terms review as a pre-collection gate. |
| PIT | Potentially usable after documented 18:00 ET only if the exact original/updated file and retrieval version are retained. Predictive use remains blocked until revision selection policy is approved. |

Suggested Landing observation identity: `source_file_family + trade_date + market_center + symbol + source_file_hash`. Never sum facilities into a purported all-market total unless every included/excluded venue is documented.

## B. Equity Short Interest

| property | verified finding |
|---|---|
| Coverage | FINRA says it publishes reports collected from broker-dealers for exchange-listed and OTC equity securities. Archive files go to 2014; pre-June-2021 files contain OTC positions only, not exchange-listed positions. |
| Frequency / date | Twice monthly. The value is a position snapshot on FINRA's designated settlement date (mid-month and month-end cadence), not a daily observation. |
| Availability | Firms report by 18:00 ET on the second business day after the settlement date; FINRA publishes on the seventh business day after settlement. Preserve both dates. |
| Fields | Settlement date, issue name, symbol, primary market, current/previous short shares, change, average daily volume, days to cover, revision flag. `symbol` is called a unique issue identifier by the glossary but must not be assumed stable across external vendors. |
| Delivery | Interactive grid: five rolling years; historical download files; FINRA documents an API for Equity Short Interest. Public/no-fee catalog listing. |
| Revision | FINRA exposes a revision flag and makes only the most recent data available in the interactive description. Original-versus-revised bytes must be retained by this project if acquisition is later authorized. |
| PIT | The earliest defensible `available_at` is the official publication date, not settlement date, filed/received date, or a guessed lag. Use is `PREDICTIVE_USE_BLOCKED` if historical reporting calendars cannot be retained. |

Suggested Landing observation identity: `settlement_date + symbol + market + source_release_version/hash`; preserve `publication_date`, `update_datetime` if delivered, and `revision_flag` verbatim.

## No-combination rule

Daily short-sale volume is a flow of reported executions; short interest is a semi-monthly position snapshot. Neither short-volume ratio nor days-to-cover is a substitute for the other, and no daily interpolation of short interest is permitted.

## Official sources

- Daily Short Sale Volume: <https://www.finra.org/finra-data/daily-short-sale-volume-transaction-data>
- FINRA interpretation notice: <https://www.finra.org/rules-guidance/notices/information-notice-051019>
- Equity Short Interest overview: <https://www.finra.org/finra-data/browse-catalog/equity-short-interest>
- Equity Short Interest files: <https://www.finra.org/finra-data/browse-catalog/equity-short-interest/files>
- Field glossary: <https://www.finra.org/finra-data/browse-catalog/equity-short-interest/glossary>
- Reporting calendar: <https://www.finra.org/filing-reporting/regulatory-filing-systems/short-interest>
- API guide: <https://www.finra.org/sites/default/files/Equity_Short_Interest_Data_File_Download_API.pdf>
