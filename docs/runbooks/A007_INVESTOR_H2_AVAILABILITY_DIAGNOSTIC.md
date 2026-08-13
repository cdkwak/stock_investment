# A007 Investor H2 historical availability diagnostic

This is a prepared, **unexecuted** Landing-only diagnostic. This preparation
does not authorize live access. It must not run while another KRX stream owns
the shared D lock.

The frozen scope is exactly one authenticated `MDCSTAT30301` KOSPI volume
request for `2012-01-05..2014-01-03`. The expected set is exactly 494 canonical
KOSPI dates derived from checksum-bound retained 2012, 2013, and 2014 universe
partitions. Its date-list SHA-256 is
`ed5e64f0fdf3fcb968462521f307c545f74b0cc00c006e618f9bc8a1c1931e9f`.
Limits are one business request, six total raw HTTP requests, zero retries, no
redirects, and parallelism one.

The shared range runner enforces the authenticated transport, exact POST
endpoint and form, no query/JSON body, zero-retry adapters, raw/business caps,
and `data/state/d_owned_krx_short_selling.lock`. It writes the lossless response,
SHA-256 provenance, and append-only call ledger before parsing. It never writes
an A007 checkpoint or Normalized dataset.

Classification is intentionally narrow:

- exact 494-date set: `H2_FULL_RANGE_AVAILABLE`;
- exactly one `2014-01-03` row with all four investor components and total zero:
  `PRE_AVAILABILITY_COLLAPSE`;
- any other valid-shaped subset or value pattern: `AMBIGUOUS_STOP`.

HTML/restriction content, HTTP failure, malformed JSON, unexpected fields,
duplicate/out-of-scope dates, invalid or negative integers, or component/total
mismatch stops immediately. There is no retry under any classification.

Only a separately authorized operator may execute it, with every guard:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnose_a007_investor_h2.py `
  --acknowledge-no-active-krx-stream `
  --confirm-one-live-request `
  --confirm-landing-only `
  --confirm-scope 20120105_20140103_KOSPI_volume_H2_availability
```

Preserve any single Landing result. Never retry H2, synthesize dates or zeros,
or resume Investor from this diagnostic alone.
