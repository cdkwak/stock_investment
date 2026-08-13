# A007 Investor H1 historical availability diagnostic

This is a prepared, **unexecuted** Landing-only diagnostic. It must not be run
while another KRX stream owns the shared D lock, and this implementation task
does not authorize live access.

The frozen scope is exactly one authenticated `MDCSTAT30301` KOSPI volume
request for `2010-01-04..2012-01-04`. The expected set is exactly 502 canonical
KOSPI dates derived from checksum-bound retained 2010, 2011, and 2012 universe
partitions. Limits are one business request, six total raw HTTP requests, zero
retries, no redirects, and parallelism one.

Before business I/O the runner verifies the authenticated transport, explicit
zero-retry adapters, POST endpoint, complete form body, no query/JSON body, and
the shared `data/state/d_owned_krx_short_selling.lock`. The response is captured
losslessly with SHA-256 provenance and an append-only ledger before parsing.
HTML/restriction content, HTTP failure, malformed JSON, unexpected top-level or
row fields, duplicate/out-of-scope dates, invalid/nonnegative integers, or an
investor-total mismatch stop immediately. No checkpoint or Normalized output is
written.

Classification is intentionally narrow:

- exact 502-date set: `H1_FULL_RANGE_AVAILABLE`;
- exactly one `2012-01-04` row with every investor component and total equal to
  zero: `PRE_AVAILABILITY_COLLAPSE`;
- every other valid-shaped subset or value pattern: `AMBIGUOUS_STOP`, with no
  retry.

If separately authorized later, execution requires all four confirmations:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnose_a007_investor_h1.py `
  --acknowledge-no-active-krx-stream `
  --confirm-one-live-request `
  --confirm-landing-only `
  --confirm-scope 20100104_20120104_KOSPI_volume_H1_availability
```

Preserve the single Landing result regardless of classification. Never retry
H1, synthesize missing dates/zeros, or resume Investor from this diagnostic.
