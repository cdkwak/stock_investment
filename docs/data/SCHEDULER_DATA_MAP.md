# 스케줄러 데이터 연결표

상태: `현재 참조 문서 / 안정적 연결표`

기준 시각: `2026-09-03 KST`

이 문서는 Windows 작업 스케줄러의 Data 작업, 논리 스케줄러 레인,
87개 Dataset Universe, 그리고 Universe 밖의 운영 작업을 사람이 읽기 쉽게
연결한다. 다음 두 질문에 답하는 문서다.

- 어떤 작업이 어떤 데이터를 담당하는가?
- 자동화되지 않은 데이터는 왜 자동화되지 않았는가?

이 문서는 실행 시점의 상태나 데이터 사용 자격을 결정하지 않는다.

- [스케줄러 상태](../project/SCHEDULER_STATUS.md): 설치된 작업 정의, 주기 변경,
  현재 실패와 통합 결정을 관리한다. 실제 작업 스케줄러 조회 결과와 실행 영수증이
  현재 실행 상태의 최종 근거다.
- [데이터 상태](DATA_STATUS.md): 현재 Data 우선순위, 차단 사유와 다음 작업을 관리한다.
- [데이터셋 인덱스](DATASET_INDEX.md): 데이터셋 탐색을 담당하며, 스키마와 의미는
  각 계약이 결정한다.
- [GUI 최신화 상태 계약](../gui/GUI_REFRESH_STATUS_CONTRACT.md): 화면별 기준 시각,
  최신성, 마지막 정상 반영과 다음 반영 가능 시점을 관리한다.

작업이나 레인이 이 표에 있다는 이유만으로 최신성, 최종성, 예측 사용 가능 여부,
실제 데이터 갱신 성공을 추정하면 안 된다.

## 요약

| 구분 | 개수 | 의미 |
|---|---:|---|
| 전체 Dataset Universe | 87 | 논리적 보존·현재 데이터셋 기록 |
| 자동화 활성 | 43 | 22개 논리 레인에 연결됨. 미연결 0개 |
| 자동화 비활성 | 44 | 수동·연구·이벤트, 계약·의미 미확정, 갱신하지 않는 보존 자료 |
| 활성 Windows Data 작업 | 13 | 8개 작업이 43개 등록 데이터셋을 담당하고, 5개 작업은 Health·계좌·현재 화면용 운영을 담당 |
| 비활성 과거 작업 | 1 | KB IVSA0070 시장 스냅샷이며 KB 계좌 작업이 아님 |

Windows 작업 하나가 여러 레인을 담당할 수 있고, 레인 하나가 여러 데이터셋을
갱신할 수도 있다. 따라서 데이터셋마다 Windows 작업이 하나씩 필요하지는 않다.

## Windows Data 작업 연결표

