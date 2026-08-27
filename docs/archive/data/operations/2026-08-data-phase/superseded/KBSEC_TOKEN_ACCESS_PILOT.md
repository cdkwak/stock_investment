# KB Securities token access pilot

Classification: **SUPERSEDED_HISTORICAL_EVIDENCE**

The `TOKEN_FAILED` result below is historical evidence for the retired flat token
request only. It is not the current KB authentication procedure and does not imply a
global provider access block. The later corrected official nested
`dataHeader`/`dataBody` OAuth envelope supersedes this pilot and is routed only by
the current [KB daily snapshot operation](../../../../../data/operations/KBSEC_DAILY_MARKET_SNAPSHOT.md).
Do not execute this archived procedure.

This pilot validates only the KB Securities OAuth endpoint. It never calls an
account, order, or market-data endpoint. The runner enforces one request, zero
retries, and an exclusive local KB pilot lock.

Run only after confirming no other KB request stream exists:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\pilot_kbsec_token.py --confirm-live-token
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

## Zero-network retained-run audit

The retained run was re-audited offline on 2026-08-13. The checkpoint records
one request and zero retries; the ledger contains exactly one attempt and one
HTTP 500 result. Its response digest matches the digest in the redacted Landing
record, the exclusive lock is released, and a scan using the configured KB
credential values found no credential value in any retained run file. The three
retained file SHA-256 values are:

- `call_ledger.jsonl`: `f9bb799d2172e8963294d4810c3a667888b56fc6ade67c24b324ff95f8755fbe`
- `checkpoint.json`: `3a859bcf45bac7cd43395d4a5042d6685b0a6906a55aa7b42614dddc6de343a6`
- `response.redacted.json`: `5d82259ee8b9f8cdf8453281b48746721496ee897b419a0ab3bddd39e212b555`

The response body is deliberately a safe redacted Landing observation, not a
replayable lossless OAuth response, because persisting the returned access token
would violate the repository secret policy. The raw byte count and response
SHA-256 are retained solely as identity evidence.
