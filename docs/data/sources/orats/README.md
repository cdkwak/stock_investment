# ORATS option data candidate

## Status

- Project status: `CONTRACT_ONLY_NO_ENTITLEMENT / SUBSCRIPTION_REQUIRED`.
- Data class: licensed delayed options API.
- Proposed use: read-only SPX, QQQ, and NDX option volume and open-interest
  put/call ratios for the Dashboard.
- Strict offline Dataset Contracts, a transport-free parser, Derived ratio
  calculation, and a fail-closed Published projection exist. They are not in
  the runtime Contract registry and create no collection authority.
- No subscription, credential, HTTP adapter, collector, state, Landing, or
  retained dataset currently exists.

The smallest evaluated route is the ORATS Delayed Data API. Its `cores`
response defines total call/put volume and open interest per requested
underlying, allowing the project to calculate volume P/C and OI P/C without
downloading every strike. This is a provider-scoped aggregate, not the OCC or
Cboe consolidated market-total series.

## Official reference

- [ORATS Data API plans and coverage](https://orats.com/data-api)
- [Delayed Data API and cores endpoint](https://orats.com/docs/delayed-data-api)
- [ORATS field definitions](https://orats.com/docs/definitions)
- [ORATS terms and conditions](https://orats.com/terms-conditions)

The evaluated personal delayed plan was listed at USD 99/month with 20,000
requests/month, approximately 15-minute delayed current data, and EOD history
from 2007. Recheck the current commercial terms before purchase.

## Authentication

The future adapter must load its token from an environment variable whose name
is defined with the approved implementation. Do not place credentials, request
headers, account details, or sample balances in this guide, tests, artifacts,
or GUI messages.

## Implemented offline route

- Contract-only schemas: `src/stock_data/contracts/us_option_pcr.py`
- Strict decoded-payload parser: `src/stock_data/providers/orats_options.py`
- Provider-neutral ratios: `src/stock_data/derived/us_option_pcr.py`
- Dashboard projection gates: `src/stock_data/published/us_option_pcr.py`

The parser requires exactly one same-date row for each of SPX, QQQ, and NDX,
explicit capture time and Landing hash, non-negative integer counts, and no
extra ticker. Derived volume and OI ratios keep each underlying separate and
return null for a zero denominator. Published ratios remain null until
entitlement, volume finality, and provider root scope are all explicitly
confirmed. Historical PIT remains blocked even after descriptive display is
allowed.

## Proposed bounded pilot

After subscription and an explicit one-call pilot approval, request only
`SPX,QQQ,NDX` and the fields required to validate:

- provider ticker and trade date;
- call and put volume;
- call and put open interest;
- provider update timestamp.

Before any persistence, verify HTTP and JSON success, all three requested
tickers, non-negative integer totals, unique `trade_date + underlying`, and the
provider/root meaning. Recalculate each ratio locally; a zero denominator
produces null, never zero. Keep SPX, QQQ, and NDX separate.

## Required entry gate

1. Approve the recurring subscription cost.
2. Obtain written confirmation or a one-call pilot showing that the delayed
   personal plan returns SPX, QQQ, and NDX through `cores`.
3. Confirm whether SPX includes SPXW and other weekly roots, and record display,
   retention, historical, and internal-use rights.
4. Review the existing contract-only schemas and approve a Landing-first HTTP
   adapter; keep the drafts outside the runtime registry until that gate passes.
5. Run at least five business days of provider-total reconciliation before the
   Dashboard may display a number.

Until these gates pass, `US_OPTION_PCR` remains numeric-free and explicitly
unlinked. Do not label a provider-universe aggregate as `미국 전체 P/C`, do not
substitute Korean option ratios, and do not invent a `가격 PCR`.
