# Capture-first global current refresh

Status: `FRED_DAILY_AUTOMATION_ACTIVE / YAHOO_INDEX_ETF_FUTURES_AUTOMATION_ACTIVE / FIVE_NEW_SYMBOLS_COLLECTED_20260902 / DOLLAR_INDEX_REGISTERED_NOT_YET_COLLECTED / DESCRIPTIVE_CURRENT_ONLY`.

The `fred_vix`, `fred_yields`, and `fred_fx` phases are explicitly authorized
for bounded as-retrieved operational collection. Their reviewed promotion may
refresh the corresponding Normalized datasets; `fred_yields` may also refresh
the derived Treasury spread in its existing atomic transaction. Missing
realtime/vintage/series-last-updated metadata remains null, and predictive use
stays `PIT_BLOCKED_PENDING_VINTAGE_RESOLVER`. Yahoo automation is separately
bounded to the exact registered index, ETF, and Dashboard-futures scopes below;
this does not authorize fallback sources, another symbol, or an inferred end date.

`scripts/manual/collect/refresh_global_current.py` prepares one bounded provider phase.
The live command never changes a production Normalized or Derived root.

- `yahoo`: up to 6 sequential calls (SP500, NASDAQ Composite, NASDAQ-100,
  SOX, Dow Jones, Dollar Index `DX-Y.NYB`); `--symbols` uses one call per selected canonical id
- `yahoo_etf`: up to 2 sequential calls (SOXX, EWY); a subset is registry-bound
- `yahoo_dashboard_futures`: up to 5 sequential calls (NQ=F, GC=F, CL=F,
  ES=F, YM=F); a subset is registry-bound
- `fred_yields`: exactly 3 sequential calls (DGS2, DGS10, DGS30)
- `fred_fx`: exactly 2 sequential calls (DEXKOUS, DEXJPUS)
- `fred_vix`: exactly 1 call (VIXCLS)

The registry-bound `yahoo_etf` phase contains retained SOXX plus EWY, first
collected and promoted on 2026-09-02. The `yahoo` phase contains retained SP500,
NASDAQ_COMPOSITE, and NASDAQ100; SOX and DOW_JONES were first collected and
promoted on 2026-09-02; DOLLAR_INDEX (`DX-Y.NYB`) is
`REGISTERED_NOT_YET_COLLECTED`. Existing symbols allow only one completed-session
append; a missing registered symbol uses the explicitly bounded onboarding
window. Every symbol keeps retry zero, exact retained-overlap validation, atomic
promotion, and a pre-network same-target replay. This does not authorize another
ETF/index symbol or a fallback.

FRED expected dates are provider-publication dates, not direct projections of
the completed XNYS session. H.15 yields use the official weekday 16:15 ET
release, H.10 FX uses the official weekly Monday 16:15 ET release, and VIXCLS
uses its separately observed next-business-day window. The pre-H.15 probe
correctly reported `EXPECTED_LAG`. After the gate, the installed
`STOCK_DATA_FRED_DAILY` task promoted DGS2/DGS10/DGS30 through 2026-08-17 and
refreshed the dependent spread; its immediate second trigger was a pre-network
`NOOP` with API 0. FX remains current through 2026-08-14 under H.10, and VIXCLS
remains current through 2026-08-17.

Each item has a frozen, explicit start and end in the checkpoint. Requests have
a hard call cap and retry count zero. Every response is atomically captured
under `data/landing/global_current_refresh/<run_id>/` before a complete candidate
is written under `data/staging/global_current_refresh/<run_id>/`. Each Landing
body is hash-bound to its call record. Yahoo overlap starts from each symbol's
own retained maximum.

The dashboard-futures phase is descriptive-only and scheduler-enabled at 22:10
KST, after the explicit next-US-business-day 08:00 ET availability gate. Yahoo live-forming
bars remain in immutable Landing evidence but are excluded from completed-daily
candidates when their timestamp equals `meta.regularMarketTime` away from the
exchange-local day boundary. Returned rows outside the explicit requested range
are also excluded. The endpoint must have `VALID` OHLC and a finite close for
every selected symbol or that symbol stops before publication.

