# Project Goal

Owner: **User**

Status: `ACTIVE_USER_GOAL / P0_TO_P4_AUTONOMOUS_SEQUENCE_SELECTED_20260827`

## Core objective

사용자가 정할 투자기간과 위험 한도 안에서 투자수익률을 극대화할 수
있도록, 언제 사고·보유하고·비중을 줄이고·팔아야 하는지를 쉽게 판단하게
한다. 현재 시장과 계좌를 한눈에 파악하고 시장 상황에 맞는 포트폴리오
조정 근거를 제공하는 것이 중심 성과다.

과도한 고평가·쏠림과 시장 급락에서 큰 손실을 가능한 한 피하되, 검증
결과 수익 목표에 유리하다면 레버리지 ETF도 적극적인 수단으로 검토한다.
결과는 매일 쓰기 편한 간결한 Dashboard에서 확인하고, 장기적으로는
개인 노트북이 꺼져 있어도 어디서든 안전하게 조회할 수 있어야 한다.

## Capability map

| Pillar | Desired outcome | Main scope |
|---|---|---|
| 1. 투자 판단 | 오늘 무엇이 달라졌고 무엇을 검토할지 이해한다 | 시장·과거 비교·밸류에이션·거시경제·국채·파생상품·섹터 |
| 2. 계좌와 포트폴리오 | 내 자산 상태와 조정 영향을 한눈에 파악한다 | 잔고·현금·자동 시가평가·다중 통화·포트폴리오 조정 |
| 3. 수익과 위험 관리 | 급락을 방어하면서 검증된 기회를 활용한다 | 버블·급락 위험·스트레스 검증·레버리지 ETF |
| 4. 매일 사용하는 Dashboard | 복잡한 근거를 짧고 이해하기 쉽게 소비한다 | 간결한 GUI·일일 Agent 요약·지표 설명·자동 갱신 |
| 5. 안정적인 운영 | 실패를 놓치지 않고 어디서든 안전하게 조회한다 | 데이터 신뢰성·이상 자동 발견·Inbox·상시 서버·원격 조회 |

이 표와 다섯 개 Pillar는 Goal을 찾기 위한 고정 목차다. 아래 기준은
검증 가능한 세부사항이며 실행 우선순위나 phase 선택이 아니다.

## Current user-selected autonomous sequence

작업 대상은 `Stock Investment Rev1` 하나이며 레거시 `Stock Investment`는
수정하지 않는다. 현재 자율작업은 다음 순서로 프로젝트를 실제 사용 가능한
상태로 발전시킨다. P0는 다른 기능보다 우선하며, 각 단계는 데이터 의미,
단위, 기준시각, PIT 안전성, 원자적 저장과 마지막 유효값 보존을 지킨다.

1. **P0 일일 갱신과 운영 신뢰성:** 2026-08-26 완료분을 시작으로
   KOSPI/KOSDAQ 지수, 시장 PER/PBR, 투자자 수급, 환율, 금리와 VIX를
   최신화한다. Dashboard의 모든 `갱신 필요`를 실제 원자료·상태·예상
   최신일과 대조하고, 작업 미등록, 실행 실패, 프로세스 중단, 공급자 지연,
   상태 판정 오류로 원인을 분류한다. 수동 전체 갱신, 다음 예약 실행,
   재시도·백오프·정제된 오류 기록, 실행 중 Dashboard 재반영까지 검증한다.
2. **P1 선행 실적 데이터:** Forward EPS, 이익추정치 변화와 Forward ROE의
   합법적이고 재현 가능한 소스를 조사하고, 빈티지·구성종목·가중·발표시점이
   보존되는 PIT 계약을 만든다. 검증 전에는 현재/후행 값을 대체하지 않는다.
