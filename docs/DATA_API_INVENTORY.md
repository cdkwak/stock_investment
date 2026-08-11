# Public Data API Inventory

Scope: local official guides only. This document defines source roles and dataset mappings before implementation. It does not assert live behavior or historical coverage that the guides do not state.

## Common contract

- Provider: Public Data Portal (`data.go.kr`), Financial Services Commission APIs.
- Transport: REST `GET`; XML and JSON; `serviceKey` authentication.
- Pagination: list operations use `pageNo`, `numOfRows`, and return `totalCount`.
- Date filters: normally `basDt`, with some services also offering `beginBasDt`, `endBasDt`, and `likeBasDt`. KOFIA trust statistics use `basYm`.
- Refresh: all eight guides state once daily.
- Documented endpoint limit: 4,000-byte maximum message size, 500 ms average response time, and 30 TPS. A daily request quota is not stated; the portal approval page remains authoritative for the assigned quota.
- License: guide appendices describe Public Data Type 1–4 licenses but do not identify which type is assigned to each API. Commercial use, attribution, modification, and redistribution must therefore be confirmed on each current portal product page before operation.
- Coverage: service start/deployment dates and sample dates are not historical-coverage guarantees. Unless explicitly noted below, the guide does not state the earliest available observation.
- Collection rule: save one lossless landing response per operation/page, then fan out normalized datasets. Never call the same operation separately for each downstream dataset.

## API and dataset mapping

### 1. 금융위원회_주식시세정보

- Service: `getStockSecuritiesInfoService`
- Base URL: `https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService`
- Primary operation: `getStockPriceInfo`; endpoint `/getStockPriceInfo`
- Other operations: `getPreemptiveRightCertificatePriceInfo`, `getSecuritiesPriceInfo`, `getPreemptiveRightSecuritiesPriceInfo` (out of current equity scope).
- Request: common pagination/auth fields plus `basDt`/date ranges, `likeSrtnCd`, `isinCd`/`likeIsinCd`, `itmsNm`/`likeItmsNm`, `mrktCls`, and optional value-range filters.
- Response: `basDt`, `srtnCd`, `isinCd`, `itmsNm`, `mrktCtg`, `mkp`, `hipr`, `lopr`, `clpr`, `trqu`, `trPrc`, `lstgStCnt`, `mrktTotAmt`, `vs`, `fltRt`.
- Coverage: not stated. Service start is 2021-11-16; this is not the data start. Live probing separately established 2020 availability.
- Mapping/role: **Primary** for `kr_equity_price_daily` and `kr_equity_market_cap_daily` from one shared response. `vs` and `fltRt` remain source fields in landing unless a future contract needs them; do not mix them into price contract without approval.

### 2. 금융위원회_KRX상장종목정보

- Service: `GetKrxListedInfoService`
- Base URL: `https://apis.data.go.kr/1160100/service/GetKrxListedInfoService`
- Operation: `getItemInfo`; endpoint `/getItemInfo`
- Request: common fields plus `basDt`/date ranges, short code, ISIN, item name, corporate registration number, and corporation name filters.
- Response: `basDt`, `srtnCd`, `isinCd`, `mrktCtg`, `itmsNm`, `crno`, `corpNm`.
- Coverage: not stated. Service start is 2021-11-16; live probing separately established 2020 availability.
- Mapping/role: **Primary** for `kr_equity_universe_daily`. It supplies point-in-time identity/universe snapshots, not listing lifecycle dates. Stable identity fields may enrich `kr_equity_master`, but the full daily response must not be duplicated as master history.

### 3. 금융위원회_주식발행정보

- Service: `GetStocIssuInfoService_V3`
- Documented base URL: `http://apis.data.go.kr/1160100/GetStocIssuInfoService_V3` (guide marks SSL unavailable). HTTPS support must be verified without credentials before implementation; do not silently downgrade after an HTTPS failure.
- Operations:
  - `getItemBasiInfo_V3`: item identity/lifecycle; fields include `crno`, `isinCd`, `itmsShrtnCd`, company/name, security type, par value, `issuStckCnt`, `lstgDt`, `lstgAbolDt`, deposit registration/cancellation dates and issuance form.
  - `getStocIssuInfo_V3`: issuance events; adds issuance serial/date/round, reason code/name, issued count and listing date.
  - `getLockUpRetuInfo_V3`: lock-up registration/return events, market/listing classification, quantities and reason.
  - `getStocIssuStat_V3`: company-level common/preferred total issued counts (`onskTisuCnt`, `pfstTisuCnt`).
