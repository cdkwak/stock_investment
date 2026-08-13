# KB Securities token access pilot

This pilot validates only the KB Securities OAuth endpoint. It never calls an
account, order, or market-data endpoint. The runner enforces one request, zero
retries, and an exclusive local KB pilot lock.

Run only after confirming no other KB request stream exists:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot_kbsec_token.py --confirm-live-token
```

Each execution writes a redacted response, call ledger, and checkpoint under
`data/landing/diagnostics/kbsec_token_pilot/<run_id>/`. OAuth tokens and credential
values are intentionally never persisted; the raw response byte length and SHA-256
retain evidence identity without making the token recoverable. A successful token
result is `TOKEN_OK_AUDIT_REQUIRED`: independently audit that run before authorizing
any IVSA0070 request.

The 2026-08-13 run
`20260813T122256Z_686cca26e4454e74a501cd9ac0470fdc` made exactly one request with
zero retries and returned HTTP 500/result `9999`/process `E021`. Response SHA-256:
`3901655c56d2ef818787247483b0773be645255e6d6e80634f6a3dd13daa106d`.
The checkpoint is `TOKEN_FAILED`; no downstream request was made.