| Windows 작업 | KST 실행 주기 | 고정 책임 |
|---|---|---|
| `STOCK_DATA_FRED_DAILY` | 매일 06:00 | `FRED_DAILY`; 자동화 활성 거시경제 데이터 4개 |
| `STOCK_DATA_GLOBAL_ETF_SOXX_DAILY` | 매일 06:10 | `GLOBAL_ETF_DAILY`; 등록된 8개 심볼의 자동화 활성 ETF 데이터 1개 |
| `STOCK_DATA_GLOBAL_INDEX_DAILY` | 매일 06:20 | `GLOBAL_INDEX_DAILY`; 자동화 활성 글로벌 지수 데이터 1개 |
| `STOCK_DATA_DAILY_HEALTH` | 매일 06:30 | 외부 API 호출 없이 87개 typed universe의 Health 산출물 생성·검증; 공급자 데이터 없음 |
| `STOCK_DATA_TOSS_ACCOUNT_DAILY` | 매일 07:00 | 식별자를 제거한 Toss 읽기 전용 계좌 스냅샷; 87개 시장 데이터 Universe 밖 |
| `STOCK_DATA_KBSEC_ACCOUNT_DAILY` | 매일 07:10 | 식별자를 제거한 KB `SSQM2952` 읽기 전용 계좌 스냅샷; 87개 시장 데이터 Universe 밖 |
| `STOCK_DATA_TOSS_DOMESTIC_30M` | 평일 09:00~15:00, 30분마다 | `000660`, `005930`, `KOSPI`, `KOSDAQ`의 화면용 현재 관측값; 정규화 이력·Canonical 이력 미기록 |
| `STOCK_DATA_KR_MARKET_DAILY_0910` | 매일 09:10 | `KR_INDEX_FUNDAMENTAL_DAILY`, `SHORT_SELLING_DAILY`, `LIQUIDITY_CREDIT_DAILY` 확인 관측 및 설명용 주식 밸류에이션 관측 |
| `STOCK_DATA_KR_MARKET_DAILY_1410` | 매일 14:10 | `CANONICAL_EQUITY_DAILY`, `SHORT_SELLING_DAILY`, `LENDING_DAILY` |
| `STOCK_DATA_BOK_TREASURY_DAILY` | 매일 17:10 | `BOK_TREASURY_OBSERVATION_DAILY`만 실행; 이 작업의 실행 파일은 provider scheduler가 아님 |
| `STOCK_DATA_KR_MARKET_DAILY_2030` | 매일 20:30 | Canonical 다음 `KR_EQUITY_PROVISIONAL_DAILY`, 이어서 `KR_ETF_PRICE_DAILY`; KOSPI200 폭·공매도 거래/잔고/투자자·대차·VKOSPI·국내지수·파생·시장 투자자·유동성/신용·LS t8462 Raw·Toss 국채 OHLC·`BOK_FX_DAILY` |
| `STOCK_DATA_GLOBAL_FUTURES_DAILY` | 매일 22:10 | `GLOBAL_COMMODITY_DAILY`; 자동화 활성 상품선물 데이터 1개 |
| `STOCK_DATA_YAHOO_MARKET_30M` | 매시 :02/:32 | 현재 화면용 17개 경로: 완료된 30분봉 13개와 공급자 원형 15분봉 4개; 정규화 이력·Backtest 미기록 |

`STOCK_PROJECT_ISSUE_STATE_SYNC`와 두 `STOCK_TELEGRAM_*` 작업은 Data 작업이
아니다. 비활성 `StockInvestmentRev1-KBSecDailySnapshot` 작업은 별도의
IVSA0070 시장 스냅샷이며 KB 계좌 자동화로 세면 안 된다.

## 자동화 활성 Dataset Universe: 43개