The unattended futures lane is fixed to NQ=F, GC=F, CL=F, ES=F, and YM=F.
ES=F and YM=F were first collected and promoted on 2026-09-02. The Dollar Index
is an index-lane `DX-Y.NYB` symbol, not a continuous future.
Existing symbols permit only a one-session append, while a missing registered
symbol uses the bounded onboarding window. Each symbol requires its own HTTP 200
response with retry zero, validates overlap and revision reports, and promotes
through an independent whole-dataset CAS transaction. One failed symbol remains
unpromoted and does not block valid peers. A same-target replay exits before
network access. It does not provide intraday
streaming, individual expiries, official settlements, volume/OI semantics, or
predictive PIT eligibility.

Installed times are global ETFs 06:10 KST, global indices 06:20 KST, and the
Dashboard-futures bundle 22:10 KST. Existing task actions omit `--symbols`, so
registry defaults automatically include the corrected registered symbols on the next natural
run; no Windows task definition is added or modified. Each prior actual task and retained-date replay
returned result 0; the replay used API 0. Provider completion atomically refreshes
Health V2 from contract-valid production dates.

The Landing audit binds exactly one unique call record to every frozen plan
item, including provider, operation, URL, parameters, HTTP 200 status, and body
hash. Every item must remain inside its planned window, reach the explicit end,
and overlap retained coverage. FRED revision checks are per series: omitted
dates and finite-to-null changes fail closed, while finite revisions and
null-to-finite observations are reported separately.

The checkpoint records the production pre-manifest, request plan, call/status
accounting, capture hashes, overlap revision counts, candidate manifest, and
publication state. Omitted retained keys inside the returned response range,
schema failure, unexpected coverage, or production drift fails closed. Existing
production roots remain byte-identical.

Use `--end` for the reviewed completed-source date and
`--confirm-live-landing-only`. Review the Landing bodies, frozen plan, revision
report, candidate coverage, and manifests before publication.

Publication is a separate zero-network command using `--promote-checkpoint` and
`--confirm-offline-promotion`. The operator must also supply the exact
`--approval-digest` printed in the reviewed checkpoint. It performs a
content-manifest CAS and installs a
copy of each whole candidate root with rollback. A yield candidate also rebuilds
the Treasury spreads; yield and spread roots promote in the same transaction.
Candidate evidence remains retained after promotion.

The approval digest binds the pre-production operational state, exact call and
HTTP-status accounting, retry count, frozen paths, call-record hashes, revision
details, and every candidate/pre-production manifest. Changing any reviewed
field invalidates approval.

Dataset and operational-state candidates are promoted together. A durable
transaction journal is written before staging begins and supports deterministic
rollback after an interrupted, uncommitted transaction or cleanup after a
committed transaction. Committed recovery reconstructs any missing canonical
target from a fingerprint-verified candidate or stage before deleting backups;
if no verified new copy exists, it preserves all remaining copies and stops.
Rollback only installs a backup whose fingerprint matches the recorded
pre-transaction target; a still-valid canonical target is preferred over an
unknown or partial backup. Journal entries must match the exact ordered
candidate-to-production mapping, so swapping dataset and state sources is
rejected without mutation. The global refresh lock is acquired before recovery
or promotion preflight and remains held through final publication.
All candidate, Landing, production, and state manifests
are checked again after the provider lock is acquired. Paths must match the
run/phase topology; symlinks, junctions/reparse points, extra files, and unknown
partition layouts are rejected.

Run and audit each Yahoo symbol, FRED yields, FRED FX, and FRED VIX separately.
A Yahoo symbol failure preserves its prior-valid rows while independent valid
symbols may publish; a failure in one FRED phase cannot partially publish another.
FRED current observations do not establish
vintage/revision history; retained historical provenance limitations remain.
Prepared FRED checkpoints record an operational as-retrieved observation view
using the immutable call timestamp. The fredgraph CSV does not expose source
real-time intervals or series `last_updated`, so those fields remain null and
predictive use stays `PIT_BLOCKED_PENDING_VINTAGE_RESOLVER`.

The first authorized FRED validation completed through the explicit reviewed
end date 2026-08-14 for VIX, Treasury yields, USD FX, and the derived Treasury
spread. Each phase stopped after candidate preparation for review before its
separate zero-network promotion command. The FRED path now verifies exact
end-date rows, operational state coverage, and the production manifest before
run allocation; a 2026-08-14 VIX replay returned `NOOP_IDEMPOTENT`, API 0, and
mutation 0 while retaining the predictive vintage block.
