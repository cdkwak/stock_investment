# Archived GUI Status Snapshot

> Historical snapshot captured before the 2026-08-27 agent-routing
> compaction. It is evidence only, not current authority. Relative links are
> preserved as historical text and may no longer resolve from this archive.

# GUI Status

## Current phase

`AUTONOMOUS_GUI_ENGINEERING_ACTIVE / DASHBOARD_CURRENT_STAGE_AND_COLD_BOUND_VALIDATED / OTHER_COVERAGE_INCOMPLETE`

GUI and application-service work may proceed in parallel with Data and
Backtest without a separate phase approval or mandatory queue registration for
ordinary direct user tasks. Queue-backed work still requires a claim. Agents may
add screens, interactions, typed services, charts, local configuration,
read-only account views, and versioned Backtest/model result consumers while
preserving privacy, freshness, source identity, numeric suppression, and domain
ownership. Provider transport and canonical promotion stay in Data-owned code;
that architectural separation is not a ban on GUI implementation.

The future evidence-bound Korean daily summary is defined by
[`DAILY_MARKET_SUMMARY_CONTRACT.md`](DAILY_MARKET_SUMMARY_CONTRACT.md). It is a
GUI-owned projection contract and currently supplies no summary runtime,
provider call, account action, or trading authority.

Generated files under `artifacts/gui_validation/` now have one bounded,
reference-aware maintenance owner:
`scripts/maintenance/prune_gui_validation_artifacts.py`. The command is dry-run
by default and atomically records only repository-relative paths in
`.retention_manifest.json`. Each dry-run also records a deterministic plan
digest over the exact inventory metadata/content hashes, active references,
policy, bundle and keep/delete partition. Apply requires that exact digest via
`--reviewed-plan-digest`; a newly added or modified artifact, reference change,
policy change, malformed manifest, or digest mismatch rejects the operation
before quarantine or deletion. Malformed drive-absolute, rooted, encoded-path,
or traversal reference text is never persisted verbatim: only a bounded reason
code is reported, and apply remains fail-closed until the active reference is
corrected. The command protects every existing artifact named by active
documentation, source, scripts, or tests; all artifacts in the newest
`YYYYMMDD` filename cohort (or newest UTC modification-date cohort when no
filename date exists); and the 20 newest remaining files. Only a freshly
computed eligible list of contained regular files may be removed with
`--apply`. Apply first atomically moves each exact candidate into a same-root
private transaction quarantine, revalidates file identity, SHA-256 and active
references, then publishes `PURGE_COMMITTED` before irreversible removal.
Pre-commit failures restore moved files; partial recovery receipts distinguish
actual quarantine, restored originals, and original-path conflicts. Links or
reparse points, path/content changes after planning, archive-only references,
and paths outside the GUI validation root fail closed or remain out of scope.
This retention policy changes no GUI runtime, canonical Data, or acceptance
meaning. Canonical missing active-document references are reported in the
manifest rather than treated as files eligible for deletion. The first verified application on
2026-08-26 removed 15 unreferenced generated files (40 to 25 files; 4,432,782 to
2,625,253 bytes); a second apply was idempotent with zero eligible/deleted files
and no remaining quarantine. Because those two early runs predated immutable
receipts, the second run and later dry-runs overwrote the original live
manifest. The exact known counts, paths and sizes are preserved honestly in
`.retention_receipts/historical-20260826-first-apply.reconstructed.json`; it is
explicitly not an original receipt and does not guess the lost transaction id,
plan digest, or per-file hashes. Every subsequent apply atomically reserves a
unique transaction receipt directory before mutation, hard-links a fully
flushed immutable `PURGE_COMMITTED` receipt before irreversible removal, and
adds a separate immutable `APPLIED` receipt after success. A late final-receipt
collision is therefore detected while originals are still restorable from
quarantine. Later dry-runs and applies cannot overwrite either receipt, and
receipt files are excluded from the generated-artifact retention inventory.

The local current-observation coverage now exposes the exact recurring Toss
active-session projection for both `000660` and `005930`. Each row is accepted
only through its symbol-bound `TOSS_ACTIVE_SESSION_60M` route, exact XKRX
identity, `KRW per share` unit, aware provider timestamp, Toss provider,
`PROVISIONAL` finality, and display-only/PIT-blocked contract. Missing, stale,
malformed, identity/unit/route-mismatched, or NXT-close-only state remains
numeric-free. The separate LS `005930` observation and time-window-inferred NXT
close remain independent rows and never substitute for this active-session
projection. The GUI performs no provider or persistent write.

Dashboard `data_health` now summarizes the same validated retained 80-dataset
`HealthArtifactView` used for `health_rows`, rather than the nine market-card
fallback. `STALE` or `UNKNOWN` actionable rows therefore prevent an overall
`CURRENT` claim; counts for current, expected lag, stale/unknown failure,
operational block, predictive block, and research-only scope come from those
exact typed rows. Missing or invalid Health artifacts yield numeric-free
`UNKNOWN` summary state. This is a local read-only projection and does not
change card freshness or authorize provider, scheduler, or Data writes.

The current retained [2026-08-27 01:13:43 KST Health
artifact](../../artifacts/daily_health/universe_data_v2_20260819.json) (SHA-256
`be17a80bd3d7bddaed01c5361fbc2baad1fab6931dc8742cbf0ee41629145c67`)
has 80 rows and 80 unique dataset keys. Its actual-user scheduled execution
finished with process result 0 and validates all 35 runtime probes with zero
failures, including retained Yahoo native-15-minute history plus Toss and BOK
Korean Treasury roots. The former Toss Treasury failure was an exact-directory
ACL omission; `k4545` now has inheritable Modify on that one root and all 103
existing descendants while the owner and prior ACEs remain unchanged. The 30
automation-enabled rows remain exactly `CURRENT=8`, `EXPECTED_LAG=22`, and
`STALE/UNKNOWN=0`; actionable incident count is zero. The [accepted-date state](../../data/state/canonical_equity_accepted_dates.json)
and [breadth state](../../data/state/canonical_equity_breadth_status.json) each
contain the same eight canonical-equity dates through 2026-08-25; breadth is
`COMPLETE` with no pending date, and all five canonical-equity Health rows are
`EXPECTED_LAG / VALIDATED` at latest=expected=2026-08-25. All seven derivatives
Health rows use the completed-successor T+1 policy and are likewise
`EXPECTED_LAG / VALIDATED` at 2026-08-25. This Health projection remains
the status source for GUI rendering and does not turn `EXPECTED_LAG` into a
same-day freshness claim.

The existing `한국 국채` group remains deliberately numeric-free while BOK ECOS
817Y002 publication finality is `UNKNOWN`. The installed Data-owned 17:10 KST
observation task is collecting the remaining next-provider-day evidence; the
GUI will not substitute Yahoo Treasury quote indices, futures prices, or the
older Toss curve for an official BOK yield. Numeric exposure remains the next
consumer step after the three-batch review gate and contract-valid promotion.

The Dashboard derivative service now exposes Basis, volume PCR, OI PCR, Call
Wall, and Put Wall through `DERIVED_DAILY_T_PLUS_1` with
`DEPENDENCY_DRIVEN / enabled=True`. Production read-back gives all five
`display_state=VALUE`, `as_of=expected_as_of=2026-08-25`, and no unavailable
reason. The displayed values are Basis `2.92`, volume PCR `0.972263`, OI PCR
`1.649416`, Call Wall `1,597.5`, and Put Wall `700.0`. Stale, missing,
date-mismatched, or nonfinite input still suppresses its number.

The service now passes each derivative metric the exact Health-resolved T+1
expected date. After midnight on 2026-08-27 it therefore continues to accept
eligible 2026-08-25 Basis/PCR/Wall observations rather than independently and
incorrectly demanding 2026-08-26. This removes the false Dashboard
`갱신 필요` state without weakening the Health gate or accepting an ineligible
same-session derivative date.
If an automated derivative Health row or its exact expected date is absent, the
Dashboard now fails closed instead of falling back to a locally inferred date;
the metric is numeric-free `UNAVAILABLE`. Direct manual/research calls retain
their explicit local completed-date fallback.

Dashboard consumes the accepted local `kr_index_fundamental_daily` contract as
a descriptive, read-only market valuation view. Exact KRX `1001`/`2001`
identities must share the latest completed XKRX date and reconcile with the
accepted state row count before KOSPI/KOSDAQ weighted PER or PBR can display.
The visible comparison uses separate 5-calendar-year and 10-calendar-year
as-of-only empirical percentiles with exact metric-specific non-null counts and
date spans; the complete retained-history arithmetic mean, median, signed
differences, percentile, count and span remain inspectable in the tooltip. The
neutral percentile rails do not label low/high observations as cheap,
expensive, tops, bottoms, or trade signals. KRX owns the provider-native ratio
formula and its Forward/TTM horizon remains unresolved, so this surface says it
is not a forward ratio and never relabels it. Provider nulls and malformed or
duplicate rolling-window identities remain independently numeric-free rather
than falling back to the complete-history percentile. Rolling percentiles
outside 0--100, nonpositive/noninteger counts, future or noncanonical spans,
and spans outside their declared 5/10-year boundary are suppressed per metric.
Rows later than the accepted as-of date are excluded before value/identity
validation, so a malformed future observation cannot change the accepted
current view.
Missing, stale, duplicate, nonfinite, identity-mismatched, date-mismatched, or
state-mismatched input suppresses the affected numeric view. The values remain
`NON_PREDICTIVE`, use no fill/fallback, and perform no provider or persistent
write. GUI freshness now uses the same typed
`KRX_NEXT_TRADING_DAY_0910` availability policy as the scheduler: after an XKRX
close, the prior accepted valuation remains visible as an expected provider lag
until the next trading day at 09:10 KST. Only a missing observation that is due
under that shared policy becomes numeric-free `갱신 필요`; the GUI no longer
demands an unpublished same-day valuation immediately after market close.

The same tab keeps `Valuation` and `Earnings Momentum` as independent axes.
Forward EPS/BPS/ROE, estimate revisions/breadth and a matching forward
earnings-yield gap are currently unsupported, so the Earnings axis is `N/A`
and the visible market-regime state is `고점·저점 판정 보류`. Current KRX PER or
PBR never substitutes for a forward estimate, and no expected-earnings growth,
expected-book growth, PBR/ROE residual, multiple-expansion attribution,
`EARLY RECOVERY`, `LATE BULL`, or `TOP RISK` state is calculated.
The visible market-regime evidence line integrates the supported context
without creating an opaque composite score. It reports three independent axes:
complete price/trend/volatility evidence, accepted KOSPI PER/PBR historical
position, and PIT-safe forward earnings momentum. Current production evidence
reads `2/3`; Forward EPS/Revision/ROE is named as missing and the high/low-point
conclusion remains withheld.

The persisted `MARKET_FLOW` preference identity remains stable and maps to one
wide `시장 수급 · 밸류·실적` tab container below the KOSPI chart without a
preference-schema change. This replaces the default Dashboard NQ continuous-
futures panel, which remains off even when an older saved preference contains
its stable section id. The descriptive KRX weighted PER/PBR view is the default
visible tab: each market retains its accepted current/mean/percentile copy and
full tooltip evidence while a two-lane mini chart shows the PER and PBR
historical-percentile positions. The adjacent `시장 수급` tab keeps accepted
KOSPI/KOSDAQ latest and complete-week-to-date values and adds zero-centred,
period-relative signed bars for foreign, institution and individual flow;
exact KRW values and completeness evidence remain in labels/tooltips.
`신용·자금` stays independently reachable. The former empty U.S. tab is
removed because CFTC weekly participant positions are not a semantic substitute
for Korean daily investor net flow; the panel states that unsupported scope
without fabricating values. Market breadth is not promoted into this surface.
Neither tab calls a provider or changes Data.