| 논리 레인 | 담당 작업·슬롯 | 개수 | 데이터셋 ID |
|---|---|---:|---|
| `FRED_DAILY` | `STOCK_DATA_FRED_DAILY` | 4 | `fred_treasury_yield_daily`, `fred_usd_fx_daily`, `fred_vix_daily`, `us_treasury_spread_daily` |
| `GLOBAL_ETF_DAILY` | `STOCK_DATA_GLOBAL_ETF_SOXX_DAILY` | 1 | `global_etf_price_daily` (`SOXX`, `EWY`, `SOXL`, `TQQQ`, `QLD`, `TLT`, `QQQ`, `SPY`) |
| `GLOBAL_INDEX_DAILY` | `STOCK_DATA_GLOBAL_INDEX_DAILY` | 1 | `global_index_price_daily` |
| `GLOBAL_COMMODITY_DAILY` | `STOCK_DATA_GLOBAL_FUTURES_DAILY` | 1 | `global_commodity_futures_daily` |
| `BOK_TREASURY_OBSERVATION_DAILY` | `STOCK_DATA_BOK_TREASURY_DAILY` 17:10 | 1 | `bok_ecos_kr_treasury_yield_source_observation` |
| `BOK_FX_DAILY` | 한국장 20:30 | 1 | `bok_ecos_usd_krw_daily`; 16:00 이후 당일 공급자 날짜를 대상으로 하며 화면·계좌평가 전용, 발표 시각/PIT 검증 전 Backtest 차단 |
| `KR_INDEX_FUNDAMENTAL_DAILY` | 한국장 09:10 | 1 | `kr_index_fundamental_daily` |
| `CANONICAL_EQUITY_DAILY` | 한국장 14:10·20:30 | 5 | `kr_equity_canonical_universe_daily`, `kr_equity_market_cap_daily`, `kr_equity_price_daily`, `kr_equity_universe_daily`, `kr_market_breadth_daily` |
| `KR_EQUITY_PROVISIONAL_DAILY` | 한국장 20:30, Canonical 레인 직후 | 1 | `kr_equity_price_provisional_daily`; KOSPI/KOSDAQ 시장별 1회, 총 2회 호출; 화면·조건 알림 전용, Backtest 차단 |
| `KR_ETF_PRICE_DAILY` | 한국장 20:30, 잠정 주식 가격 레인 다음 | 2 | `kr_etf_master`, `kr_etf_price_daily`; 로컬 KRX/ETF 관심종목과 보존 master의 합집합만 수집 |
| `SHORT_SELLING_DAILY` | 한국장 09:10·14:10·20:30 | 1 | `kr_short_selling_trading_daily` |
| `SHORT_SELLING_BALANCE_DAILY` | 한국장 20:30 | 1 | `kr_short_selling_balance_daily` |
| `SHORT_SELLING_INVESTOR_DAILY` | 한국장 20:30 | 1 | `kr_short_selling_investor_daily` |
| `LENDING_DAILY` | 한국장 14:10·20:30 | 3 | `kr_stock_lending_daily`, `kr_stock_lending_market_daily`, `kr_stock_lending_participant_daily` |
| `LIQUIDITY_CREDIT_DAILY` | 한국장 20:30 잠정·다음 09:10 확인 | 2 | `kr_market_liquidity_daily`, `kr_credit_balance_daily` |
| `KOSPI200_BREADTH_DAILY` | 한국장 20:30 | 3 | `kr_index_constituent_daily`, `kr_kospi200_constituent_price_daily`, `kr_kospi200_breadth_daily` |
| `KR_INDEX_DAILY` | 한국장 20:30 | 2 | `kr_index_daily`, `kr_kospi200_index_daily` |
| `DERIVATIVES_PRICE_DAILY` | 한국장 20:30 | 7 | `kr_kospi200_futures_daily`, `kr_kospi200_futures_nearest_listed_daily`, `kr_kospi200_futures_provider_bridge_daily`, `kr_kospi200_option_pcr_daily`, `kr_kospi200_option_walls_daily`, `kr_kospi200_options_daily`, `kr_kospi200_options_provider_bridge_daily` |
| `MARKET_INVESTOR_DAILY` | 한국장 20:30 | 2 | `kr_market_investor_trading_daily`, `kr_market_investor_net_purchase_bridge_daily` |
| `VKOSPI_DAILY` | 한국장 20:30 | 1 | `kr_vkospi_daily` |
| `LS_T8462_DAILY` | 한국장 20:30 | 1 | `ls_t8462_daily_raw`; Raw 전용, Normalized·예측 승격 없음 |
| `TOSS_KR_TREASURY_DAILY` | 한국장 20:30, T+1 | 1 | `kr_treasury_yield_daily`; 6개 만기 OHLC 원자적 갱신 |
| **합계** |  | **43** | 자동화 활성 데이터셋은 모두 연결됨 |

한국장 슬롯에서 일부 레인을 반복하는 것은 발표 시각, 실패 격리와 제한된 따라잡기를
위한 의도된 구성이다. 반복 실행이 중복 데이터셋을 만드는 것은 아니다.

## 43개 자동화 데이터셋 밖의 예약 작업

아래 작업도 예약 실행되지만, 자동화 활성 이력 데이터셋이 추가된 것으로 세면 안 된다.

