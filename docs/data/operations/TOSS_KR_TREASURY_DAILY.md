# Toss 한국 국채 수익률 일별

상태: `LIVE_VALIDATED / KR_BUNDLE_V5_SCHEDULED / AS_RETRIEVED`

이 실행은 Toss Open API의 한국 국채 2·3·5·10·20·30년물 일봉 OHLC를
`kr_treasury_yield_daily`에 갱신한다. Toss 공급자 관측값을 그대로 보존하며
BOK 공식 국채 수익률, KOFIA 최종 고시값 또는 Yahoo 국채 지수로 재분류하지
않는다.

## 실행 경계

- 담당 레인: `TOSS_KR_TREASURY_DAILY`
- 담당 작업: `STOCK_DATA_KR_MARKET_DAILY_2030`, 한국장 묶음 계약 v5
- 대상일: 실행 시점에 완료된 직전 XKRX 거래일, 즉 운영상 T+1
- 공급자 호출: 미완료 대상에서 정확히 6회, 만기별 1회
- Landing: `data/landing/tossinvest/<operation>/<instrument>/`
- Normalized: `data/normalized/kr_treasury_yield_daily/`
- 상태: `data/state/toss_kr_treasury_daily_incremental.json`

단일 레인 확인은 다음 진입점을 사용한다.

```powershell
.\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py `
  --project-root . `
  --lane TOSS_KR_TREASURY_DAILY
```

공급자 호출 없이 계획과 현재 완료 상태만 확인하려면 `--dry-run`을 추가한다.

## 원자성·검증

1. 여섯 응답을 각각 변경하지 않은 Landing 봉투로 먼저 저장한다.
2. 각 만기 응답에 정확한 대상일이 있어야 한다.
3. 여섯 만기가 제공하는 미수집 날짜 집합이 모두 같아야 한다.
4. 계약과 기본키 검증을 통과한 뒤 여섯 만기를 한 번에 원자적으로 승격한다.
5. 어느 한 만기라도 실패·누락·스키마 오류이면 기존 Normalized와 상태를
   보존하고 전체 승격을 중단한다.
6. 이미 여섯 만기가 모두 완료된 대상일은 토큰·시장 호출 0회로 끝난다.

첫 운영 검증은 공급자 호출 6회로 여섯 만기를 동일한 T+1 대상일까지
갱신했고, 즉시 재실행은 API 0으로 끝났다. 이 경로는 일별 OHLC 운영
데이터에는 사용할 수 있지만 BOK 발표 최종성의 증거나 서로 다른 공급자의
자동 대체 규칙으로 사용하면 안 된다.
