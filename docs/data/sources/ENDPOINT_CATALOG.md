# Endpoint catalog

이 표는 에이전트가 브라우저 검색 전에 확인하는 빠른 목록이다. 실제 실행
승인, 최신 상태, 데이터 계약을 대체하지 않는다. `Endpoint / operation`이
비어 있거나 `NONE`이면 이름을 추측해 호출하지 않는다.

## HTTP and file routes

| Source | Status | Endpoint / operation | Auth environment | Project implementation |
|---|---|---|---|---|
| FRED current CSV | ACTIVE | `https://fred.stlouisfed.org/graph/fredgraph.csv` | none | `src/stock_data/providers/fred.py` |
| FRED / ALFRED JSON | PILOT | `https://api.stlouisfed.org/fred/series/observations` | `FRED_API_KEY` | `scripts/manual/pilot/pilot_fred_alfred_*.py` |
| Yahoo chart | ACTIVE empirical | `https://query1.finance.yahoo.com/v8/finance/chart/<TICKER>` | none | `src/stock_data/providers/yahoo.py` |
| Toss OAuth | ACTIVE support | `POST /oauth2/token` | `TOSSINVEST_CLIENT_ID`, `TOSSINVEST_CLIENT_SECRET` | `src/stock_data/providers/tossinvest/client.py` |
| Toss market candles | ACTIVE selected | `GET /api/v1/market-indicators/<SYMBOL>/candles` | Toss OAuth | same client; symbol allowlist applies |
| Toss investor flow | ACTIVE selected | `GET /api/v1/market-indicators/<KOSPI_OR_KOSDAQ>/investor-trading` | Toss OAuth | same client |
| Toss stock observations | ACTIVE selected | `GET /api/v1/stocks/<6_DIGITS>/<PROGRAM_OR_SHORT_OR_CREDIT_OR_LENDING>` | Toss OAuth | same client; exact path allowlist applies |
| data.go.kr stock price | ACTIVE / PILOT | `/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo` | `DATA_GO_KR_SERVICE_KEY` | `src/stock_data/providers/data_go_kr/stock_price.py` |
| data.go.kr listed universe | ACTIVE / PILOT | `/1160100/service/GetKrxListedInfoService/getItemInfo` | same | `src/stock_data/providers/data_go_kr/universe.py` |
| data.go.kr derivatives | ACTIVE / PILOT | `GetDerivativeProductInfoService/getStockFuturesPriceInfo`, `getOptionsPriceInfo` | same | `src/stock_data/providers/data_go_kr/data_v1.py` |
| data.go.kr KOFIA statistics | ACTIVE / PILOT | liquidity and credit operations in `ENDPOINTS` | same | same registry |
| data.go.kr lending | ACTIVE / PILOT | detail, progress, and participant operations in `ENDPOINTS` | same | same registry |
| data.go.kr corporate actions | PILOT | dividend and rights V2 operations in `ENDPOINTS` | same | same registry |
| BOK ECOS | PILOT | `/api/StatisticSearch/<KEY>/json/kr/<START_ROW>/<END_ROW>/<TABLE>/<CYCLE>/<START>/<END>/<ITEM>/` | `BOK_ECOS_API_KEY` | `scripts/manual/pilot/pilot_bok_ecos_treasury.py` |
| OpenDART list | PILOT | `https://opendart.fss.or.kr/api/list.json` | `OPENDART_API_KEY` | `src/stock_data/providers/opendart_free_issue.py` |
| OpenDART free issue | PILOT | `fricDecsn.json`, `pifricDecsn.json` | same | same provider |
| CFTC disaggregated | RETAINED RAW | `https://www.cftc.gov/files/dea/history/fut_disagg_txt_<YEAR>.zip` | none | `src/stock_data/providers/cftc.py` |
| CFTC financial futures | RETAINED RAW | `https://www.cftc.gov/files/dea/history/fut_fin_txt_<YEAR>.zip` | none | same provider |
| FINRA daily short volume | PILOT | `https://cdn.finra.org/equity/regsho/daily/CNMSshvol<YYYYMMDD>.txt` | none | `scripts/manual/pilot/pilot_finra_short_data_landing.py` |
| FINRA short interest | PILOT | `https://api.finra.org/data/group/otcMarket/name/EquityShortInterest` | follow official FINRA access contract | same pilot/provider |
| KB Securities snapshot | ACTIVE snapshot | business operation `IVSA0070` | `KBSEC_BASE_URL`, `KBSEC_APP_KEY`, `KBSEC_APP_SECRET` | `src/stock_data/providers/kbsec/market_summary.py` |
| LS derivatives flow | RETAINED RAW | TR `t8462` | checked-in `LS_*` contract | `scripts/manual/collect/collect_ls_t8462_daily_raw.py` |
| LS program/market candidates | RETAINED / PILOT | TR `t1633`, `t8428` | same | matching scripts under `scripts/manual/` |
| KRX / pykrx | ACTIVE / RETAINED | library method or reviewed exact KRX screen | route-specific | `src/stock_data/providers/pykrx/`, `krx_mdc/`, `krx_open_api/` |
| FinanceData Marcap | RETAINED FILE | reviewed annual file; no runtime HTTP endpoint | none | `src/stock_data/providers/financedata_marcap/equity.py` |
| Cboe | BLOCKED candidate | `NONE` | not selected | no project adapter |
| ORATS | CONTRACT-ONLY / SUBSCRIPTION-REQUIRED | `NONE` | transport-free `src/stock_data/providers/orats_options.py` parses bounded delayed `cores` evidence for SPX/QQQ/NDX | no HTTP adapter or credential route; subscription, entitlement, finality, and root-scope pilot required |

## Request rules

1. 먼저 이 표의 project implementation과 공급자 README를 읽는다.
2. endpoint를 문자열로 새로 작성하기 전에 checked-in 상수나 client가 있는지 확인한다.
3. 인증값이 query/path에 들어가면 full URL과 `response.url`을 출력하지 않는다.
4. 1회 또는 작은 범위로 시작하고 timeout을 명시한다. 자동 retry는 추가하지 않는다.
5. HTTP 200만으로 성공 처리하지 않는다. provider result code, JSON/file 형식,
   requested scope, 날짜, pagination, 중복, 필수 필드, valid-empty를 검증한다.
6. 검증된 응답은 immutable Landing에 먼저 저장하고 atomic promotion 경로를 사용한다.

## What this catalog intentionally omits

- 주문, 정정, 취소, 이체, 출금 endpoint
- 계좌번호, 토큰, key, secret, Authorization header 예제
- 승인되지 않은 웹 화면 scraping code
- 비공식 endpoint의 안정성 또는 재배포 권리 보장
- Dashboard에서 사용할 수 있다는 자동 승인
