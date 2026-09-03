# Canonical equity daily scheduler operation

Status: `LIVE_VALIDATED_THROUGH_20260825 / BOUNDED_CATCHUP_ACTIVE`

## Purpose

Advance the retained Korean equity price, market-cap, provider-universe,
canonical-universe, and market-breadth datasets without skipping an XKRX
session. The lane is descriptive and `PIT_LIMITED`; scheduler eligibility does
not change its predictive-use classification.

## Schedule and finality

- Lane: `CANONICAL_EQUITY_DAILY`.
- Eligible bundle occurrences: 14:10 and 20:30 KST.
- Provider availability: D+1 XKRX business day at or after 13:00 KST.
- Same-session pykrx rows are never accepted into this dataset. They remain in
  `kr_equity_price_provisional_daily` for display and condition alerts until the
  D+1 canonical row takes reader precedence.
- Each occurrence advances consecutive oldest-missing eligible XKRX sessions,
  stopping after at most three sessions, six logical API calls, ten elapsed
  minutes, or the first unresolved date.
- A current target replays with provider API calls `0`.

The operation cannot jump to the newest date while an earlier session is
unaccepted. Each date remains an independent atomic boundary.

## Provider and call budget

The operation uses the registered data.go.kr price/capitalization and listed
universe endpoints. It permits at most one request page per stream and two
logical provider calls per date, up to six logical calls per occurrence,
`numOfRows=9999`, and retry count `0`. Credentials are read only by
the live supplier and are never written to logs, receipts, Landing, or errors.

Each successful response is atomically captured before the next request is
made. Therefore a second-stream failure preserves the first successful Landing
object as diagnostic evidence without promoting production data.

## Acceptance and failure behavior

Promotion requires both exact-date streams to be non-empty and to pass the
existing schema, identity, key, market, and date validation. A valid-empty,
partial, mismatched, malformed, transport, or promotion failure preserves all
prior production files and accepted-date state.

After validation, the existing transaction machinery atomically promotes the
price, market-cap, provider-universe, and canonical-universe datasets. The
affected market-breadth partition and completion checkpoint are refreshed
immediately afterward. The lane succeeds only after accepted-date and breadth
read-back reach the selected session.

A valid-empty result is `DEGRADED_VALID_EMPTY_PRESERVED`; the Korean bundle
returns a failing process status for any degraded lane while continuing its
independent lanes and refreshing Health once.

## Activation boundary

The code, registry, schedule mapping, and offline tests may be installed before
network use. The first real provider operation must occur only after independent
review accepts this change. That reviewed run must advance at least one missing
eligible session or retain a typed fail-closed result. No ACL mutation, recursive
repair, historical source substitution, or Backtest promotion is authorized.

The first reviewed live occurrence completed on 2026-08-26 KST for target
2026-08-19. Run `canonical-equity-20260819-fe6d4c9a5c9249c8bb263d63bb28c156`
used exactly two provider calls with retry count zero. Its price/cap and universe
Landing hashes bind to the accepted-state manifest; production readback reached
2026-08-19 for price, market cap, provider universe, canonical universe, and
breadth with zero duplicate primary keys. Breadth exactly matched an independent
recalculation, and accepted-date replay was `NOOP_IDEMPOTENT` with provider calls
zero. Health now contract-validates all five outputs and reports 2026-08-19
against the available-through date 2026-08-24.

Activation does not authorize skipping the remaining backlog. Each later 14:10
or 20:30 occurrence advances consecutive oldest-missing eligible sessions within
a maximum of three sessions, six logical API calls, and ten elapsed minutes.
Every date remains a separate two-stream atomic transaction. The operation stops
at the first failed, invalid, or valid-empty date and never jumps over it; all
existing Landing capture, idempotency, rollback, and fail-closed gates remain.