The top strip keeps exactly ten configured cards in one row at logical widths
1280, 1365, and 1440 at DPR 1 and 1.5. Each card retains its full title, 10px
typed value text, a dedicated visible compact status row such as
`확정·08-25`, `갱신·08-25`, or `확인·N/A` for current, unavailable, and unknown
states, and an 18px
completed-session sparkline with at least 10 logical pixels of drawable plot
height. The date uses the metric's retained `as_of` only; timestamp-bearing
current observations are shortened to their display date without changing the
tooltip's full source timestamp. Real stale or unknown inputs remain
numeric-free with their fail-closed status rather than being hidden as a
cosmetic success. Provider-free separate-process Qt validation at both DPRs
confirms all three widths have ten columns, zero horizontal overflow, full
valuation label bounds, valuation default visibility, and reachable Market
Flow. The current GUI service and page regression suites pass 476 tests with
one intentional skip.
The default and current local density are now `COMPACT`: every top card is 92px
high, comparison copy remains prepared but hidden until the user explicitly
selects `DETAIL`, and a data reread cannot re-expand the strip to 112px. An
actual-root offscreen hot rereads after the natural 2026-08-27 01:02 and 01:32
Yahoo tasks showed all ten cards in one row, nonempty values, 5--36 finite sparkline points,
and zero labels containing `갱신 필요`. KOSPI/KOSDAQ flow displayed exact
2026-08-26 completed values; KOSPI/KOSDAQ PER and PBR displayed accepted
2026-08-25 values under the not-yet-due 09:10 publication policy. The same
The 01:32 occurrence advanced Yahoo VIX from the 01:00 to the completed 01:30
KST bar (`15.61`). The same Dashboard service and widget instances were reused
for both reads and reflected the new value without a restart.
Fresh exact Toss KOSPI/KOSDAQ headlines now derive only a
KST calendar-date cutoff from their aware retained source timestamp before
reading the separate completed-daily index series; the clock-bearing display
label is never passed to the daily query. Invalid or missing daily rows suppress
only the graph, and no current snapshot is appended or interpolated. A
provider-disabled production-root snapshot is `COMPLETE`: both Korean cards
retain their current headline and render 20 verified daily points through
2026-08-25 with zero horizontal overflow and byte-identical source/state/Health
inputs. That cutoff requires the exact `RETRIEVAL_TIMESTAMP` basis plus aware,
identical retained source/retrieval instants; a missing or provider-time basis
is rejected before either graph or comparison use. Their daily-average
comparison uses that same typed cutoff and the
latest eligible completed-daily value against its own 5/20-day means; it never
parses the presentation-only Toss clock label or requires the current headline
to equal a daily close. Malformed, missing, or older boundaries suppress only
the graph/comparison. The production proof emits zero clock-label parse warnings
and performs zero network calls. The current combined GUI services and
Dashboard/Backtest owner validation passes 268 tests with one intentional skip;
the independent GUI service suite passes another 208 tests.
The B400 owning regression
now passes: the Korean-equity and U.S.-ETF workspaces both fit the accepted
1,600px MainWindow while every context-watchlist action remains visible at or
above its minimum size.

An open MainWindow now watches exactly the project-local
`artifacts/daily_health/` and
`data/state/current_observations/global60m_current/` directories. If either
directory does not yet exist, only its closest existing project-local parent is
watched until creation, after which the exact directory watch is restored.
Directory creation, an atomic current-projection replacement, and a short burst
of replacements all route through the existing 900 ms single-shot/coalesced
Dashboard local-read lane. The watcher never evaluates or starts the optional
current-observation acquisition runner, never calls a provider, and performs no
Data write. Malformed or stale projections remain governed by the existing
typed numeric-suppression boundary. Watch paths and both reload timers are
stopped and released during final window close.

Dashboard current cards now have a typed provider-free fast stage independent
of the full local snapshot lane. It reads only exact accepted current JSON
projections, never Health, Parquet history, provider transport, account state,
or scheduler state, and publishes the ten card identities plus USD/KRW and
accepted Yahoo/Cboe Treasury quote rows without clearing charts, flows,
valuations, account, or other full-snapshot surfaces. The stage uses its own
service instance, so the full reader's mutable `LocalParquetQuery` caches are
not shared across threads. Reload bursts retain only the latest pending stage;
close waits for both managed lanes to reach zero QThreads without forced
termination.

Dashboard startup now distinguishes the bounded asynchronous local-read interval
from a typed unavailable or stale result. Until the full snapshot and selected
market chart arrive, all ten card bodies and the chart title/status explicitly
say `불러오는 중…`; the independent current-JSON fast stage may replace a card
with an accepted value earlier. The normal typed render then replaces every
loading label with its exact value/freshness or fail-closed unavailable state.
This changes no provider, Health, history, account, or scheduler behavior. The
owning Dashboard preference regression passes 22 tests and the 1600px dense-card
and responsive-layout slice passes 2 tests; an actual 150%-DPI 2560px desktop
smoke confirmed ten cards in one row and explicit chart loading before the full
local snapshot completes. The Phase-1 bundle-identity regression
`RQ-20260826T025726-5467` is now closed: the read-only
`BACKTEST_GUI_BUNDLE` check returns `PASS`, and all five accepted artifact files
remain byte-identical with `results_reviewed=false`.

The 2026-08-26 07:40 KST provider-free native smoke independently returned
`NATIVE_GUI_1600X900=PASS`, ten page contracts, zero failed or clipped pages,
rendered market chart, quiescent/closed workers, and byte-identical protected
user data with zero external calls or mutations. The later
`RQ-20260826T094237-B400` 1,674px Korean-equity regression is repaired without
hiding a control: the context-watchlist rail minimum is 210px and its five
actions use a two-row grid instead of one width-forcing row. The exact 1,600px
page regression and a four-test chart/workspace/watchlist/responsive slice pass.
The full owning GUI module reached 228 passed and 1 skipped; its sole unrelated
account-close timing failure passed immediately in isolated rerun.

The accepted Backtest NAV and drawdown plots now publish result-specific screen-
reader descriptions derived only from the already validated close-proxy view.
They expose the exact curve period and observation count; NAV exposes validated
initial/ending NAV and total return; drawdown exposes validated maximum
drawdown. Both descriptions retain the development-only, non-executable and
sealed-holdout-unreviewed boundary. Failed atomic rerender restores the prior
descriptions with the prior curves, while a direct unavailable/legacy view
clears stale descriptions. No metric is recomputed and no holdout outcome is
read. The three focused accepted/rollback/unavailable regressions pass.

Each stage result carries a generation and every numeric retains its exact
provider/retrieval timestamp basis. A late full snapshot may replace a staged
numeric only when it has a strictly newer valid source timestamp. A newer
malformed or stale stage suppresses an older current-only value but does not
erase an independently valid finalized-daily surface. The full snapshot has a
10-second composition budget, checks it between independent sections, and
returns `DEGRADED_BOUNDED` with stable section reasons instead of starting more
work after exhaustion; already accepted independent surfaces remain available.
No worker is force-killed.

The inverse race follows the same source-time rule: a rejected current-stage
metric replaces a current-only full metric only when the rejected source is not
strictly older. A rejected 08:00 UTC stage therefore cannot erase a valid 09:00
UTC full metric, while a rejected 10:00 UTC stage still suppresses that older
current-only value. Missing source time remains governed by generation/fail-
closed semantics rather than an invented timestamp. Equal aware instants and
every unprovable ordering caused by a missing, timezone-naive, or malformed
timestamp are regression-tested to keep the rejected stage numeric-free; only
a strictly older, fully normalized rejected instant preserves the valid full
metric.

E694 validation on 2026-08-26 measured the provider-disabled production-root
service stage at 2.080 seconds and its direct complete snapshot at 1.877 seconds
(`COMPLETE`, zero degraded reasons). The supported offscreen production-root
MainWindow cold smoke published the stage at 0.613 seconds, drained the full
local read at 6.621 seconds, and closed with zero QThreads. A deterministic
blocked-full-read widget test published USD/KRW card and rate-row values within
its 2-second bound, kept the event loop responsive, and rejected
an older late full result. Service coverage proves Parquet `read`/`tail` are
never touched by the stage, and missing, malformed, non-current inputs are
numeric-free. The current full service module passes 182 tests. After the
inverse-race and asynchronous-close regressions, the full owning GUI module
passes 216 tests with 1 skipped and the known missing retained Backtest artifact
assertion explicitly deselected. Closing while the provider-free current stage
is active remains nonblocking; a bounded cooperative event drain reaches zero
managed QThreads without termination. The Backtest artifact is outside E694's
allowed scope, while all current-stage, coalescing, stale-suppression,
responsiveness, and clean-close tests pass.

The active
[`GUI_REFRESH_STATUS_CONTRACT.md`](GUI_REFRESH_STATUS_CONTRACT.md) now has a
strict provider-free runtime implementation. Dashboard current observations,
Data Health, read-only account snapshots, and unsupported U.S. investor
classification render as four independent lifecycle rows with cadence,
observation semantics, source as-of, last accepted success, freshness,
retained-value warning, and evidence-bound next eligibility. Missing or
malformed local metadata leaves timestamps null and never promotes a numeric
value. Dashboard shows the compact overall lifecycle state and the exact
30-minute local reread cadence; Data Status exposes the detailed rows and one
`dashboard-local-reread` action. That action enters only the existing 900 ms
coalesced local-read lane and cannot start a provider, change Task Scheduler, or
write Data/account state. Account refresh remains the separately owned manual
Account-page operation in the current implementation; agents may add a
bounded Data-owned periodic/off-thread read-only refresh after its owning tests
pass. U.S. investor classification stays
explicitly unsupported and numeric-free. The projector reads only typed GUI
views plus the fixed Yahoo-current and Daily-Health receipt paths, retaining
only sanitized completion metadata. Owning validation passes 251 tests with one
intentional skip, including malformed/partial receipts, retained warnings,
provider-call-zero reread, watcher coalescing, Market Flow preservation,
responsive layout, and deterministic worker shutdown.
Yahoo last-success requires all exact 17 terminal route outcomes and reconciled
counts; a partial `PASS` fragment remains null. Daily Health requires positive
dataset/runtime-validation evidence, zero failures, and API zero. Current
display candidates without an aware valid source timestamp are suppressed as
`UNKNOWN` with `SOURCE_TIMESTAMP_INVALID`, even if an upstream object marks
`displays_value=true`.

The documentation-only
[`INDICATOR_SEMANTIC_CATALOG_CONTRACT.md`](INDICATOR_SEMANTIC_CATALOG_CONTRACT.md)
defines `indicator-semantic-catalog/v1` for future indicator explanations. It
separates immutable definition/formula/unit/horizon/aggregation/source meaning
from observation availability. Runtime source-as-of, market date, freshness,
last success, and refresh cadence render exclusively through one component
foreign key to `gui-refresh-status/v1`; the explanation stores no duplicate
timing fields, while static source-publication cadence remains a distinct source
semantic. Forward, trailing, current, and
unresolved values never substitute; unsupported and temporarily unavailable
states remain numeric-free; historical comparators require a definition digest
match; thresholds require exact provenance; incompatible units require labelled
independent axes/panels or a versioned normalization. The current KRX weighted
PER/PBR, Wilder RSI14, 60-session disparity, and VIX/VKOSPI examples preserve
their accepted boundaries without selecting a new source, inventing forward
PER, formula, threshold, or runtime widget. KRX PER/PBR publication/revision
finality remains `UNRESOLVED` and descriptive/non-predictive; immutable
comparator definitions are separate from changing runtime coverage and counts.

The desktop entry point now emits correlated start/stop and terminal-failure
events to the bounded local `runtime-diagnostic/v1` application store. The GUI
Backtest service emits the same strict sanitized contract for an injected
runner failure while preserving the existing visible/raised failure outcome.
The schema has no message, traceback, locals, arguments, URL, credential,
account, holding, order, or raw-response field, and logging failure is inert.
This is local diagnostic evidence only and authorizes no provider, scheduler,
dataset, account, or Backtest-result mutation.

### 2026-08-25 masked live Account validation

- The user-authorized manual Toss refresh completed through the active
  read-only account route and the GUI accepted the resulting sanitized local
  projection as `TOSS_READ_ONLY` at 23:56 KST. The inspected 1600x900 Account
  capture had `금액 숨김` enabled before the window became visible: headline
  amounts are `N/A`, holdings identity/quantity/value, allocation, and history
  are suppressed, and no account number or authentication field appears.
- The capture is user-owned under the Windows default Pictures/Screenshots
  location rather than repository evidence. The persisted Landing, Normalized,
  and state boundaries remain Data-owned; KB is an independent read-only source
  and is never merged into the Toss result. Family/manual slots remain local.
- A provider-free unit slice covering manual Account presentation, Net Worth
  page/service, account privacy, Toss read-only runtime, and mocked Toss/KB
  provider boundaries passes `430 passed, 2 skipped`. It performs no live
  account call and emits no account identifier or credential evidence.

### 아빠 manual holdings current-price cache

The dated `아빠` acquisition basis can now be joined to a separate, sanitized
v1 current-price cache. The join requires the exact normalized basis SHA-256
and ordered section/ticker identities, so quantity, average cost and purchase
total still come only from the accepted manual snapshot and are never rewritten.
Accepted rows expose their explicit provider and aware as-of labels plus KRW
current price, market value and unrealized P/L; within-section/currency weights
come from accepted prices only. Unsupported or typed-failed rows remain
numeric-free, and an incomplete section suppresses its aggregate valuation.

The GUI performs no provider call and no persistent write. The current operation
uses injected local evidence and API-zero tests only; no live Yahoo/FDR route,
real symbol map, recurring schedule, cross-currency total, account authentication,
order, or transfer is active. Data owns the cache contract and atomic refresh
boundary in
[the current operation](../data/operations/FAMILY_ACCOUNT_HOLDING_CURRENT_PRICES.md).

### 2026-08-23 account buying power and daily-average correction

- The read-only Toss account projection now retains official currency-specific
  `cashBuyingPower` for KRW and USD alongside holdings. The GUI labels it
  `현금 매수가능`; it does not call it deposit/cash balance, does not FX-sum
  currencies, and still leaves cash balance and realized P/L unavailable.
- The desktop runtime reads only the three required allowlisted Toss
  configuration names from process environment or project-root `.env`, with
  process values taking precedence. Startup is a provider-call-zero local read;
  the current Account-page click refreshes off the GUI thread, and busy clicks
  coalesce to at most one further manual cycle. Agents may implement a bounded
  Data-owned periodic/off-thread read-only refresh under standing authority;
  startup may remain a provider-zero local read and no per-cycle approval is
  required.
