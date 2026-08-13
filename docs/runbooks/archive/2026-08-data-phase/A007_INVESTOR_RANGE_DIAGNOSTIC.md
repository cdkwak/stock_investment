# A007 Investor range diagnostic

Status: **EXECUTED / PASS** on 2026-08-13. The single run
`20260813T065923Z_6ec6cf7bde1c414988cddb7d275fd7a2` returned exactly the five
expected dates, five positive totals, six raw HTTP responses, and one business
response. Its classification is `MULTI_DATE_RANGE_CONFIRMED`; response SHA-256 is
`2368c004fd9e3e287730fc378faffd94e07e7c621f01901f17b34567d8f57aca`.
The immutable body, sidecar, manifest, and ledger remain under the diagnostic
Landing root. This PASS did not change Investor state or authorize resume.

## Purpose

The first historical Investor scope requested 2008-01-02 through 2009-12-30
but returned only the end date.  That response was retained and the production
collector correctly stopped before checkpoint or Normalized mutation.  A
one-day pilot cannot establish that `MDCSTAT30301` still honors multi-day
ranges.

This utility makes exactly one business request for KOSPI volume over five
recent canonical dates, 2026-08-04 through 2026-08-10.  The final date has a
retained positive one-day pilot observation.  It does **not** retry the failed
historical scope and cannot resume A007.

## Preconditions

- D has confirmed the KRX cooldown has ended.
- No KRX/pykrx collector or pilot process is active.
- `data/state/d_owned_krx_short_selling.lock` is absent.
- The installed pykrx version is exactly 1.2.8 and `.env` supplies `KRX_ID`
  and `KRX_PW`.

## Single authorized command

Run once, manually, from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\manual\diagnose_a007_investor_range.py --acknowledge-cooldown-ended --confirm-one-live-request
```

The two flags prevent accidental execution.  The runner permits at most six
raw HTTP requests, including authentication, and exactly one business request.
It has no retry loop or parallel execution.

## Evidence and decision

Evidence is isolated under
`data/landing/diagnostics/a007_investor_range/<run_id>/`:

- immutable `response.json`
- immutable `response.json.provenance.json`
- append-only `call_ledger.jsonl`
- immutable `manifest.json`

There is no checkpoint, state, Parquet, or Normalized write.  Successful JSON
may be labelled `text/html` by KRX, so validation examines the body.  PASS
requires the exact five expected dates, unique rows, the exact source schema,
nonnegative integer values, component/total equality, and at least one positive
total.  HTML, restriction/error payloads, empty output, a one-date collapse,
duplicates, extra dates, or schema/domain failures stop immediately.

- `MULTI_DATE_RANGE_CONFIRMED`: range mechanics work for the bounded recent
  window.  Review a separately bounded historical-availability plan before
  changing or resuming Investor collection.
- Any failure: preserve the evidence, make no retry, and keep Investor stopped.

