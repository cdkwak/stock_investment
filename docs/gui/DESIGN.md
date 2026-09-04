# GUI Design Contract

역할: 이 문서는 표시 설계 계약이며, 현재 런타임·우선순위·퇴역 상태의 권위는 [GUI Status](GUI_STATUS.md)다.

## 0. Research Log

- Existing Dashboard audit evidence: 제거됨 (backup/repo-cleanup-phase2-20260903 브랜치에 보존). Its recorded finding was that the first screen gave ten equal-weight asset cards, exposed technical chart controls before answering the daily investor question, mixed Korean and English navigation, and repeated unavailable states without a concise explanation or next action.
- Existing Data Status audit evidence: 제거됨 (backup/repo-cleanup-phase2-20260903 브랜치에 보존). Its recorded finding was that an operator-oriented health table appeared as a top-level peer of everyday investing pages and used scheduling/data-pipeline language better kept behind an advanced tooling entry point.
- User feedback, 2026-09-02: the GUI is not being revisited because it feels built for agents rather than for the investor.
- Curated OMH design-data lookup was attempted but the `omh` CLI was unavailable on PATH. No tokens were imported from that optional local reference lane; all decisions below are explicit project decisions.

## 1. Atmosphere & Identity

- Audience: one technically capable but time-constrained Korean investor checking the app daily.
- Primary direction: **premium / soft consumer fintech** — calm, trustworthy, and quick to scan.
- Borrowed direction: operational clarity for evidence tables and advanced diagnostics, because dates, source identity, and fail-closed states must remain precise.
- Three adjectives: **calm, legible, decision-oriented**.
- Signature element: a top-of-screen **오늘의 판단** panel that separates verified observations, portfolio relevance, and unavailable evidence instead of showing a grid of equally important system cards.
- The interface is not an agent console. Internal dataset IDs, scheduler terminology, health gates, PIT codes, and diagnostic controls remain available in details but never lead the default experience.

## 2. Color

- `bg.canvas`: `#F4F7FB`
- `bg.surface`: `#FFFFFF`
- `bg.subtle`: `#F8FAFD`
- `text.primary`: `#152033`
- `text.secondary`: `#526177`
- `text.muted`: `#7A879A`
- `border.default`: `#D8E0EB`
- `accent.primary`: `#245EA8` — deliberate trust/selection color, limited to navigation, focus, and one primary action; never used as decoration.
- `state.positive`: `#187A55`
- `state.warning`: `#A86100`
- `state.negative`: `#B93846`
- `state.unknown`: `#66758C`
- Discipline: approximately 70% canvas/surface, 20% layered subtle surfaces, at most 10% accent and semantic color.
- Contrast: WCAG AA minimum for text and controls. Semantic meaning never relies on color alone; every state has a Korean label and icon/shape.

## 3. Typography

- Family: `Pretendard`, `SUIT`, `Noto Sans KR`, `Segoe UI`, sans-serif.
- Tabular figures: same family with tabular-number feature where supported.
- Scale: caption 14, body 15, emphasized body 16, section title 19, page title 26, key value 30 px.
- Korean body text never falls below 14 px; normal body line height is 1.55 and compact labels 1.4.
- Use at most regular, medium, and semibold weights. Avoid all-caps English labels.
- Korean copy uses natural line breaks and `word-break: keep-all` semantics where available. Internal codes may break anywhere only inside advanced evidence views.

## 4. Spacing & Layout

- Base unit: 4 px; main scale: 4, 8, 12, 16, 24, 32.
- Desktop target: 1366×768 and larger; minimum validated target: 900×640.
- 본문 최대 1760px, 그 이상은 좌우 여백. 상단 내비게이션만 화면 너비 전체를 사용한다.
- Default Dashboard order:
  1. page identity, last accepted time, and one refresh-status sentence;
  2. **오늘의 판단**: up to three verified takeaways, portfolio relevance, and one uncertainty/next check;
  3. asymmetric key-market summary: Korea, US/risk, rates/FX, and account impact;
  4. one primary chart with controls collapsed under **차트 설정**;
  5. supporting evidence and advanced diagnostics.
