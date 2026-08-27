# Data Candidate Queue

These files retain candidate, blocked, or promotion-gated operation designs.
They are reference material only. Their presence does not authorize execution,
provider calls, dataset mutation, promotion, backup creation, or restore.

To activate one, Data Status must select the exact scope and route it to an
applicable operation under `docs/data/operations/`. Until then, agents should
not read these files during normal startup.

| Candidate | Current reason for retention |
|---|---|
| [KRX equity fundamental Raw daily](KRX_EQUITY_FUNDAMENTAL_RAW_DAILY_REVIEW_REQUIRED.md) | Publication, revision, duplicate, and PIT semantics remain gated |
| [KRX ETF Raw daily incremental](KRX_ETF_RAW_DAILY_INCREMENTAL_REVIEW_REQUIRED.md) | Candidate incremental procedure; not selected by Data Status |
| [KRX foreign ownership Raw daily](KRX_FOREIGN_OWNERSHIP_RAW_DAILY_REVIEW_REQUIRED.md) | Publication/finality and cross-source semantics remain gated |
| [Local data backup and restore](LOCAL_DATA_BACKUP_RESTORE.md) | Fixture-verified design; production selection and restore promotion remain gated |