3. **P2 시장 온도 통합:** 현재 가격·추세·변동성·수급으로 구성된 시장
   온도에 밸류에이션 위치와 실적 모멘텀을 결합한다. 세 축은 근거를 각각
   확인할 수 있어야 하며, 미지원 축을 0점으로 채우거나 하나의 불투명한
   점수로 감추지 않는다. 고점·저점은 검증 전까지 확정 판정하지 않는다.
4. **P3 역사적 검증:** 사전 등록한 시장 온도·밸류에이션·실적 상태를
   3개월·6개월·12개월 후 수익률과 최대낙폭에 대해 purged walk-forward로
   검증한다. 개발 중 최종 holdout은 봉인하고 거래비용과 기회비용을 함께
   보고한다.
5. **P4 종목 발굴:** 과매도, 실적 상향과 상대적 저평가 후보를 찾는 화면을
   만든다. 시장·섹터·자기 역사 비교, 재무건전성과 가치 함정을 분리해
   보여주며 후보는 자동 추천이나 주문 지시로 표현하지 않는다.

P0의 최신성 복구가 현재 최우선이다. 다만 외부 공급자의 정확한 발표시각을
기다리는 동안에는 writer lane을 반납하고, P1 계약·P2 인터페이스·P3 오프라인
검증처럼 독립적으로 안전한 작업을 계속한다.

## 1. 투자 판단

- **오늘과 과거 위치:** 국내외 시장의 당일 상태, 과매수·과매도, 가격,
  심리와 수급을 기준시각과 함께 보여준다. 동일 정의의 과거 분포,
  백분위와 장기 구간을 비교하되 출처·빈도·시점·산식이 다른 값을 하나의
  역사로 조용히 연결하지 않는다.
- **파생상품:** 사용 가능한 선물, 옵션, 베이시스, PCR, 변동성, 가격과
  수급을 함께 설명한다. 사용 지표와 기준 구간을 밝히고 확정적인 매매
  신호로 표현하지 않는다.
- **밸류에이션:** KOSPI 선행 PER·PBR은 출처, 기준일, 예상치 기간,
  구성종목 집계·가중방식과 라이선스가 검증된 경우에만 표시한다.
  선행·후행을 구분하고, 미지원 값을 추정하거나 다른 값으로 대신하지
  않는다.
- **지표 선택과 설명:** 선행·후행·현재 PER, 시장 PER·PBR과 검증된
  지표를 차트나 보조 패널에 선택할 수 있다. 단위가 다른 지표는 독립
  축·패널 또는 명시적 정규화를 쓰며, 정의·산식·단위·해석·한계·출처·
  기준일·갱신주기·집계와 과거 비교 범위를 짧은 한국어로 설명한다.
- **거시경제:** 국내외 성장, 물가, 고용, 통화·유동성, 신용, 재정,
  환율과 원자재를 선행·동행·후행으로 구분한다. 추세, 시장 예상과의
  차이, 발표치 수정과 과거 분포를 함께 보고 `성장 가속·둔화`,
  `물가 상승·하락`, `유동성 완화·긴축` 국면의 근거·상충·불확실성을
  보여준다.
- **국채와 금리:** 국내외 만기별 국채금리·가격·금리곡선을 확인한다.
  가격과 금리의 역관계를 혼동하지 않고 정책·명목·실질금리,
  기대인플레이션과 기간 프리미엄을 검증 가능한 범위에서 구분한다.
  주식 할인율과 주식·국채 상관관계의 국면별 변화도 설명한다.
- **국채 ETF:** TLT 같은 상품은 개별 국채와 구분하여 듀레이션, 금리
  민감도, 분배금, 비용과 추적오차를 반영한다.
- **섹터와 저평가 후보:** 여러 기간의 수익률, 상대강도, 시장 폭과
  수급으로 상승 확산과 소수 종목 쏠림을 구분한다. 낮은 PER·PBR만으로
  저평가를 확정하지 않고 시장·섹터·자기 과거 대비 가치, 이익·현금흐름·
  재무건전성, 부채·유동성·구조적 쇠퇴에 따른 가치 함정을 함께 본다.
  상승 추세와 저평가는 별도 상태로 표시한다.
