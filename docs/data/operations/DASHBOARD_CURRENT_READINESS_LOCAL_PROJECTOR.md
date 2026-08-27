# Dashboard current-readiness local projector

Status: `TERMINAL_CONSUMED_UR235_API_ZERO`.

The supported repair entrypoint is
`scripts/manual/repair/project_dashboard_current_readiness.py`. It requires an
explicit timezone-aware audit clock and confirmation. Fixture/non-production
paths require `--confirm-nonproduction-projector`. The owned production
`docs/data/DASHBOARD_64_CURRENT_READINESS.csv` requires the distinct
`--confirm-production-projector` plus an exact current activation manifest and
this runbook's `ACTIVE_EXACT_PRODUCTION` status. UR-226 installs those artifacts
only for its fixed one-use operation; all other attempts fail before any CSV
read or write.

It reads only the existing readiness CSV and these allowlisted local artifacts:
the exact SOXX current observation; Naver mobile-home KOSPI/KOSDAQ/USD-KRW
observations; exact mobile-basic 000660/005930 observations; and the global60m
scheduler log plus state. It never constructs provider transport, starts a
scheduler/GUI worker, or changes a source state. Strict identity/provider/route/
unit/finality/display-only/PIT and timezone-aware source timestamp checks precede
the shared today-KST/<=60-minute gate.

Only explicitly mapped dynamic CSV fields can change. Unmapped rows remain exact.
Missing, malformed, mismatched, future, or stale observations become numeric-free
with a typed reason. A global60m scheduler/state failure preserves prior source
timestamps and records `GLOBAL60M_NO_CURRENT_PUBLICATION`; it never publishes a
current value. Writes validate UTF-8 RFC4180, 17 columns, 64 unique IDs, then
use atomic replacement and durable readback.

UR-235 used one API-zero projection at audit clock `2026-08-21T21:34:00+09:00`,
using terminal UR-193 SOXX and four strict UR-232 retained-Landing global60m
envelopes. Its preimage/postimage readback and no-write replay passed; the
manifest/runbook are terminalized and any later production command fails closed.
