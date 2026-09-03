# OpenDART corporate-action incremental evidence pilot

Status: `STANDING_LIVE_RESEARCH_AUTHORIZED / RAW_EVIDENCE_ONLY / PRODUCTION_PROMOTION_GATED`

UR-110 defined one historical bounded issuer-specific incremental pilot. Its
exact receipt and budget remain idempotent evidence, but the old one-shot
permission boundary is superseded by standing Data authorization. An agent may
run a new bounded issuer/date scope, provider-aware retry, and Landing/Raw
semantic or PIT investigation with a new idempotency key. This pilot does not by
itself establish a corporate-action factor, adjusted price, canonical event,
GUI marker, scheduler, or Backtest input.
UR-106 financial statements are outside this operation.

## Contract and PIT boundary

- Contract-only observations:
  `contracts/opendart_corporate_action_intake.py`.
- Immutable filing key:
  `(source_operation, landing_response_body_sha256, source_item_ordinal)`.
- Filing version identity is the 14-digit `rcept_no`; it is not a canonical
  event ID. Original/correction links remain null with
  `UNVERIFIED_NO_EXPLICIT_PARENT` unless an official source supplies the exact
  parent receipt.
- `receipt_timestamp_utc` remains null because `rcept_dt` is a date, not an
  intraday publication time. `observation_time_utc` is retained capture time;
  `available_at_utc` is the later of capture and the next calendar date in KST;
  `usable_from` is explicit but no pilot event is predictive-eligible.
- OpenDART `corp_code` and current `stock_code` create only a current-at-capture
  identity observation. Market, class, ISIN, effective dates, and predecessor /
  successor edges remain null until exact KRX/KIND official evidence closes
  them. Names, ticker equality, price continuity, and FinanceDataReader never
  create an identity edge.

## Frozen baseline and incremental scope

The immutable ECOPRO BM run
`20260813T191033Z_1891cf7c6047424aa484f66fea129bfc` is reused with API zero as
the bounded baseline. Its accepted list cursor is:

```text
receipt_date=20220406
receipt_no=20220406002324
```

Do not repeat that run or its `2022-04-01..2022-04-15` requests. The historically
selected incremental reconciliation is exactly:

| Field | Frozen value |
|---|---|
| Company | ECOPRO BM |
| `corp_code` | `01160363` |
| Date window | `2022-06-14..2022-06-14` |
| Purpose | capture the later receipt-date boundary and test append-only correction handling without inferring a parent edge |

For the historical UR-110 scope, the date is selected from retained receipt
`20220614000068`; it is not inferred
from a market effective/listing date.

## Exact request matrix and budget

| Sequence | Operation | Public parameters | Cap |
|---:|---|---|---:|
| 1 | official OpenDART `list.json` | exact issuer/date; `last_reprt_at=N`, `pblntf_ty=B`, ascending date, page 1, count 100 | 1 GET |
| 2 conditional | `list.json` page 2 | only when page 1 declares exactly two pages | 1 GET |
| next | official `fricDecsn.json` | exact issuer/date | 1 GET |
| next | official `pifricDecsn.json` | exact issuer/date | 1 GET |

The historical UR-110 cap is four GETs, zero POST, timeout 10 seconds each,
parallelism one, and at most two list pages. A new scope must declare a bounded
provider-aware retry/backoff and total-call cap without changing its exact
issuer/date/endpoint identity. A one-page historical list consumes three GETs
total. More than two pages, exhausted HTTP/transport/auth/rate/source-status error,
redirect, malformed/empty-success schema, credential echo, call-accounting
drift, duplicate receipt conflict, pagination gap, or artifact write/readback
failure stops the exact occurrence. No alternate endpoint, source, issuer, date,
or FinanceDataReader route is silently selected; a separately scoped
investigation uses a new idempotency key and checkpoint.

## Credential and Landing rules

- The application runtime may call `load_dotenv(project_root/.env)` and then use
  `OPENDART_API_KEY`. Agents never open, inspect, print, summarize, copy, or
  modify `.env` or the key.
- The credential appears only in the in-memory request parameter. Recorded URLs
  have no query string; manifests, summaries and state contain only public
  parameters, counts, status, timing, byte counts and hashes.
- Each response body is atomically staged under
  `data/landing/diagnostics/opendart_corporate_action_incremental/<run_id>/`.
  The full run directory is the commit boundary. A credential scan runs before
  commit. No authentication response/header is retained or summarized.
- Final state is atomically written to
  `data/state/opendart_corporate_action_incremental/ur110_pilot.json` and binds
  the exact scope, cursor and Landing fingerprint.
- A completed exact-scope rerun verifies retained hashes and returns
  `NOOP_API_ZERO_REPLAY` before loading `.env` or creating a session.

## Cursor, pagination, dedup and corrections

Pages must be contiguous `1..total_page` with identical pagination totals.
Receipts deduplicate by `rcept_no`; identical repeats collapse, conflicting
fields fail closed. The cursor is the lexicographic pair
`(rcept_dt, rcept_no)`, so same-date later receipts remain observable.
Incremental windows begin from the last accepted receipt date with overlap;
rows at or below the cursor are not re-appended.

A correction indicator triggers later event-family re-observation only through
a separately bounded issuer/date operation. Every newly returned receipt is
append-only. No original is overwritten, and issuer/date/name/terms proximity
never manufactures `original_receipt_no` or `revises_receipt_no`.

## Event-family decision gate

Every family is classified `complete_candidate`, `observation_only`, or
`unsupported`. `complete_candidate` requires one immutable final filing version,
an explicit revision edge/original state, exact security class and stable ID,
official KRX/KIND effective/ex/listing evidence, complete final economic terms,
and explicit observation/availability/finality. The historical pilot does not expect any
family to pass this gate.

Incomplete observations remain numeric-free for factors and cannot enter
Normalized/Derived/Published, adjusted history, GUI, scheduler or Backtest.
A newly fully evidenced family may be reviewed and reclassified by an agent
under the owning contract and standing Data runbook. Queue claim rules apply
only when that work is queue-backed; no separate user or Lead permission is
required. UR-110 never activates a higher layer merely by capturing Raw
evidence.

## Command, idempotency, and new scopes

```powershell
.\.venv\Scripts\python.exe `
  scripts\manual\pilot\opendart_corporate_action_incremental.py `
  --project-root . --confirm-ur110-live-pilot
```

The `--confirm-ur110-live-pilot` flag is a deliberate live-network safety
acknowledgement by the running agent, not evidence of a separate user approval.

After a completed historical call budget and API-zero replay, preserve that
receipt as evidence. A new date/range or revised implementation may run under
the standing Data authorization with a new idempotency key and checkpoint.

## Current execution checkpoint

The first external-execution escalation was historically rejected before
process start; provider requests, responses, Landing writes, state writes, and
consumed budget were all zero. That rejection no longer acts as a permission
gate. An agent may run or modernize the operation under standing Data authority
after validating the current company/date/endpoints, secret injection,
provider limits, and checkpoint/idempotency boundary.
The offline gate evidence was 제거됨
(backup/repo-cleanup-phase2-20260903 브랜치에 보존).