- **시점 안전성:** 거시지표의 출처, 발표시각, 대상 기간, 빈도,
  계절조정, 단위, 잠정·확정·수정과 다음 발표일을 보존한다. 과거
  검증에는 당시 공개된 빈티지만 쓰고, 섹터 분류·유니버스·구성종목도
  기준시점과 버전을 보존해 생존편향을 막는다.
- **판단 출력:** 종목과 포트폴리오를 `매수 검토`, `보유`,
  `비중 축소 검토`, `매도 검토`로 설명한다. 시장·거시·금리·
  밸류에이션·기술·심리·파생상품·보유비중과 현금을 연결하고, 기준시각,
  근거, 불확실성, 무효 조건, 위험과 포트폴리오 영향을 표시한다.
  탐색 후보는 자동 추천이 아니다.

## 2. 계좌와 포트폴리오

- 현금, 통화별 주문가능금액, 보유종목·수량과 브로커 평가정보를 매일
  읽기 전용으로 확인하고 마지막 성공시각과 실패 사유를 표시한다.
- 보유종목을 가격 갱신 대상으로 자동 연결한다. 예를 들어 TLT 가격이
  30분 주기로 갱신되면 평가금액과 추정 순자산도 자동 재계산한다.
- 계좌 원본시각, 가격시각과 환율시각을 각각 보존한다. 30분 값은
  실시간 스트리밍이 아니라 그 주기의 최신 시가평가로 표시하며,
  휴장·지연·누락·오래됨을 구분한다.
- 다중 통화 순자산은 검증된 현금·보유가치와 환율로 계산한다. 하나라도
  유효하지 않으면 전체 순자산을 확정값으로 표시하지 않는다.
- 브로커 계좌를 보유수량과 현금의 외부 원본으로 두고 내부 평가는
  표시용 추정치로 구분한다.
- 조정안에는 현재·제안 비중, 이유, 거래비용, 세금, 환율, 유동성,
  집중위험과 계좌 영향을 보여주며 최종 결정은 사용자가 내린다.

## 3. 수익과 위험 관리

- **급락 경보:** AI 관련 자산을 포함한 시장을 미리 버블로 단정하지
  않는다. 밸류에이션, 시장 폭, 산업 쏠림, 신용·유동성, 추세, 변동성,
  파생상품과 거시환경을 종합해 `과열 가능성`, `위험 확대`,
  `추세 훼손`, `급락 진행`을 구분한다.
- **계좌 방어:** 위험 상승 시 집중도와 예상 손실을 계산하고 비중 축소,
  분산, 현금 확보와 검증된 방어수단을 이유·비용과 함께 검토한다.
- **역사적 검증:** 닷컴버블, 금융위기와 코로나 급락 등 가능한 구간에서
  시점 안전하게 검증하고, 최대낙폭뿐 아니라 오경보, 너무 이른 매도와
  반등 기회비용도 평가한다. 모든 급락을 예측한다고 표현하지 않는다.
- **레버리지 ETF:** 레버리지를 선택 가능한 수단으로 두되 항상 수익을
  높인다고 가정하지 않는다. ETF와 증거금 거래를 구분하고 일일 재설정,
  경로의존성, 추적오차, 보수와 큰 폭의 하락을 반영한다.
- **분할매수 전략:** `무한매수법` 등을 이름으로 추측하지 않는다.
  대상 ETF, 총 회차, 회차별 금액, 주기, 추가매수 조건, 현금버퍼,
  최대노출과 익절·축소·중단 조건을 버전된 계약으로 정의한다.
- **비교와 스트레스:** 같은 시점 안전 데이터에서 무레버리지,
  정액·정기매수와 단순 보유를 비교하고 금융비용·보수·추적오차·
  거래비용·세금·환율을 차감한다. 급락, 변동성·상관 급변, 갭 하락,
  증거금 부족, 강제청산과 재조정 경로를 시험한다.