- A market-closed finalized headline is now eligible for its independent
  completed-daily 5/20-session comparison. Yahoo current headlines compare the
  latest completed daily observation with that daily series' own arithmetic
  means; intraday/current prices are not mixed into daily means. Stale or
  ineligible headlines remain numeric-free, and Bitcoin still has no accepted
  completed-daily history route for a 20-session comparison.
- RSI14 remains the verified Wilder calculation. The 60-day disparity remains
  `close / MA60 * 100` in data and is displayed as the equivalent signed
  `(close / MA60 - 1) * 100` percentage-point distance around zero.
- Top-card session traces accept the scheduled collector's completed `30m`
  output as well as retained `60m` output and expose the exact retained interval.
  This repairs the blank mini-graphs caused when collection moved to 30-minute
  bars while the GUI reader still required the legacy 60-minute literal.

### 2026-08-22 derivative, Treasury, funding, and responsive Dashboard update

- Finalized daily metrics no longer pass through the current-only provider-time
  gate. KOSPI200 basis/PCR/walls and VKOSPI therefore render by their explicit
  daily source date. When a retained derivative date trails the latest completed
  KRX session, the card shows the numeric only as `MARKET_CLOSED_LAST_VERIFIED`
  with the exact retained date; it is not labelled realtime or current.
- `파생상품 요약` now includes the official same-date KOSPI+KOSDAQ short-selling
  trading-value aggregate. It remains distinct from short balance, lending
  balance, and the two-symbol Toss watchlist.
- The unified `STOCK_DATA_YAHOO_MARKET_30M` task's typed `^FVX`, `^TNX`, and
  `^TYX` current projections are now local-read inputs to the Treasury rows.
  The visible conventional tenor set is 2Y/10Y/30Y: official FRED daily 2Y is
  retained because Yahoo has no accepted 2Y spot-yield symbol, while Yahoo/Cboe
  15-minute delayed 10Y/30Y values are primary for those tenors. `^FVX` remains
  collected and visible through Data Status, not as a replacement for 2Y.
  The retained current observations show yield-level values (`^TNX=4.738`,
  `^TYX=5.276`), so the Dashboard applies no additional scale conversion.
  FRED official daily yields remain separate tooltip references.
  No 2Y yield is inferred from the Yahoo 2Y Treasury-futures price.
- The retained LS OpenAPI `t8462` KOSPI200 futures investor Raw observation is
  connected to the Basis card as a separately dated foreign net-contract detail.
  It is descriptive/PIT-blocked and never presented as a futures price, OI, or
  realtime execution feed.
- The market-flow panel adds one `신용·자금` tab. Credit financing, investor
  deposits, brokerage receivables, and forced-sale amount retain independent
  source/date/freshness labels. A stale retained value is visibly dated; a
  missing value remains `보존값 없음`; no KB/LS/Toss field is silently promoted
  as an official KOFIA replacement.
- Dashboard market cards, main left/right columns, and derivative cards now
  reflow at width breakpoints. All four temperature gauges and their evidence
  summary own a truthful minimum height; shorter windows scroll vertically
  rather than clip or overlap. A 2560x1400 composition remains non-scrolling,
  and all profiles retain zero horizontal overflow.
- Responsive ratios use logical Qt pixels so Windows 125%/150% scaling enters
  the appropriate compact profile: `>=1450` uses 10 market columns and a 2:1
  chart/side ratio; `1180–1449` uses 5 columns and 3:2; `900–1179` uses 4
  columns with vertical body stacking; `<900` uses 2 columns, vertical session
  labels, and stacking. The application minimum is 900x640 rather than
  1200x800, and stacked body height now covers the full chart plus side-panel
  minimums instead of clipping the tail.
- The temperature summary exposes `근거 n/3` and names RSI, MA60, and the single
  VKOSPI-or-VIX volatility axis. Missing axes withhold the 10-point score.
  Materially old FX and stale breadth are numeric-free; detailed freshness,
  revision, and next-action copy remains in Data Status/tooltips. Derivative
  badges such as `최근 검증` and `발행 대기` are no longer persistent Dashboard
  text.

Dashboard와 Data Status의 정보 책임은 분리되어 있다. NQ=F 일봉 차트는
보존된 완료 일봉의 마지막 날짜·종가·직전 일봉 대비 등락만 표시하며 별도
60분 current observation 시각을 일봉 기준일로 합성하지 않는다. 화면 제한은
`최근 120개 표시 · 전체 252개 보유`로 명시한다. Dashboard 환율·금리 영역은
일상적으로 유효한 FX와 미국 공식 금리만 유지하며, 한국 국채 최종성·금리차,
VIX 선물 식별, 미국 옵션 P/C entitlement 같은 운영 판단 항목은 Data Status가
소유한다. Data Status는 current route와 Dashboard 항목별로 `판정`, `유효/관측
기준`, `출처/경로`, `시간 기준 또는 세션·최종성`, `다음 조치`를 한 행에
노출한다. 판정과 조치는 `ACCEPT`, `EXPECTED_LAG`, `STALE`, `UNAVAILABLE`,
`BLOCKED_*` 및 `KEEP`, `WAIT_PROVIDER_SCHEDULE`, `RUN_AUTHORIZED_LANE`,
`DO_NOT_USE`, `VERIFY_*`로 기계 판독할 수 있다. 실제 로컬 입력의 1600x900
offline smoke는 Dashboard와 Data Status를 포함한 5개 페이지에서 clipping 0,
worker 종료 PASS, provider/scheduler/data mutation 0을 확인했다.
환율 행은 typed current 판정이 숫자 표시를 거부하면 함께 전달된 일봉
시계열의 마지막 값으로 숫자를 복구하지 않는다. 따라서 Data Status의
`UNAVAILABLE`/`DO_NOT_USE` 판정과 Dashboard 숫자 노출이 모순되지 않는다.

### 2026-08-22 시장 수급·차트 표시 계약

- `시장 수급`의 KOSPI·KOSDAQ은 가격 카드가 아니라 외국인·기관·개인의
  일별 순매수 금액과 주간 누계를 뜻한다. 마지막 완료 KRX 거래일과 날짜가
  일치하고 `DAILY_FINAL`인 값은 공급자 이벤트 시각이 없어도 `장마감`으로
  표시한다. 장중 실시간 수급으로 오인하지 않으며, 오래된 거래일·부분 주간·
  단위 불일치는 계속 숫자를 숨긴다.
- 2026-06-03(지방선거일)과 2026-07-17(제헌절 임시 휴장)은 공식 KRX
  일회성 휴장일로 캘린더에 반영했다. 따라서 차트의 이전 `2일 missing`은
  공급자 결측이 아니라 캘린더 누락이었으며 더 이상 경고하지 않는다.
- 차트 선택기는 `KOSPI`, `KOSDAQ`, `Nasdaq 100`, `Nasdaq 100 Futures`,
  `Nasdaq`, `S&P 500`, `SOXX`, `GOLD`, `WTI`를 한 번에 고를 수 있다.
  `보조지표` 버튼을 눌렀을 때만 설정 패널을 펼친다. RSI14는 독립 0~100
  패널과 30/70 기준선을, 60일 이격도는 `(종가/MA60-1)*100`의 0 기준
  독립 패널을 사용한다. 둘은 동시에 서로 다른 하단 패널을 차지하지 않는다.
- 차트 화면은 공급자·route·원시 timestamp를 반복 노출하지 않는다. 그
  상세는 Data Status가 소유하며 차트에는 값, 기간, 지표와 필요한 세션
  경고만 남긴다.

The Dashboard header exposes two compact calendar-derived session groups. The
domestic group independently labels `KRX 장중/장마감 09:00~15:30` and
`NXT 장중/장마감 08:00~20:00`, so the KRX-close/NXT-open interval is explicit.
The U.S. group labels `장 시작 전`, `정규장`, `애프터장`, or `장마감` and
prints the corresponding KST range. XNYS session boundaries are converted from
America/New_York on the selected trading date, so summer/winter DST and exchange
holidays are calendar-derived rather than hard-coded. Provider, timestamp, and
route detail remain in Data Status rather than in the Dashboard header/cards.

The prior generic `확인 필요` copy is also narrowed for current-card failures.
An over-age or prior-date provider timestamp renders `갱신 필요`; a daily-only
row without a provider source timestamp renders `실시간 미연동`; the badge is
`실시간 없음`. This changes presentation only and does not promote daily data
to current data.

The top market strip is exactly ten cards, in this order and spelling:
`KOSPI`, `KOSDAQ`, `Nasdaq 100`, `Nasdaq`, `S&P 500`, `SOXX`, `GOLD`, `WTI`,
`BITCOIN`, and `USD/KRW`. SPY and VIX are not top-strip cards. A fresh completed Yahoo
60-minute projection replaces only its matching card. Cash index/ETF cards show
`60분 완료`; Nasdaq 100, GOLD, and WTI show `선물 거래`; BITCOIN shows `24시간`.
Nasdaq 100 is the `NQ=F` continuous-futures route and is not governed by the
U.S. cash-session header. Detailed source/session semantics stay in Data Status.
Each card shows both the absolute and percentage move against its exact retained
previous-provider-session comparison when that comparison matches the current bar.

The current visual rule supersedes the earlier badge copy: all ten card badges
are hidden. Futures/cash/24-hour source type, 60-minute completion, provider
session, and cadence are recorded only in Data Status. Cards retain only the
name, value/change, daily-average context, and compact session graph.
The accepted thirteen-route run populated the seven overseas cards, both
Korean session traces, and the separate USD/KRW current-only card. KOSPI and KOSDAQ remain finalized-KRX headline values
after close. The inspected 1600x900 offscreen composition had zero horizontal
and vertical scrollbars; all ten cards fit in one row with the value/change
line above its compact session graph.
KOSPI 6912.95 (+60.37, +0.88%) and KOSDAQ 801.94 (-38.95, -4.63%) now come from
the finalized 2026-08-21 KRX daily rows after close; the earlier 15:01 provisional
web snapshot no longer replaces a same-date finalized close.

Every top card now has a compact 14px completed-session sparkline slot.
It reads a strict display-only completed 30- or 60-minute session trace and never substitutes a
multi-day daily series. KOSPI/KOSDAQ use completed bars from 09:00 KST only as
the visual trace while the card value stays the finalized KRX close. U.S. cash
indices/SOXX, provider-labelled futures sessions, and Bitcoin's UTC day are
cut separately. Missing, malformed, single-point, or headline-mismatched traces
remain blank without suppressing a separately valid headline.
The dashed horizontal baseline is the exact retained previous-provider-session
close used by the card's absolute/percentage comparison. A latest value above
that baseline is red with a light red fill; a value below it is blue with a
light blue fill. Nasdaq 100 (`NQ=F`) and Bitcoin traces reset at the current
XNYS session open after that boundary arrives; the same exchange-calendar
conversion supplies the DST-safe open instant. This reset is visual only and
does not rewrite the stored provider trace or historical/Backtest data.
USD/KRW uses the exact `KRW=X` previous-provider-session close for its signed
change and percentage. Its visual trace resets at 08:00 KST; Friday's final
completed bar remains visible during the reviewed weekend closure instead of
being mislabeled as a live refresh failure. The lower FX row prefers this typed
current-only value when eligible. Official daily yield rows may show their
latest retained finalized value and date, but are never relabeled realtime.

Earlier compatibility evidence used a hidden right-side market-flow panel with
an empty `미국장` placeholder. That historical layout is superseded by the
current wide `시장 수급 · 밸류·실적` container documented above: its domestic
`KOSPI`, `KOSDAQ`, and `신용·자금` surfaces are visible below the primary chart,
the misleading empty U.S. tab is removed, and PER/PBR shares the same container
as an independent typed tab. No domestic investor category is mapped onto U.S.
data.
The final KRX trace bar ends at 15:30 KST rather than an artificial 16:00
hourly boundary. Inspected point counts are 7/7 for KOSPI/KOSDAQ and
19/3/3/3/19/19/17 for Nasdaq 100/Nasdaq/S&P 500/SOXX/GOLD/WTI/BITCOIN.

Freshness is session-aware. Nasdaq/S&P 500/SOXX retain the latest completed
16:00 ET cash-session close after market shutdown. NQ/GOLD/WTI retain the
reviewed Friday provider-session close over the weekend and are not labelled
official settlements. Bitcoin remains continuous and still requires a fresh
completed bar. This removes false `갱신 필요` states without hiding a real
continuous-source outage.

The current Dashboard contract distinguishes three time bases. A provider-time
route uses its timezone-aware provider timestamp. A broker snapshot may instead
use a timezone-aware retrieval timestamp only when its route explicitly labels
`timestamp_basis=RETRIEVAL_TIMESTAMP`; this means "received within 60 minutes",
not that the provider event time was known. A separately verified market close
remains fixed under its close/session contract. An unlabelled retrieval time or
a daily source-date label is never promoted implicitly. Provider/retrieval time
basis, source route, and session state are shown in Data Status, not added to
Dashboard cards; the Dashboard keeps only values and the two top market-session
labels. Current observations remain display-only and PIT-blocked.