| 운영 | 정확한 경계 |
|---|---|
| Yahoo 현재 30분 | `USD_KRW_60M`, `UST2_FUTURES_60M`, `UST10_FUTURES_60M`, `UST30_FUTURES_60M`, `KOSPI_CURRENT_60M`, `KOSDAQ_CURRENT_60M`, `SP500_CURRENT_60M`, `NASDAQ_CURRENT_60M`, `NQ_FUTURES_CURRENT_60M`, `SOXX_CURRENT_60M`, `GOLD_CURRENT_60M`, `WTI_CURRENT_60M`, `BITCOIN_CURRENT_60M` |
| Yahoo 원형 15분 | `^VIX`, `^FVX`, `^TNX`, `^TYX`; 현재 화면 전용이며 가짜 `:00/:30` 미국채 봉으로 리샘플링하지 않음 |
| Toss 국내 현재값 | 정확한 식별자 `000660`, `005930`, `KOSPI`, `KOSDAQ`; 현재 화면 전용 |
| Toss 계좌 | 식별자를 제거한 읽기 전용 계좌 스냅샷; 종목 탐색·주문·이체·브로커 변경 없음 |
| KB 계좌 | 식별자를 제거한 읽기 전용 `SSQM2952` 스냅샷; 7개 `kb_*_snapshot` 시장 데이터와 별개 |
| Daily Health | 외부 API 호출 없이 87개 typed universe를 투영·검증 |
| BOK 국채·환율 | 국채 관찰은 17:10 전용 작업, 환율은 20:30 한국장 묶음이다. 둘 다 43개에 포함되며 미검증 최종성을 주장하지 않음 |

보존된 `market_price_15m_observation`과 `market_price_60m_observation` 이력은
`STATIC_COMPLETE / NO_REFRESH` 상태다. Yahoo 작업은 별도의 현재 상태만 갱신한다.

## 자동화 비활성 Dataset Universe: 44개

자동화 비활성이 곧 스케줄러 누락을 뜻하지는 않는다. 현재 분류는 다음과 같다.

### 기존 묶음 편입 완료: 7개

| 데이터셋 ID | 현재 경계 | 실제 위치 |
|---|---|---|
| `kr_market_liquidity_daily`, `kr_credit_balance_daily` | 두 번 관찰해 동일하면 안정으로 표시하되 최종 불변을 주장하지 않음 | 기존 한국장 20:30 잠정 관찰과 다음 09:10 확인 |
| `kr_short_selling_balance_daily` | 공식 T+2 18:10 이후, 정정 가능, `AS_RETRIEVED`, 예측 차단 | 기존 한국장 20:30; 한 번에 최대 3개 연속 누락일 따라잡기 |
| `kr_short_selling_investor_daily` | 공식 당일 18:10 이후, `AS_RETRIEVED`, 예측 차단 | 기존 한국장 20:30; 한 번에 최대 3개 연속 누락일 따라잡기 |
| `ls_t8462_daily_raw` | 18개 범위 Raw만 보존, 공급자 최종성 미확정 | 기존 한국장 20:30; Normalized·Published 쓰기 0 |
| `bok_ecos_kr_treasury_yield_source_observation` | BOK 원천 발표 관찰이며 Canonical 금리 아님 | 기존 BOK 17:10 작업 유지; 한국장 묶음에 중복 추가하지 않음 |
| `kr_treasury_yield_daily` | Toss의 6개 만기 국채 OHLC, BOK 데이터와 의미가 다름 | 기존 한국장 20:30, T+1 대상까지 6회 호출·전 만기 원자적 갱신 |

### 수동·연구·이벤트·미구현 재분류

아래 표는 자동화 비활성 데이터의 상세 경계를 기록한다.
`kr_etf_master`와 `kr_etf_price_daily`는 검증된 첫 수집 뒤
`KR_ETF_PRICE_DAILY`로 이동했으므로 더 이상 이 비활성 목록에 포함하지 않는다.

