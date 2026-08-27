# U.S. Data Daily Maintenance Architecture — Draft

> Architecture only. Existing KRX scheduling, locks, retry policy, states, or Landing paths are not modified.

## Common control flow

```text
Provider-specific availability evidence
        -> missing-date / missing-release detection
        -> one provider-defined attempt
        -> Landing-first immutable capture
        -> schema + provenance + hash validation
        -> dataset-specific promotion gate
```

The shared framework must not impose a shared retry rule. Each source contract defines its own rate/access behavior, allowed request shape, expected availability delay, version/revision handling, and failure stop condition.

## Minimum run ledger (one source/date/release)

`source`, `dataset_candidate`, `requested_for_date`, `source_value_date`, `source_release_date`, `source_available_at`, `attempted_at`, `request_locator`, `http_or_transport_outcome`, `content_hash`, `schema_fingerprint`, `revision_indicator`, `landing_path`, `status`, `stop_reason`.

No source availability evidence means the job may record a non-attempted pending state; it must not invent a release time.

## Provider-specific policy matrix

| source family | availability trigger | collection unit | rate/access rule | revision rule | stop / promotion policy |
|---|---|---|---|---|---|
| Licensed U.S. OHLCV (Norgate/Polygon) | Vendor EOD schedule and licensed entitlement | Vendor archive/file or one dated API response | Contract-specific; no cross-provider concurrency assumption | Preserve source revision/version and raw/adjusted basis | Schema/identity/type anomaly stops family; promotion requires security-master and adjustment review |
| FINRA short-sale volume | FINRA documents by 18:00 ET trade date | Individual facility/consolidated daily file | Public access, but future runbook must specify polite bounded request rate | Retain original and explicitly marked Updated file separately | File family/column anomaly stops; no all-market aggregation promotion |
| FINRA short interest | Official publication date, not settlement date | One settlement-date release/file/API page | API/download policy is FINRA-specific | Preserve revision flag and retrieval version; current presentation may only expose latest | Missing publication evidence blocks predictive use; no daily interpolation |
| SEC EDGAR | Filing acceptance/dissemination or nightly bulk publication, depending source family | Accession/document or provider bulk snapshot | SEC guidance: no more than 10 requests/sec across machines; identify client as required by SEC policy | Retain accession/as-filed observation; nightly bulk files are separate versions | Schema/taxonomy/context anomaly blocks metric promotion, not raw retention if bytes are valid |
| Cboe P/C + VIX | Not established historically for every observation | Future authorized archive/file capture only | Do not automate until website terms and archive scope are accepted | Retain source file version | `PIT_BLOCKED`; no predictive promotion |

## Isolation and safety

- Every U.S. candidate receives a distinct `data/landing/us_<source>_<dataset>/` and `data/state/us_<source>_<dataset>/` namespace only after authorization.
- A failed attempt cannot overwrite a valid prior capture or checkpoint.
- A hash mismatch is a new source version, not a reason to silently replace bytes.
- A schema fingerprint change, unexpected identifier collision, missing required provenance, or undocumented price-basis change is fail-closed.
- Promotion is dataset-specific: an accepted daily price Landing capture does not authorize distribution, universe, short interest, or fundamentals promotion.

## Non-goals

This draft does not implement a scheduler, use any lock, initiate a request, create namespaces, or change KRX behavior. It intentionally preserves the existing project principles of a single documented attempt, 403/429 stop, and schema-anomaly fail-closed behavior without asserting that every U.S. provider has identical rules.