The recurring domestic reader now gives exact UR-246 Toss projections first
priority for KOSPI, KOSDAQ, 000660, and 005930; absent or invalid Toss state
falls through to the already accepted local KB/LS/Naver boundaries without
merging values. The executable provider order is Toss → KB → LS. The two index
rows and both equity rows remain under the shared provider-time <=60-minute
gate, while close-only states keep the separate `장마감` contract. Provider,
time-basis, and session details continue to render only in Data Status.

The natural 2026-08-26 09:00 KST `STOCK_DATA_TOSS_DOMESTIC_30M` occurrence
completed all four exact identities with retry/fallback/replay calls zero and
Task Scheduler result 0. KOSPI and KOSDAQ carried current 09:00 provider/retrieval
times; both equity routes carried the accepted 08:49:59 provider time. A direct
provider-free Dashboard fast-stage read immediately returned KOSPI and KOSDAQ as
`VALUE / CURRENT_RETRIEVAL_TIME` from their exact Toss routes. This proves the
local collection-to-Dashboard path for that occurrence; it does not change the
provisional, display-only, PIT-blocked semantics or make process exit alone an
outcome-complete receipt.

For the four existing Yahoo Global60m identities, the GUI now prefers the
independent scheduled completed-bar projection over UR-232's retained recovery.
This projection is display-only and does not depend on historical merge
success; historical revision conflicts therefore cannot erase a valid current
row. Its historical Windows-task state remains disabled. Data agents may replace
or enable a current-only operation under the standing Data API authorization
after validating the current contract, provider limits, and scheduler ownership;
no separate permission-only approval is required.

The Korean-equity NXT-close display has one market-calendar exception. On a KR
non-trading date, an exact retained observation may remain numeric only when its
source date equals `ExchangeTradingCalendar(KR).latest_completed_session()` and
its provider timestamp is inside the verified 19:55--20:00 KST close window.
It renders as `MARKET_CLOSED_LAST_FINAL` / `장마감`, retains inferred and
`NOT_LIVE` provenance, and is never called today/current/realtime. An older
session, a timestamp outside the close window, or a new trading date remains
fail-closed.

The final accepted full-layout 2026-08-21 16:43 KST offscreen readback found zero
current numeric surfaces at that clock. Retained UR-167 KOSPI 6,907.74 and KOSDAQ 799.99 have source
time 15:01 KST, USD/KRW 1,385.8 has source time 15:29, and the retained 000660
pilot has source time 13:26:15; all exceed the 60-minute gate at the exact
readback clock. The bounded 2x2 current grid and all Dashboard sections remain
visible without clipping or scrollbars, while the stale numerics are removed
with typed reasons. This is honest offscreen Qt evidence, not native-window
evidence and not a claim that all 64 visible data surfaces are current. UR-161
and UR-167 are terminal/no-repeat; UR-168 composes only their exact public
manifests. UR-176 produced no usable post-close numeric, and UR-177/UR-181
accepted no KB slice with an independent provider timestamp for the shared
gate. Canonical history and Backtest remain isolated.

UR-192 adds only the accepted retained UR-190 Nasdaq SOXX state to the same
local boundary. At the exact injected 2026-08-21 17:20 KST offscreen capture,
the provider timestamp 17:08 KST passed today-KST/source-age<=60m: the SOXX
tape/coverage and headline showed `526.6332 USD per share`, `NASDAQ_OFFICIAL`,
snapshot/`PROVISIONAL`, display-only and PIT-blocked. The existing daily SOXX
chart remains a separate retained historical view. KOSPI, KOSDAQ, USD/KRW,
000660, daily references and unavailable routes remain numeric-free. This is
one hash-gated retained observation and local reread, not provider polling or
all-64 realtime completion; the capture has no scrollbars and zero workers
after clean close.

UR-193 then accepted independent 17:30, 18:30, 19:00, 19:30 and 20:00 KST windows
with one GET each, strict provider timestamps 17:28, 18:31, 19:00, 19:30 and
20:01 KST, Landing/projection hash readback and API-zero replay. The latest
accepted observation is `527.46 USD per share`. UR-220's 19:05 KST local audit verified that the Dashboard reader
and shared freshness gate expose the same value and updated only `tape_soxx` and
`coverage_fdr_soxx` in the 64-row CSV; the other 62 parsed rows remained
semantically unchanged. UR-222 then captured the actual Dashboard with the
accepted Qt-offscreen fallback at logical 1600x900: the compact UI truthfully
rounded SOXX to `529 USD`, structured readback retained `529.0132`, every other
unproved current row remained numeric-free, both columns and all cards fit with
zero scrollbars, and worker count was zero before and after clean close. This is
offscreen rather than native-window evidence. UR-226 and UR-229 subsequently
exercised separate one-use gated production projector operations: durable preimage
backup, atomic replace/readback and no-write idempotent replay passed; the latest
20:13 reconciliation keeps exactly the two SOXX rows visible at source 20:01 and
records the four global60m semantic/finality rejections as hidden prior-preserving
rows. Each manifest/runbook was terminalized. UR-198 composes UR-193's exact read-only manifest/ledger predicate into
the existing single GUI acquisition worker alongside UR-161/UR-167/UR-191.
Only an exact manifested, unattempted window constructs that collector; terminal,
orphaned, inactive or malformed state performs a local reread with API zero.
This provides a bounded 30-minute SOXX acquisition path, not a scheduler or an
all-surface realtime claim. Later windows remain independently gated, and the
shared today-KST/source-age<=60-minute rule still decides whether the numeric is
visible.

UR-233 adds the separate UR-232 retained-Landing current boundary. At its exact
21:27:16 KST local audit, four provider bars ending 21:00 KST passed the shared
<=60-minute gate: `KRW=X` is `KRW per USD`, while `ZT=F`, `ZN=F`, and `ZB=F`
remain provider-native continuous-futures prices and are never relabelled as
Treasury yields. The four local UR-118 envelopes retain source timestamp,
route, Landing hash/run provenance, display-only and PIT-blocked flags. A new
one-use UR-233 API-zero projector updated only the four corresponding readiness
surfaces (plus existing allowlisted SOXX/Naver reconciliation); its preimage,
atomic readback and terminal manifest are retained. The official USD/KRW row is
still a separate stale numeric-free route. Actual 1600x900 offscreen evidence
shows all five accepted local rows (SOXX plus four UR-232 rows), no scrollbars,
and zero workers before/after close; it is not native evidence or a broader
realtime claim.

UR-199/UR-203/UR-204 prepared the date-bound 2026-08-24 Korean session only:
exact 000660 and
005930 mobile-basic observations have independent per-identity/window claims,
strict Landing/readback validation, atomic prior preservation and a supported
operational entrypoint. The GUI uses only the public read-only eligibility API
inside the same serial worker. The 000660 coverage and 005930 header read their
separate exact local projections through the shared today-KST/source-age<=60
gate; daily charts remain independent. Missing or malformed manifests/ledgers,
pre-date clocks and terminal claims perform API-zero local rereads. This is
historical readiness for the first 2026-08-24 09:30 KST boundary, not a current
numeric claim. That boundary is now expired and authorizes no later-date call;
the paragraph records historical implementation evidence only.

UR-198 composes the existing UR-161, UR-167, UR-191, and UR-193 public
manifest routes only through the existing single current-acquisition worker.
Each route is read-only evaluated independently; inactive, malformed, or
durable-terminal routes inject no collector and leave the regular local reread
provider-free. Active routes run serially, contain a sibling failure, then cause
one local reread through their one composed worker completion. UR-193's 18:00
boundary is an API-zero expired record and 17:30/18:30/19:00/19:30/20:00 are
terminal accepted; the former 20:30 KST boundary is expired and cannot be
reclaimed or substituted with a later date.

UR-204 added only UR-203's public `eligible_identities(root, now)` preflight and
its supported Naver-equity callable to that same worker. The callable is not
constructed for missing, malformed, inactive, or durable-terminal state, so
those outcomes remain API-zero local rereads. The exact date-bound 000660 and
005930 projections are local readers only, pass the shared today-KST/provider
source-age<=60-minute gate before a numeric is shown, and do not alter daily
charts. Their first eligible boundary was 09:30 KST on 2026-08-24; no GUI test
or integration invoked it, and the expired manifest cannot be reused.

UR-209 verifies the worker uses UR-208's terminal-aware half-open predicates:
UR-191 calls `eligible_boundary(root, now)` and UR-203 calls
`eligible_identities(root, now)`. At 09:31 KST each can bind only the 09:30
boundary; at 10:00 KST each can bind only 10:00, never a 09:30 backfill. The
same injected clock reaches the supported callable. Missing/malformed manifests
or ledgers and terminal current records inject no runner and remain API-zero
local rereads. The serial failure containment, coalescing, one local reread and
clean shutdown boundaries remain unchanged.

UR-115 adds one strictly separate current-display observation to the Korean
equity page. The validated FDR 0.9.202/Naver `000660` pilot is shown as
`업데이트됨` with its source date and retrieval timestamp, while the existing
provider-native EOD frame, indicators, comparison, canonical data, and Backtest
remain unchanged. The current-display file is exact-symbol scoped and atomic;
another symbol never sees the value. Failed yfinance `^GSPC` history and
WebSocket attempts produced no numeric projection and do not replace retained
global data.

UR-116 additionally retains one retry-zero, ten-route FDR/Yahoo daily batch.
Seven routes validated through 2026-08-20; Dashboard projects S&P 500, Nasdaq,
SOXX, NQ continuous, Gold, and WTI as clearly labelled daily current-display
fallbacks, while the Yahoo/FDR VIX observation remains separate and official
FRED VIX keeps ownership. KOSPI, KOSDAQ, and USD/KRW failed only their tested
routes and were not retried. These display observations remain `PIT_BLOCKED`
and never alter finalized history or Backtest inputs.

UR-120 integrates those retained current-display projections with the accepted
UR-118 current-observation foundation and the KB/Toss/LS adapter boundaries.
The Dashboard exposes a local-only coverage matrix with exact
provider/route/interval/source-date/retrieval/freshness/finality detail:
accepted FDR daily observations remain displayable, while KB (closed capture
window/no timestamp-valid slice), Toss KOSPI (one-shot OAuth/transport failure;
market GET zero/no retry), Toss KOSDAQ (not selected), and LS t8412 (no accepted
2026-08-21 current projection) are explicitly numeric-free. Startup, manual, watcher and
30-minute events coalesce into one GUI-thread local reread; this makes zero
provider requests, does not refresh account data, and stops cleanly at window
close. The remaining automation gap is intentional: no broker current route
has a newly accepted local display projection, so the timer is not a provider
refresh claim. The LS detail reader is pinned to only
`data/state/current_observations/ls_t8412_current.json`, route
`ls-t8412-current:XKRX:005930`, identity `KR_EQUITY_CURRENT/XKRX/005930`.
It rejects malformed, wrong-identity, wrong-date, and non-15-minute state; an
accepted future record would be shown only as a provisional native-price,
`AS_RETRIEVED`, display-only/PIT-blocked retained observation in the `005930`
detail/header and coverage tooltip. UR-135's 2026-08-21 bounded attempt failed
at OAuth (OAuth 1, t8412 0, retry 0), so no state was accepted and the LS row
currently remains numeric-free with `LS_T8412_CURRENT_15M_UNAVAILABLE`.

The value-bearing FDR coverage rows are specifically
`RETAINED_AS_RETRIEVED`, not `CURRENT`: their daily source date is an
as-retrieved source-date label rather than a provider availability or live
refresh timestamp. The visible and accessible coverage detail makes that
boundary explicit.

FAST ITERATION V2.1 corrects the active Dashboard without changing collectors,
production data, schedulers, or account APIs:

- UR-111 adds a freshness-truth overlay without making stale data current.
  Contract-valid retained Dashboard, Index Graph, Korean-security and accepted
  SOXX history remains visible when Health is `STALE`, with a prominent
  `STALE RETAINED HISTORY` warning, exact retained/expected dates and blocked
  current-data claims/actions. Unreadable, invalid, empty, identity-mismatched
  and unauthorized routes remain numeric-free. A transport-free coordinator
  and metadata-only local poller are implemented and focused-tested. UR-120
  adds a coalesced 30-minute **local GUI reread only**; it is not a provider
  refresh or scheduler. Existing 900 ms Health-directory debounce remains API
  zero.

- The duplicated `시장 상황` panel is removed. Daily changes have one owner in
  the top market tape.
- The active body is a 1600x900 logical-pixel two-column layout. KOSPI and NQ
  charts are on the left; market temperature, official FX/rates, and the
  collapsed account state are on the right.
- `시장 온도` separates RSI14 momentum, signed MA60 distance, and VIX/VKOSPI
  percentiles. RSI keeps visible 30/70 references; MA60 uses a centered zero
  reference instead of a one-direction progress interpretation.
- Its summary emits an overbought/oversold candidate only when RSI and signed
  MA60 direction agree. Volatility remains context, and the copy requires
  price-reversal and volume confirmation; disagreement explicitly withholds a
  conclusion.
