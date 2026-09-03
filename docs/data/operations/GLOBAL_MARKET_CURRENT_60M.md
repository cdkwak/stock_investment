# Unified Yahoo Market Current 30m Polling

Status: AUTOMATION_ACTIVE_UNIFIED_YAHOO_30M_20260822

This operation owns only the display projection for the exact Yahoo identities
`^KS11`, `^KQ11`, `KRW=X`, `ZT=F`, `ZN=F`, `ZB=F`, `^GSPC`, `^IXIC`, `NQ=F`, `SOXX`,
`GC=F`, `CL=F`, and `BTC-USD`, plus native-15m `^VIX`, `^FVX`, `^TNX`, and
`^TYX`. SPY is excluded. It never writes Normalized,
Canonical, Published, or Backtest data.

## Fixed boundary

- supported command: `scripts/maintenance/run_yahoo_market_current.py`
- serial request cap: seventeen GETs, one per exact identity
- timeout: provider adapter default 30 seconds
- current implementation retry/fallback: zero; agents may add finite transient
  retry/backoff or a verified identity-preserving fallback without fresh
  approval while retaining the 17 logical-identity cap per cycle
- Landing capture: `data/landing/yahoo_market_current/<run_id>/`
- projection: `data/state/current_observations/global60m_current/<series>.json`
- session trace: `data/state/current_observations/global60m_current/<series>.session.json`
- native-15m projection: `data/state/current_observations/yahoo_native15m_current/<series>.json`
- result log: `artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json`
- cadence when activated: every 30 minutes from minute 02
- global bar interval: provider-native completed `30m`
- VIX/Treasury bar interval: provider-native completed `15m`; no resampling
- installed task: `STOCK_DATA_YAHOO_MARKET_30M`, `Ready`, `PT30M`, first
  boundary `2026-08-22T15:32:00+09:00`
- power policy: allow start on battery, continue after switching to battery,
  and wake the computer for a due trigger
- missed-start policy: `StartWhenAvailable=true`; the current-only collector's
  session/completed-bar gates and atomic prior preservation remain authoritative
- current display gate: completed bar age at most 60 minutes while its market is
  active; a verified latest session close remains fixed while that market is closed
- Treasury identities are continuous-futures prices, never yields

Each successful identity is promoted independently with atomic readback and an
API-zero replay. A failed identity preserves its prior projection. Historical
overlap or revision conflicts are outside this operation and cannot block a
valid current projection.

## Active authorization

Standing authorization covers changing the global current bars from completed
60m to completed 30m, polling both Yahoo lanes every 30 minutes, and merging
their Windows tasks into one `STOCK_DATA_YAHOO_MARKET_30M` task. The single
process executes identities serially and preserves each prior valid projection
when another identity fails. The operation retains exactly
ten visible Dashboard rows: KOSPI (`^KS11`) and KOSDAQ (`^KQ11`) first, then
Nasdaq-100 continuous futures (`NQ=F`), Nasdaq
Composite (`^IXIC`), S&P 500 (`^GSPC`), SOXX (`SOXX`), Gold continuous futures
(`GC=F`), WTI continuous futures (`CL=F`), Bitcoin (`BTC-USD`), and USD/KRW
(`KRW=X`) last. The operation also retains the three approved non-card
Treasury-futures rows, for thirteen
exact identities total. SPY is not called. Historical and Backtest writes remain
zero. Before expansion verification, hash the existing
Normalized 60-minute dataset and Backtest roots. After the operation, verify
those hashes are unchanged and read back every independently accepted current
projection and the result log. The new task runs every 30 minutes at minute
02/32. Its installer registers `STOCK_DATA_YAHOO_MARKET_30M` first, then removes
`STOCK_DATA_GLOBAL_MARKET_60M`, `STOCK_DATA_GLOBAL_MARKET_15M_CBOE_VIX`, and
`STOCK_DATA_GLOBAL_MARKET_15M_TREASURY_QUOTE`.

The validation records below describe the superseded 60m implementation and
remain historical evidence only.