- Request: common fields plus `basDt`, `crno`, `stckIssuCmpyNm`.
- Coverage: not stated. Guide records service history from 2017-04-01 and V3 deployment on 2020-04-01, neither proving observation coverage.
- Mapping/role: `getItemBasiInfo_V3` is **Primary lifecycle source** for `kr_equity_master`; `getStocIssuInfo_V3` maps to optional `kr_equity_issuance_event`; `getLockUpRetuInfo_V3` maps to optional `kr_equity_lockup_return_event`; `getStocIssuStat_V3` maps to optional `kr_equity_issuance_status_daily`. Issued counts are not substitutes for listed shares without source reconciliation.

### 4. 금융위원회_파생상품시세정보

- Service: `GetDerivativeProductInfoService`
- Base URL: `https://apis.data.go.kr/1160100/service/GetDerivativeProductInfoService`
- Operations:
  - `getStockFuturesPriceInfo` (`/getStockFuturesPriceInfo`): futures `basDt`, product category, short code, ISIN, name, OHLC, spot/settlement price, volume, trading value, open interest.
  - `getOptionsPriceInfo` (`/getOptionsPriceInfo`): option identity plus OHLC, next-day base price, implied volatility, volume, trading value and open interest.
- Request: common date/identity/product filters; futures additionally supports value, volume and open-interest ranges; options supports implied-volatility ranges.
- Coverage: not stated. Service start is 2021-11-16.
- Mapping/role: **Primary candidate** for `kr_derivatives_futures_daily` and `kr_derivatives_options_daily`, pending live schema/coverage validation. PCR and basis are **derived** only: PCR from normalized option activity and basis from normalized futures plus the separately sourced underlying spot/index value. Do not store them in normalized source datasets.

### 5. 금융위원회_금융투자협회종합통계정보

- Service: `GetKofiaStatisticsInfoService`
- Base URL: `https://apis.data.go.kr/1160100/service/GetKofiaStatisticsInfoService`
- Operations and normalized mappings:

| Operation | Main source fields | Dataset / role |
|---|---|---|
| `getTrustScaleInfo` | `basYm`, sector, trust category/type, measure and value | optional `kr_trust_scale_monthly` / Primary candidate |
| `getFundTotalNetEssetInfo` | date, fund category, public/private method, net assets | optional `kr_fund_nav_aggregate_daily` / Primary candidate |
| `getCMAStatus` | date, management target, investor class, firms, accounts, balance | `kr_cma_balance_daily` / Primary candidate |
| `getGrantingOfCreditBalanceInfo` | KOSPI/KOSDAQ/total credit-financing and stock-lending balances, subscription and collateral loans | `kr_credit_balance_daily` / Primary candidate |
| `getSecuritiesMarketTotalCapitalInfo` | investor deposits, derivatives deposits, RP balance, unsettled receivables, forced-sale amount/rate | `kr_market_liquidity_daily` / Primary candidate |
| `getDLSAndDLBInfo` | month, DLS/DLB class, public/private, status, amount/count | `kr_structured_product_issuance_monthly` with `product_family=DLS_DLB` |
| `getELSAndELBInfo` | month, ELS/ELB class, public/private, status, amount/count | same dataset with `product_family=ELS_ELB`; separate API call but one shared schema |
| `getDerivationProductTradingInfo` | month, product/exchange/country/account/customer classifications, volume and USD value | optional `kr_overseas_derivatives_activity_monthly` / Primary candidate |

- Request: common pagination/auth and exact/range date filters; each operation adds its own classifications and amount filters. Trust statistics use `basYm`; structured products and overseas derivatives use month-like `basDt` samples.
- Coverage: generally not stated. Only the overseas-derivatives operation explicitly says data are provided from July 2014.
- Design: do not merge CMA, credit, market liquidity, fund NAV, or trust scale into one wide table. They have different grain and semantics. DLS/DLB and ELS/ELB may share one normalized schema distinguished by `product_family`.

### 6. 금융위원회_주식대차거래정보

- Service: `GetCMStckLnbInfoService`
- Base URL: `https://apis.data.go.kr/1160100/service/GetCMStckLnbInfoService`
- Operations:
  - `getStckLnbDetail`: date/market/symbol-level executed, repaid and balance shares plus balance amount.
  - `getStckLnbProgress`: market-total daily executed, repaid and balance shares/amount.
  - `getStckLnbInvpnDetail`: participant/detail-level lender and borrower executed amounts and ratios.
- Request: common date filters; detail supports market/name/symbol; participant operation supports participant classes.
- Coverage: not stated. Service start is 2023-11-15.
- Mapping/role: **Primary candidates** for `kr_stock_lending_daily`, `kr_stock_lending_market_daily`, and `kr_stock_lending_investor_daily`. These are lending records, not short-sale execution or short-balance datasets. The aggregate operation is a source aggregate, not a derived sum unless reconciliation proves equivalence.

