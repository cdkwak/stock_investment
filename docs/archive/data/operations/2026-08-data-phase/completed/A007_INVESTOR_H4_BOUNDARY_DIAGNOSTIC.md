# A007 Investor H4 boundary-pair diagnostic

Status: **EXECUTED / AUDITED PASS**. The single retained run
`20260813T111424Z_f53404faea84403d93aff521b5e255f0` made five authentication
requests and one business request, all HTTP 200 with retry zero. The 218-byte
body SHA-256 is
`2b9a2d54f7f20f188918d2404c294c6001592c52bc350e80bd4ffed6b5540a8d`.
It returned only 2017-05-22 with a positive total; 2017-05-19 was absent, so the
audited classification is `BOUNDARY_SHAPED_CONFIRMED`. Do not execute it again.

The H4 retained response was an exact positive 154-date suffix beginning
`2017-05-22`. The smallest boundary test is one `MDCSTAT30301` KOSPI volume
request for the two retained canonical dates `2017-05-19..2017-05-22`; date-list
SHA-256 is `a8e1c5b7be734fb70104c2a93405a36610ccd9dbef05e85cb3bf55789ececfd1`.

It uses the shared D lock, one business/six raw call caps, retry zero,
parallelism one, exact POST/form/no-query gates, and Landing-first capture. It
writes no checkpoint or Normalized data.

- both dates present with valid positive totals: `RANGE_WINDOW_EFFECT`;
- only `2017-05-22` present with a positive total: `BOUNDARY_SHAPED_CONFIRMED`;
- any zero, other subset, schema/domain/restriction shape: stop, no retry.

Even `BOUNDARY_SHAPED_CONFIRMED` applies only to KOSPI volume and does not prove
other markets, value mode, total-date parity, or broader historical production
coverage. It does not authorize Investor resume or synthetic missing rows.

Historical command retained for audit only; do not execute:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnostic\diagnose_a007_investor_h4_boundary.py `
  --acknowledge-no-active-krx-stream `
  --confirm-one-live-request `
  --confirm-landing-only `
  --confirm-scope 20170519_20170522_KOSPI_volume_H4_boundary_pair
```