- The same summary now displays a descriptive `과매도 강도 0.0–10.0`. RSI14
  contributes at most 4 points, signed MA60 downside distance at most 3, and
  one volatility percentile at most 3. VKOSPI is preferred for KOSPI and VIX is
  fallback-only, so correlated fear gauges are never double-counted. If any
  axis is unavailable, the score is withheld rather than treating missing data
  as zero.
- NQ is labelled `NQ=F 연속선물`. The retained 252 completed Yahoo daily OHLC
  rows remain available, while the default daily view shows the latest 120 bars
  so candles remain legible. Weekly/monthly aggregation remains observation-only.
- FRED DGS2 is the visible official daily 2Y value; Yahoo/Cboe delayed `^TNX`
  and `^TYX` are the visible primary 10Y/30Y values in the rate panel. Yahoo
  ZT/ZN/ZB continuous-futures prices are no longer shown as
  rates or converted to yields.
- The meaningless `가격 PCR` card is removed. The local contract now exposes
  separately labelled KOSPI200 option volume P/C and OI P/C. U.S. option P/C
  remains numeric-free until a documented, rights-compatible Data-owned route
  and validated identity/unit/timing/finality contract exist. Agents may
  research and implement that route under standing Data authority. Yahoo UR-094
  establishes only that its exact tested unofficial access mode lacked accepted
  access evidence, not that Yahoo web option functionality is unavailable.
- An unavailable account occupies a 42px collapsed row. No balance is inferred.
- The separate Account page now accepts only validated, identifier-free local
  snapshots. Toss and KB provider refreshers are injected independently only
  when each source's explicitly named process environment is complete. In the
  current implementation the user click runs both sources independently off
  the GUI thread and startup remains local/provider-call-zero; there is no
  periodic account timer yet. Agents may add a bounded Data-owned periodic or
  OS-scheduled read-only refresh after owning tests pass. One click runs the two sources independently off the GUI
  thread, preserves each prior valid source on failure, and never merges their
  responses. KB SSQM2952 was live-validated read-only on 2026-08-26; family
  attribution remains local-only and incomplete combined totals stay hidden.
  Both KB token and account POSTs reject redirects, and the production session
  ignores ambient proxy/`.netrc` configuration; the provider/runtime/
  coordinator plus full owning GUI run passes 264 tests with one skip. Account
  completion rechecks a transient Qt `isRunning()` state instead of dropping
  the only completion notification, then releases only its exact stopped lane
  before deferred Qt deletion. The deterministic transient-state regression
  and ten fresh-process race groups (50 checks) verify nonblocking close and one
  coalesced successor both finish with zero running QThreads.
- The KOSPI200 breadth row consumes the exact retained 2026-08-12 membership
  and price join through Health V2 and shows 81 advancing / 111 declining / 8
  unchanged only while the typed row is CURRENT. Date mismatch, stale, invalid,
  or unreadable inputs clear all three numbers.
- Yahoo `^FVX`, `^TNX`, and `^TYX` native 15-minute quote indices are presented
  separately from official FRED yields. They retain their own units and KST
  timestamps and never replace DGS2/DGS10/DGS30 or the contracted 10Y-2Y spread.
- The runtime now has one `DashboardPage` directly based on `QScrollArea`.
  The two dead Dashboard generations, their duplicate `시장 상황`, and their
  obsolete `PRICE_PCR` restoration path are removed.
- Yahoo NQ=F, GC=F, and CL=F cards say `완료일봉`, retain the Yahoo date, and
  explicitly state that they are not realtime quotes. NQ weekly/monthly views
  keep the partial aggregate visible but label it `진행 중 집계` through the
  last observed daily bar.
- Delayed 60-minute values remain fail-closed after four hours during the
  trading week. A Friday finalized bar may remain visible only during the
  reviewed common weekend closure and only for at most 72 hours; this is not a
  holiday-aware calendar policy.
- Dashboard, Index, and individual-equity share-volume panels use one shared
  visible-range formatter. Each view states exactly one unit in the axis title
  (`거래량(억주)`, `거래량(만주)`, or `거래량(주)`), renders three to five
  short ticks without scientific/SI-offset notation, and keeps the exact
  unscaled share count in hover/detail text. The fixed 72px plot gutter is
  shared by price, volume, and indicator panels so resize/maximize/reparent
  cannot misalign their linked x-ranges.
- Market-wide investor flow is no longer attached to the selected chart. A
  dedicated right-side tabbed panel keeps KOSPI and KOSDAQ separate and shows
  foreigner, institution, and individual signed KRW values for both the latest
  accepted session and Monday-to-date. The weekly view counts actual XKRX
  sessions, labels a partial week, and suppresses only the weekly numbers when
  a required retained session or provider/unit boundary is missing.
- The new `종목 차트` page searches the retained Korean equity master by Korean
  company name or exact ticker and requires the user to select the displayed
  `name · ticker · KOSPI/KOSDAQ · 보통주` identity before any price read. It
  serves only provider-native original daily OHLCV through the typed
  `kr_equity_price_daily` Health gate, labels the view `원본(미조정)`, and keeps
  15-minute selection, adjusted prices, total-return inference, and
  corporate-action markers unavailable until their own contracts are accepted.
  Search and bounded symbol reads run outside the GUI thread. A new selection
  immediately clears the prior candles, prices, indicators, crosshair, hover,
  and status before the next local result can render.
- Index Graph and `종목 차트` can each open an independent top-level chart
  window. A detached page clones the current symbol, period, indicator modes,
  fitted or manual zoom/pan range, crosshair, and detail state once, then owns
  all later changes independently. Every detached local read runs in its own
  bounded worker over the main window's shared read-only `DashboardService`;
  closing a detached or main window drains workers and deletes the chart window.

Focused GUI validation is `61 passed`. The U.S. option contract/parser/ratio
E2E slice passes `79` focused tests. The complete repository suite is
`1134 passed, 6 skipped`; its seven warnings are emitted by the installed
`exchange_calendars` dependency. The earlier native evidence correctly hid the
then-retained 2026-08-18 Basis/PCR/Wall scope against expected 2026-08-19.
UR-023 subsequently advanced the complete production scope through 2026-08-19
with retained Landing/API 0 and an API-0 replay; UR-095 then bounded domestic
GUI reads to the Health-verified date. Its native Dashboard and Index captures
show populated 120-session KOSPI price/volume and the current derivatives row
through 2026-08-19, while later unverified local rows remain excluded. FRED
2Y/10Y/30Y yields `4.19% / 4.72% / 5.31%`. The native Windows DPR 1.5 capture
`artifacts/gui/dashboard_v21_native_1600x900_20260820.png` verifies Korean
glyphs, the zero-centered disparity scale, unclipped gauges, and the complete
derivatives row. Hidden native launch/termination also passes. The full suite
and project-wide status reconciliation pass at this phase exit.

UR-036's user-equivalent current-build native validation now covers Index Graph
KOSPI 120D at 1600x900, maximized, restored, and repeated resize. Only the price
axis owns a width-aware ISO-date label row; linked volume/indicator axes emit no
duplicate ticks. The 120 Health-verified observations remain fitted across the
full width through 2026-08-19, volume stays independently scaled, and the
unified Index/Period/RSI14/60D-disparity controls remain unclipped. Focused axis
validation passed `5` tests, and native cleanup reported zero QThreads. This
also closes the retained UR-035 session-axis and UR-047 chart-control native
acceptance conditions without repeating their prior tests.

UR-062 adds an optional typed common-base-100 comparison to the individual
Korean-equity and dedicated U.S.-ETF pages. Korean comparisons use only the
exact matching KOSPI/KOSDAQ index identity, provider-native original target
prices, finite date-unique observations, and a one-to-one exact-date inner join;
the first common date is exactly 100 and holidays are never forward-filled.
Currency, price/index basis, period, common start, freshness, and relative
changes stay explicit, and detached windows clone the comparison state. Native
main 1600x900 and detached 1200x760 evidence is accepted with `4` focused tests.
The thirteen U.S. ETF identities remain numeric-free before any ETF or global-
benchmark file read until their separate production price and benchmark lanes
are accepted; no adjusted/total-return/FX, alpha, or Backtest path was opened.

UR-099 consolidates the existing MA5/20/60/120, Volume, RSI14, and 60-day
disparity display controls into one compact Korean, keyboard-accessible
`IndicatorControlPanel` for Dashboard, Index, Korean equity, and U.S. ETF
contexts. The panel is the single presentation owner; Dashboard's legacy toggle
widgets were removed after Lead rework. Existing formulas and defaults are
unchanged, detached windows clone the settings, and schema-v3 stores only
presentation preferences through the existing atomic primary/last-valid
boundary. Invalid/private-shaped preference content returns to documented
defaults. Retained validation is `15` focused tests plus `3` Dashboard-owner
rework tests and native Index/Dashboard normal/maximized captures with clean
shutdown.

UR-100 is accepted: EMA20, ATR14, ADX14, OBV, Bollinger Bands, and bandwidth
are descriptive local calculations with exact warm-up/missing-value semantics;
their single lower-panel owner prevents incompatible unit overlays. UR-101 is
accepted: the individual-equity workspace preserves its exact identity, local
tabs, indicator settings, manual ranges, and detached-window state while keeping
dividend and option values numeric-free. UR-103 is accepted: Korean equity
daily/weekly/monthly presentation is a local transform of the already-loaded
exact daily frame. It uses XKRX calendar/reference-date completeness,
valid-volume-only sums, explicit incomplete aggregates, local-only state reset
on timeframe change, removable keyboard/mouse two-point measurements, and
detached timeframe/measurement/range copying. It creates no provider request,
Data mutation, scheduler action, comparison path, or Backtest input; UR-062
remains the sole common-base-100 comparison owner.

UR-109 is submitted for Lead acceptance. Dashboard now uses the same unified
presentation preference owner as before, but its renderer honors every exposed
MA5/20/60/120, EMA20, and Bollinger control on the price plot. RSI14 Overlay
uses an independent 0–100 right axis and 30/70 guides; 60-day disparity Overlay
uses its own signed percentage-point right axis and 0 (=100%) guide. They never
occupy Dashboard's hidden lower indicator widget. Turning a control Off clears
its curve, guide, axis, legend text, accessible chart state, and hover value;
the two-row Dashboard control arrangement remains readable at 1600x900.
Focused Dashboard/preference/lifecycle validation passed 7 tests. Native Windows
Malgun Gothic evidence is
`artifacts/gui_validation/ur109_dashboard_indicators_native_1600x900_20260821.png`
(logical 1600x900; DPR 1.5 capture 2400x1350) and
`artifacts/gui_validation/ur109_dashboard_indicators_native_maximized_20260821.png`
(2561x1334 capture); both show the enabled MA5, MA120, EMA20, BB(20,2), RSI14,
and disparity overlays with readable controls. The native capture process
closed with zero workers. No Data/API/scheduler/Backtest operation occurred.
Lead rework then completed the remaining control-to-hover parity: the Dashboard
tooltip now lists each enabled MA5/20/60/120, EMA20, and individual Bollinger
upper/mid/lower value as well as enabled RSI14/disparity overlays, and clears
them all on Off. The retained owner test now turns each control on and off
individually to assert its exact plot owner/name and cleanup; the preference
restart test now renders a retained frame and verifies restored curves, axes,
legend, tooltip, and hidden lower panel. The two rework-owning tests passed;
the layout was unchanged, so the accepted native captures were deliberately not
repeated.

The integrated GUI module passes `60` tests after the readable-volume change.
Native captures
`artifacts/gui_validation/ur089_volume_axis_1600x900_20260820.png`,
`artifacts/gui_validation/ur089_volume_axis_maximized_20260820.png`, and
`artifacts/gui_validation/ur089_stock_volume_axis_detached_1600x900_20260820.png`
verify billion-scale `거래량(억주)` ticks `0/5/10/15`, lower-volume
`거래량(만주)` ticks, unclipped titles, aligned panels, and exact unscaled
stock volume in the detail row. The capture processes closed normally.

UR-043 adds `7` focused search/identity/routing/state/worker tests; the complete
GUI service and widget modules pass `127` tests. Native captures
`artifacts/gui_validation/ur043_individual_equity_1600x900.png` and
`artifacts/gui_validation/ur043_individual_equity_maximized.png` verify Korean
search controls, exact identity and price-mode copy, 120 legible daily candles,
MA/volume alignment, exact-share hover, and no clipping at 1600x900 or the
1707x889 maximized logical viewport. The native DPR 1.5 1600x900 grab is
2400x1350 physical pixels, and the capture process closed normally. Current
retained production truth remains fail-closed: Samsung `005930` resolves to its
KOSPI ordinary-share identity. Under UR-111, a `STALE` Health classification no
longer erases an otherwise contract-valid retained frame; the page renders that
history with exact as-of/expected metadata and a stale warning. Exact protected
canonical production files are still unreadable to the current process, so
their date is not guessed and the page remains numeric-free until a valid read
succeeds.

UR-040 moves the old KOSPI-only flow strip into the separate KOSPI/KOSDAQ
right-side market-flow panel. The complete GUI service and widget modules pass
`131` tests. Native Malgun Gothic captures at logical 1600x900 and maximized
1707x889 show both market tabs, signed direction text, two accepted XKRX
sessions through 2026-08-19, no chart compression, and zero horizontal or
vertical Dashboard scrolling. The capture process closed normally. Evidence:
`artifacts/gui_validation/ur040_market_flow_native_1600x900_20260820.png` and
`artifacts/gui_validation/ur040_market_flow_native_maximized_20260820.png`.