Cash index and ETF comparisons use the previous provider session close. NQ,
Gold, and WTI use the previous provider-labelled futures-session close, not an
official settlement. Bitcoin uses the previous provider UTC-day close. These
session bases remain explicit in Data Status.

Card badges do not expose these labels. Data Status owns them: KOSPI/KOSDAQ
use finalized KRX closes plus trace-only Yahoo bars; Nasdaq, S&P 500, and SOXX
use the completed U.S. cash session ending 16:00 ET; NQ/Gold/WTI are Yahoo
vendor-continuous futures whose weekend-close values are not official
settlements; Bitcoin remains a 24-hour source and still expires when its latest
completed bar exceeds the freshness limit.

KOSPI and KOSDAQ headlines remain owned by the finalized KRX daily close after
the Korean session ends. Their Yahoo routes are trace-only: they may draw the
completed 60-minute path from 09:00 KST, but they cannot replace the official
headline close. Every top-card trace is display-only, contains at least two
completed bars, and is cut to its reviewed cash, futures-provider, or UTC-day
session rather than falling back to multi-day daily history.

## Thirteen-route session-trace result

- result: `PASS`; run ID
  `global60m-current-20260821T171806Z-032c8a87d21e48618f5699d275697e82`
- calls: 13/13 serial; retry/fallback zero; history writes zero
- all terminal outcomes:
  `CURRENT_PROJECTION_ACCEPTED / COMPLETED_BAR_ATOMIC_READBACK`
- KOSPI/KOSDAQ traces contain seven points and end at the actual 15:30 KST
  close; the final source bar is not extended to 16:00
- other visible trace counts: NQ 19, Nasdaq 3, S&P 500 3, SOXX 3, Gold 19,
  WTI 19, Bitcoin 17
- finalized KRX headlines: KOSPI `6912.95`, `+60.37`, `+0.88%`; KOSDAQ
  `801.94`, `-38.95`, `-4.63%`
- Normalized 60m, `src/market_backtest`, and Published Backtest pre/post hashes
  were unchanged
- inspected 1600x900 offscreen Dashboard: nine requested cards in one row,
  nine visible session traces, zero scrollbars, zero QThreads before/after close
- evidence: 제거됨 (backup/repo-cleanup-phase2-20260903 브랜치에 보존)

The later session-aware verification run
`global60m-current-20260821T221832Z-ae754c1454cd46baa78dd779ed30d739`
also passed 13/13 with retry/fallback and history writes zero. Scheduler XML
readback confirms `PT30M`, start boundary `2026-08-22T07:32:00+09:00`, and the
exact `--current-only` action.

## 2026-08-22 FX 08:00 session verification

- external-network run
  `global60m-current-20260822T001402Z-a79491d25e3541f4ad7e552e4a7297a8`
  accepted all 13 routes; retry/fallback and history writes were zero
- `KRW=X` accepted `1385.97998046875 KRW per USD` at provider time
  07:00 KST, with previous-provider-session change `-0.1199951171875`
  (`-0.008657%`)
- the immutable USD/KRW Landing body was SHA-256 verified as
  `e63924bc583264c8416ebea8c9614258bd973aee3a1800f83556a347f14f522b`
  and replayed with external API calls zero to build the corrected 08:00 KST
  session trace (23 completed points)
- Friday's final FX bar may remain visible during the reviewed weekend closure;
  it is a fixed provider-session close, not a live tick or official H.10 value
- pre/post hashes were unchanged: Normalized 60m
  `d02e23779d2a1214030abf3f39067d4641b5e136168057d0c51fb729f1c11245`,
  `src/market_backtest`
  `6a400463e3e7aac8bff8256a2f55a365fe544d4a8a09a83066c9a8161e0fec0f`,
  and empty Published Backtest
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

## Prior four-route validated result

- one-shot result: `PASS`
- run ID: `global60m-current-20260821T161254Z-589753f328804883ab57ce8e2d8e32ef`
- provider calls: 4/4; retry/fallback: 0
- accepted values at completed-bar timestamp `2026-08-22 01:00 KST`:
  - `KRW=X`: `1385.3900146484375 KRW per USD`
  - `ZT=F`: `102.94921875 provider native continuous futures price`
  - `ZN=F`: `108.28125 provider native continuous futures price`
  - `ZB=F`: `108.96875 provider native continuous futures price`
