# 검증(Research) 페이지

## 목적과 경계

`/research`는 규칙 후보의 적합·홀드아웃·사이클·포워드 성과를 읽기
전용으로 표시한다. 규칙 생성, 후보 편집, 데이터 수집 및 주문 기능은 없다.
대시보드는 `src/stock_data/research`를 import하지 않고 아래의 보존 파일을
데이터로만 읽는다.

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

두 API 모두 로컬 및 허용된 tailnet 읽기 경로에서 사용할 수 있다. 쓰기
endpoint는 없다.

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
