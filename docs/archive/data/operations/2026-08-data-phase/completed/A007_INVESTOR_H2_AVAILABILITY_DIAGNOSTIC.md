# A007 Investor H2 historical availability diagnostic

The single authorized Landing-only diagnostic is retained at
`20260813T105434Z_e4ea0268a64947a293293e5989f42c8c`; H2 must not be run again.
Its reproducible zero-network audit confirms five authentication calls plus one
business call, all HTTP 200, and exact `PRE_AVAILABILITY_COLLAPSE`: the only row
is `2014-01-03` with every component and total zero. Body SHA-256 is
`f2c0e796a69b989dd1a0b6048d7e4b13d23e0e6e0907bb26d976f3166ae49f4a`.

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

Reproduce the audit without network or writes (default dry-run):

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\audit\verify_a007_investor_h2.py `
  .\data\landing\diagnostics\a007_investor_h2\20260813T105434Z_e4ea0268a64947a293293e5989f42c8c
```

The H2 wrapper reuses the hardened H1 verifier, including exact manifest and
request-chain regeneration, credential scan, original-artifact CAS, and optional
content-addressed append-only evidence. Preserve the Landing result. Never retry
H2, synthesize dates or zeros, or resume Investor from this diagnostic alone.