- **사용자 한도:** 최대 레버리지, 최대낙폭, 손실 한도, 현금·증거금
  버퍼와 축소 조건을 넘는 제안을 차단하고 GUI에 유효 레버리지,
  비용, 손실 확대, 청산 여유와 축소 조건을 표시한다.
- **전략 검증:** 재현 가능한 백테스트, 검증·테스트 구간과 손대지 않은
  최종 구간을 사용한다. 벤치마크 수익률, 최대낙폭, 변동성, 비용과
  기회비용을 함께 공개하며 미래수익을 보장하지 않는다.

## 4. 매일 사용하는 Dashboard

- 첫 화면은 제한된 요약 카드로 시장, 거시·국채, 섹터, 위험, 계좌와
  오늘의 핵심 판단만 보여준다. 중복·상시 경고·전문가 설정은 상세
  보기로 내려 간결한 정보 우선순위를 유지한다.
- 필요한 근거만 자연스럽게 펼쳐보고 선택한 화면·지표는 다음 실행에도
  유지한다. 일반 화면 크기에서 핵심 정보가 잘리거나 불필요한 스크롤에
  묻히지 않는다.
- 일일 Agent 요약은 시장, 과매수·과매도, 밸류에이션, 거시·국채,
  파생상품·변동성·수급, 섹터와 급락 위험을 짧은 한국어로 설명한다.
  직전 대비 변화, 오늘 볼 항목과 현재 계좌의 의미를 연결한다.
- 관측 사실, 규칙 기반 해석, 불확실한 추론과 제안을 구분하고
  출처·기준시각·최신성을 연결한다. 누락·오래됨·상충을 밝히며 근거
  없는 서사·수치·매매 결론을 만들지 않는다.
- 요약은 검증된 로컬 서비스 입력만 사용한다. GUI가 몰래 provider나
  외부 AI를 호출하지 않으며 필요한 생성 경로는 별도 승인된
  application-service가 소유한다.
- 앱 시작과 실행 중 승인된 갱신이 자동 동작하고 한 번에 수동 재시도할
  수 있다. `실시간`, `지연 시세`, `30분 주기`, `일간 확정`,
  `최근 장 마감`을 구분하며 지원하지 않는 빈도를 실시간처럼 보이지
  않는다.
- 마지막 성공시각, 데이터 기준시각, 다음 갱신 예정과 진행·성공·부분
  실패·오래됨을 쉬운 한국어로 표시한다. 하나의 실패로 전체 화면을
  비우지 않고 마지막 검증값에 기준시각과 경고를 붙인다. 갱신은 GUI
  조작을 멈추게 하지 않는다.

## 5. 안정적인 운영

- 모든 핵심 값과 판단에는 출처, 기준시각, 최신성, 산식과 불확실성이
  따른다. 오래됐거나 의미가 검증되지 않은 값은 숨기고 이유를 표시한다.
- 갱신 실패, 장기 미갱신, 스키마·단위·시점 이상과 검증 실패를 GUI에만
  두지 않고 Agent가 읽는 구조화된 로컬 상태에 기록한다.
- 문제 상태에는 안정적인 코드·지문, 대상, 최초·최근 발생시각, 반복 횟수,
  마지막 성공시각, 심각도, 재시도 가능 여부와 정제된 증거 위치를 둔다.
  동일 원인은 하나로 집계하고 복구도 보존한다.
- GUI, 운영 점검과 Goal 검토 Agent가 같은 상태를 읽는다. 일시적인
  단일 실패는 제외하고 반복·지속시간·기대 갱신시각·중요도 기준을 넘는
  문제만 Queue 전체와 중복 검사해 `inbox/new`에 발견 등록한다.
  자동 발견은 실행·재수집·Ready·Done 전이를 허가하지 않는다.