- Normalized 60m aggregate SHA-256 remained
  `B29CED2734179C85BB49429D0AF26ED6BAF6D6EF741C034DD40260D9AB5CDF1F`
  across four files.
- Backtest aggregate SHA-256 remained
  `05BE32EE97A2B0E0C75080C829B0FE93CACEB82AE5C82C36BCB518738A349465`
  across 41 files.
- local GUI readback exposed all four as `CURRENT_COMPLETED_60M`.
- `STOCK_DATA_GLOBAL_MARKET_60M` is `Ready`, runs hourly at minute 12,
  and its registered arguments include `--current-only`.

## Nine-route expansion result

- result: `PASS`; run ID
  `global60m-current-20260821T162837Z-399223ffedea4e6b8914d20a07807005`
- calls: 9/9 serial; retry/fallback zero; history writes zero
- new exact completed-bar projections:
  - S&P 500 index `^GSPC`: `7677.97021484375 index points`, 00:30 KST
  - SPY ETF `SPY`: `765.9550170898438 USD per share`, 00:30 KST
  - Nasdaq Composite `^IXIC`: `26173.673828125 index points`, 00:30 KST
  - Nasdaq-100 continuous futures `NQ=F`: `29443.0 index points`, 01:00 KST
  - SOXX semiconductor ETF `SOXX`: `517.9000244140625 USD per share`, 00:30 KST
- all timestamps above are 2026-08-22 KST and passed the shared 60-minute gate
- the four pre-existing routes also returned
  `CURRENT_PROJECTION_ACCEPTED / COMPLETED_BAR_ATOMIC_READBACK`
- Normalized 60m manifest hash remained
  `3A00F4BB05D36F57010D56712398C740643A1E8DA0030CE5EF965A96ED404E52`
  across four files
- `src/market_backtest` manifest hash remained
  `16E7C31410A5F42BF9B3CE8188E6FF748244CFB63FA59EE8A05F204409E443E3`
  across sixteen files; the empty `data/published/backtest` root hash also remained
  `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`
- local Dashboard readback exposes the five new rows independently with a
  `60분 완료` badge; offscreen 1600x900 inspection has zero scrollbars
- Scheduler readback remains `Ready`, `PT1H`, with the exact `--current-only`
  action; the unchanged entrypoint now owns the nine-route allowlist

## Eleven-route 30-minute result

- first sandboxed process: 11 typed transport failures; no accepted body and no
  history write. The required external-network execution was then run once.
- external-network result: `PASS`; run ID
  `global60m-current-20260821T165228Z-346e357ce9ae4a9bae81e49fbd116167`
- calls: 11/11 serial; retry/fallback zero; history/Backtest writes zero
- seven visible projections at readback:
  - `NQ=F` 29443.0, source 01:00 KST, change +82.75 (+0.2818%)
  - `^IXIC` 26204.564453125, source 01:30 KST, change +137.398453125 (+0.5271%)
  - `^GSPC` 7683.41015625, source 01:30 KST, change +42.25015625 (+0.5529%)
  - `SOXX` 517.8350219726562, source 01:30 KST, change -3.66497802734375 (-0.7028%)
  - `GC=F` 4672.0, source 01:00 KST, change +81.10009765625 (+1.7665%)
  - `CL=F` 86.80999755859375, source 01:00 KST, change +0.34999847412109375 (+0.4048%)
  - `BTC-USD` 77178.828125, source 01:00 KST, change +4162.8984375 (+5.7014%)
- pre/post manifests were unchanged: four Normalized 60m files
  `536497dc...38b02`, sixteen `src/market_backtest` files `604e0236...2e62b`,
  and empty Published Backtest `e3b0c442...b855`
- Task Scheduler readback: state `Ready`, interval `PT30M`, exact
  `--current-only` action, next run 02:12 KST. The retained LastTaskResult=1
  predates the next registered run and is not represented as a successful task
  result; the manual bounded validation above is the accepted operation result.