UR-044 adds `새 창에서 열기` to Index Graph and the individual-equity page.
The complete GUI service/widget modules pass `134` tests, including three new
multi-window state, worker-thread, failure-isolation, and deletion-lifecycle
tests. Windows-native Malgun Gothic evidence shows main Index plus a detached
exact `삼성전자 · 005930 · KOSPI` fail-closed view, and the reverse main-stock
plus detached KOSPI 120D arrangement. The detached Index preserves the fitted
`-2.38..121.38` session range across 1050x680 to 1500x900 resize and retains the
shared readable volume unit. Final cleanup leaves detached count 0, running
QThreads 0, and the main window hidden. Evidence:
`artifacts/gui_validation/ur044_main_index_detached_stock_native_20260820.png`
and
`artifacts/gui_validation/ur044_main_stock_detached_index_resized_native_20260820.png`.

U.S. option P/C remains a truthful blocker, not a fabricated zero. The
unregistered ORATS contract-only Normalized→Derived→Published slice strictly
parses separately scoped SPX, QQQ, and NDX volume/OI totals, preserves capture
evidence, returns null for zero denominators, and hides all ratios until
entitlement, finality, and root scope are explicitly confirmed. No number may
appear before subscription approval, a bounded Landing pilot, and five-session
reconciliation. See the [ORATS source guide](../data/sources/orats/README.md).
Purchasing an ORATS subscription or accepting its terms remains user-only;
agents may independently research and implement a public or already-entitled
rights-compatible source under the standing Data runbook.

## Superseded Dashboard V2 status

`DASHBOARD_GUI_V2_QUALITY_ACCEPTED`

Dashboard V2 reorganizes the accepted local-only view around market summary,
KOSPI/NQ charts, market direction, overbought/oversold state, a compact account
placeholder, FX/rates, and the derivative summary. KOSPI RSI14 now uses Wilder's
original SMA seed plus recursive smoothing and reads `45.82` for 2026-08-19;
the 60-day disparity reads `86.14%`, rendered as `60일선 대비 -13.9%`. The
combined user-facing state is `단기 모멘텀 중립 · 중기 추세 약세`. VIX and
VKOSPI ranks use the latest observation's percentile within the trailing 250
valid retained sessions and currently render `낮음` at 10.8% and `높음` at
68.0%, respectively.

Native Windows visual QA passes normal Korean glyph rendering, hierarchy,
semantic colors, and zero clipping/overlap/scroll at 2560x1440 and actual DPR
1.5. NQ daily/weekly/monthly views render 252/53/13 completed bars; price PCR
remains numeric-free; Basis remains source-native difference; Wall remains the
maximum-OI strike. The desktop identity `ChangdaeNote\k4545` now has additive
`ReadAndExecute`/traverse access only on the two retained Basis/PCR roots. Their
protected inheritance, owners, and existing ACL entries are preserved; no
write, modify, delete, or FullControl permission was added. Native runtime and
both final captures show the retained 2026-08-18 Basis `-3.75` and volume PCR
`1.09642` through the typed `EXPECTED_LAG` view. Network, provider, collector,
promotion, scheduler, and Data/state mutation counts remain zero.

The approved 2560x1440 Market Overview Phase 1 is implemented over one typed,
fail-closed `DashboardMetricView` boundary. Every Dashboard number now carries
its dataset/series identity, value/unit, retained and expected dates, source,
freshness, PIT meaning, automation state, display state, reason, and route.
Only `CURRENT` and provider-normal `EXPECTED_LAG` metrics whose retained date
matches Health V2 may expose a value. `STALE`, `UNKNOWN`, `BLOCKED`, missing,
malformed, and date-mismatched inputs have `value=None`; a failed refresh clears
the affected card instead of preserving an earlier number.

The landscape first screen now uses a width-prioritized, height-capped horizontal layout and
follows the
`artifacts/gui/dashboard_market_overview_v1.png` direction: a ten-slot market
strip, separate KOSPI and Nasdaq-100 Yahoo continuous-futures chart regions, centered
market/overbought-oversold explanations, a horizontal derivatives summary, and
right-side account/asset and compact FX/rate panels. The account area is now
taller than the rate panel and contains `총자산`, `주식평가`, `예수금`,
`주문가능`, `평가손익`, and a blank cumulative-asset chart. It remains
explicitly `연동 전 / NOT_AVAILABLE`; no balance is fabricated or persisted.
The retained runtime displays KOSPI,
KOSDAQ, SOXX, NASDAQ, S&P 500, VIX, VKOSPI, USD/KRW, U.S. 2Y/10Y/30Y, the
contracted derived 10Y-2Y spread, and descriptive Yahoo-continuous NQ=F, GC=F,
and CL=F. The three futures series are explicitly `PIT_BLOCKED`, never treated
as individual expiries or official settlements, and use only completed daily
bars through 2026-08-18. NQ=F now preserves OHLC and renders a 252-observation
candlestick view with `일봉 / 주봉 / 월봉` selection; weekly and monthly bars
aggregate only observed daily rows and never fill missing sessions. KOSPI foreign/institution/individual flow now exact-date joins the 2026-08-19
KOSPI close to the jointly promoted Toss KOSPI/KOSDAQ daily rows. Stale/gated
basis/PCR/Wall inputs were refreshed by a bounded two-call official 2026-08-18
run. The GUI now exposes descriptive KOSPI200 Basis, volume PCR, and front
retained-maturity Call/Put maximum-OI strikes with their dates and limits.
Price PCR remains numeric-free because no price-PCR contract exists. NDX is not used for NQ, SOX is not used for SOXX, OI PCR is not
used as price PCR, and the Treasury spread is never recomputed in the GUI.
The KOSPI RSI14 gauge includes visible 30 and 70 reference ticks; its tooltip
labels them as oversold/overbought references and explicitly says they are not
an investment-decision rule. No arbitrary thresholds were added to percentile
or disparity gauges.

The compact FX/rate panel now pairs finalized delayed 60-minute Yahoo bars with
official FRED daily observations. `KRW=X` is shown as `60M 지연` beside the
official H.10 daily FX value. `ZT=F`, `ZN=F`, and `ZB=F` are explicitly labelled
`선물가격`; they are never displayed with a percent sign or converted into a
yield. The official 2Y/10Y/30Y FRED yields remain separate `공식 일일` percent
values. The accepted capture is
`artifacts/gui/dashboard_global_60m_delayed_official_daily_20260819.png`.
The NQ candlestick and refreshed-derivatives capture is
`artifacts/gui/dashboard_nq_candles_derivatives_20260819.png`.

An in-process local file watcher observes only `artifacts/daily_health/` and
`data/state/current_observations/global60m_current/` (or each target's closest
safe project-local parent until creation) and debounces changes for 900 ms. A
successful atomic Health V2 or accepted current-projection replacement therefore
updates an already-open Dashboard without a provider call. A runtime validation
failure clears only the affected number and cannot reuse an older static date.

The 2560-wide composition uses a 9:3:4 main-column ratio and balanced 58:42
KOSPI/NQ chart heights, avoiding the earlier full-height vertical stretch.
Compileall and diff-check passed. Native Windows launch/termination passed with
no residual process. The latest scaled-display verification capture is
`artifacts/gui/dashboard_autonomous_final_rsi_thresholds_2560x1440_150pct_20260819.png`.
At 100%, vertical/horizontal scroll maxima were both zero and the complete
derivatives panel ended at logical y=789. At the 150%-equivalent 1707x960
viewport, both scroll maxima and top-card overlap were also zero. The final
1707x900 capture also has vertical scroll maximum zero with both RSI ticks visible. Socket-connect
attempts and running worker threads were zero, and the before/after `data/`
inventory remained identical at 83,964 files and 19,908,205,342 bytes.

The accepted daily-use v1 Index, Data Status, and descriptive Backtest pages are
preserved. Dashboard bounded chart selection and cached Volume, MA60, RSI14,
and 60-day disparity interactions remain available, but a chart is now cleared
when its selected metric fails the typed freshness gate. Project-domain
selection remains GUI and is owned only by Project Status.

A fourth read-only Backtest page now consumes only the typed local
`BacktestResultService` over the retained descriptive replay artifact. It shows
experiment status, frozen coverage/digest, thresholds, horizons, signal counts,
supplied metrics, development crisis diagnostics, and explicit sealed-holdout
rows. It performs no feature, signal, label, or metric calculation and
explicitly displays `NOT A PORTFOLIO BACKTEST - EQUITY CURVE UNAVAILABLE`.

The same page now exposes a separate, clearly labelled development-only fixed
RSI14 scenario panel through `backtest-gui-scenario-adapter/v1`. It accepts no
editable threshold or search control: only the predefined LOW30/HIGH70
conditional study and fixed 30-entry/70-exit next-open scenario are eligible.
The panel shows conditional versus unconditional summaries, exact signal
coverage, accepted next-open ledger metrics and same-entry matched-hold
differences while stating that no winner is selected and no recommendation is
provided. Missing exact typed development inputs, insufficient signals and
no-entry comparison remain numeric-free. Evaluation runs on the existing
managed Backtest background lane, preserves the last accepted panel on failure,
and participates in clean window shutdown. The adapter rejects any holdout date
before reading identity, usable clock, price, RSI, outcome or metric values; it
does not open the sealed holdout, call a provider/account, or mutate Data. The
focused service/Page/worker slice passes 17 tests, accepted Backtest engine
regressions pass 63 tests, and the 1600x900 offscreen panel has zero horizontal
scroll with all four summary cards visible.

The Data Status page now reads the retained 80-row Dataset Universe Health V2
artifact through a strict local adapter. It shows eleven columns, including the
exact typed blocker category and user-readable PIT meaning, plus
five read-only filters: Operational, Daily, Blocked, Research/Static, and All.
The 60m contract is visible as an `INTRADAY / READY_WITH_LIMITS` row with
contract-validated runtime coverage through 2026-08-19. Acceptance validation
renders 80 All rows and 62 Daily rows. The current typed projection contains
36 operational specifications and preserves all 17 blocked rows without
inference. The
acceptance screenshots are retained under `artifacts/gui_validation/` for all
four pages at both target viewports, plus the 1500x900 Blocked filter view.
Missing finality/date evidence remains `UNKNOWN`; GUI network, collector, and
mutation counts remain zero.

## Completed prerequisites

| Decision | Document | Current result |
|---|---|---|
| Retained physical Dashboard inputs | [Dashboard Data Map](DASHBOARD_DATA_MAP.md) | Existing typed artifacts, latest dates, refresh routes, and missing variables mapped |
| KB + LS + Toss source coverage | [Dashboard Provider Coverage](DASHBOARD_PROVIDER_COVERAGE.md) | Provider-native coverage, semantic limits, and fallback gaps are audited; no unconditional coverage |
| MVP daily source ownership | [Dashboard Daily Source Routing](DASHBOARD_DAILY_SOURCE_ROUTING.md) | Daily-final, provisional snapshot, and existing-history refresh routes are explicitly separated |
| KOSPI200 Option Wall prerequisite | [Data Status](../data/DATA_STATUS.md), [Dashboard Data Map](DASHBOARD_DATA_MAP.md) | KOSPI200 spot history and explicit same-date EOD T+1 Raw Wall joins are ready; Active Wall threshold remains unset |
| VIX and VKOSPI | [Dashboard Data Map](DASHBOARD_DATA_MAP.md) | Separate local daily series serve latest value, 1D change/change %, 20D/60D/250D percentile, source, market date, freshness, and PIT-limited status; no ratio/spread/composite |

The completed prerequisites are:

- [Dashboard Data Map](DASHBOARD_DATA_MAP.md)
- [Dashboard Provider Coverage](DASHBOARD_PROVIDER_COVERAGE.md)
- [Dashboard Daily Source Routing](DASHBOARD_DAILY_SOURCE_ROUTING.md)

They establish the available artifacts and source decisions used by the completed
GUI MVP. These GUI evidence documents do not themselves own an API call,
refresh, scheduler, collector, backfill, provider research, or canonical
promotion; the higher-priority standing Project/Data authority applies and may
establish the corresponding Data-owned operation.

## Implemented MVP pages

1. Dashboard
2. Index
3. Data Status shell
4. Backtest descriptive-result shell

The Dashboard source map describes physical artifacts. The Provider Coverage audit
describes the three-provider capability boundary. The Daily Source Routing document
owns the final MVP display/daily/history source choices. Current Data collection
gates and limitations remain owned by [Data Status](../data/DATA_STATUS.md); this
GUI status deliberately does not duplicate or relax that authority.

The GUI must distinguish retained Raw Call/Put Wall values from any future
Active Wall view. It must not invent an activation threshold, suppress a
source-valid extreme strike, or imply intraday/PIT safety from the EOD T+1
same-date join.

## Runtime and data boundary

- Presentation and local-query services do not directly own provider transport
  or persistent market-data promotion.
