# SEC U.S. Fundamental and Filing Source Architecture Audit

> Official SEC EDGAR/data.sec.gov only. Architecture proposal; no SEC API or bulk archive was downloaded.

## Source families and purpose

| source | coverage / update | acquisition shape | correct role | PIT warning |
|---|---|---|---|---|
| `submissions` API | Filing history by CIK; JSON updates in real time as filings disseminate | Per-CIK JSON; `submissions.zip` is nightly bulk | Filing/disclosure metadata, former names, exchanges and tickers | Current response is mutable; retain accession and retrieval version |
| `companyfacts` API | XBRL facts across company submissions; XBRL required since 2009 | Per-CIK JSON; `companyfacts.zip` nightly bulk | Source observation access for standard taxonomy facts | API aggregation must not select one canonical period fact |
| Company-concept API | One CIK/taxonomy/tag | Per CIK-concept JSON | Narrow source validation | A tag is not an economic concept across all filers without mapping |
| XBRL frames API | Cross-entity calendar-aligned latest fact | REST | Exploratory comparison only | It selects a last-filed fact: unsuitable as immutable as-filed observation store |
| Financial Statement Data Sets | Quarterly ZIPs from 2009 Q1 onward; quarterly update | `SUB`, `TAG`, `NUM`, `PRE` TXT | Bulk flattened **as-filed** primary-statement observations | Reprocessing/republication requires archive version/hash; it is not a substitute for original filing metadata |
| EDGAR filing documents | 10-K, 10-Q, 8-K and amendments/variants | Filing index/complete submission/accession files | Authoritative document and acceptance evidence | Availability is filing dissemination, not reporting period end |

SEC documents that the APIs contain submissions and XBRL for 10-Q, 10-K, 8-K, 20-F, 40-F, 6-K and variants. The API JSON is updated in real time as filings disseminate, while `companyfacts.zip` and `submissions.zip` are republished nightly around 03:00 ET. SEC guidance limits automated access to 10 requests/second across machines.

## Raw observation grain: `us_company_fundamental_observation`

**One row per fact as filed in one filing context; never one row per company-period canonical value.**

Minimum fields:

```text
observation_id                 # source/accession/context/tag/unit keyed, not ticker
cik
accession_number (adsh)
form_type
filing_document_url
is_amendment
amends_accession_number        # null until explicit evidence exists
filed_date
accepted_at                    # EDGAR acceptance timestamp
available_at                   # source-backed project availability policy
source_retrieved_at
source_version_hash
period_start
period_end
fiscal_year (fy)
fiscal_period (fp)
duration_quarters (qtrs)
instant_or_duration
taxonomy
tag
tag_version
is_custom_taxonomy
unit
decimals
value
dimensions_or_context_id
coregistrant
source_family                  # EDGAR_DOCUMENT / COMPANYFACTS / FSDS
source_locator
```

In FSDS `NUM`, the documented distinct fact key includes `adsh`, `tag`, `version`, `ddate`, `qtrs`, `uom`, and `coreg`. Direct XBRL facts can need further context/dimension identity. Never deduplicate solely by CIK, tag, period end, or fiscal period.

## Period, filing and availability semantics

| field | meaning | disallowed substitution |
|---|---|---|
| `period_end` | Economic reporting-period end/context | Public availability |
| `filed_date` | SEC filing calendar date | Acceptance time |
| `accepted_at` | SEC acceptance timestamp | Nightly bulk publication time |
| `available_at` | Explicit source-backed usability policy | Guessed lag from period end |
| `source_retrieved_at` | When this project observed bytes | SEC publication time |

For a future PIT policy, `available_at = accepted_at` may be considered only for an original EDGAR filing after acceptance/dissemination evidence is retained. Company Facts and FSDS are derivative delivery channels and must retain their own collection/republication time; neither may replace a filing's availability evidence.

## Amendments, restatements, and taxonomy changes

- Preserve 10-K/A and 10-Q/A as additional observations. A subsequent-amendment indicator is not a mandate to overwrite the original.
- Preserve taxonomy/tag/version, custom-tag status, context/dimension, unit, decimals, form, accession and timestamps exactly.
- A future canonical value must explicitly specify metric mapping, dimensions, period selection, amendment policy, and availability policy. This audit defines none.

## CIK and ticker mapping

CIK is the primary EDGAR registrant reference. SEC submissions metadata can contain current/former names, exchanges and tickers, but is not a historical listed-security master. Ticker-to-CIK can be many-to-many over time. Join it to a security master only through an independently evidenced effective-dated relationship.

## Architecture decision

SEC is a high-value official Raw filing/fact source, not a ready canonical fundamentals dataset. Recommended future sequence: preserve filings/observations Landing-first; retain amendments and alternatives; define metric-specific contract and availability rules offline; then consider promotion. This document authorizes no bulk download, scheduler, retry, contract, or promotion.

## Official sources

- EDGAR APIs/bulk/update schedule: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- SEC developer resources/rate guideline: <https://www.sec.gov/about/developer-resources>
- Financial Statement Data Sets: <https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets>
- FSDS technical documentation: <https://www.sec.gov/file/financial-statement-data-sets>
- EDGAR filings/search: <https://www.sec.gov/search-filings>
