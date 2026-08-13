# Non-KRX source readiness audit

Audit date: 2026-08-13. This is a repository-evidence audit; it made no live
request and does not change any Dataset Contract, registry, checkpoint, or
Data-status classification.

Supersession note: this ranked table records the evidence available when the
audit ran. Since then, all six allowlisted schema-only migrations were completed
and verified without changing logical values, and A007 Trading and Balance
completed. Rank 3 is therefore resolved. Rank 7 is no longer blocked by an
active A007 production stream, but every post-A007 KRX pilot remains separately
D-authorized, cooldown-gated, and subject to the single-stream rule. See
`project/DATA_STATUS.md` and `DATA_PHASE_HANDOFF_20260813.md` for current status; the
historical findings below are intentionally not rewritten.

Further supersession from the credential-validation cycle: OpenDART credentials
were configured and its three-call pilot completed valid-empty; BOK ECOS metadata,
value, and historical source-observation collection completed; two bounded DGS10
ALFRED intervals passed offline audit but yielded no useful multi-version revision
evidence; Rights retained a complete 12/12 response; and a second dividend snapshot
attempt for `basDt=20260813` stopped as exact source-success valid-empty after one
call. KB token access reproduced HTTP 500/result `9999`/process `E021`. The ranked
table below remains historical; use `project/DATA_STATUS.md` and provider runbooks for
current gates.

## Ranked blockers

| Rank | Area | Current evidence | Real next gate |
|---:|---|---|---|
| 1 | Yahoo/FRED provenance | Normalized artifacts and coarse completion states exist, but the retained runs have neither exact response Landing nor per-call evidence. | Use the new opt-in `capture_root` on every future collection; old responses cannot be reconstructed or represented as lossless captures. |
| 2 | Corporate actions | The FSC Rights v2 contract can retain observations, and one diagnostic established source usability. It supplies neither adjustment terms nor a canonical economic-event identity. OpenDART's bounded free-issue observation pilot and parser are offline-tested but unexecuted. | Obtain `OPENDART_API_KEY`, approve one exact issuer/window, run the three-call zero-retry pilot, and audit revision/security-class behavior before defining a schema. Splits, mergers, reductions, and pre-2015 coverage remain separate source gaps. |
| 3 | Six schema migrations | The allowlisted offline tool covers the six named datasets and has crash/recovery tests. No migration state/artifact in the current data tree proves that any apply run completed. | D runs `verify`, reviews each plan, then applies each dataset serially with the exact confirmation digest; this is artifact maintenance, not source discovery. |
| 4 | Dividend snapshot append | The append implementation now has durable journal recovery, layout/fingerprint preflight, and interruption tests. | Wait for a genuinely new independently captured complete dividend Landing snapshot, then run the offline builder once. Do not manufacture a second snapshot from the current Landing. |
| 5 | BOK ECOS Treasury | Two-phase diagnostic is offline-tested. Official table/item identities were reviewed in prose, but execution still requires a reviewed executable config, API key, and a one-call metadata capture before value calls. | Supply `BOK_ECOS_API_KEY`; run metadata phase only, independently approve its digest, then consider the bounded value phase. Publication/revision semantics remain blocked even if values match Toss. |
| 6 | KOFIA/FSC credit extension | Market aggregate history is complete from 2021-11-09. No documented official survivorship-safe per-symbol source exists, and the current Landing lacks capture time/call ledger. | Ask KOFIA/FSC for units, earliest coverage, cutoff/revisions, and security-level availability. Only then consider the documented two-retrieval aggregate pilot. |
| 7 | Post-A007 KRX pilots | ETF, foreign ownership, and fundamentals have bounded offline-tested pilots; program trading still lacks a verified request/response contract. | These remain after A007 and under the single KRX stream. They are intentionally not runnable during this audit. |

## Implemented readiness improvement

Yahoo and FRED fetch/collection functions now accept an optional
`capture_root`. When supplied, each HTTP response is captured before HTTP or
payload parsing as one atomically committed directory:

```text
<capture_root>/<provider>/<operation>/<UTC timestamp>_<call id>/
  response.body   # exact response.content bytes
  call.json       # redacted request scope, HTTP status, byte count and SHA-256
```

The recorder rejects URLs containing query strings and parameter names that
look credential-bearing. It retains no request/response headers except the
response content type. A malformed business payload is still landed before the
parser fails, which preserves diagnostic evidence without allowing it to
overwrite Normalized data.

Example future roots:

```python
landing = project_root / "data" / "landing" / "public_http"
collect_global_indices(start, end, root=global_dataset_root, capture_root=landing)
collect_fred(fred_normalized_root, start=start, capture_root=landing)
```

Omitting `capture_root` preserves the existing API for tests and callers, but
production collection should treat it as required. This mechanism improves
future provenance only. It deliberately does not alter or backfill existing
completion states and does not claim the historical artifacts are now
provenance-complete.
