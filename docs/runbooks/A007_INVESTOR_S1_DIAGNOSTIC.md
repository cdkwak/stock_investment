# A007 Investor S1 diagnostic

## Purpose

This is the first bounded historical-availability discovery request after the
recent five-date range diagnostic passed. It makes exactly one KOSPI
`MDCSTAT30301` **volume** request for 2024-08-07 through 2026-08-07. PASS
requires all 485 canonical KOSPI trading dates from the exact retained 2024,
2025, and 2026 canonical-universe partitions.

The run is diagnostic only. It does not resume Investor, retry the failed
2008--2009 request, or write checkpoint, state, Normalized, or Parquet output.

## Preconditions and command

D must confirm cooldown has ended, no other KRX stream is active, and
`data/state/d_owned_krx_short_selling.lock` is absent. The installed pykrx
version must be exactly 1.2.8 and `.env` must contain `KRX_ID` and `KRX_PW`.

Run once manually from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\manual\diagnose_a007_investor_s1.py --acknowledge-cooldown-ended --confirm-one-live-request --confirm-landing-only --confirm-scope 20240807_20260807_KOSPI_volume_S1_diagnostic
```

All four confirmations are required. PASS requires exactly six total raw HTTP
requests (five authentication and one business), exactly one business request,
no redirect, retry count zero, and one shared D-owned lock. Any different raw
request count is preserved and stopped as an authentication-path anomaly.
Before business I/O, the runner rejects a retry-enabled/unknown transport and
installs explicit zeroes for total, connect, read, redirect, status, and other
retry dimensions on the authenticated session. The business transaction must
be POST with exactly `bld`, `strtDd`, `endDd`, `inqCondTpCd`, and `mktTpCd`
and their frozen S1 values; missing, extra, query, JSON, or wrong values fail
before the business network call.

## Evidence and stop gates

Each attempt has a unique immutable directory under
`data/landing/diagnostics/a007_investor_s1/<run_id>/`, separate from all prior
diagnostics. It contains the lossless response, provenance sidecar, manifest,
and append-only call ledger. The manifest records all 485 expected dates, their
frozen digest, exact canonical source file hashes, scope, caps, and zero-write
semantics.

PASS is `S1_FULL_RANGE_CONFIRMED`: exactly the `OutBlock_1` top-level JSON
shape, exactly 485 unique expected dates, exact row source fields, nonnegative
integers, component/total equality, and at least one positive total.
HTML/restriction/error content, empty output, a one-date
collapse, any subset/extra/duplicate date, schema drift, bad totals, a second
business request, or a seventh raw request stops immediately. Preserve the
attempt and do not retry. A PASS supports planning the next bounded discovery
step; it does not itself authorize historical automation.
