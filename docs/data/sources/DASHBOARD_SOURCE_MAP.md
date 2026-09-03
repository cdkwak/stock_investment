# Dashboard source map

역할: 이 문서는 datasource 관점의 빠른 지도이며, 표시 선택 권위는 [Dashboard Daily Source Routing](../../gui/DASHBOARD_DAILY_SOURCE_ROUTING.md), 현재 상태 권위는 [Data Status](../DATA_STATUS.md)다.

Dashboard 숫자를 어디서 읽어야 하는지 빠르게 판단하기 위한 datasource
관점의 지도다. 현재 날짜와 operational 상태는 [Data Status](../DATA_STATUS.md),
실제 표시 정책은 [Dashboard Daily Source Routing](../../gui/DASHBOARD_DAILY_SOURCE_ROUTING.md),
GUI 상태는 [GUI Status](../../gui/GUI_STATUS.md)가 권위 문서다.

## Market overview

| Display metric | Primary persisted source | Provider | Required display semantics | Forbidden substitution |
|---|---|---|---|---|
| KOSPI latest tile | finalized Toss daily candle | Toss | provider and `as_of` visible | KB snapshot or KRX history silently replacing latest tile |
| KOSPI history / RSI14 / 60-day distance | `kr_index_daily`, `market=KOSPI` | KRX via pykrx | completed daily bars only | Toss/KB spliced into the KRX series |
| KOSDAQ latest tile | finalized Toss daily candle | Toss | provider and `as_of` visible | KB snapshot promoted as history |
| KOSDAQ history | `kr_index_daily`, `market=KOSDAQ` | KRX via pykrx | completed daily bars only | another provider fill |
| KOSPI200 spot | `kr_kospi200_index_daily` | KRX via pykrx | EOD T+1 meaning | futures `spot_value` substitute |
| Investor net purchase | `kr_market_investor_net_purchase_bridge_daily` | provider-preserving bridge | price date and flow date must match | stale flow joined to a newer price |
| SOXX | contracted global ETF daily dataset | Yahoo empirical route | SOXX label and provider date | SOX index substitute |
| Nasdaq-100 futures | contracted `NQ=F` continuous-futures daily series | Yahoo empirical route | continuous contract, interval, date, timezone | NDX cash index or exact-expiry claim |
| Gold / WTI | contracted continuous-futures daily series | Yahoo empirical route | descriptive continuous futures | spot/official settlement claim |
| VIX | `fred_vix_daily` | FRED | observation date and percentile window | intraday Cboe claim |
| VKOSPI | `kr_vkospi_daily` | KRX | finalized observation and date | VIX proxy |

RSI14 must use Wilder smoothing on the displayed contracted history. Threshold
labels are normally `<30 oversold`, `30-70 neutral`, and `>70 overbought`; the
number and threshold lines must use the same series and interval. A percentile
must state its lookback, such as 250 observations.

## FX and rates

| Display metric | Primary persisted source | Unit / meaning | Important limit |
|---|---|---|---|
| USD/KRW official daily | FRED `DEXKOUS`-based contracted dataset | provider-defined FX observation | observation is not guaranteed live intraday |
| U.S. 2Y | FRED `DGS2` | annual percent yield | Treasury futures price cannot substitute |
| U.S. 10Y | FRED `DGS10` | annual percent yield | same |
| U.S. 30Y | FRED `DGS30` | annual percent yield | same |
| 10Y-2Y | `us_treasury_spread_daily` | percentage points, same-date `DGS10-DGS2` | never combine providers or mismatched dates |
| delayed 60-minute FX/rates context | contracted Yahoo 60m dataset | provider FX or Treasury **futures price** | futures price is not a yield and not an official rate |

If the GUI promises 60-minute freshness, it must label the provider, price/rate
meaning, finalization rule, and source timestamp. FRED daily data must not be
relabeled as real time.

## Derivatives summary

| Display metric | Dataset / route | Display gate | Failure behavior |
|---|---|---|---|
| KOSPI200 futures basis | `kr_kospi200_futures_nearest_listed_daily.settlement_basis` | regular-session accepted row and verified source-native difference status | hide number and show update/permission reason |
| KOSPI200 option volume P/C | `kr_kospi200_option_pcr_daily.volume_pcr` | provider-preserving regular KOSPI200 option put volume divided by call volume | valid-empty is null, never zero; weekly options are outside the adopted modern category |
| KOSPI200 option OI P/C | `kr_kospi200_option_pcr_daily.open_interest_pcr` | provider-preserving put open interest divided by call open interest | distinct from volume, price, or premium P/C |
| Raw Call/Put Wall | accepted option bridge + same-date KOSPI200 spot | retained maturity, tie/status evidence, explicit EOD join | never label as gamma/active wall |
| Futures investor flow | reviewed official KRX screen 15007 manual CSV | exact product/session/source label | LS `t8462` cannot substitute |
| U.S. option P/C | unregistered contract-only ORATS normalized/derived/published schemas and offline E2E exist; no retained data or approved runtime provider | blocked pending subscription, entitlement, finality, root-scope validation, and five-session reconciliation | keep SPX/QQQ/NDX separate; never scrape Cboe/OCC pages or substitute Korean/Yahoo data |

On Windows, read/traverse permission is part of the display gate. The two derived
paths below may be numerically valid while the native user cannot read them:

- `data/derived/kr_kospi200_futures_nearest_listed_daily`
- `data/derived/kr_kospi200_option_pcr_daily`

The GUI must fail closed for only the affected metrics. Do not broaden ACLs,
copy values into another folder, or fall back to stale data. Follow the latest
GUI status for the native ACL result.

## Account area

Market-data sources are not account sources. Future account display must use a
separate read-only `AccountSnapshotView` contract carrying broker, account alias,
capture time, cash, holdings, valuation, and schema status. Never place account
payloads, account numbers, tokens, or real balances under this datasource guide.
