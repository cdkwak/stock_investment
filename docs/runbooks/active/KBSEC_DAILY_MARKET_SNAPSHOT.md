# KB Securities daily market snapshot

Status: **AUTH_FIXED / DATE_SEMANTICS_REVIEW_REQUIRED**

The target is one provisional `IVSA0070` snapshot per Korean trading day at
approximately 17:00 KST. This is a lightweight point-in-time observation stream,
not an official historical replacement and not continuous polling.

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

Manual invocation for the next bounded daily capture:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect_kbsec_daily_snapshot.py `
  --project-root . --confirm-live-daily --confirm-access-restored
```

A normal scheduled invocation omits `--confirm-access-restored` after the first
successful Rev1 run. Schedule creation remains deferred until that run is audited,
so a machine scheduler cannot generate repeated same-day probes.

## Preserved source fields

The existing seven contracts preserve separate provisional snapshots for:

- KOSPI/KOSDAQ breadth: upper-limit, advancing, unchanged, declining, lower-limit;
- program trading: arbitrage and non-arbitrage net purchase;
- investor flow: KOSPI, KOSDAQ, futures, CALL option, PUT option, STAR futures, and
  stock futures net purchase by exact investor code/name;
- liquidity: customer deposits, receivables, credit balance, futures deposits and
  their changes;
- derivative quotes/summary: instrument identity, price/change, volume, open interest;
- domestic indices and other global market-state symbols.

No verified mini-futures or mini-options source fields exist in the retained success
fixture. They must not be guessed. The complete raw IVSA0070 response is preserved so
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
Do not register the recurring task until one post-close snapshot verifies slice dates.