- GUI startup and rendering remain local/read-only by default. A GUI action or
  timer may enqueue an allowlisted asynchronous Data-owned read-only operation
  through a typed application-service boundary; secrets and provider parameters
  never enter presentation state.
- Presentation/local-query services must not themselves backfill, collect,
  rewrite, or promote Data. Data-owned services retain those responsibilities.
- A snapshot may be displayed only with its source, as-of, and provisional state
  where required; it must not be appended to canonical daily history.
- Provider values must not be averaged, merged, or silently substituted.
- Short-selling uses `LATEST OFFICIAL` and `LATEST PROVIDER` blocks, not a
  `LIVE TODAY` claim. The official block is KRX/pykrx (`KRX_ONLY` through
  2025-03-03 and `KRX_NXT_COMBINED` thereafter); the provider block is a
  separately labelled per-symbol `KRX_ONLY_EMPIRICALLY_CONFIRMED` EOD view.
  An `Additional venue inferred` value may appear only when their market dates
  match, and is never displayed as an official NXT observation.
- `CURRENT_SNAPSHOT` and `LATEST_FINAL_DAILY` are distinct GUI routes. An
  accepted historical daily artifact may supply its latest finalized
  observation for descriptive market-trend display, with source, market date,
  freshness, and retained PIT/status limits visible.

## Next GUI-domain action

UR-7C1A adds a separate provider-free, read-only `Research Workspace` over the
existing typed Korean-equity and U.S.-ETF local series routes. The workspace
composes the accepted chart, exact displayed OHLCV rows, instrument facts,
selected exact-identity watchlist, and source/freshness/status context without
recalculating or persisting market values. A stale, unavailable, mismatched, or
contract-invalid series clears both numeric panels. `Ctrl+K` selection preserves
the exact market and symbol and returns to the workspace through the existing
market-specific managed worker.

Workspace presentation preferences are a separate schema-versioned local-user
file under `artifacts/local_user/`. Named presets contain only the five
allowlisted panel identifiers, their complete order, visibility, bounded logical
size, and keyboard focus order. Strict validation rejects unknown, duplicate,
private-shaped, or value-bearing state; atomic primary/last-valid persistence
recovers to the last valid state or the safe default. Users can change panel
visibility, order, size, save/reload a named preset, and reset all workspace
preferences. No provider request, Data/cache write, account/community/order
surface, or live execution path was added.

UR-7D4F adds a provider-free `Ctrl+K` global symbol switcher over the existing
accepted Korean-equity and U.S.-ETF local identity catalogs. Search runs on the
existing managed local-read worker, merges only exact typed identities, shows
market/security type/currency, and requires explicit selection when results are
ambiguous. Selection routes to the existing market-specific chart page, whose
normal begin-series boundary clears prior state before its existing local read.
Each Korean-equity and U.S.-ETF chart page now keeps a visible right-side
`관심종목 · 최근 흐름` panel beside the chart. It renders exact saved identities
and uses the existing managed local watchlist read to show eligible latest
price/daily change, five-session return, return across the latest up-to-20
retained closes, and a compact min/max-normalized sparkline. Stale, unavailable,
or invalid series show no numeric or sparkline. List selection and
open/add/remove/reorder still delegate to the existing chart and atomic
watchlist paths. The panel adds no provider call, cache, popularity/recent-item
ranking, or persisted market value.

Dashboard V2 implementation, native visual composition, and the exact two-root
desktop read gate are accepted. The earlier Index reload task
`RQ-20260824T033915-781C` and Account chart task `RQ-20260824T034123-DDA1` are
Done and are not next actions. GUI is an active domain-parallel engineering
surface. Preserve typed fail-closed behavior and narrow read-only ACL scope.
Queue-backed work must be claimed, while direct user-assigned GUI work may
proceed without first creating a queue item. GUI work may evolve schemas through
their owning versioned contracts and may request or consume Data-owned provider
routes; it must not call providers from presentation code, mutate accounts,
silently aggregate incompatible FX, promote blocked values, or invent values.
The application entry point is `app.py`; local services live under
`src/stock_data/gui/`.

## Scope boundary

- A dashboard is a descriptive display. A `DASHBOARD_READY` entry is not a
  claim that the value is point-in-time safe for a backtest.
- `PIT_BLOCKED`, `RAW_ONLY`, `SEMANTIC_BLOCKED`, and
  `SOURCE_RESEARCH_REQUIRED` entries are not default MVP display inputs.
- Provider/source boundaries stay visible; GUI code must not merge, average, or
  silently substitute values.
- The Data Status shell may expose retained artifact state and request an
  allowlisted typed Data-owned read-only refresh; it must not construct or call
  provider transport directly or bypass the owning operation's contracts.
- Existing-credential read-only account refresh and local simulated paper
  workflows are allowed through typed services. Real or paper-broker order
  routing, account mutation, transfers, and live execution remain prohibited.

## UR-049 local watchlists and favorites

The GUI now has a separate `관심종목` page backed by versioned atomic local user
configuration under `artifacts/local_user/`. The saved identity is the exact
`market + symbol` tuple with its retained name, ISIN, listing date, and security
type; it is never joined to the fixed provider-validation watchlist or any Raw,
Normalized, Canonical, Processed, Indicator, or account snapshot layer.

The default `관심종목` list and additional named lists support create, rename,
remove, list/item reordering, and duplicate-free add/remove actions from both
search results and the current chart. Rows open the exact saved identity in the
main chart or an independent detached window. Eligible local series show price,
change, KST reference, and freshness; stale, missing, renamed, delisted, and
read-failure series keep the saved row while suppressing every numeric value and
showing the reason. Opening or editing a list performs no provider call.

Persistence uses a schema version and revision, fsynced temporary files,
atomic replacement, and a last-valid backup. A failed replacement leaves the
primary configuration unchanged; a malformed primary can recover from the
backup and be repaired on the next valid mutation without overwriting that
backup first.

## UR-042 Index Graph information layer

Index Graph now consumes a typed `IndexSeriesView` through the retained Health
row for `kr_index_daily` or `kr_kospi200_index_daily`. Only `CURRENT` and
accepted `EXPECTED_LAG` views whose last frame date equals the Health date can
show numbers. Stale, unknown, blocked, unreadable, invalid-identity, and
date-mismatched views clear the frame, latest marker, changes, period statistics,
and indicators together.

The compact information layer shows the readable KRX series name, exact identity,
period, latest accepted close/change/rate, selected-period high/low, point unit,
daily price basis, truthful KST session reference, and freshness. Provider,
dataset, expected date, and exact provenance stay out of the persistent header
and remain in `출처·기준 상세`. Its legend is generated from the same configured
price, MA, volume, RSI14, and 60-day-disparity definitions used to plot the
visible series. Crosshair detail reports only retained OHLC, change/rate, exact
share volume, and enabled/available indicators; leaving the plot restores the
latest accepted observation. An eligible view adds a labelled latest-value
marker, while unavailable views remain numeric-free.

## UR-052 continuous-line rendering

Price, MA5/20/60/120, RSI14, and 60-day-disparity curves now share one
presentation-only pyqtgraph path. Each curve uses local antialiasing and a
cosmetic round-cap/round-join pen, while preserving the exact source x/y
coordinates and leaving candles and volume bars on their existing crisp paths.
No smoothing, interpolation, resampling, rounding, fill, or indicator
recalculation is performed.

The connection mask joins adjacent valid accepted-session observations, keeps
known exchange closures connected, and breaks both sides of a NaN, unexpected
session, or accepted missing session. The UR-042 legend derives its readable
labels and colors from the same curve-style definitions. Focused geometry and
state tests plus native normal, maximized, detached, 125% DPR, and 150% DPR
checks verified exact coordinates, visible genuine gaps, sharp aligned
candles/volume/crosshair/marker, and clean worker/window shutdown.

## UR-053 in-chart RSI and disparity overlays

The `Overlay` state now renders RSI14 and 60-day disparity across the price
plot's exact accepted-session x positions while keeping each indicator in its
own read-only ViewBox and right-side scale. The price ViewBox alone owns the
price/candle Y range, so enabling either or both indicators cannot compress or
expand prices. RSI keeps its exact 0–100 scale with visible 30/70 guides.
Disparity is rendered as the exactly equivalent signed percentage-point
distance from 100, with a labelled `0=100%` axis and neutral guide; crosshair
detail continues to report the retained exact disparity percentage.

Overlay views are pixel-aligned and X-linked to price through zoom, pan,
resize, maximize, and detached-window copies. Every render clears old curves,
guides, and axes before applying the current Off/Overlay/Panel state, and NaN
or accepted-session gaps use the UR-052 connection mask. Native 1600x900,
maximized, detached index, and detached candlestick checks verified readable
dual-axis identity, reference guides, uncompressed candles, exact crosshair
values, and complete window/thread cleanup.

## UR-054 dedicated U.S. ETF chart universe

The GUI now registers a dedicated `미국 ETF` screen and service/work path for
the exact thirteen-fund seed catalog: `SOXL`, `TQQQ`, `QLD`, `KORU`, `TLT`,
`TLTW`, `QQQ`, `SPY`, `QQQI`, `QDVO`, `GPIQ`, `JEPQ`, and `JEPI`. Each saved
identity carries its exact ticker/name, issuer, exposure, USD currency,
inception date, leverage/daily-reset or income style, distribution style, and
official issuer product reference. In particular, `KORU` is the Direxion Daily
MSCI South Korea Bull 3X Shares ETF, not a KOSPI/KOSPI200 proxy, and `TLT`/
`TLTW` remain ETF market prices rather than Treasury yields.

This universe is not added to the primary market-index selector and remains
separate from the Korean individual-equity screen. Search, chart selection,
watchlist persistence/quotes, and detached windows route by exact identity to
the U.S. ETF service; spoofed or incomplete saved identities fail closed. The
chart accepts only provider-native USD OHLCV with a typed displayable Health
state, exact symbol/provider/currency identity, separately retained adjusted
close semantics, valid positive OHLC ranges, integer nonnegative volume,
date-symbol uniqueness, and no row before the fund's inception. Adjusted price,
total return, distributions, corporate-action markers, KRW conversion, and
Backtest remain unavailable.

Current retained production coverage is still `SOXX_ONLY`; SOXX is not one of
the thirteen seed funds. Therefore all thirteen are searchable but numeric-free
in production with the exact scope reason, and the current GUI service performs
no price-file read, provider lookup, substitution, collection, or promotion for
them. Data agents may onboard a contract-valid seed-fund source under standing
authority; numeric display remains suppressed until that retained route and
Health/freshness gates pass. The
current retained global-ETF Health row is also stale at 2026-08-18 versus the
2026-08-19 expected completed date, which independently prevents numeric
display. A synthetic authorized-scope fixture proves the future eligible path
still requires typed freshness and original provider-native USD semantics.

The owning GUI service/widget/watchlist slice passes `180` tests. Native
synthetic-only 1600x900 and detached 1200x760 captures verify the separate tab,
exact SPY identity, unclipped numeric-free state, unchanged KOSPI/KOSDAQ/
KOSPI200 primary selector, and zero remaining windows or QThreads after close.
Evidence:
`artifacts/gui_validation/ur054_us_etf_native_1600x900_20260820.png`,
`artifacts/gui_validation/ur054_us_etf_detached_1200x760_20260820.png`, and
`artifacts/agent_runs/ur054_us_etf_chart_acceptance_20260820.json`. No external
call, production Data/state mutation, scheduler change, or Backtest action
occurred.

## UR-057 session-aware 15-minute card sparklines

Top market cards no longer use their retained multi-day daily series as
miniature lines. A separate typed `DashboardSparklineView` now requires one
accepted local lane, provider-native 15-minute interval, exact completed-session
grid, completed bars, matching source timestamp/KST as-of, and displayable
freshness before a sparkline can appear. Daily values remain independently
labelled completed-daily headlines and are never represented as intraday.

The accepted production lanes currently make only Yahoo `^VIX` / `CBOE_VIX`
eligible among the top cards. Its 26 native bars are shown for the exact current
or last completed regular session, with `Yahoo15m`, KST as-of, session date,
delay, and a `not 24-hour` boundary. FRED VIXCLS remains the separate daily
Primary, and the CFE VX futures card remains a distinct numeric-free identity.
KOSPI, KOSDAQ, SOXX, Nasdaq Composite, S&P 500, NQ, Gold, and WTI have no
accepted production native-15-minute card lane, so their daily miniature lines
are removed and their asset-specific inactive-lane reasons are retained in
details. In particular, the unaccepted UR-030 XNYS lane is not activated and
the proposed 09:00 KST NQ/WTI visual-day anchor is not applied before native
session, maintenance, and roll evidence exists.

Focused tests cover current/last-session labelling, pre-open and weekend reuse,
U.S. DST, early-close grid rejection, missing/live-forming bar rejection,
independent asset failures, and no daily fallback or NaN bridging. Native
1600x900 and maximized checks show only the eligible VIX sparkline, preserve all
26 exact values, fit the two-line KST/session label, and close without workers.

## UR-058 completed-daily average comparisons

