# KRX Investor access-recovery sentinel

Status: `COMPLETE / ARCHIVED / DO_NOT_RERUN`.

Run `20260813T190529Z_2727396e704a44b197db4a4159b333bd` completed after
the enforced cooldown: five authentication responses plus exactly one business
response, all HTTP 200, retry 0. The business body SHA-256 is
`c60d119f3d75c1fd063e28bb8ad7fd34df21c8380d91b6ea7b8c284db48a2c38`.
It reproduced `BOUNDARY_SHAPED_CONFIRMED`: only the positive 2017-05-22 row was
returned and 2017-05-19 was absent. Offline audit passed with zero credential hits.
Access recovered for this scope, but historical-range semantics remain incomplete.
No production checkpoint or Normalized data changed.

This is the only authorized KRX action after cooldown. It repeats only the first
previously restricted parity scope: KOSPI trading value for 2017-05-19..2017-05-22.
The runner binds the retained HTTP 403 evidence, enforces at least six hours of
cooldown, acquires the shared D lock, installs retry 0 on the authenticated transport,
allows one business request (six raw calls including authentication), and writes only
immutable diagnostic Landing evidence.

If the business response is HTTP 403/429, HTML/restriction, or any schema/domain/date
anomaly, stop immediately. Do not retry and do not proceed to KOSDAQ parity or a
production Investor backfill. A successful response proves only that this one scope is
accessible; it does not authorize collection by itself. This runbook is now
record-only.