- 비밀번호, 토큰, 계좌 원문, 원시 응답, 예외 메시지와 전체 traceback은
  상태·로그·Queue에 복사하지 않고 안정적인 코드와 정제된 진단만 남긴다.
- 장기적으로 수집, 검증, 계산, 상태 보관과 일일 요약은 노트북과 분리된
  항상 켜진 환경에서 수행한다. 데스크톱·웹·모바일은 로컬 파일이나
  provider 대신 버전된 읽기 전용 application-service로 같은 결과를
  조회한다.
- 원격 조회에는 인증, 암호화 전송, 짧은 세션, 최소권한과 접근 기록을
  적용하고 비밀정보는 서버 경계에만 둔다. 장애 시 마지막 성공시각과
  오래됨을 표시한다.
- 남는 PC, NAS 또는 클라우드는 비용, 운영 부담, 라이선스와 보안을
  비교한 뒤 선택한다. 아직 확정된 배포 결정이나 실행 권한이 아니다.

## Cross-cutting boundaries

- 정확한 `수익률 극대화` 기준에는 사용자가 정할 투자기간, 벤치마크,
  최대손실·변동성, 필요한 현금과 거래비용 범위가 필요하다. 그전에는
  하나의 전략이나 포트폴리오를 최적이라고 확정하지 않는다.
- 설명과 제안은 참고 근거이며 미래수익 보장이나 자동 주문 지시가 아니다.
- 별도 승인 전까지 자동주문, 이체, 무인 자동매매, 인터넷 공개와 무인
  복구 실행을 허가하지 않는다.
- Goal은 phase나 실행 권한이 아니다. 현재 사용자 지시, Project Status,
  선택된 domain Status, 계약, checkpoint와 active runbook이 provider
  호출, Data mutation, credential·account access, scheduler 변경,
  paper/live execution 권한을 계속 소유한다.

This is the active user-owned Goal. Agents must not invent, broaden, optimize, or
rewrite it unless the user explicitly instructs them to do so.

## User collaboration operating goal

- The user may continuously state ideas, corrections, priorities and desired
  outcomes without waiting for implementation to finish.
- The conversation-facing Listener records only explicit user intent in the
  Goal layer. It does not own Queue triage, project execution, worker dispatch,
  review supervision or long-running implementation.
- A separate Project Manager reads the Goal and current Status, owns the global
  Queue, resolves duplicates and dependencies, selects Domain Leads, and keeps
  implementation and review moving asynchronously.
- Domain Leads manage Workers and Reviewers. Their findings return to the
  Project Manager for managed intake; findings do not silently rewrite the
  user-owned Goal.
- The internal workflow is intentionally evolvable. The Project Manager may
  tune Lead topology, Queue stages, backlog targets, model profiles and review
  policy from observed bottlenecks, while preserving user ownership of the Goal
  and the non-delegable financial, legal, access and secret boundaries.
- The Project Manager keeps a concise current-workflow snapshot and an
  append-only workflow change log. Each material change records when and why it
  changed, the affected roles or Queue stages, and the expected operational
  effect so the Listener and future agents can reconstruct the current design.
- The Listener reads those Manager-owned records instead of inferring workflow
  state from conversation history. When the user asks, it presents the current
  structure and recent changes in a compact form such as Mermaid, without
  requiring the user to inspect implementation details.
- The Listener should acknowledge and retain new user intent promptly even
  while Project Manager and Domain Lead work continues in the background.

## Goal-to-Inbox planning contract

A designated planning agent must use
[`goal-inbox-planner`](../../.agents/skills/goal-inbox-planner/SKILL.md) for a
bounded planning pass. It compares this Goal with current Status and registers
only evidenced, non-duplicate discoveries in `inbox/new`.

```text
AGENTS.md
  -> PROJECT_GOAL.md
  -> PROJECT_STATUS.md
  -> selected domain STATUS
  -> goal-inbox-planner
  -> request queue inbox/new
```
