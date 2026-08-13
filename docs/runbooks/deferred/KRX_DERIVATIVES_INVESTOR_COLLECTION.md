# KRX derivatives investor collection (deferred)

Applies only to `kr_kospi200_futures_investor_trading_daily` and
`kr_kospi200_options_investor_trading_daily` from authenticated free KRX Basic
Statistics screen `[15007]`.

## Gates

- **Do not run:** KRX terms Article 10(2) and Article 12(2) leave automated
  collection/copying unauthorized. Resume only with retained explicit KRX permission
  evidence; record its SHA-256 digest in the checkpoint.
- Do not request dates before 1999-04-26 or synthesize the earlier target gap.
- Confirm that internal automated retention is permitted before a live pilot.
- Freeze the inspected request parameter mapping and response fields in retained
  evidence; the collector intentionally contains no guessed KRX parameter codes.
- Use one KRX stream, retry zero, at least five seconds between business calls.
- Stop immediately on HTTP 403/429, HTML/restriction bytes, authentication anomaly,
  non-JSON, or schema change.

## Scope and cost

The normalized grain is date × product × option-right × session × exact source
investor label. It preserves source sell, buy, and net-buy for volume and trading
value together with the selected source units. Futures use option-right `NA`;
options preserve `ALL`, `CALL`, and `PUT`; sessions preserve `ALL`, `REGULAR`, and
`NIGHT`.

The daily-trend query exposes one measure/side at a time and permits less than two
years per call. Through 2009-12-31 the frozen complete scope is 108 futures calls
and 324 options calls (six date chunks). This is materially larger than a default
ALL-session/ALL-right extraction and must be reviewed before production.

## Pilot and resume

1. Generate `bounded_pilot_plan(product)`: exactly one 1999-04-26..1999-05-02
   request, ALL session, ALL/NA right, sell volume.
2. Only after explicit permission, inject the separately reviewed authenticated
   transport into `collect_landing_serial` and supply the retained permission
   evidence SHA-256. Without it the function fails before creating artifacts or
   calling transport. Never put credentials or cookies in request specs.
3. The response bytes are atomically retained before validation. The append-only
   request ledger records scope, retry 0, status, hash, classification, and row count.
4. The checkpoint is updated after each verified response. Resume skips only
   completed request IDs after verifying every Landing hash; an orphan requires audit.
5. Validate exact row fields, source units, investor labels, date continuity, and
   source net = buy − sell before enabling normalization or a historical plan.

Landing location: `data/landing/krx_basic_statistics/derivatives_investor/<run_id>/`.
Checkpoint and ledger live inside the same immutable run directory. Published or
Normalized writes remain disabled until the bounded pilot establishes the raw field
mapping and terms gate passes.

Source evidence: [investor-statistics audit](../../data/audits/KRX_DERIVATIVES_INVESTOR_STATS_AUDIT.md).
