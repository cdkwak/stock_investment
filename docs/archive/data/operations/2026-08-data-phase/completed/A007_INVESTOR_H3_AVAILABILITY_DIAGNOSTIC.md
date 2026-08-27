# A007 Investor H3 historical availability diagnostic

The single retained run is
`20260813T110213Z_581e739a8dab4e439206a73b5b838d46`; do not execute H3 again.
Its zero-network audit confirms auth five plus one business call, all HTTP 200,
and an exact sole `2016-01-06` all-zero `PRE_AVAILABILITY_COLLAPSE` response.

The frozen scope is one authenticated `MDCSTAT30301` KOSPI volume request for
`2014-01-06..2016-01-06`. Exactly 494 retained canonical KOSPI dates are bound
to the checksum-verified 2014, 2015, and 2016 partitions; the date-list SHA-256
is `f7e9ea0562ab3b198d690300e8eb4faad015d56b6bea0c4d9919cf599332f28e`.
Limits are one business request, six raw HTTP requests including authentication,
retry zero, no redirects, and parallelism one.

The shared hardened runner enforces the authenticated transport, exact POST
endpoint and form, no query or JSON body, zero-retry adapters, caps, and
`data/state/d_owned_krx_short_selling.lock`. It captures the lossless response,
SHA-256 provenance, and append-only ledger before parsing. It never writes an
A007 checkpoint or Normalized dataset.

Classification is limited to:

- exact 494-date set: `H3_FULL_RANGE_AVAILABLE`;
- exactly one `2016-01-06` row with all components and total zero:
  `PRE_AVAILABILITY_COLLAPSE`;
- any other valid-shaped subset/value pattern: `AMBIGUOUS_STOP`.

HTML/restriction content, HTTP failure, malformed JSON, unexpected fields,
duplicate/out-of-scope dates, invalid/negative integers, and component/total
mismatch stop immediately with no retry.

Reproduce the audit without network or writes (default dry-run):

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\audit\verify_a007_investor_h3.py `
  .\data\landing\diagnostics\a007_investor_h3\20260813T110213Z_581e739a8dab4e439206a73b5b838d46
```

Preserve the single Landing result. Never retry H3 or infer availability from a
partial/ambiguous response.
