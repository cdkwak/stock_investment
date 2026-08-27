# A007 Investor H4 historical availability diagnostic

Status: **EXECUTED / AMBIGUOUS STOP**. The single authorized Landing-only run
`20260813T110838Z_8dd9ac10cc3d41508a9371f62323552c` made five authentication
requests and one business request, all HTTP 200 with retry zero. It must not be
run again.

The 24,556-byte body (SHA-256
`e49731fcae3884457ead31250733e79401a2a68479e4918dafa1cde201c5ac01`)
returned 154 of 490 expected dates and the original terminal event is
`AMBIGUOUS_STOP:154/490`. The rows form the exact canonical suffix
2017-05-22..2018-01-05 and all 154 totals are positive; the exact 336-date prefix
2016-01-07..2017-05-19 is absent. This shape alone did not prove a source
availability boundary and did not authorize retry or Investor resume.

The frozen scope is one authenticated `MDCSTAT30301` KOSPI volume request for
`2016-01-07..2018-01-05`. Exactly 490 retained canonical dates are bound to the
checksum-verified 2016, 2017, and 2018 partitions; date-list SHA-256 is
`c35bced36e8ea2715a7ced1ffce7d2c6d5ac9e0c5c399dfec57aa5a09b789c50`.
Limits are one business request, six total raw calls, retry zero, no redirects,
and parallelism one.

The hardened shared runner enforces exact authenticated POST/form/no-query
transport and the shared `data/state/d_owned_krx_short_selling.lock`, then writes
lossless Landing body, provenance, and append-only ledger before parsing. It
writes no A007 checkpoint or Normalized data.

Classification is exact full set → `H4_FULL_RANGE_AVAILABLE`; sole end-date
`2018-01-05` all-zero row → `PRE_AVAILABILITY_COLLAPSE`; any other valid subset
or value pattern → `AMBIGUOUS_STOP`. HTML/restriction, HTTP/schema/date/domain
failures stop immediately without retry.

Historical command retained for audit only; do not execute:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnostic\diagnose_a007_investor_h4.py `
  --acknowledge-no-active-krx-stream `
  --confirm-one-live-request `
  --confirm-landing-only `
  --confirm-scope 20160107_20180105_KOSPI_volume_H4_availability
```

The separately audited boundary-pair run returned sole positive 2017-05-22 and
no 2017-05-19, classified `BOUNDARY_SHAPED_CONFIRMED`. This conclusion applies
only to KOSPI volume. It does not prove KOSDAQ/value parity, authorize synthetic
zeros, or permit production resume. Never retry H4.
