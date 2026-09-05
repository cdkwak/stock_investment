/* Local account page: provider-free reads and loopback-only writes. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (value, digits = 2) => value === null || value === undefined ? "—" : Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits });
  const money = (value) => value === null || value === undefined ? "—" : `₩${Math.round(Number(value)).toLocaleString("ko-KR")}`;
  const nativeMoney = (value, currency) => value === null || value === undefined ? "—" : currency === "USD" ? `$${fmt(value, 2)}` : money(value);
  const compactMoney = (value) => value === null || value === undefined ? "—" : `₩${(Number(value) / 1e8).toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}억`;
  const signedMoney = (value) => value === null || value === undefined ? "—" : `${Number(value) > 0 ? "+" : Number(value) < 0 ? "−" : ""}₩${Math.abs(Math.round(Number(value))).toLocaleString("ko-KR")}`;
  const pct = (value) => value === null || value === undefined ? "—" : `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;
  const valueClass = (value) => value === null || value === undefined ? "muted" : Number(value) > 0 ? "up" : Number(value) < 0 ? "down" : "muted";
  const shortDate = (value) => {
    if (!value) return "—";
    const text = String(value);
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text.slice(5);
    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) return text;
    const parts = Object.fromEntries(new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(parsed).map((part) => [part.type, part.value]));
    return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  };
  const today = () => {
    const now = new Date();
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  };
  const optionHtml = (options, selected) => (options || []).map((item) => `<option value="${esc(item.value)}" ${item.value === selected ? "selected" : ""}>${esc(item.label)}</option>`).join("");
  const JOURNAL_PRICE_LABELS = Object.freeze({
    BUY: { label: "매수 단가 (원/주)", hint: "실제 매수 체결가를 1주 기준으로 입력하세요.", required: true },
    SELL: { label: "매도 단가 (원/주)", hint: "실제 매도 체결가를 1주 기준으로 입력하세요.", required: true },
    DIVIDEND: { label: "주당 배당금 (세전)", hint: "세금이 빠지기 전 1주당 배당금을 입력하세요.", required: true },
    TRANSFER_IN: { label: "단가 (선택)", hint: "입고 당시 단가를 아는 경우에만 입력하세요.", required: false },
    TRANSFER_OUT: { label: "단가 (선택)", hint: "출고 당시 단가를 아는 경우에만 입력하세요.", required: false },
    OTHER: { label: "단가 (선택)", hint: "금액 계산이 필요한 경우에만 단가를 입력하세요.", required: false },
  });

  let payload = null;
  let manualAccounts = [];
  let assetRows = [];
  let liabilityRows = [];
  let selectedReturnWindow = "3M";
  let returnPeriodHydrated = false;
  let netWorthOverlayVisible = false;
  let holdingAccountFilter = "ALL";
  let holdingCurrencyFilter = "ALL";
  let holdingSortKey = "weight_pct";
  let holdingSortDirection = "desc";
  let allocationGroup = "asset_class";
  let journalPayload = { events: [], summary: {}, gaps: [] };
  let selectedJournalDays = 90;
  let journalVisibleRows = 10;
  let journalSearchTimer = null;
  let journalSearchSequence = 0;
  let journalSearchMatches = [];
  let selectedJournalIdentity = null;
  let manualSearchTimer = null;
  let manualSearchSequence = 0;
  let manualSearchMatches = [];
  let activeManualSearchInput = null;
  const symbolResolveStates = new WeakMap();
  let toastTimer = null;

  function showToast(message) {
    const toast = $("account-toast");
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 8000);
  }

  function setWriteStatus(statusHost, message, { toast = true } = {}) {
    $(statusHost).textContent = message;
    if (toast) showToast(message);
  }

  function writeStatusText(status, result, successDetail = "") {
    const detail = result && result.error ? ` · ${result.error}` : "";
    if (status === 403) return `403 · 폰에서는 저장 불가${detail}`;
    if (status === 400) return `400 · 항목 필드 오류${detail}`;
    if (status >= 200 && status < 300) return `200 · 저장 완료${successDetail ? ` · ${successDetail}` : ""}`;
    return `${status || "연결 오류"} · 저장 상태 확인 필요${detail}`;
  }

  async function writeJson(url, method, body, statusHost, successDetail = "") {
    $(statusHost).textContent = "요청 전송 중…";
    let response;
    let result;
    try {
      response = await fetch(url, {
        method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      result = await response.json();
    } catch (error) {
      const message = "연결 오류 · 요청을 보내지 못했거나 응답을 받지 못했습니다";
      setWriteStatus(statusHost, message);
      const wrapped = new Error(message); wrapped.handled = true; throw wrapped;
    }
    const message = writeStatusText(response.status, result, successDetail);
    setWriteStatus(statusHost, message);
    if (!response.ok) {
      refreshWriteAudit().catch(() => {});
      const error = new Error(message); error.handled = true; throw error;
    }
    return result;
  }

  function sourceAsOf(rows, kinds) {
    return (rows || []).filter((row) => kinds.includes(row.kind)).map((row) => `${row.name} ${row.as_of_label || shortDate(row.as_of)}${row.included ? "" : " 제외"}`).join(" · ");
  }

  function renderSummary() {
    const summary = payload.summary || {};
    $("invest-total").textContent = compactMoney(summary.invest_total_krw);
    $("month-true-pnl").textContent = signedMoney(summary.month_true_pnl_krw);
    $("month-true-pnl").className = `num ${valueClass(summary.month_true_pnl_krw)}`;
    $("net-worth-total").textContent = compactMoney(summary.net_worth_krw);
    $("invest-asof").textContent = sourceAsOf(payload.rows, ["api", "manual"]) || "연결된 투자 자산 없음";
    const netWorthSources = sourceAsOf(payload.rows, ["asset", "liability"]);
    $("net-worth-asof").textContent = summary.net_worth_krw === null || summary.net_worth_krw === undefined
      ? "기타 자산·부채 스냅샷 없음"
      : `부동산·예금 포함 · ${summary.net_worth_as_of_label || "기준일 미상"} 기준`;
  }

  function renderSourceRows() {
    const rows = payload.rows || [];
    $("account-source-rows").innerHTML = rows.length ? rows.map((row) => {
      const inclusion = row.included ? (row.partial ? "부분 포함" : "포함") : "제외";
      const asOf = row.as_of_label || shortDate(row.as_of);
      const cashTitle = row.cash_note || "";
      return `<tr>
      <td class="source-identity"><b>${esc(row.name)}</b><div class="muted source-note">${esc(row.note || "")}</div><div class="source-mobile-meta"><span>기준일 ${esc(asOf)}</span><span>${esc(inclusion)}</span></div></td>
      <td class="num ${row.value_krw < 0 ? "down" : ""}">${money(row.value_krw)}</td>
      <td class="num source-cash" title="${esc(cashTitle)}" aria-label="${esc(cashTitle || `현금 ${money(row.cash_krw)}`)}">${money(row.cash_krw)}</td>
      <td class="num ${row.pnl_krw > 0 ? "up" : row.pnl_krw < 0 ? "down" : ""}">${money(row.pnl_krw)}</td>
      <td class="num source-asof-cell">${esc(asOf)}</td>
      <td class="source-inclusion-cell"><span class="chip ${row.included ? "" : "dashed"}">${esc(inclusion)}</span></td>
    </tr>`;
    }).join("") : `<tr><td colspan="6" class="unavailable">표시할 계좌나 자산이 없습니다.</td></tr>`;
  }

  function renderPerformance() {
    const metric = ((payload || {}).return_metrics || {})[selectedReturnWindow] || {};
    const label = selectedReturnWindow === "ALL" ? "전체" : selectedReturnWindow;
    const holdings = payload.holdings || {};
    $("return-period-label").textContent = metric.start_date && metric.end_date ? `${shortDate(metric.start_date)}~${shortDate(metric.end_date)}` : label;
    const dollarAssets = holdings.usd_assets_usd === null || holdings.usd_assets_usd === undefined ? "—" : `$${fmt(holdings.usd_assets_usd, 2)}`;
    const fxEffect = pct(holdings.fx_effect_pct);
    $("return-metrics").innerHTML = metric.reason ? `<div class="unavailable">${esc(metric.reason)}</div>` : `
      <div class="return-row"><span>전체 진짜 손익</span><b class="num ${valueClass(metric.true_pnl_krw)}">${signedMoney(metric.true_pnl_krw)}</b></div>
      <div class="return-row" title="입출금 시점을 반영한 실제 투입금 대비 수익률"><span>돈 가중 (내 실제 수익률)</span><b class="num ${valueClass(metric.return_pct_modified_dietz)}">${pct(metric.return_pct_modified_dietz)}</b></div>
      <div class="return-row" title="입출금 영향을 잘라내고 이어 붙인 운용 성과"><span>시간 가중 (운용 실력)</span><b class="num ${valueClass(metric.return_pct_twr)}">${pct(metric.return_pct_twr)}</b></div>
      <div class="return-row"><span>KOSPI 동기간</span><b class="num ${valueClass(metric.kospi_return_pct)}">${pct(metric.kospi_return_pct)}</b></div>
      <div class="return-row"><span>증권사 표시 손익</span><b class="num ${valueClass(metric.broker_reported_pnl_krw)}">${signedMoney(metric.broker_reported_pnl_krw)}</b></div>
      <div class="return-row"><span>달러 자산 · 환율 효과</span><b class="num">${dollarAssets} · ${fxEffect}</b></div>`;
    $("mobile-return-summary").textContent = metric.reason ? metric.reason : `돈가중 ${pct(metric.return_pct_modified_dietz)} · 시간가중 ${pct(metric.return_pct_twr)} · KOSPI ${pct(metric.kospi_return_pct)}`;
    const history = payload.total_asset_history || [];
    const benchmark = payload.benchmark || [];
    const shownHistory = metric.start_date ? history.filter((point) => point.t >= metric.start_date) : history;
    const shownBenchmark = metric.start_date ? benchmark.filter((point) => point.t >= metric.start_date) : benchmark;
    const netWorth = payload.net_worth || {};
    const shownNetWorth = netWorthOverlayVisible && Number(netWorth.snapshot_count || 0) >= 2
      ? (netWorth.timeline || []).filter((point) => !metric.start_date || point.t >= metric.start_date)
      : [];
    const chartLabels = payload.chart_labels || {};
    window.SIChart.renderLineChart($("total-asset-chart"), shownHistory, {
      series: [
        { key: "invest", label: chartLabels.primary || "총 투자자산", color: "#1f1d1a", points: shownHistory },
        { key: "kospi", label: chartLabels.benchmark || "KOSPI (시작값 맞춤)", color: "#2a78d6", points: shownBenchmark },
        { key: "net-worth", label: chartLabels.net_worth || "순자산 스냅샷", color: "#8a847b", points: shownNetWorth },
      ],
      ariaLabel: "총 투자자산과 KOSPI 동기간 추이",
      emptyMessage: "총 투자자산 관측이 2개 이상이면 선이 표시됩니다.",
      // 계좌 편입(측정 범위 변경) 날짜: the jump on that day is a scope change, not a return.
      events: (payload.scope_changes || []).filter((item) => !metric.start_date || item.date >= metric.start_date).map((item) => ({ t: item.date, label: item.label || "계좌 편입" })),
    });
    const paths = $("total-asset-chart").querySelectorAll(".si-series-line");
    if (shownNetWorth.length >= 2 && paths.length >= 3) paths[2].classList.add("net-worth-overlay-line");
    const overlayButton = $("net-worth-overlay");
    overlayButton.disabled = Number(netWorth.snapshot_count || 0) < 2;
    overlayButton.setAttribute("aria-pressed", netWorthOverlayVisible ? "true" : "false");
  }

  function visibleHoldingRows() {
    const rows = ((payload || {}).holdings || {}).rows || [];
    const filtered = rows.filter((row) => (holdingAccountFilter === "ALL" || row.account_group === holdingAccountFilter)
      && (holdingCurrencyFilter === "ALL" || row.currency === "USD"));
    const numeric = new Set(["quantity", "market_value_krw", "weight_pct", "return_pct"]);
    return [...filtered].sort((left, right) => {
      const a = left[holdingSortKey], b = right[holdingSortKey];
      if (a === null || a === undefined) return b === null || b === undefined ? String(left.id).localeCompare(String(right.id), "ko") : 1;
      if (b === null || b === undefined) return -1;
      const compared = numeric.has(holdingSortKey) ? Number(a) - Number(b) : String(a).localeCompare(String(b), "ko");
      return holdingSortDirection === "asc" ? compared : -compared;
    });
  }

  function renderHoldings() {
    const rows = visibleHoldingRows();
    $("holding-rows").innerHTML = rows.length ? rows.map((row) => {
      const multiple = Number(row.leverage_multiple || 1);
      const leverage = multiple > 1 ? `<span class="leverage-chip">${fmt(multiple, 0)}배</span>` : "";
      const weight = row.weight_pct;
      const width = weight === null || weight === undefined ? 0 : Math.min(100, Math.max(1, Number(weight) * 2));
      return `<tr class="${row.valued ? "" : "holding-unvalued"}" title="${esc(row.reason || "")}">
        <td class="holding-name"><div class="holding-name-main"><b>${esc(row.name || row.symbol)}</b>${leverage}</div><small class="holding-mobile-meta">${esc(row.account)} · ${esc(row.asset_class)}${row.reason ? ` · ${esc(row.reason)}` : ""}</small></td>
        <td class="muted">${esc(row.account)}</td><td class="muted">${esc(row.asset_class)}</td>
        <td class="num">${fmt(row.quantity, 6)}</td><td class="num">${money(row.market_value_krw)}</td>
        <td class="holding-weight"><span class="weight-track"><i class="${multiple > 1 ? "leveraged" : ""}" style="width:${width}%"></i></span><span class="num">${row.weight_pct === null || row.weight_pct === undefined ? "—" : `${fmt(row.weight_pct, 1)}%`}</span></td>
        <td class="num ${valueClass(row.return_pct)}">${pct(row.return_pct)}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="7" class="unavailable">선택한 조건의 보유 자산이 없습니다.</td></tr>`;
    document.querySelectorAll(".holdings-table th button").forEach((button) => {
      const active = button.dataset.sort === holdingSortKey;
      button.classList.toggle("active", active);
      const base = button.textContent.replace(/\s[↑↓]$/, "");
      button.textContent = active ? `${base} ${holdingSortDirection === "asc" ? "↑" : "↓"}` : base;
    });
    renderAllocation();
  }

  function groupedAllocations(rows) {
    const groups = new Map();
    rows.filter((row) => row.market_value_krw !== null && row.market_value_krw !== undefined).forEach((row) => {
      const key = String(row[allocationGroup] || "기타");
      groups.set(key, (groups.get(key) || 0) + Number(row.market_value_krw));
    });
    const ordered = [...groups.entries()].sort((a, b) => b[1] - a[1]);
    if (ordered.length <= 6) return ordered;
    return [...ordered.slice(0, 5), ["기타", ordered.slice(5).reduce((sum, item) => sum + item[1], 0)]];
  }

  function renderAllocation() {
    const rows = visibleHoldingRows();
    const groups = groupedAllocations(rows);
    const total = groups.reduce((sum, item) => sum + item[1], 0);
    const leveraged = rows.filter((row) => Number(row.leverage_multiple || 1) > 1 && row.market_value_krw !== null && row.market_value_krw !== undefined).reduce((sum, row) => sum + Number(row.market_value_krw), 0);
    const leveragedPct = total ? leveraged / total * 100 : null;
    const colors = ["#a8621a", "#2a78d6", "#1f1d1a", "#8a847b", "#62a58a", "#c9c3b9"];
    const radius = 58, circumference = 2 * Math.PI * radius;
    let offset = 0;
    const arcs = groups.map((item, index) => {
      const length = total ? item[1] / total * circumference : 0;
      const arc = `<circle cx="78" cy="78" r="${radius}" fill="none" stroke="${colors[index]}" stroke-width="22" stroke-dasharray="${length} ${circumference - length}" stroke-dashoffset="${-offset}" transform="rotate(-90 78 78)"></circle>`;
      offset += length; return arc;
    }).join("");
    $("allocation-donut").innerHTML = total ? `<svg viewBox="0 0 156 156" role="img" aria-label="보유 비중 도넛">${arcs}<text x="78" y="72" text-anchor="middle" class="si-axis-label">레버리지</text><text x="78" y="92" text-anchor="middle" font-size="18" font-weight="600" fill="#a8621a">${fmt(leveragedPct, 1)}%</text></svg><div class="donut-legend">${groups.map((item, index) => `<div class="donut-legend-row"><i style="background:${colors[index]}"></i><span>${esc(item[0])}</span><b class="num">${fmt(item[1] / total * 100, 1)}%</b></div>`).join("")}</div>` : `<div class="unavailable">표시할 비중이 없습니다.</div>`;
    const holdings = payload.holdings || {};
    const nominal = holdings.leveraged_weight_pct, exposure = holdings.effective_exposure_pct, limit = holdings.leverage_limit_pct;
    $("leverage-gauge-value").textContent = `${nominal === null || nominal === undefined ? "—" : `${fmt(nominal, 1)}%`} / 한도 ${limit === null || limit === undefined ? "—" : `${fmt(limit, 0)}%`}`;
    $("exposure-gauge-value").textContent = exposure === null || exposure === undefined ? "—" : `${fmt(exposure, 1)}%`;
    $("leverage-gauge-bar").style.width = `${Math.min(100, Math.max(0, Number(nominal || 0)))}%`;
    $("exposure-gauge-bar").style.width = `${Math.min(100, Math.max(0, Number(exposure || 0)))}%`;
    $("leverage-limit-mark").hidden = limit === null || limit === undefined;
    $("leverage-limit-mark").style.left = `${Math.min(100, Math.max(0, Number(limit || 0)))}%`;
  }

  function renderDividends() {
    const dividend = payload.dividends || {};
    const monthly = dividend.monthly || [];
    const max = Math.max(1, ...monthly.map((row) => Number(row.amount_krw || 0)));
    const width = 600, height = 128, baseline = 102, step = width / Math.max(monthly.length, 1), barWidth = Math.min(28, step * .58);
    $("dividend-chart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="최근 12개월 세후 배당 입금"><line x1="0" x2="${width}" y1="${baseline}" y2="${baseline}" class="si-axis"></line>${monthly.map((row, index) => { const barHeight = Number(row.amount_krw || 0) / max * 78; const x = index * step + (step - barWidth) / 2; return `<rect x="${x}" y="${baseline - barHeight}" width="${barWidth}" height="${Math.max(1, barHeight)}" fill="#2a78d6"><title>${esc(row.month)} ${money(row.amount_krw)}</title></rect>${index === 0 || index === monthly.length - 1 || index === Math.floor(monthly.length / 2) ? `<text x="${x + barWidth / 2}" y="120" text-anchor="middle" class="si-axis-label">${esc(row.month.slice(2))}</text>` : ""}`; }).join("")}</svg>`;
    $("dividend-summary").innerHTML = `<div class="dividend-kpi"><span>올해 누적</span><b class="num">${money(dividend.year_total_krw)}</b></div><div class="dividend-kpi"><span>월 평균</span><b class="num">${money(dividend.monthly_average_krw)}</b></div><div class="dividend-kpi"><span>배당 종목 수</span><b class="num">${fmt(dividend.symbol_count, 0)}종목</b></div><div class="dividend-kpi"><span>배당 수익률 (평가금액 대비)</span><b class="num">${pct(dividend.yield_pct)}</b></div>`;
    $("dividend-symbols").innerHTML = (dividend.by_symbol || []).length ? dividend.by_symbol.map((row) => `<div class="dividend-symbol-row"><span>${esc(row.symbol)}</span><b class="num">${money(row.amount_krw)}</b></div>`).join("") : "";
    $("dividend-empty").hidden = Boolean((dividend.entries || []).length);
    $("dividend-empty").textContent = dividend.reason || dividend.empty_note || "기록 없음 · KB 배당은 자동 수집 예정 · 토스·연금은 캡처로 입력";
  }

  function hydrateReturnPeriod() {
    const period = payload.return_period || {};
    if (!returnPeriodHydrated) {
      selectedReturnWindow = period.default_window || "3M";
      returnPeriodHydrated = true;
    }
    document.querySelectorAll("#return-range button").forEach((button) => {
      button.classList.toggle("on", button.dataset.v === selectedReturnWindow);
    });
    const allButton = document.querySelector('#return-range button[data-v="ALL"]');
    if (allButton) allButton.textContent = period.all_label || "전체";
  }

  function resetCashFlowForm() {
    $("cash-flow-id").value = "";
    $("cash-flow-date").value = today();
    $("cash-flow-amount").value = "";
    $("cash-flow-account").value = "";
    $("cash-flow-memo").value = "";
    $("cancel-cash-flow").hidden = true;
  }

  function renderCashFlows() {
    const flows = payload.cash_flows || {};
    const entries = flows.entries || [];
    $("cash-flow-rows").innerHTML = entries.length ? entries.map((entry) => `<tr>
      <td class="num">${esc(entry.date)}</td><td class="num ${valueClass(entry.amount_krw)}">${signedMoney(entry.amount_krw)}</td>
      <td>${esc(entry.account)}</td><td>${esc(entry.memo || "")}</td>
      <td><button type="button" class="text-button edit-cash-flow" data-flow-id="${esc(entry.id)}">수정</button><button type="button" class="text-button delete-cash-flow" data-flow-id="${esc(entry.id)}">삭제</button></td>
    </tr>`).join("") : `<tr><td colspan="5" class="unavailable">기록된 입출금이 없습니다.</td></tr>`;
    const monthly = flows.monthly_subtotals || [];
    $("cash-flow-monthly").innerHTML = monthly.length ? monthly.map((row) => `<div class="breakdown-row"><span>${esc(row.month)}</span><b class="num ${valueClass(row.amount_krw)}">${signedMoney(row.amount_krw)}</b></div>`).join("") : `<div class="unavailable">월별 합계가 없습니다.</div>`;
    if (flows.reason) $("cash-flow-status").textContent = flows.reason;
  }

  function journalSideLabel(side) {
    return ({
      BUY: "매수", SELL: "매도", DIVIDEND: "배당 추정", "DIVIDEND?": "추정(미확인)",
      TRANSFER_IN: "입고", TRANSFER_OUT: "출고", OTHER: "기타",
    })[side] || side;
  }

  function currencySummary(values) {
    const entries = Object.entries(values || {});
    return entries.length ? entries.map(([currency, value]) => nativeMoney(value, currency)).join(" · ") : "—";
  }

  function closeJournalSearch() {
    journalSearchMatches = [];
    $("journal-search-results").hidden = true;
    $("journal-search-results").innerHTML = "";
    $("journal-name").setAttribute("aria-expanded", "false");
  }

  function renderJournalSearch(matches, reason = null) {
    journalSearchMatches = matches || [];
    const host = $("journal-search-results");
    host.innerHTML = journalSearchMatches.length ? journalSearchMatches.map((item, index) => `
      <button type="button" class="journal-search-option" role="option" data-search-index="${index}">
        <b>${esc(item.name)} <span class="num">${esc(item.symbol)}</span></b>
        <small>${esc(item.market)} · ${esc(item.security_type)}${item.listing_date ? ` · 상장 ${esc(item.listing_date)}` : ""}</small>
      </button>`).join("") : `<div class="journal-search-empty">${esc(reason || "일치하는 종목이 없습니다.")}</div>`;
    host.hidden = false;
    $("journal-name").setAttribute("aria-expanded", "true");
  }

  function selectJournalIdentity(item) {
    if (!item) return;
    selectedJournalIdentity = item;
    $("journal-name").value = item.name || "";
    $("journal-name").dataset.autoFilledName = $("journal-name").value;
    $("journal-symbol").value = item.symbol || "";
    $("journal-currency").value = item.market === "US ETF" ? "USD" : "KRW";
    setSymbolResolveHint($("journal-symbol"), `✓ ${item.name} · ${item.market}`, "success");
    closeJournalSearch();
  }

  function scheduleJournalSearch() {
    const query = $("journal-name").value.trim();
    if (selectedJournalIdentity && query !== selectedJournalIdentity.name) {
      selectedJournalIdentity = null;
      $("journal-symbol").value = "";
      delete $("journal-name").dataset.autoFilledName;
      clearSymbolResolveHint($("journal-symbol"));
    }
    window.clearTimeout(journalSearchTimer);
    const sequence = ++journalSearchSequence;
    if (!query) {
      closeJournalSearch();
      return;
    }
    journalSearchTimer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
        const result = await response.json();
        if (sequence !== journalSearchSequence) return;
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        renderJournalSearch(result.matches || [], result.reason);
      } catch (error) {
        if (sequence !== journalSearchSequence) return;
        renderJournalSearch([], `검색 실패 · ${error.message}`);
      }
    }, 350);
  }

  function closeManualSearch() {
    if (activeManualSearchInput) {
      const host = activeManualSearchInput.closest(".manual-search-field")?.querySelector(".manual-search-results");
      if (host) { host.hidden = true; host.innerHTML = ""; }
      activeManualSearchInput.setAttribute("aria-expanded", "false");
    }
    manualSearchMatches = [];
    activeManualSearchInput = null;
  }

  function renderManualSearch(input, matches, reason = null) {
    activeManualSearchInput = input;
    manualSearchMatches = matches || [];
    const host = input.closest(".manual-search-field").querySelector(".manual-search-results");
    host.innerHTML = manualSearchMatches.length ? manualSearchMatches.map((item, index) => `
      <button type="button" class="journal-search-option manual-search-option" role="option" data-search-index="${index}">
        <b>${esc(item.name)} <span class="num">${esc(item.symbol)}</span></b>
        <small>${esc(item.market)} · ${esc(item.security_type)}</small>
      </button>`).join("") : `<div class="journal-search-empty">${esc(reason || "일치하는 종목이 없습니다.")}</div>`;
    host.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function scheduleManualSearch(input) {
    const query = input.value.trim();
    if (query !== input.dataset.selectedName) {
      input.closest(".holding-row").querySelector('[data-field="ticker"]').value = "";
      delete input.dataset.selectedName;
      delete input.dataset.autoFilledName;
      clearSymbolResolveHint(input.closest(".holding-row").querySelector('[data-field="ticker"]'));
    }
    window.clearTimeout(manualSearchTimer);
    const sequence = ++manualSearchSequence;
    if (!query) { closeManualSearch(); return; }
    manualSearchTimer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
        const result = await response.json();
        if (sequence !== manualSearchSequence) return;
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        renderManualSearch(input, result.matches || [], result.reason);
      } catch (error) {
        if (sequence !== manualSearchSequence) return;
        renderManualSearch(input, [], `검색 실패 · ${error.message}`);
      }
    }, 350);
  }

  function selectManualIdentity(item) {
    if (!item || !activeManualSearchInput) return;
    const input = activeManualSearchInput;
    const row = input.closest(".holding-row");
    const editor = input.closest(".manual-account-editor");
    input.value = item.name || "";
    input.dataset.selectedName = input.value;
    input.dataset.autoFilledName = input.value;
    const symbolInput = row.querySelector('[data-field="ticker"]');
    symbolInput.value = item.symbol || "";
    editor.querySelector('[data-field="currency"]').value = item.currency || (item.market === "US ETF" ? "USD" : "KRW");
    setSymbolResolveHint(symbolInput, `✓ ${item.name} · ${item.market}`, "success");
    closeManualSearch();
  }

  function symbolResolveTargets(input) {
    if (input.id === "journal-symbol") {
      return {
        nameInput: $("journal-name"), currencyInput: $("journal-currency"),
      };
    }
    const row = input.closest(".holding-row");
    const editor = input.closest(".manual-account-editor");
    return {
      nameInput: row?.querySelector('[data-field="name"]'),
      currencyInput: editor?.querySelector('[data-field="currency"]'),
    };
  }

  function symbolResolveHint(input) {
    return input.closest(".symbol-code-field")?.querySelector(".symbol-resolve-hint");
  }

  function clearSymbolResolveHint(input) {
    const hint = symbolResolveHint(input);
    if (!hint) return;
    hint.textContent = "";
    hint.classList.remove("success", "warning");
    hint.hidden = true;
  }

  function setSymbolResolveHint(input, message, state) {
    const hint = symbolResolveHint(input);
    if (!hint) return;
    hint.textContent = message;
    hint.classList.remove("success", "warning");
    hint.classList.add(state);
    hint.hidden = false;
  }

  function invalidateSymbolResolve(input) {
    const state = symbolResolveStates.get(input);
    if (state) {
      window.clearTimeout(state.timer);
      state.sequence += 1;
    }
    clearSymbolResolveHint(input);
  }

  function scheduleSymbolResolve(input) {
    let state = symbolResolveStates.get(input);
    if (!state) {
      state = { timer: null, sequence: 0 };
      symbolResolveStates.set(input, state);
    }
    window.clearTimeout(state.timer);
    const sequence = ++state.sequence;
    const code = input.value.trim();
    if (!code) {
      clearSymbolResolveHint(input);
      return;
    }
    state.timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/stocks/resolve?code=${encodeURIComponent(code)}`);
        const result = await response.json();
        if (sequence !== state.sequence || !input.isConnected || input.value.trim() !== code) return;
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        if (!result.found) {
          setSymbolResolveHint(input, "미등록 코드 · 종목명으로 검색하세요", "warning");
          return;
        }
        const { nameInput, currencyInput } = symbolResolveTargets(input);
        const currentName = nameInput?.value.trim() || "";
        if (nameInput && (!currentName || currentName === nameInput.dataset.autoFilledName)) {
          nameInput.value = result.name || "";
          nameInput.dataset.autoFilledName = nameInput.value;
          if (nameInput.classList.contains("manual-name-search")) {
            nameInput.dataset.selectedName = nameInput.value;
          } else if (input.id === "journal-symbol") {
            selectedJournalIdentity = result;
          }
        }
        input.value = result.symbol || code.toUpperCase();
        if (currencyInput) currencyInput.value = result.currency;
        setSymbolResolveHint(input, `✓ ${result.name} · ${result.market}`, "success");
      } catch (error) {
        if (sequence !== state.sequence || !input.isConnected) return;
        setSymbolResolveHint(input, `코드 확인 실패 · ${error.message}`, "warning");
      }
    }, 300);
  }

  function updateJournalPriceField() {
    const setting = JOURNAL_PRICE_LABELS[$("journal-side").value] || JOURNAL_PRICE_LABELS.OTHER;
    $("journal-price-label").textContent = setting.label;
    $("journal-price-hint").textContent = setting.hint;
    $("journal-price").required = setting.required;
    $("journal-price").placeholder = setting.required ? "필수" : "선택";
  }

  function savedJournalSummary(result, body) {
    const saved = (result.events || []).find((entry) => (
      entry.origin === "manual" && entry.date === body.date
      && entry.account_label === body.account_label && entry.side === body.side
      && String(entry.memo || "") === String(body.memo || "")
    ));
    const row = saved || body;
    const identity = row.symbol ? `${row.name || row.symbol} (${row.symbol})` : (row.name || "종목");
    const quantity = row.quantity === null || row.quantity === undefined ? "" : ` · ${fmt(row.quantity, 6)}주`;
    const price = row.price === null || row.price === undefined ? "" : ` · ${nativeMoney(row.price, row.currency)}`;
    return `저장했습니다 · ${row.date} · ${row.account_label} · ${identity} · ${journalSideLabel(row.side)}${quantity}${price}`;
  }

  function resetJournalForm() {
    $("journal-date").value = today();
    $("journal-account").value = "미래에셋";
    $("journal-symbol").value = "";
    $("journal-name").value = "";
    delete $("journal-name").dataset.autoFilledName;
    $("journal-side").value = "BUY";
    $("journal-currency").value = "KRW";
    $("journal-quantity").value = "";
    $("journal-price").value = "";
    $("journal-memo").value = "";
    selectedJournalIdentity = null;
    ++journalSearchSequence;
    invalidateSymbolResolve($("journal-symbol"));
    closeJournalSearch();
    updateJournalPriceField();
  }

  function renderJournal() {
    const events = journalPayload.events || [];
    const shown = events.slice(0, journalVisibleRows);
    $("journal-rows").innerHTML = shown.length ? shown.map((entry) => {
      const recurring = entry.recurring_like ? '<span class="journal-tag">모으기/소액</span>' : "";
      const manualDelete = entry.origin === "manual" ? `<button type="button" class="text-button delete-journal-entry" data-entry-id="${esc(entry.id)}">삭제</button>` : "";
      const memo = entry.memo ? ` · ${esc(entry.memo)}` : "";
      return `<tr class="${entry.recurring_like ? "journal-recurring" : ""}">
        <td class="num">${esc(entry.date)}</td><td>${esc(entry.account_label)}</td>
        <td><b>${esc(entry.name || entry.symbol || "현금")}</b><div class="muted">${esc(entry.symbol || "")}</div>${recurring}</td>
        <td>${esc(journalSideLabel(entry.side))}</td><td class="num">${fmt(entry.quantity, 6)}</td>
        <td class="num">${nativeMoney(entry.price, entry.currency)}</td><td class="num">${nativeMoney(entry.amount, entry.currency)}</td>
        <td class="num ${valueClass(entry.realized_pnl_est)}">${entry.realized_pnl_est === null || entry.realized_pnl_est === undefined ? "—" : nativeMoney(entry.realized_pnl_est, entry.currency)}</td>
        <td class="journal-basis"><span>${esc(entry.basis || "")}${memo}</span>${manualDelete}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="9" class="unavailable">선택한 기간에 매매일지 항목이 없습니다.</td></tr>`;
    const remaining = Math.max(0, events.length - shown.length);
    $("journal-more").hidden = remaining === 0;
    $("journal-more").textContent = remaining ? `더 보기 (${Math.min(10, remaining)}개)` : "더 보기";
    const summary = journalPayload.summary || {};
    $("journal-summary").innerHTML = `<span>매수 <b>${fmt(summary.buys || 0, 0)}건</b></span><span>매도 <b>${fmt(summary.sells || 0, 0)}건</b></span><span>실현손익 추정 <b>${esc(currencySummary(summary.realized_pnl_est))}</b></span><span>배당 추정 <b>${esc(currencySummary(summary.dividends_est))}</b></span>`;
    const gaps = journalPayload.gaps || [];
    $("journal-gaps").textContent = gaps.length ? `누락 구간 ${gaps.length}개 · 중간 스냅샷이 없는 구간은 매매·배당을 추정하지 않았습니다. · ${journalPayload.note || ""}` : (journalPayload.note || "");
  }

  async function loadJournal() {
    const response = await fetch(`/api/trade-journal?days=${selectedJournalDays}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    journalPayload = result;
    renderJournal();
  }

  function manualAccountHtml(account, accountIndex) {
    const positions = account.positions || [];
    return `<div class="manual-account-editor" data-account-index="${accountIndex}">
      <div class="editor-head"><b>${esc(account.label || `새 계좌 ${accountIndex + 1}`)}</b><button type="button" class="text-button remove-account">계좌 삭제</button></div>
      <input type="hidden" data-field="source_id" value="${esc(account.source_id || "")}">
      <div class="input-form-grid manual-account-fields">
        <label class="field"><span>계좌 이름</span><input data-field="label" value="${esc(account.label || "")}" placeholder="예: 미래에셋"></label>
        <label class="field"><span>통화</span><select data-field="currency"><option value="KRW" ${account.currency !== "USD" ? "selected" : ""}>KRW</option><option value="USD" ${account.currency === "USD" ? "selected" : ""}>USD</option></select></label>
        <label class="field"><span>현금</span><input data-field="cash" type="number" min="0" step="any" value="${esc(account.cash ?? 0)}"></label>
        <label class="field"><span>기준일</span><input data-field="snapshot_date" type="date" value="${esc(account.snapshot_date || today())}"></label>
      </div>
      <div class="holding-head"><span>보유 종목</span><button type="button" class="button add-holding">종목 추가</button></div>
      <div class="holding-rows">${positions.map((position, positionIndex) => `<div class="holding-row input-form-grid" data-position-index="${positionIndex}">
        <div class="manual-search-field">
          <label class="field"><span>종목명</span><input class="manual-name-search" data-field="name" value="${esc(position.name || "")}" maxlength="80" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="manual-search-results-${accountIndex}-${positionIndex}" placeholder="이름만 입력하거나 검색 결과 선택"></label>
          <div class="journal-search-results manual-search-results" id="manual-search-results-${accountIndex}-${positionIndex}" role="listbox" hidden></div>
        </div>
        <label class="field symbol-code-field"><span>종목코드·티커</span><input class="manual-code-input" data-field="ticker" value="${esc(position.ticker || "")}" placeholder="코드 입력 시 종목명 확인"><small class="symbol-resolve-hint" role="status" aria-live="polite" hidden></small></label>
        <label class="field"><span>수량</span><input data-field="quantity" type="number" min="0" step="any" value="${esc(position.quantity ?? "")}"></label>
        <label class="field"><span>평균단가</span><input data-field="average_cost" type="number" min="0" step="any" value="${esc(position.average_cost ?? "")}"></label>
        <label class="field"><span>수동 현재가</span><input data-field="manual_price" type="number" min="0" step="any" value="${esc(position.manual_price ?? "")}" placeholder="미국 종목만" title="한국 종목은 최신 종가를 자동으로 씁니다"></label>
        <button type="button" class="text-button remove-holding">삭제</button>
        ${position.included === false ? `<div class="holding-warning">평가 불가 · ${esc(position.note || "가격 없음")}</div>` : ""}
      </div>`).join("") || `<div class="unavailable">종목이 없습니다. 현금만 있는 계좌도 저장할 수 있습니다.</div>`}</div>
    </div>`;
  }

  function renderManualAccounts() {
    $("manual-accounts-form").innerHTML = manualAccounts.length
      ? manualAccounts.map(manualAccountHtml).join("")
      : `<div class="unavailable">수동 계좌가 없습니다. ‘계좌 추가’로 시작하세요.</div>`;
  }

  function collectManualAccounts() {
    return [...document.querySelectorAll(".manual-account-editor")].map((editor) => ({
      source_id: editor.querySelector('[data-field="source_id"]').value || undefined,
      label: editor.querySelector('[data-field="label"]').value.trim(),
      account_kind: "GENERAL",
      currency: editor.querySelector('[data-field="currency"]').value,
      cash: Number(editor.querySelector('[data-field="cash"]').value || 0),
      snapshot_date: editor.querySelector('[data-field="snapshot_date"]').value,
      positions: [...editor.querySelectorAll(".holding-row")].map((row) => ({
        ticker: row.querySelector('[data-field="ticker"]').value.trim(),
        name: row.querySelector('[data-field="name"]').value.trim(),
        quantity: Number(row.querySelector('[data-field="quantity"]').value),
        average_cost: row.querySelector('[data-field="average_cost"]').value === "" ? null : Number(row.querySelector('[data-field="average_cost"]').value),
        manual_price: row.querySelector('[data-field="manual_price"]').value === "" ? null : Number(row.querySelector('[data-field="manual_price"]').value),
      })),
    }));
  }

  function netWorthRowHtml(row, index, kind) {
    const options = payload.net_worth.options || {};
    const classOptions = kind === "asset" ? options.asset_classes : options.liability_classes;
    const classField = kind === "asset" ? "asset_class" : "liability_class";
    return `<div class="net-worth-input-row input-form-grid" data-kind="${kind}" data-index="${index}">
      <label class="field"><span>항목명</span><input data-field="name" value="${esc(row.name || "")}"></label>
      <label class="field"><span>분류</span><select data-field="${classField}">${optionHtml(classOptions, row[classField])}</select></label>
      <label class="field"><span>금액(원)</span><input data-field="amount_krw" type="number" min="0" step="1" value="${esc(row.amount_krw ?? "")}"></label>
      <label class="field"><span>평가일</span><input data-field="valuation_date" type="date" value="${esc(row.valuation_date || $("net-worth-date").value || today())}"></label>
      <label class="field"><span>평가 방법</span><select data-field="valuation_method">${optionHtml(options.valuation_methods, row.valuation_method || "USER_DECLARED")}</select></label>
      <label class="field"><span>출처</span><select data-field="valuation_source">${optionHtml(options.valuation_sources, row.valuation_source || "USER_LOCAL")}</select></label>
      <label class="field"><span>불확실성</span><select data-field="uncertainty">${optionHtml(options.uncertainties, row.uncertainty || "EXACT")}</select></label>
      <label class="field"><span>명의</span><select data-field="holder_role">${optionHtml(options.holder_roles, row.holder_role || "SELF")}</select></label>
      <label class="field"><span>평가 상태</span><select data-field="valuation_status">${optionHtml(options.valuation_statuses, row.valuation_status || "CURRENT")}</select></label>
      <button type="button" class="text-button remove-net-worth-row">삭제</button>
    </div>`;
  }

  function renderNetWorthForm() {
    $("asset-form-rows").innerHTML = assetRows.length ? assetRows.map((row, index) => netWorthRowHtml(row, index, "asset")).join("") : `<div class="unavailable">자산 항목이 없습니다.</div>`;
    $("liability-form-rows").innerHTML = liabilityRows.length ? liabilityRows.map((row, index) => netWorthRowHtml(row, index, "liability")).join("") : `<div class="unavailable">부채 항목이 없습니다.</div>`;
  }

  function collectNetWorthRows(kind) {
    const classField = kind === "asset" ? "asset_class" : "liability_class";
    return [...document.querySelectorAll(`.net-worth-input-row[data-kind="${kind}"]`)].map((row) => ({
      name: row.querySelector('[data-field="name"]').value.trim(),
      [classField]: row.querySelector(`[data-field="${classField}"]`).value,
      amount_krw: row.querySelector('[data-field="amount_krw"]').value === "" ? null : Number(row.querySelector('[data-field="amount_krw"]').value),
      valuation_date: row.querySelector('[data-field="valuation_date"]').value,
      valuation_method: row.querySelector('[data-field="valuation_method"]').value,
      valuation_source: row.querySelector('[data-field="valuation_source"]').value,
      uncertainty: row.querySelector('[data-field="uncertainty"]').value,
      holder_role: row.querySelector('[data-field="holder_role"]').value,
      valuation_status: row.querySelector('[data-field="valuation_status"]').value,
    }));
  }

  function renderTimeline() {
    const host = $("net-worth-chart");
    const points = (payload.net_worth.timeline || []).filter((point) => point.v !== null && point.v !== undefined);
    $("timeline-note").textContent = payload.net_worth.timeline_note || "";
    window.SIChart.renderLineChart(host, points, {
      benchmark: payload.net_worth.benchmark || [], ariaLabel: "순자산과 KOSPI 동기간 추이",
      emptyMessage: `실제 순자산 관측이 2개 이상이면 선이 표시됩니다.${points.length === 1 ? ` · 현재 ${money(points[0].v)}` : ""}`,
    });
  }

  function renderBreakdown() {
    const netWorth = payload.net_worth || {};
    $("breakdown-asof").textContent = netWorth.as_of ? `${netWorth.as_of_label || shortDate(netWorth.as_of)} 기준` : "";
    $("net-worth-breakdown").innerHTML = (netWorth.breakdown || []).length ? netWorth.breakdown.map((row) => `<div class="breakdown-row">
      <span><i class="${row.kind}"></i>${esc(row.name)}${row.complete ? "" : ' <small class="muted">미완전</small>'}</span>
      <b class="num ${row.kind === "liability" ? "down" : ""}">${row.kind === "liability" ? "−" : ""}${money(row.value_krw)}</b>
    </div>`).join("") : `<div class="unavailable">저장된 자산·부채 구성이 없습니다.</div>`;
  }

  function renderWriteAudit() {
    const attempts = payload.recent_write_attempts || [];
    const pathLabels = {
      "/api/net-worth": "순자산", "/api/manual/accounts": "수동 계좌",
      "/api/manual/dividends": "배당 기록",
      "/api/trade-journal/manual": "매매일지", "/api/cash-flows": "입출금",
      "/api/watchlists": "관심목록", "/api/watchlist/items": "관심종목",
      "/api/watchlist/items/move": "관심종목 순서", "/api/watch-conditions": "관심조건",
    };
    const countLabels = {
      accounts: "계좌", positions: "종목", assets: "자산", liabilities: "부채",
      entries: "항목", lists: "목록", items: "종목", conditions: "조건",
    };
    $("write-audit-list").innerHTML = attempts.length ? attempts.map((entry) => {
      const counts = Object.entries(entry.row_counts || {}).map(([key, value]) => `${countLabels[key] || key} ${value}`).join(" · ");
      const status = entry.status === 403 ? "403 · 폰에서는 저장 불가"
        : entry.status === 400 ? "400 · 항목 필드 오류"
          : entry.status === 200 ? "200 · 저장 완료" : `${entry.status} · 저장 오류`;
      return `<div class="write-audit-row">
        <span class="num">${esc(shortDate(entry.ts))}</span>
        <b>${esc(pathLabels[entry.path] || entry.path)}</b>
        <span>${entry.client_kind === "loopback" ? "PC 직접 접속" : "폰·중계 접속"}</span>
        <span class="${entry.status === 200 ? "up" : "down"}">${esc(status)}</span>
        <span class="muted">${esc(counts || "행 변경 없음")}</span>
      </div>`;
    }).join("") : `<div class="unavailable">기록된 저장 시도가 없습니다.</div>`;
  }

  async function refreshWriteAudit() {
    const response = await fetch("/api/account");
    if (!response.ok) return;
    const latest = await response.json();
    payload.recent_write_attempts = latest.recent_write_attempts || [];
    renderWriteAudit();
  }

  function hydrateState() {
    hydrateReturnPeriod();
    manualAccounts = ((payload.manual_accounts || {}).accounts || []).map((account) => ({
      ...account, positions: account.valued_positions || account.positions || [],
    }));
    const latest = (payload.net_worth || {}).latest;
    assetRows = latest ? latest.assets.map((row) => ({ ...row })) : [];
    liabilityRows = latest ? latest.liabilities.map((row) => ({ ...row })) : [];
    $("net-worth-date").value = latest ? latest.as_of_date : today();
    $("dividend-date").value = today();
  }

  function renderAll() {
    renderSummary(); renderPerformance(); renderHoldings(); renderDividends(); renderCashFlows(); renderJournal(); renderManualAccounts(); renderNetWorthForm(); renderWriteAudit();
    $("account-safety").textContent = payload.safety_note || "";
  }

  async function refresh() {
    const [accountResponse, journalResponse] = await Promise.all([
      fetch("/api/account"), fetch(`/api/trade-journal?days=${selectedJournalDays}`),
    ]);
    if (!accountResponse.ok) throw new Error(`HTTP ${accountResponse.status}`);
    payload = await accountResponse.json();
    const journalResult = await journalResponse.json();
    if (!journalResponse.ok) throw new Error(journalResult.error || `HTTP ${journalResponse.status}`);
    journalPayload = journalResult;
    hydrateState(); renderAll();
  }

  async function postJson(url, body, statusHost) {
    await writeJson(url, "POST", body, statusHost);
    await refresh();
  }

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const searchOption = target.closest(".journal-search-option");
    const manualSearchOption = target.closest(".manual-search-option");
    if (manualSearchOption) {
      selectManualIdentity(manualSearchMatches[Number(manualSearchOption.dataset.searchIndex)]);
    } else if (searchOption) {
      selectJournalIdentity(journalSearchMatches[Number(searchOption.dataset.searchIndex)]);
    } else if (target.id === "add-manual-account") {
      manualAccounts.push({ label: "", currency: "KRW", cash: 0, snapshot_date: today(), positions: [] }); renderManualAccounts();
    } else if (target.classList.contains("remove-account")) {
      manualAccounts = collectManualAccounts();
      manualAccounts.splice(Number(target.closest(".manual-account-editor").dataset.accountIndex), 1); renderManualAccounts();
    } else if (target.classList.contains("add-holding")) {
      manualAccounts = collectManualAccounts();
      const index = Number(target.closest(".manual-account-editor").dataset.accountIndex);
      manualAccounts[index].positions.push({ ticker: "", name: "", quantity: "", average_cost: null, manual_price: null }); renderManualAccounts();
    } else if (target.classList.contains("remove-holding")) {
      manualAccounts = collectManualAccounts();
      const accountEditor = target.closest(".manual-account-editor");
      const positionRow = target.closest(".holding-row");
      manualAccounts[Number(accountEditor.dataset.accountIndex)].positions.splice(Number(positionRow.dataset.positionIndex), 1); renderManualAccounts();
    } else if (target.id === "add-asset") {
      assetRows = collectNetWorthRows("asset"); liabilityRows = collectNetWorthRows("liability");
      assetRows.push({ name: "", asset_class: "CASH", amount_krw: "", valuation_date: $("net-worth-date").value, valuation_method: "STATEMENT_VALUE", valuation_source: "OFFICIAL_STATEMENT", uncertainty: "EXACT", holder_role: "SELF", valuation_status: "CURRENT" }); renderNetWorthForm();
    } else if (target.id === "add-liability") {
      assetRows = collectNetWorthRows("asset"); liabilityRows = collectNetWorthRows("liability");
      liabilityRows.push({ name: "", liability_class: "MORTGAGE", amount_krw: "", valuation_date: $("net-worth-date").value, valuation_method: "STATEMENT_VALUE", valuation_source: "OFFICIAL_STATEMENT", uncertainty: "EXACT", holder_role: "SELF", valuation_status: "CURRENT" }); renderNetWorthForm();
    } else if (target.classList.contains("remove-net-worth-row")) {
      assetRows = collectNetWorthRows("asset"); liabilityRows = collectNetWorthRows("liability");
      const row = target.closest(".net-worth-input-row");
      (row.dataset.kind === "asset" ? assetRows : liabilityRows).splice(Number(row.dataset.index), 1); renderNetWorthForm();
    } else if (target.id === "net-worth-overlay") {
      netWorthOverlayVisible = !netWorthOverlayVisible; renderPerformance();
    } else if (target.closest("#return-range button")) {
      document.querySelectorAll("#return-range button").forEach((button) => button.classList.remove("on"));
      target.classList.add("on"); selectedReturnWindow = target.dataset.v; renderPerformance();
    } else if (target.closest("#holding-account-filter button")) {
      const button = target.closest("button");
      document.querySelectorAll("#holding-account-filter button").forEach((item) => item.classList.remove("on"));
      button.classList.add("on"); holdingAccountFilter = button.dataset.account; renderHoldings();
    } else if (target.closest("#holding-currency-filter button")) {
      const button = target.closest("button");
      document.querySelectorAll("#holding-currency-filter button").forEach((item) => item.classList.remove("on"));
      button.classList.add("on"); holdingCurrencyFilter = button.dataset.currency; renderHoldings();
    } else if (target.closest(".holdings-table th button")) {
      const button = target.closest("button");
      holdingSortDirection = holdingSortKey === button.dataset.sort && holdingSortDirection === "desc" ? "asc" : "desc";
      holdingSortKey = button.dataset.sort; renderHoldings();
    } else if (target.closest("#allocation-tabs button")) {
      const button = target.closest("button");
      document.querySelectorAll("#allocation-tabs button").forEach((item) => item.classList.remove("on"));
      button.classList.add("on"); allocationGroup = button.dataset.group; renderAllocation();
    } else if (target.id === "open-dividend-form") {
      $("dividend-dialog").showModal();
    } else if (target.closest("#journal-range button")) {
      document.querySelectorAll("#journal-range button").forEach((button) => button.classList.remove("on"));
      target.classList.add("on"); selectedJournalDays = Number(target.dataset.days); journalVisibleRows = 10;
      loadJournal().catch((error) => { $("journal-status").textContent = `조회 실패 · ${error.message}`; });
    } else if (target.id === "journal-more") {
      journalVisibleRows += 10; renderJournal();
    } else if (target.classList.contains("edit-cash-flow")) {
      const entry = ((payload.cash_flows || {}).entries || []).find((row) => row.id === target.dataset.flowId);
      if (entry) {
        $("cash-flow-id").value = entry.id; $("cash-flow-date").value = entry.date;
        $("cash-flow-amount").value = entry.amount_krw; $("cash-flow-account").value = entry.account;
        $("cash-flow-memo").value = entry.memo || ""; $("cancel-cash-flow").hidden = false;
      }
    } else if (target.classList.contains("delete-cash-flow")) {
      (async () => {
        try {
          await writeJson("/api/cash-flows", "DELETE", { id: target.dataset.flowId }, "cash-flow-status", "삭제 완료");
          resetCashFlowForm(); await refresh();
        } catch (error) { if (!error.handled) setWriteStatus("cash-flow-status", `삭제 실패 · ${error.message}`); }
      })();
    } else if (target.id === "cancel-cash-flow") {
      resetCashFlowForm(); $("cash-flow-status").textContent = "";
    } else if (target.classList.contains("delete-journal-entry")) {
      (async () => {
        try {
          await writeJson("/api/trade-journal/manual", "DELETE", { id: target.dataset.entryId }, "journal-status", "삭제 완료");
          await refresh();
        } catch (error) { if (!error.handled) setWriteStatus("journal-status", `삭제 실패 · ${error.message}`); }
      })();
    }
    if (!target.closest(".journal-search-field")) closeJournalSearch();
    if (!target.closest(".manual-search-field")) closeManualSearch();
  });

  document.addEventListener("DOMContentLoaded", async () => {
    $("manual-accounts-form").addEventListener("input", (event) => {
      if (event.target instanceof HTMLInputElement && event.target.classList.contains("manual-name-search")) {
        scheduleManualSearch(event.target);
      } else if (event.target instanceof HTMLInputElement && event.target.classList.contains("manual-code-input")) {
        invalidateSymbolResolve(event.target);
      }
    });
    ["blur", "change"].forEach((eventName) => {
      $("manual-accounts-form").addEventListener(eventName, (event) => {
        if (event.target instanceof HTMLInputElement && event.target.classList.contains("manual-code-input")) {
          scheduleSymbolResolve(event.target);
        }
      }, eventName === "blur");
    });
    $("manual-accounts-form").addEventListener("keydown", (event) => {
      if (!(event.target instanceof HTMLInputElement)) return;
      if (event.target.classList.contains("manual-code-input") && event.key === "Enter") {
        event.preventDefault(); scheduleSymbolResolve(event.target);
      } else if (event.target.classList.contains("manual-name-search")) {
        if (event.key === "Escape") closeManualSearch();
        if (event.key === "Enter" && manualSearchMatches.length) {
          event.preventDefault(); selectManualIdentity(manualSearchMatches[0]);
        }
      }
    });
    $("journal-name").addEventListener("input", scheduleJournalSearch);
    $("journal-name").addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeJournalSearch();
      if (event.key === "Enter" && journalSearchMatches.length) {
        event.preventDefault();
        selectJournalIdentity(journalSearchMatches[0]);
      }
    });
    const journalSymbolInput = $("journal-symbol");
    journalSymbolInput.addEventListener("input", () => invalidateSymbolResolve(journalSymbolInput));
    ["blur", "change"].forEach((eventName) => {
      journalSymbolInput.addEventListener(eventName, () => scheduleSymbolResolve(journalSymbolInput));
    });
    journalSymbolInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault(); scheduleSymbolResolve(journalSymbolInput);
      }
    });
    $("journal-side").addEventListener("change", updateJournalPriceField);
    $("save-dividend").addEventListener("click", async () => {
      try {
        await postJson("/api/manual/dividends", {
          date: $("dividend-date").value,
          symbol: $("dividend-symbol").value.trim(),
          amount_krw: Number($("dividend-amount").value),
          account: $("dividend-account").value.trim(),
        }, "dividend-status");
        $("dividend-symbol").value = "";
        $("dividend-amount").value = "";
        $("dividend-account").value = "";
        $("dividend-dialog").close();
      } catch (error) {
        if (!error.handled) setWriteStatus("dividend-status", `저장 실패 · ${error.message}`);
      }
    });
    $("save-manual-accounts").addEventListener("click", async () => {
      try { await postJson("/api/manual/accounts", { schema_version: 1, accounts: collectManualAccounts() }, "manual-status"); }
      catch (error) { if (!error.handled) setWriteStatus("manual-status", `저장 실패 · ${error.message}`); }
    });
    $("save-net-worth").addEventListener("click", async () => {
      try {
        await postJson("/api/net-worth", { as_of_date: $("net-worth-date").value, assets: collectNetWorthRows("asset"), liabilities: collectNetWorthRows("liability") }, "net-worth-status");
        $("save-net-worth").classList.remove("attention");
      } catch (error) {
        if (!error.handled) setWriteStatus("net-worth-status", `저장 실패 · ${error.message}`);
      }
    });
    // Rows added or edited in the 순자산 form are NOT persisted until 새 스냅샷 저장 is pressed —
    // say so, loudly, the moment anything changes.
    const markNetWorthDirty = () => {
      $("net-worth-status").textContent = "저장 안 됨 · 아래 '새 스냅샷 저장'을 눌러야 순자산에 반영됩니다";
      $("save-net-worth").classList.add("attention");
    };
    ["asset-form-rows", "liability-form-rows"].forEach((id) => {
      $(id).addEventListener("input", markNetWorthDirty);
      $(id).addEventListener("change", markNetWorthDirty);
    });
    ["add-asset", "add-liability"].forEach((id) => $(id).addEventListener("click", () => setTimeout(markNetWorthDirty, 0)));
    document.addEventListener("click", (event) => {
      if (event.target instanceof HTMLElement && event.target.classList.contains("remove-net-worth-row")) setTimeout(markNetWorthDirty, 0);
    });
    $("save-cash-flow").addEventListener("click", async () => {
      try {
        const body = { date: $("cash-flow-date").value, amount_krw: Number($("cash-flow-amount").value), account: $("cash-flow-account").value.trim(), memo: $("cash-flow-memo").value.trim() };
        if ($("cash-flow-id").value) body.id = $("cash-flow-id").value;
        await postJson("/api/cash-flows", body, "cash-flow-status"); resetCashFlowForm();
      } catch (error) { if (!error.handled) setWriteStatus("cash-flow-status", `저장 실패 · ${error.message}`); }
    });
    $("save-journal-entry").addEventListener("click", async () => {
      try {
        const body = {
          date: $("journal-date").value, account_label: $("journal-account").value.trim(),
          symbol: $("journal-symbol").value.trim(), name: $("journal-name").value.trim(),
          side: $("journal-side").value, quantity: Number($("journal-quantity").value),
          price: $("journal-price").value === "" ? null : Number($("journal-price").value), currency: $("journal-currency").value,
          memo: $("journal-memo").value.trim(),
        };
        const result = await writeJson("/api/trade-journal/manual", "POST", body, "journal-status");
        const success = savedJournalSummary(result, body);
        resetJournalForm(); journalVisibleRows = 10; await refresh(); setWriteStatus("journal-status", `200 · 저장 완료 · ${success.replace(/^저장했습니다 · /, "")}`);
      } catch (error) { if (!error.handled) setWriteStatus("journal-status", `저장 실패 · ${error.message}`); }
    });
    resetCashFlowForm();
    resetJournalForm();
    try { await refresh(); }
    catch (error) { $("account-safety").textContent = `계좌 화면을 불러오지 못했습니다. · ${error.message}`; }
  });
})();
