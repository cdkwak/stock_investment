# LS t8412 current 15-minute pilot

## State

`FAILED_BOUNDED_OAUTH_STAGE_20260821 / NO_REPEAT`

## Frozen boundary

- LS OpenAPI `POST /oauth2/token`, then one serial `POST /stock/chart` with
  `tr_cd=t8412`, `tr_cont=N`, `tr_cont_key=""`, symbol `005930`, date
  `2026-08-21`, and native `ncnt=15` only.
- OAuth maximum 1; t8412 business maximum 1; timeout 10 seconds; retry 0;
  continuation/pagination 0. Any failure consumes the date and stops globally.
- Runtime-only application loading of the existing `.env` is permitted. No
  direct file inspection or secret/auth/header/token/body logging or retention
  is permitted.
- Retain a successful chart body first under the isolated Landing root. Failure
  bodies and response headers are never retained. Reuse the t8412 Raw parser
  without assigning bar start/end semantics, then the UR-124 adapter.
- Only `data/state/current_observations/ls_t8412_current.json` may receive an
  atomic UR-118 observation: route `ls-t8412-current:XKRX:005930`, identity
  `KR_EQUITY_CURRENT/XKRX/005930`, interval 15m, display-only/PIT-blocked.
  No Raw projection, Normalized, canonical, Published, Backtest, GUI or
  scheduler mutation is allowed.

## Acceptance

The response must have the exact symbol/date, unique provider labels, native
regular-session grid, finite positive OHLC, integer nonnegative volume, and
preserved KST provider label. Current-session/incomplete bars remain
`AS_RETRIEVED` and provisional by provenance; the label never asserts bar start
or end or final EOD. Atomic readback and API-zero replay are required. The
completed 2026-08-12 calls remain excluded.

## Completed result

The one authorized process consumed OAuth `1` and stopped before t8412
(`business=0`, retry/continuation `0`). No successful response was available
for Landing and no typed observation was written. The sanitized one-date state
replayed at API `0`; this route/date is consumed and must not be retried.
