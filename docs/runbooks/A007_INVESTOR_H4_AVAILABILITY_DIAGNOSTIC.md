# A007 Investor H4 historical availability diagnostic

This is a prepared, **unexecuted** Landing-only diagnostic. Preparation does
not authorize KRX access, and it must not run while another stream owns the
shared D lock.

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

Only separately authorized execution may use all guards:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnose_a007_investor_h4.py `
  --acknowledge-no-active-krx-stream `
  --confirm-one-live-request `
  --confirm-landing-only `
  --confirm-scope 20160107_20180105_KOSPI_volume_H4_availability
```

Never retry H4 or infer availability from partial/ambiguous output.
