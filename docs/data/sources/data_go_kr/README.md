# data.go.kr financial APIs

## Status

- Project status: `ACTIVE` and `PILOT`, depending on dataset contract.
- Accepted scopes include stock prices/universe, derivatives, lending, liquidity,
  credit balance, dividends, and rights schedules.

## Official reference

- [Financial Services Commission stock price API](https://www.data.go.kr/data/15094808/openapi.do)
- [Financial Services Commission KOFIA statistics API](https://www.data.go.kr/data/15094809/openapi.do)
- [Public Data Portal](https://www.data.go.kr/)

The stock-price page describes business-day D+1 publication after 13:00 even
when portal metadata uses the phrase real time. Use each API page's own update
rule; do not infer one rule for every `1160100` service.

The KOFIA statistics page names the liquidity and credit-balance operations and
labels the product update cycle `실시간`, but does not publish an exact availability
clock or revision-freeze rule. Treat that label as discovery metadata, not daily
finality evidence. The liquidity/credit route therefore permits only the bounded
historical validation selected by the active runbook until repeated availability
and revision observations support a reviewed policy.

## Authentication

- Environment variable: `DATA_GO_KR_SERVICE_KEY`
- Common query keys: `serviceKey`, `pageNo`, `numOfRows`, `resultType=json`

## Safe read example

Prefer the existing client/collector. A raw request is allowed only after the
specific endpoint and filters are confirmed in its official API page:

```python
import os
import requests

from stock_data.providers.data_go_kr.stock_price import STOCK_PRICE_ENDPOINT

params = {
    "serviceKey": os.environ["DATA_GO_KR_SERVICE_KEY"],
    "pageNo": 1,
    "numOfRows": 100,
    "resultType": "json",
    "basDt": "20260818",
}
response = requests.get(STOCK_PRICE_ENDPOINT, params=params, timeout=(3.05, 10))
response.raise_for_status()
payload = response.json()
if not isinstance(payload, dict):
    raise ValueError("data.go.kr response is not an object")
```

Do not log `response.url`. Validate the service result code, item list, total
count, requested date, pagination, and valid-empty semantics before Landing.

## Project endpoint registry

The exact checked-in endpoints live in
`src/stock_data/providers/data_go_kr/data_v1.py` and include:

- `GetKofiaStatisticsInfoService`: liquidity and credit balance
- `GetDerivativeProductInfoService`: stock futures and options prices
- `GetCMStckLnbInfoService`: lending detail, progress, and participants
- `GetStocDiviInfoService_V2` and `GetStocRighScheService_V2`

Other routes:

- Stock prices: `src/stock_data/providers/data_go_kr/stock_price.py`
- Listed universe: `src/stock_data/providers/data_go_kr/universe.py`
- Example collector: `scripts/manual/collect/collect_data_go_kr_stock_issuance_snapshot.py`

## Boundaries

- Endpoint families have different fields, dates, pagination, publication, and empty-result rules.
- Do not guess filters or treat HTTP 200 as dataset success.
- data.go.kr observations retain their provider identity; they are not relabelled as direct KRX screen responses.