The ten top market cards now give visual priority to signed 5-day and 20-day
arithmetic-mean comparisons while keeping a short text status in the corner.
Price-like series use `latest / mean - 1` percentages. Official Treasury yields
and the contracted 10Y−2Y spread use absolute basis-point differences, never a
percentage-relative yield comparison. Exact dataset/source/as-of/freshness,
means, averaging frequency, and date coverage remain in tooltip/detail and Data
Status rather than persistent headline copy.

A typed `DashboardAverageComparisonView` accepts only the same displayable latest
daily metric and date-unique, finite, completed daily observations whose latest
date and value match that metric. It never fills, resamples, mixes an intraday
timestamp, silently deduplicates, or includes a partial/live observation. Five
and twenty actual observations are gated independently; duplicate dates,
stale/unknown/read-failed current state, partial rows, latest-value mismatch, and
insufficient history remain numeric-free for the affected comparison. These are
descriptive comparisons without signal language or direction-by-color encoding.

UR-057 remains independent: the VIX native-15-minute completed-session sparkline
and its exact KST/session label stay visible beside the completed-daily comparison
without feeding either mean. Focused service/widget validation passes 160 tests.
Native 1600x900 and maximized DPR 1.5 captures have zero Dashboard scroll,
unclipped comparison rows, the VIX sparkline intact, and zero running QThreads
after close. Evidence is retained in
`artifacts/agent_runs/ur058_average_comparisons_native_1600x900.png` and
`artifacts/agent_runs/ur058_average_comparisons_native_maximized.png`.

## UR-074 read-only Account composition

The Dashboard now has a compact `내 계좌` panel that opens the separate Account
page and shows only validated, identifier-free local snapshot projections. It
keeps total assets, evaluated securities, cash/buying power, unrealized P/L,
exact KST reference, freshness, holding count, and actual value history distinct;
unsupported, stale, partial, mixed-currency, or missing fields remain numeric-free.
An intentional `계좌 데이터 없음` state replaces the former blank space.

The full `계좌 관리` page provides currency-separated headline cards, a
reconciled allocation view (top five and at least 3% individually, remainder as
`기타` with exact tooltip breakdown), a holdings table, actual two-point-or-more
value history, and provider/account subtotals. Family-registered/user-funded
scope remains explicitly labelled as economic attribution rather than legal
ownership. The page is explicitly separate from the future real-estate,
jeonse, liability, and net-worth view.

RQ-C7D0 adds local-only API-less account management to that page. The user may
add, edit, or explicitly delete independent pension, ISA, and general accounts;
each record requires an exact basis date, KRW currency, a six-digit ticker,
positive quantity, and reconciled optional average-cost/purchase-total values.
The schema-versioned registry is stored atomically under `artifacts/local_user/`
and rejects account-number-shaped labels or position names. These records expose
dated purchase basis only: current price, market value, P/L, and total assets
remain null and the records are excluded from the user-fund aggregate.

The same page has an explicit `아빠 시트 CSV 가져오기` action for a user-exported
copy of the existing Google Sheets `아빠` tab. It strictly accepts the two
contracted ISA/general sections and their exact basis columns, ignores exported
current-price/current-total columns, replaces only those two imported source
IDs, and preserves independently entered accounts. There is no connector in the
desktop runtime, no automatic or daily Sheet read, no spreadsheet write, no
provider call, and no persisted spreadsheet identity. Invalid CSV or a corrupt
registry fails closed without overwriting the prior valid file; other configured
account sources remain independently readable. Focused store/parser/service
validation passes 256 tests, and the four Account selection, import/upsert/remove,
privacy, worker, and native-width regressions pass.

The privacy switch masks every monetary value and holding identity without
mutating retained snapshots, and suppresses allocation/history visuals. Focused
projection, freshness, mixed-currency, privacy, empty-state, worker, and GUI
validation passes 167 tests. Native synthetic-only 1600x900 and maximized
populated, masked, and empty captures verify the Dashboard path, full Account
composition, no horizontal overflow, and clean thread/window shutdown. No live
account/provider call or credential access is part of this GUI checkpoint.

## UR-072 separate local Net Worth composition

The application now registers a separate `순자산` tab beside, but never inside,
the brokerage `Account` page. It reads only the accepted UR-034 identifier-free,
exact-dated local history and never substitutes an earlier date, calls a provider,
or automatically reconciles brokerage balances. A valid typed view presents
liquid financial assets, total assets, total liabilities, net worth, and unused
credit limit as five distinct headline values.

The GUI service also exposes an immutable typed Net Worth timeline projection
over those existing history records. It deterministically selects the latest
revision for each exact snapshot date by aware UTC save time and digest
tie-break, then returns chronological `DISPLAYABLE` or explicit `GAP` points.
Only a complete displayable snapshot may expose net worth, and its change is
available only against the immediately previous complete date when both
currency semantics match. Partial, stale, inconsistent, invalid, and non-KRW
snapshots remain reason-coded numeric-free gaps; the projection never
interpolates, forward-fills, substitutes a prior value, or performs
cross-currency inference. It is read-only and changes no persisted schema,
history, provider, Data, or account state. Net Worth chart rendering remains
not yet implemented and is represented by Ready queue task FC62. Its completed
F2B9 dependency and Ready state do not reserve `main_window.py` or block
unrelated GUI work; a worker may claim FC62 under the normal queue rules.

Assets and liabilities stay on opposite sides of the balance sheet. In
particular, a paid jeonse deposit is an asset while a jeonse loan is a separate
liability; a drawn overdraft balance is debt while its unused limit is labelled
and excluded. Each class card retains the generic registered-holder role,
economic-owner role, valuation date, controlled method/source, status, and
uncertainty without exposing names, claim identifiers, addresses, account
numbers, paths, or free-form private fields.

Non-current entries suppress their own amounts and null only the totals affected
by the accepted backend contract. Corrupt or absent history renders an
intentional numeric-free empty state and never forwards a prior value. The local
privacy switch masks every monetary value without changing the snapshot. Exact
date removal is limited to trailing revisions of that selected date and deletes
newest-first, so interruption leaves a valid retryable audit chain; older dates
beneath newer history are rejected rather than rewritten.

The owning Net Worth service module passes 21 tests, including 5 focused
timeline tests for revision selection, ordering, valid deltas, explicit gaps,
currency mismatch, deterministic duplicates, empty history, and input
immutability. The prior broader backend, persistence, privacy,
Account/Dashboard regression, navigation, and Net Worth GUI evidence remains
accepted. Its native synthetic-only populated 1600x900, masked maximized, and
empty maximized captures verify readable Korean labels, no horizontal overflow,
Account separation, and zero remaining account workers or detached windows
after close. No external call, production personal snapshot, credential access,
or environment-file inspection occurred.

## UR-063 local Dashboard layout preferences

Dashboard presentation now has a separate versioned local preference file for
market-card and section visibility/order, pinned cards, compact/detail density,
default market/period/NQ selections, and logical window geometry. The 화면 구성
dialog supports drag controls and labelled keyboard reorder actions. Pinned cards
remain exact typed identities and move ahead of unpinned cards without changing
their data, freshness, or source. Global local-data status, the configuration
entry point, and Data Status remain reachable when cards or sections are hidden.

Writes atomically replace a strict schema and retain a last-valid copy. Missing
settings use the accepted 1600x900 default; schema v1 migrates to v2; invalid or
extra/private-shaped fields are rejected; corrupt primary settings recover from
the last valid copy or fail closed to the exact default. Reset changes only this
presentation file and the accepted geometry. Watchlists, credentials, account
values, provider payloads, market observations, and scheduler state are outside
the schema.

The focused persistence/migration/accessibility/failure and shared GUI slice
passes 92 tests. Synthetic-empty native checks passed customized 1600x900,
restart restore, maximized, exact reset, and corrupt-last-valid recovery, with
all windows/workers closed. Changing layout alone caused no chart read or
provider operation; no external call, production personal value, credential,
or environment-file access occurred.

## Offline release-readiness smoke

The supported `scripts/maintenance/run_release_readiness_smoke.py` command now
performs one bounded native 1600x900 startup/navigation/shutdown check over all
10 registered pages. It repeats the same local Dashboard/chart reads to detect
cache instability, verifies typed stale/unknown numeric suppression, checks
retained Health schema v2 and required data-root readability, and reads the 10
accepted `STOCK_DATA_*` Windows tasks without mutation. Each task must match its
exact action, arguments, working directory, trigger type/time/repetition,
`StartWhenAvailable`, `IgnoreNew`, and execution-limit policy; an invisible task
namespace is a release failure rather than a degraded success.

The report separates release failures, degradation, expected provider lag, and
approved deferred/blocked features. It records content identities rather than
payloads or account values. Exact user-owned local/account content and the wider
protected Data metadata manifest are compared before and after; update
preservation is exercised only with synthetic files in an isolated temporary
staging copy. The current release gate also requires outcome-complete retained
results for every latest due daily, KR-slot, Yahoo-30m, and Toss-30m task group.
Yahoo requires the exact ordered 17-route `(lane, series_id)` contract, one
terminal outcome per route, lane-specific accepted outcome codes, and reconciled
accepted/preserved/API counts. Eligible Toss evidence requires exactly the four
sanitized domestic route slots; ineligible evidence permits only the typed
`OPERATION` slot. Missing, duplicate, extra, wrong-lane, or duplicate-JSON-key
evidence fails closed. The gate
reconciles Health generation and scheduler readback after six governing
successful receipts; and requires cold GUI Health to render non-empty rows plus
all managed SLO counts within 30 seconds. Stale/unknown/manual/blocked rows
outside `automation_enabled` retain their reasons but no longer degrade a
release whose managed rows are all `CURRENT` or accepted `EXPECTED_LAG`.

The 2026-08-26 23:34 KST actual-user provider-free report is `DEGRADED` with
`release_blockers=[]`: 10/10 scheduler definitions match, all 8 latest due task
groups are outcome-complete, Health has 80 rows with 30/30 managed rows
acceptable and zero managed invalid rows, six governing receipts reconcile in
chronological order, and cold GUI Health rendered 80 rows in 7,786 ms against
the exact 30,000 ms bound. The sole degradation is the expected inventory of 23
unmanaged stale/unknown rows outside the managed SLO. Toss receipt
identity is additionally bound to the exact
`toss_domestic_ur246_occurrences/{scheduled_for UTC token}.json` path; a moved,
renamed, or wrong-token receipt fails closed. All 10 pages,
chart/cache, stale-value suppression, worker shutdown, user-data byte identity,
and isolated staging checks passed with provider calls, scheduler mutations, and
data mutations all zero. Evidence:
`artifacts/release_readiness/derivatives_daily_recovery_20260826.json`.

## RQ-2D6C Dashboard multi-year market chart

The Dashboard market selector and its persisted presentation preference now
share the exact ordered presets `60D`, `120D`, `1Y`, `3Y`, `5Y`, `10Y`, and
`MAX`. Finite local reads use exact final display budgets of 756, 1,260, and
2,520 retained sessions for 3Y, 5Y, and 10Y respectively, with only bounded
130-row indicator headroom. Every global index, ETF, and futures read applies
the exact selected `symbol` filter inside `LocalParquetQuery` before its row
budget is counted; it never tails a mixed-symbol result and filters afterward.
`MAX` reads the one contracted local dataset with that same exact read-time
filter; it does not scan outside the dataset root, call a provider, substitute
another identity, or write Data/history/state.

Each non-empty chart frame carries a typed `DashboardChartCoverage` describing
the requested preset/session count, exact retained row count and date span,
dataset, and series identity. Short retained history is visibly labelled
`보유 구간 일부` with both requested and available coverage instead of being
padded or called complete. `MAX` is labelled with its exact retained span and
row count. Existing Health/freshness and exact as-of gates remain authoritative;
the metadata is presentation-only and no forward fill, interpolation, gap
repair, or predictive interpretation is introduced.

Rendering is capped at 1,200 original observations. The deterministic display
selection always retains the first and last observations plus finite global
extrema for every plotted numeric field, and never aggregates or changes a
source value. The session mapping retains genuine accepted-calendar missing
sessions while treating intentionally omitted render points as thinning rather
than false source gaps. Hover, candles, indicator overlays/panels, and volume
therefore operate on one aligned bounded frame.

Focused period/preference/service/coverage tests pass 8/8, exact-symbol
multi-series and global-index/ETF/futures route regressions pass 10/10, the
full GUI service suite passes 182/182, existing Dashboard
chart/indicator regressions pass 8/8, and provider-free 1600x900 offscreen
3Y/5Y/10Y/MAX plus long-frame smoke passes 5/5 with visible controls and no
clipping. The earlier full owning preferences/service/widget run passed 410
tests with one skip and two pre-existing failures. The compact-sparkline mismatch
is now resolved by the accepted 14px contract and the current 5 focused / 72
Dashboard-regression / 1 offscreen-smoke checks; the accepted Backtest artifact
remains invalidated because the broad package digest includes unrelated
Backtest modules (first reproduced with `overnight_ml.py`; subsequent execution
and indicator modules do not change that root cause, tracked separately by
RQ-5467). A production-root provider-free proof
returned exact SP500 counts of 756/1,260/2,520 for 3Y/5Y/10Y and 24,777 for
MAX (1928-01-03 through 2026-08-24). No network/provider call occurred.