| 데이터셋 ID | 판정 | 스케줄 편입 여부와 이유 |
|---|---|---|
| `kr_market_investor_trading_daily` | **즉시 자동화 완료** | 기존 `MARKET_INVESTOR_DAILY`가 이미 이 Toss 원천을 수집해 Published 브리지를 만든다. 20:30 레인의 명시적 자동화 데이터셋으로 등록 |
| `kr_equity_dividend` | 이벤트형 유지 | 배당 발생·공시 때 추가하는 이벤트 데이터다. 빈 날까지 매일 행을 만들지 않음 |
| `kr_equity_dividend_source_observation` | 이벤트형 유지 | 배당 원천 증거다. 이벤트 감지·공식 원천 계약이 확정될 때 이벤트 작업으로 편입 |
| `kr_equity_master` | 이벤트형 유지 | 종목 신규·변경·상장폐지 이벤트용이다. 일일 가격 레인과 혼합하지 않음 |
| `kr_equity_rights_schedule` | 이벤트형 유지 | 권리 일정이 있을 때만 갱신. 일정 의미와 발표 시각 계약 전에는 정기 API 작업 미생성 |
| `kr_equity_stock_issuance_source_observation` | 이벤트형 유지 | 발행 이벤트 원천 증거이며 Canonical 기업행위 승격 규칙이 먼저 필요 |
| `kb_derivatives_summary_snapshot` | 수동 스냅샷 유지 | KB 현재값 조각이다. 이력 일봉이나 계좌 스냅샷이 아니며 전체 조각 원자성·복구 계약이 없음 |
| `kb_domestic_index_snapshot` | 수동 스냅샷 유지 | 현재 국내지수 조각. 현행 30분 현재 관측과 역할 중복을 검토한 뒤 별도 Snapshot 작업으로만 가능 |
| `kb_global_symbol_snapshot` | 수동 스냅샷 유지 | 현재 해외 심볼 조각. Yahoo 현재 경로의 자동 대체재로 확정되지 않음 |
| `kb_investor_flow_snapshot` | 수동 스냅샷 유지 | 계좌가 아니라 IVSA0070 시장 스냅샷. 비활성 과거 작업은 유지하되 계좌 작업으로 오인하지 않음 |
| `kb_market_breadth_snapshot` | 수동 스냅샷 유지 | 시장 폭 현재 조각. Canonical 일별 폭과 단위·시점이 다름 |
| `kb_market_liquidity_snapshot` | 수동 스냅샷 유지 | 현재 유동성 조각. 일별 `kr_market_liquidity_daily`와 자동 병합 금지 |
| `kb_program_trading_snapshot` | 수동 스냅샷 유지 | 현재 프로그램매매 조각. LS 일별 후보와 키·시점이 다름 |
| `kr_credit_benchmark_yield_daily` | Raw 연구 유지 | 보존 Raw만 있고 채택 계약·현재 소비자가 없다. 국채·신용잔고와 다른 변수이므로 대신 연결하지 않음 |
| `kr_equity_foreign_ownership_daily` | Raw 연구 유지 | 전 종목 일별 비용, 공급자 최종성·PIT와 현재 소비 경계가 미확정 |
| `kr_equity_fundamental_daily` | Raw 연구 유지 | 전 종목 이력 후보다. 현재 화면용 지수/주식 밸류에이션 관찰과 별개라 중복 수집하지 않음 |
| `kr_etf_ohlcv_daily` | Raw 연구 유지 | ETF 생존편향 없는 유니버스와 최종성 계약이 먼저 필요 |
| `kr_etf_universe_daily` | Raw 연구 유지 | `kr_etf_ohlcv_daily`의 PIT 유니버스 선행 조건. 단독 자동화보다 두 계약을 함께 검증해야 함 |
| `kr_kospi200_futures_investor_net_purchase_daily` | 수동 공식 파일 유지 | 설정에 연결된 공식 KRX 파일의 날짜를 운영자가 확인하는 경로이며 자동 다운로드 계약이 없음 |
| `ls_t1633_program_trading_candidate` | Raw 연구 유지 | T+1 원자적 코드는 있으나 2026-08-26 재검증도 OAuth 후 동일 범위에서 HTTP 오류가 반복돼 승격·스케줄 편입하지 않음. 1회 한정 transient retry 증거는 보존 |
| `kr_equity_credit_trading_daily` | 미구현 연구 계약 유지 | 종목 단위 Toss 신용거래 후보. 일자 집계 `kr_credit_balance_daily`와 동등하지 않음 |
| `kr_equity_program_trading_daily` | 미구현 연구 계약 유지 | 종목 단위 Toss 후보. 시장 단위 LS t1633·KB 스냅샷과 대체 관계가 아님 |
| `kr_equity_securities_lending_daily` | 미구현 연구 계약 유지 | 현행 공식 대차 데이터와 필드 유사성은 있으나 공급자·키·생존편향 동등성 미확정 |
| `kr_equity_short_selling_daily` | 미구현 연구 계약 유지 | 현행 공식 공매도 거래 데이터와 범위가 유사해도 키·필드·공급자 동등성 미확정 |
| `kr_investor_flow_daily` | 미구현 연구 계약 유지 | 현재 브리지가 실제 사용하는 두 원천과 다른 미구현 계약이며 자동 별칭 금지 |

