# KB Securities daily market snapshot

Status: **RETAINED_LANDING_COMPARISON_RECOVERED_API0 / PARTIAL_NORMALIZED_QUARANTINED_IN_PLACE / PUBLICATION_BLOCKED**

Current semantic authority: [KB snapshot contract](../research/active/KBSEC_SNAPSHOT_CONTRACT.md).
This operation may capture evidence, but it must not publish rows that fail the
contract's independent per-slice date and availability rules.

The target is one provisional `IVSA0070` snapshot per Korean trading day at
approximately 17:00 KST. This is a lightweight point-in-time observation stream,
not an official historical replacement and not continuous polling.

The 2026-08-21 selected operation is terminal. Its sole post-close invocation
made OAuth 1 + IVSA0070 1, both HTTP 200, retry zero. A historical same-date
predicate incorrectly selected `SCHEDULED_DAILY`, entered the prohibited
Normalized writer, made a partial breadth mutation, then raised a local mixed
`Timestamp`/`str` sort error before its original state append. The incident is
now durably journaled; direct same-date replay is API 0. Retained Landing and
the corrected-auth baseline were hash-gated before a local seven-slice recovery:
global symbols remain `DATE_UNRESOLVED`, liquidity is `LAGGED_SOURCE_DATE`, and
the other candidates remain pending independent review. The three accidentally
touched breadth partitions are quarantined in place as `DO_NOT_USE_OR_PROMOTE`.
No cleanup, promotion, publication, or current projection is allowed from this
quarantined incident state without a rollback contract. Lead or user approval
is not required for a separate, isolated Landing-only read-only capture with a
new run identity, semantic comparison, or contract/test work under standing
authorization. The detailed `UR-177` incident note is not retained; use the
retained run's `retained_recovery_checkpoint.json` and
`data/state/audits/kbsec_daily_snapshot/ur177_partial_normalized_quarantine.json`.

## Safety and schedule

- Run once between 16:30 and 18:00 KST on a weekday; one KST date may have only one
  recorded attempt.
- The runner has an exclusive KB lock, two-call absolute cap (OAuth + IVSA0070),
  retry zero, and append-only run/state identities.
- Historical `TOKEN_FAILED` records are evidence for the retired flat-envelope
  sentinel, not a global provider block. The canonical client uses the official
  nested `dataHeader` / `dataBody` token request.
- OAuth responses are retained as redacted bodies plus exact raw byte/hash identity;
  tokens and credentials are never persisted. A successful IVSA0070 body is retained
  byte-exact before parsing or Normalized writes.
- Every run retains capture time, source market date, provenance, response evidence,
  call ledger, checkpoint, and daily state. Prior snapshots are never overwritten.

Manual invocation remains idempotently disabled for the incident date. Future
bounded daily captures may use a new run/date under this runbook and standing
Data authorization; they do not require a fresh route activation:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_kbsec_daily_snapshot.py `
  --project-root . --confirm-live-daily --confirm-access-restored
```

A normal scheduled invocation omits `--confirm-access-restored` after the first
successful Rev1 run. Schedule creation is autonomous after isolated capture,
same-day idempotency, prior-valid preservation, and sanitized readback tests
pass, so a machine scheduler cannot generate repeated same-day probes.

## Preserved source fields

The existing seven contracts preserve separate provisional snapshots for:

- KOSPI/KOSDAQ breadth: upper-limit, advancing, unchanged, declining, lower-limit;
- program trading: arbitrage and non-arbitrage net purchase;
- investor flow: KOSPI, KOSDAQ, and stock-futures net purchase by exact investor
  code/name. The schema also declares KOSPI200 futures, CALL, PUT, and STAR-futures
  fields, but official and production responses return constant zeros; retain those
  zeros in Landing and normalize them as null with `UNAVAILABLE_FROM_IVSA0070`;
- liquidity: customer deposits, receivables, credit balance, futures deposits and
  their changes;
- derivative quotes/summary: instrument identity, price/change, volume, open interest;
- domestic indices and other global market-state symbols.

No verified mini-futures or mini-options source fields exist in the retained success
fixture, and STAR futures are a distinct product. They must not be guessed. The complete raw IVSA0070 response is preserved so
any such provider fields appearing after access recovery can be identified, reviewed,
and added without losing the original snapshot.

## Current baseline attempt

The 2026-08-14 KST baseline made exactly one retry-free OAuth request and stopped at
E021 because the Rev1 sentinel sent a flat JSON token body. The known-successful
scheduled path used the official nested KB envelope with the same base URL and
credential fingerprints, issued a token, and completed IVSA0070 at 2026-08-13
18:12 KST. The failed response remains retained as retired-path evidence. Tokens are
memory-only; the completed successful process left no reusable token cache. Do not
make a token request solely for diagnosis.

The corrected one-off run `20260813T220546Z_auth_validation` completed exactly one
OAuth request and one read-only IVSA0070 request, both HTTP 200 with retry zero.
It was captured pre-open at 07:05 KST. The response mixed inquiry date 2026-08-14,
liquidity source date 2026-08-12, and global-symbol source dates 2026-08-13; investor
and breadth were zero-valued current-session snapshots rather than 2026-08-13 closes.
Audit `e37cf7786a2f619be003390b9d1c59537a66579d20fb1770b74615f240aa1939`
therefore supersedes the earlier structural audit and blocks operational promotion.
The 33 premature Normalized rows were moved intact to
`data/quarantine/kbsec_preopen_date_semantics/20260813T220546Z_auth_validation`.
The weekday 17:00 KST task is registered. Its next run is a Landing-first comparison
capture only: it may follow the same-day pre-open validation once, writes
`slice_date_comparison.json`, and stops before Normalized publication. Review must
classify each slice as `CURRENT_DAY_CLOSE`, `PREVIOUS_DAY_CLOSE`, `INTRADAY/NIGHT`,
`LAGGED_SOURCE_DATE`, or `DATE_UNRESOLVED` before defining final per-slice contracts.
