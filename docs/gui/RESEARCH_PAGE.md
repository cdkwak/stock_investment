# 검증(Research) 페이지

## 목적과 경계

`/research`는 규칙 후보의 적합·홀드아웃·사이클·포워드 성과를 표시하고,
접힌 `규칙 실험` 카드에서 사용자가 retained Parquet만으로 규칙을 즉시
평가하게 한다. 실험 자체는 읽기 전용 계산이다. 사용자가 명시적으로 누른
후보 등록만 loopback PC에서 후보 설정과 leaderboard artifact를 쓴다. 데이터
수집, 외부 API 호출 및 주문 기능은 없다.

| 입력 | 화면 사용 |
|---|---|
| `artifacts/research/rule_leaderboard/latest.json` | 평가 메타데이터, 후보 순위, 상세, 현재 상태 |
| `config/research/rule_candidates.json` | 변경 기록 |
| `data/local/research/forward_test/signals.jsonl` | 규칙 버전별 포워드 신호 |
| `data/normalized/kr_index_daily` | `KR`의 `KOSPI200` 실현 수익률 |
| `data/normalized/global_index_price_daily` | `US_TECH`의 `NASDAQ100`, `SEMIS`의 `SOX` 실현 수익률 |

평가 결과나 신호 파일이 없거나 읽을 수 없으면 HTTP 오류나 대체 숫자 대신
`아직 평가 결과가 없습니다 · scripts/research/run_rule_leaderboard.py 실행 후 표시`
빈 상태를 반환한다.

## API

- `GET /api/research`: leaderboard artifact와 후보 변경 기록. 두 입력 파일의
  수정 시각과 크기가 같으면 메모리 캐시를 재사용한다.
- `GET /api/research/forward`: 신호와 정규화 종가로 계산한 20/60/90거래일
  실현 수익률. 신호 및 관련 Parquet 파일 시그니처가 바뀌면 다시 계산한다.
- `GET /api/research/experiment`: `side`, `basket`, `type`, 반복 `ind`,
  `levels`, `target_vol`, `horizon`을 검증해 단일 정의의 적합·홀드아웃·사이클·
  레벨·현재/유사 구간 결과를 반환한다. `ind`는
  `drawdown252:<=:-0.2` 형식이며 측에 따라 `<=` 또는 `>=`만 허용한다.
  클라이언트별 분당 10회 제한과 프로세스 세션 실험 횟수를 적용한다.
- `POST /api/research/candidates`: 직접 loopback 접속에서만 이름·이유와 마지막
  실험 정의를 `experimental` 후보로 원자적으로 추가한다. history와
  `attempt_count`를 함께 늘리고 leaderboard를 프로세스 안에서 다시 만든다.
  60초 안에 끝나지 않으면 `queued`를 반환하고 화면은 새 `rules_version`이
  보일 때까지 `GET /api/research`를 폴링한다.

세 GET API는 로컬 및 허용된 tailnet 읽기 경로에서 사용할 수 있다. relayed
클라이언트의 `후보로 등록` 버튼은 비활성화되고 `PC에서만` 도움말을 표시한다.

## 규칙 실험 UI

카드는 기본 접힘이며 낙폭/과열, KR/미국 기술주/반도체/통합, 사다리/변동성
목표/혼합, 지표 임계값, 20/60/90일 표시 기간을 고른다. 지표 범위는 252일
낙폭 -60~0%, 60일 이격 -30~+30%, RSI14 10~90, 변동성지수 백분위 0~100이다.
사다리 단계는 선택 지표 수(1~4)와 같다. 목표 변동성은 10~25%다.

프리셋은 현재 관심종목 세 조건, 낙폭 2단계, 변동성 목표 15%다. 결과는 후보
상세와 같은 적합/홀드아웃 요약, 사이클 표, 레벨 막대, 현재 상태와 과거 동일
단계 유사를 사용하되 `실험 결과 · 저장되지 않음`으로 구분한다. 홀드아웃을
보고 임계값을 고치는 행위가 과적합이며, 등록 시 시도 횟수에 남는다는 경고를
항상 표시한다.

## 표시와 계산 규칙

- 후보 정렬은 홀드아웃 `diff_60` 성과 방향을 따른다. 낙폭·혼합 규칙은 큰
  값, 과열 규칙은 더 작은(더 음수인) 값이 우선이며 과열 행에 `낮을수록
  좋음`을 표시한다.
- 홀드아웃 표본이 15 미만이거나 artifact가 작은 표본 경고를 선언하면 `⚠`를
  표시한다.
- 상승/양수는 빨강 `#c0392b`, 하락/음수는 파랑 `#2b62c0`이다.
- 평가 시각은 원본 ISO 시각을 KST로 변환해 `MM-DD HH:MM`으로 표시한다.
- 규칙 정의는 `ladder`, `vol_target`, `hybrid` 구조를 한국어 조건 문장으로
  투영한다. 수익률·기준 대비 차이에는 부호를 유지하고 상승확률·노출·변동성·
  변동성지수 백분위에는 양수 `+`를 붙이지 않는다.
- 포워드 수익률은 신호 기준일과 정확히 일치하는 정규화 지수 종가를 기준으로
  `close[t+h] / close[t] - 1`로 계산한다. JSONL의 `close`는 감사용으로만
  전달하며 계산에는 쓰지 않는다. 정확한 기준일이나 이후 거래일이 부족하면
  해당 기간은 `대기`다.
- 포워드 행의 `as_of`는 신호를 계산한 시장의 기준 세션이다. 후보에 행이 3개
  이하면 후보 패널은 요약 행만 보이도록 기본 접힘 상태로 표시한다.
- 후보별 실현 행이 5개 이상일 때만 기간별 평균을 표시한다. 각 기간도 관측이
  5개 미만이면 평균을 숨긴다.
- 레벨 결과는 날짜 축 없이 후보 레벨을 가로축으로 삼는 전용 inline SVG
  막대로 표시한다. 막대 값은 홀드아웃 60일 평균이며 양수는 빨강, 음수는
  파랑이다. 단조성 안내 문장은 막대 아래에 유지한다.
- 좁은 화면의 순위표는 카드 내부에서 가로 스크롤하며 `규칙`, `홀드아웃 n`,
  `60일 평균 (기준 대비)`를 앞 세 열에 둔다.

## 현재 상태 재사용

active이면서 `basket=KR`인 후보를 artifact 순서로 최대 두 개 투영한다. 같은
helper가 검증 API의 `current_status`와 홈 `sections.regime.research_current`를
만든다. artifact가 없거나 완전한 현재 상태가 없으면 `규칙 평가 없음`을
표시한다. 홈 캐드는 기존 60초 stale-while-revalidate 주기를 유지한다.

## 확인

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/unit/web
node --check src/stock_web/static/research.js
```