### 7. 금융위원회_주식배당정보

- Service: `GetStocDiviInfoService_V2`
- Documented base URL: `http://apis.data.go.kr/1160100/GetStocDiviInfoService_V2` (guide says SSL unsupported); apply the same HTTPS-first verification rule as issuance.
- Operation: `getDiviInfo_V2`; endpoint `/getDiviInfo_V2`
- Request: common fields plus `basDt`, `crno`, `stckIssuCmpyNm`.
- Response: company/ISIN identity, dividend record date, cash payment date, stock delivery date, dividend reason, security type, ordinary/differential dividend amounts and rates, par value and fiscal month/day.
- Coverage: not stated. A sample contains a 1994 dividend record date, but a sample is not a coverage guarantee. Service start is 2020-04-01.
- Mapping/role: **Primary** for event-grain `kr_equity_dividend`. `basDt` is retained as the source snapshot date and `dvdnBasDt` as the event record date. Adjusted prices and total-return series must be derived later; this API must not overwrite source OHLC.

### 8. 금융위원회_주식권리일정정보

- Service: `GetStocRighScheService_V2`
- Documented base URL: `http://apis.data.go.kr/1160100/GetStocRighScheService_V2` (guide says SSL unsupported); apply HTTPS-first verification.
- Operation: `getRighExerReasSche_V2`; endpoint `/getRighExerReasSche_V2`
- Request: common fields plus `basDt`, KSD issuer customer number, and issuer name.
- Response: issuer/customer/corporate identity, issuance and exercise reason codes, exercise start/end dates, transfer-agent classification, par value, fiscal date and registry-closure start/end dates.
- Coverage: not stated. Service start is 2020-04-08; the sample date is 2019-12-31 and does not establish the first available date.
- Mapping/role: **Primary source-observation candidate** for `kr_equity_rights_schedule`; `basDt` is a source snapshot date, while exercise/registry dates describe the reported schedule. Version 2 keys immutable observations by Landing response-body SHA-256 plus item ordinal and retains page/snapshot provenance. It does not claim a canonical business-event identity or historical completeness. Corporate-action factors remain blocked until stable event identity and economic terms are independently validated.

## Shared-call and lineage rules

1. `getStockPriceInfo` is fetched once per page/date and fans out to price and market-cap normalized datasets.
2. `getItemInfo` is fetched once per page/date and becomes the daily universe; master enrichment references that landing record rather than recollecting it.
3. Each issuance operation has a distinct response grain and requires its own call, but shares one provider client, authentication, pagination, error classification and landing envelope.
4. Futures and options are separate source operations. PCR/basis are recalculated only after normalized partitions pass validation.
5. KOFIA operations are not combined merely because they share a service. Only the two structured-product operations share a normalized schema.
6. Lending detail, aggregate trend and participant detail remain separate because their primary keys and units differ.
7. Dividend records are source events. Rights rows are immutable source observations, not canonical economic events; adjustments and research features remain blocked pending verified terms and identity.
8. Backtests, market breadth, and ML must consume `kr_equity_canonical_universe_daily`, not the provider-specific `kr_equity_universe_daily`. Daily membership is the union of listed-info and price sources; master data enriches identity/lifecycle but never creates a daily member by itself.

## KRX Open API approval-gated contracts

The official `stk_bydd_trd` development specification identifies production
`https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd`, GET `basDd`, with
`AUTH_KEY` in the request header. The 2019-01-02 one-shot smoke returned HTTP
401, so KRX Open API remains approval-blocked. `ksq_bydd_trd`,
`stk_isu_base_info`, and `ksq_isu_base_info` must not be called until their
product approvals and official production specifications are confirmed.

`/svc/sample/apis/` is sample-only and is never a production source.

## Implementation priority

1. Reuse and harden the existing Public Data Portal client envelope: HTTPS, decoded-once key handling, pagination, `resultCode`, valid-empty classification, rate limiting and lossless landing.
2. Productionize the already validated shared `getStockPriceInfo` fan-out to `kr_equity_price_daily` and `kr_equity_market_cap_daily`.
3. Implement `getItemInfo` as `kr_equity_universe_daily`, then reconcile identity with `getItemBasiInfo_V3` for `kr_equity_master`.
4. Smoke-test HTTPS availability and historical coverage for the three HTTP-documented V2/V3 services before writing collectors.
5. Implement stock lending detail/aggregate/participant contracts and collectors.
6. Implement futures/options normalized contracts; add PCR and basis only afterward as derived datasets.
7. Implement dividend and rights event contracts, followed by adjustment-factor derivation.
8. Add only the KOFIA datasets needed by current research, starting with credit balance and market liquidity; defer trust, fund, structured-product and overseas-derivatives statistics until there is a consumer.