- Equal-card grids are used only for genuine peers. The first screen must not give ten assets identical visual weight.
- Only the central content area scrolls. Primary navigation and the current page title remain stable.
- The Data Status, Backtest, and Research tooling surfaces move under a single **분석 도구** or **고급** navigation entry; they remain discoverable in two interactions or fewer.

## 5. Components

### Primary navigation

- Everyday entries: `오늘`, `시장`, `종목`, `관심종목`, `계좌`.
- Advanced entries grouped under `분석 도구`: `데이터 상태`, `리서치`, `백테스트`.
- Default, hover, focus-visible, active, disabled, and compact-overflow states are required.

### 오늘의 판단 panel

- Sections: `확인된 변화`, `내 계좌에 미치는 의미`, `아직 확인할 수 없는 것`.
- Maximum three takeaways and one primary next action; no fabricated narrative.
- Each takeaway carries source/as-of/freshness in a secondary detail or tooltip, not in the headline.
- When evidence is unavailable, show one consolidated explanation instead of repeated `확인 필요` cards.

### Market summary cards

- Use 3–5 grouped cards, not ten equal cards.
- Each card has a plain-language title, one key value or a numeric-free state, change context, as-of, and a details affordance.
- Stale/unsupported states clear numbers and explain the reason in Korean.

### Chart

- The chart is visible before technical settings.
- Indicators and technical parameters are collapsed under `차트 설정`; saved expert preferences may reopen them.
- Loading, valid-empty, stale, read-failure, and no-selection states are distinct and numeric-free when required.

### Evidence and Data Status

- Advanced table retains exact source, dataset identity, finality/PIT, expected/latest dates, and next action.
- Default labels use investor language (`최신 완료 세션`, `업데이트 필요`, `정상적인 발표 대기`). Internal codes appear only in expandable details or copyable evidence.
- Data Status is not a primary daily destination.

### Buttons and inputs

- One primary action per region. Secondary actions are quiet; diagnostics are tertiary.
- All controls require default, hover, focus-visible, active, disabled, loading, error, and empty behavior.

## 6. Motion & Interaction

- State transitions: 120–180 ms ease-out; no bounce or decorative motion.
- Animate only navigation selection, expand/collapse, and successful local reread confirmation.
- Respect reduced-motion settings by making all transitions immediate.
- Refresh never blocks navigation or chart interaction.

## 7. Depth & Surface

- Layering is communicated mainly by background and 1 px borders.
- Use one restrained shadow only for floating menus/dialogs; ordinary cards remain flat.
- Radius: 8 px for controls, 12 px for major panels, 16 px only for the signature Today panel.
- No glass effects, decorative gradients, or shadows on every card.

## 8. Accessibility Constraints & Accepted Debt

- Full keyboard navigation, visible focus, meaningful accessible names, and logical focus order are mandatory.
- Korean body text floor is 14 px; 200% scaling must not clip key state, dates, or actions.
- Status is communicated through label plus color/icon.
- Financial numbers preserve unit, source, as-of, freshness, and supported meaning. Stale, invalid, mismatched, or unsupported input remains numeric-free.
- Account identifiers, credentials, raw provider payloads, and order controls never enter presentation state.
- Accepted first-pass debt: no mobile layout; this remains a native desktop application validated at 900×640, 1366×768, and 2560×1440.

## First implementation slice

The first slice changes only the default daily experience and navigation hierarchy:

1. Rename and regroup top-level navigation around everyday tasks, placing operator/research pages under `분석 도구`.
2. Add the `오늘의 판단` panel using existing verified local services only; if no accepted summary exists, render a concise numeric-free evidence status rather than generated advice.
3. Group the ten equal market cards into a smaller asymmetric summary with details available on demand.
4. Collapse chart indicator controls under `차트 설정` by default.
5. Add plain-language explanation that `예상일` means `최신 완료 세션`.

No provider call, data promotion, account mutation, trading instruction, or new financial inference is part of this slice.