7개 KB 행은 시장 스냅샷이며 보유 종목, 잔고, 주문 또는 예약된 KB 계좌 스냅샷과
중복되지 않는다. 마지막 5개 역시 **완전 대체 확정이 아니므로 삭제하지 않는다.**

### 계약 또는 의미 경계 미확정: 8개

`kr_derivatives_futures_daily`, `kr_derivatives_options_daily`,
`kr_equity_sector_classification`, `kr_kosdaq150_futures_daily`,
`kr_kosdaq150_options_daily`,
`kr_kospi200_futures_investor_trading_daily`,
`kr_kospi200_options_investor_trading_daily`,
`ls_t8428_surrounding_funds_source_observation`.

이 행들은 계약, 식별자, 의미, 공급자 또는 승격 증거가 더 필요하다. 과거의
승인 대기 문구는 조사 자체를 막지 않지만, 근거가 없는 승격은 계속 차단한다.

### 자동 갱신하지 않는 보존 자료: 9개

| 구분 | 개수 | 데이터셋 ID | 보존 이유 |
|---|---:|---|---|
| 브리지의 과거 구간 입력 | 3 | `kr_market_investor_net_purchase_daily`, `krx_legacy_kospi200_futures_daily`, `krx_legacy_kospi200_options_daily` | 현재 브리지의 명시적 상류 입력이다. 새 날짜를 직접 수집하지 않지만 **삭제하면 안 된다.** |
| 현재 경로와 분리된 과거 장중 이력 | 2 | `market_price_15m_observation`, `market_price_60m_observation` | 보존 이력은 정적 완료 상태이며 현재 화면 수집은 별도 상태에 기록 |
| 완료된 과거 Raw 묶음 | 4 | `us_cftc_disaggregated_futures_only_raw`, `us_cftc_legacy_futures_only_raw`, `us_cftc_legacy_futures_options_combined_raw`, `us_cftc_tff_futures_only_raw` | 현재 계약상 과거 Raw 백필 완료·갱신 없음 |

이 9개는 데이터셋 수와 작업 수를 맞추기 위해 별도 스케줄러를 만들 대상이 아니다.
다만 앞의 과거 구간 입력 3개는 현재 Published 브리지 재구성에 필요하므로
“대체되어 삭제 가능한 자료”로 분류하지 않는다.

## 기존에 대체로 오해했던 8개 재판정

