# KRX Investor access-recovery sentinel

Status: `READY_FOR_EXACTLY_ONE_CALL`.

This is the only authorized KRX action after cooldown. It repeats only the first
previously restricted parity scope: KOSPI trading value for 2017-05-19..2017-05-22.
The runner binds the retained HTTP 403 evidence, enforces at least six hours of
cooldown, acquires the shared D lock, installs retry 0 on the authenticated transport,
allows one business request (six raw calls including authentication), and writes only
immutable diagnostic Landing evidence.

If the business response is HTTP 403/429, HTML/restriction, or any schema/domain/date
anomaly, stop immediately. Do not retry and do not proceed to KOSDAQ parity or a
production Investor backfill. A successful response proves only that this one scope is
accessible; it does not authorize collection by itself.
