# Yahoo source notes

## Source status and reference

This project uses an `UNOFFICIAL / EMPIRICAL` chart endpoint through its existing
adapter. Yahoo does not provide this project with a stable official developer
contract for that route.

- [Yahoo historical-data help](https://help.yahoo.com/kb/download-historical-data-yahoo-finance-sln2311.html)
- [Yahoo terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)

Empirical route used by the provider:
`https://query1.finance.yahoo.com/v8/finance/chart/<TICKER>`.

## Accepted uses

Yahoo supplies capture-first, locally persisted market-price observations:

| Scope | Project series | Meaning |
|---|---|---|
| Global indices | retained S&P 500, Nasdaq Composite, Nasdaq-100; registered-not-collected SOX and Dow Jones | Completed provider daily bars |
| ETF | retained SOXX; registered-not-collected EWY | Completed provider daily ETF bars |
| Continuous futures daily | retained `NQ=F`, `GC=F`, `CL=F`; registered-not-collected `ES=F`, `YM=F`, `DX=F` | Descriptive continuous-futures OHLC; not an individual contract |
| Finalized delayed 60m | `KRW=X`, `ZT=F`, `ZN=F`, `ZB=F` | Provider-finalized 60-minute price observations |

The application reads persisted Normalized data. It must not scrape the Yahoo
web page during GUI rendering.

## Non-negotiable semantics

- `NQ=F`, `GC=F`, `CL=F`, `ES=F`, `YM=F`, and `DX=F` are continuous provider series. They are not
  official settlements, exact expiries, basis series, or trusted OI series.
- `ZT=F`, `ZN=F`, and `ZB=F` are Treasury **futures prices**, never yields.
- `KRW=X` is a provider FX observation, separate from the official FRED H.10
  daily USD/KRW series.
- SOXX is not SOX. NQ is not replaced by NDX, and missing Yahoo values are not
  filled from KB or another provider.
- Daily rows whose complete price tuple is null are omitted as provider gaps and
  their dates are retained in `provider_gap_dates`; an all-gap response fails closed.
- A partially null ETF, index, or continuous-futures price bar always fails closed.
- Retained Yahoo price history is descriptive and remains PIT-blocked where the
  project lacks original availability/vintage evidence.

## Runtime route

- Provider: `src/stock_data/providers/yahoo.py`
- Daily contracts: `src/stock_data/contracts/global_market.py`
- 60m contract: `src/stock_data/contracts/market_60m.py`
- Active operation: [Global Current Refresh](../../operations/GLOBAL_CURRENT_REFRESH.md)
- Dashboard ownership: [Dashboard Daily Source Routing](../../../gui/DASHBOARD_DAILY_SOURCE_ROUTING.md)

## Safe read example

Do not reproduce browser cookies or copied Finance page URLs. Call the existing
provider with a small explicit interval/range and save only after schema and
timestamp validation. Start from `src/stock_data/providers/yahoo.py`.

Treat HTTP status, JSON shape, timezone, provider timestamps, missing bars, and
duplicate dates as unstable inputs. Browser-visible availability does not grant
redistribution rights or make the endpoint an official API.

All network work must use bounded calls, timeouts, immutable Landing captures,
contract validation, and atomic promotion. A completed-date replay should take
the API-0 path when the retained checkpoint already proves completion.

## Per-symbol option volume P/C research pilot

UR-073 adds an **unregistered, research-only, API-zero parser and projection**.
It does not add an HTTP client, runtime registry entry, scheduler, canonical
history, or Backtest input. The empirical Yahoo option route has no stable public
developer contract in this project, so a generic Finance webpage is not chain
evidence and Dashboard rendering never calls it.

The initial symbols are `SPY`, `QQQ`, `IWM`, `TLT`, `SOXX`, `SOXL`, and `TQQQ`.
`EWY`, `KORU`, and `QLD` are conditional on a retained populated chain plus
standard-contract and two-sided observed-volume validation. `DRAM` remains
price/volume comparison only and `SKHY` ADR comparison only until independently
verified standard listed options exist.

The semantic contract is:

- one result is one underlying only; symbols are never added, averaged, or
  labelled as a total U.S. market P/C;
- `volume_pcr = sum(REGULAR put volume) / sum(REGULAR call volume)` across an
  explicit retained expiry set from the same bounded capture scope;
- open-interest P/C is not computed by this pilot and cannot substitute for
  volume P/C;
- Yahoo `contractSize=REGULAR` is preserved as provider evidence but does not by
  itself prove a 100-share multiplier. Numeric display additionally requires an
  independent per-symbol multiplier-verification input;
- non-`REGULAR` contracts are excluded and counted. Duplicate contract symbols,
  duplicate expiry snapshots, cross-symbol input, missing standard-contract
  volume, or a zero call/put side fail closed;
- each result preserves capture UTC/KST, latest contract-trade UTC/KST when
  present, expiry count, exclusions, and a typed suppression reason. Yahoo does
  not expose a trustworthy chain-publication timestamp here, so contract last
  trade is labelled as such rather than treated as snapshot finality;
- the default display freshness ceiling is six hours from capture. Failed,
  empty, stale, or semantically incomplete observations remain hidden and cannot
  replace prior valid retained research data;
- retention rights, endpoint stability, rate limits, revision/finality behavior,
  standard multipliers, and live chain/liquidity evidence remain unresolved.

Code boundaries:

- contracts: `src/stock_data/contracts/yahoo_symbol_option_pcr.py`
- retained-payload parser and per-symbol derivation:
  `src/stock_data/providers/yahoo_symbol_options.py`
- Dashboard research projection: `stock_data.gui.us_option_pcr_adapter`

UR-094 subsequently selected one bounded 2026-09-18-expiry pilot. Its first
ordered SPY call at 2026-08-20 20:19 KST returned HTTP 401. The exact response
and secret-free request metadata were retained before inspection, the reviewed
global-stop rule prevented all later calls, and the immediate retained replay
matched with API 0. The final ledger is 1/7 initial calls and zero retries;
`QQQ`/`IWM`/`TLT`/`SOXX`/`SOXL`/`TQQQ`, every conditional symbol, `DRAM`, and
`SKHY` were not called. No populated-chain or liquidity claim is supported.

This HTTP 401 is evidence only that the exact unauthenticated unofficial route
tested by UR-094 was unauthorized. It is not evidence that Yahoo Finance lacks
option-chain functionality, and no other web, cookie, token, authenticated, or
alternate-host access method was inspected or attempted.

The completed runbook is
[Yahoo Symbol Option P/C Pilot](../../operations/YAHOO_SYMBOL_OPTION_PCR_PILOT.md).
The checkpoint evidence was 제거됨
(backup/repo-cleanup-phase2-20260903 브랜치에 보존).
The tested route and SPY call must not be repeated. Every real symbol remains numeric-free;
there is still no runtime registry, automation, canonical history, or Backtest
eligibility. A usable authorized Yahoo access method remains an unfilled feature
gap and requires a separate reviewed request before any investigation or call.