| 데이터셋 ID | 현재 판정 | 근거 |
|---|---|---|
| `kr_equity_credit_trading_daily` | 대체 아님 | Toss 계약은 종목 단위 신용거래이고, 현행 `kr_credit_balance_daily`는 일자 단위 집계라 행 단위가 다름 |
| `kr_equity_program_trading_daily` | 대체 아님 | Toss 계약은 종목 단위이고, `ls_t1633_program_trading_candidate`는 시장 단위 집계 후보라 동등하지 않음 |
| `kr_equity_securities_lending_daily` | 기능상 유사 경로가 있으나 완전 대체 미확인 | 현행 `kr_stock_lending_daily`와 핵심 수량이 겹치지만 공급자·키·필드 동등성 및 정식 별칭 근거가 없음 |
| `kr_equity_short_selling_daily` | 기능상 유사 경로가 있으나 완전 대체 미확인 | 현행 `kr_short_selling_trading_daily`와 범위가 유사하지만 공급자·키·필드 동등성 및 정식 별칭 근거가 없음 |
| `kr_investor_flow_daily` | 대체 아님 | 현재 투자자 브리지는 이 데이터셋이 아니라 `kr_market_investor_net_purchase_daily`와 `kr_market_investor_trading_daily`를 사용 |
| `kr_market_investor_net_purchase_daily` | 삭제 금지 과거 입력 | 2014-06-30까지의 과거 구간으로 현재 투자자 브리지의 명시적 상류 입력 |
| `krx_legacy_kospi200_futures_daily` | 삭제 금지 과거 입력 | 2019-12-30까지의 과거 구간으로 현재 선물 공급자 브리지의 명시적 상류 입력 |
| `krx_legacy_kospi200_options_daily` | 삭제 금지 과거 입력 | 2019-12-30까지의 과거 구간으로 현재 옵션 공급자 브리지의 명시적 상류 입력 |

재판정 합계는 **완전 대체 확정 0개 / 브리지 보존 입력 3개 / 기능상 유사하나
동등성 미확인 2개 / 대체 아님 3개**다. 브리지 연결은
`src/stock_data/orchestration/dataset_universe.py`의 `_UPSTREAM` 정의와
[`투자자 과거 구간 상태`](../../data/state/legacy_market_investor_import.json),
[`KOSPI200 파생 브리지 상태`](../../data/state/kospi200_derivatives_bridge_2010_present.json)를
근거로 한다.

## BOK USD/KRW 첫 bounded human run

구현 검증 중에는 실시간 네트워크 호출을 하지 않았다. 저장된 키를 출력하지 말고,
저장소 루트에서 아래 순서로 첫 백필, API 0 dry-run, 일일 레인 실행을 수행한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe .\scripts\manual\collect\refresh_bok_ecos_fx_daily.py --project-root . --start 2026-06-01 --end 2026-09-03 --confirm-live
.\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane BOK_FX_DAILY --dry-run
.\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane BOK_FX_DAILY
```

세부 출처·미검증 경계는
[`731Y001 USD/KRW 계약`](sources/bok_ecos/731Y001_USD_KRW_DAILY.md)이 소유한다.

## 유지관리 규칙

데이터셋의 `automation_enabled`, `scheduler_lane`, 담당 작업 또는 편입 예정 묶음이
바뀌면 이 표도 갱신한다. 다음 자료와 대조한다.

1. `src/stock_data/orchestration/dataset_universe.py`와 86행 typed universe
2. `src/stock_data/orchestration/daily_operations.py`와 공급자별 스케줄러 레인 정의
3. `scripts/register_data_operations_tasks.ps1`와 배포 준비 작업 정의 정책
4. 현재 Data Status와 Scheduler Status

실시간 `Ready`, `LastTaskResult`, 다음 실행 시각, 실행 영수증, 최신성 또는 장애 기록은
이 문서에 넣지 않는다. 그런 변동 정보는 Scheduler Status, Data Status, Health 또는
해당 운영 증거에 기록한다. 자격 증명이나 직접 계좌 식별자는 절대 기록하지 않는다.
