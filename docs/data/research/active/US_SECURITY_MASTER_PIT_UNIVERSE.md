# US Security Master and PIT Universe — Design Draft

> Design only. This creates no contract, database, data artifact, or promotion policy.

## Objective and non-negotiable rules

The model must represent an instrument's identity separately from its mutable trading symbol. A delisted instrument is never deleted. A corporate action is not a symbol change, and a symbol change is not proof of entity continuity. Any field without source-effective and source-availability evidence is retained as observation-only and is `PREDICTIVE_USE_BLOCKED`.

## Logical tables

### 1. `us_security_master`

One row per `stable_security_id`, never reused. `stable_security_id` is project-assigned only after a provider-stable key and source evidence are accepted; it is not a ticker.

| field | rule |
|---|---|
| `stable_security_id` | Immutable project ID; source-backed, never inferred from ticker/name |
| `provider_security_id`, `figi`, `share_class_figi`, `cik` | Nullable source identifiers with `identifier_source`; none is assumed universal |
| `company_name`, `asset_type`, `is_etf`, `is_reit`, `exchange` | Observed attributes, versioned through effective-dated history rather than overwritten |
| `listing_date`, `delisting_date`, `delisting_reason` | Source observations; date semantics and reason vocabulary retained verbatim |
| `source`, `source_record_locator`, `collected_at`, `source_available_at` | Mandatory provenance/availability fields |

### 2. `us_security_symbol_history`

One row per symbol assignment interval, not per company.

Required fields: `stable_security_id`, `ticker`, `ticker_start_date`, `ticker_end_date`, `exchange`, `provider_symbol_id`, `relationship_type`, `relationship_evidence_locator`, `effective_from`, `effective_to`, `available_at`, `source`, `collected_at`.

`relationship_type` is nullable until sourced (`rename`, `successor`, `share_class`, `recycled_symbol`, `unknown`). A same ticker on adjacent dates cannot create a relationship. A ticker collision is an anomaly and blocks promotion.

### 3. `us_security_price_daily`

Grain: one source-labelled price representation per `stable_security_id`, `trade_date`, `price_basis`, `source_version`.

Required fields: raw `open/high/low/close/volume` exactly as delivered; `price_basis` (`RAW`, `SPLIT_ADJUSTED`, `DIVIDEND_AND_SPLIT_ADJUSTED`, `VENDOR_UNSPECIFIED`); `source_trade_date`; `published_at`/`available_at` when evidenced; `retrieved_at`; content hash; source locator. `VENDOR_UNSPECIFIED` is non-predictive until clarified.

### 4. `us_security_distribution`

Grain: one source event observation, not a canonical corporate action. Required fields: `provider_event_id` where available, `stable_security_id`, `event_type`, `ex_date`, `record_date`, `payment_date`, `declaration_date`, `split_from`, `split_to`, `cash_amount`, `currency`, `source_announced_at`, `available_at`, `source`, `collected_at`, `source_locator`.

Do not synthesize a dividend from a price gap or a split from a ratio. Multiple observations about one economic event remain distinct until a future versioned event contract is approved.

### 5. `us_security_universe_daily` (optional)

Grain: `universe_name`, `as_of_trade_date`, `stable_security_id`, `source_snapshot_id`. It records membership/eligibility only. Required evidence: source effective date/listing interval **and** availability timestamp. A current master reconstructed after the fact cannot populate a PIT universe. If a provider only gives historical index constituents, this table may be built only for that named index universe, not the all-listed market.

## PIT gates

| attribute/use | minimum evidence | state without it |
|---|---|---|
| Tradable/universe membership on D | effective interval plus when the provider made it available | `PREDICTIVE_USE_BLOCKED` |
| Daily price signal on D | provider EOD publication/correction cutoff | `PREDICTIVE_USE_BLOCKED` |
| Distribution-adjusted returns | event availability time and exact vendor adjustment method | `PREDICTIVE_USE_BLOCKED` |
| Delisting exit treatment | final trading date/reason and source availability | `PREDICTIVE_USE_BLOCKED` |
| Symbol continuity | explicit vendor relationship/ID evidence | `IDENTITY_UNRESOLVED` |

## Source mapping implications

- Norgate provides useful delisting suffixes and historical constituents, but its own lifecycle FAQ prohibits inferring identity from an old familiar ticker.
- Polygon can preserve composite/share-class FIGI, ticker, MIC, active/delisted fields, and a query date. Its current response is still a retrieved snapshot, not immutable historical-vintage evidence.
- SEC CIK identifies a filing registrant, not necessarily an individual listed security/share class. It belongs as a nullable reference identifier, not the primary price key.

## Explicitly deferred

No schema registration, physical table, database migration, Parquet materialization, full universe snapshot, backfill, canonical action matching, or predictive promotion is authorized by this draft.
