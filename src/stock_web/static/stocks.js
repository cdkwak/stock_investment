/* Local stocks page: retained reads and loopback-only preference writes. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const number = (value, digits = 1) => value === null || value === undefined
    ? "—" : Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const pct = (value, digits = 1) => value === null || value === undefined
    ? "—" : `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}%`;
  const tone = (value) => value === null || value === undefined ? "muted" : value > 0 ? "up" : value < 0 ? "down" : "muted";
  const arrow = (value) => value > 0 ? "▲" : value < 0 ? "▼" : "";
  const uid = () => window.crypto && crypto.randomUUID ? crypto.randomUUID() : `condition-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const compact = (value) => window.SIChart ? SIChart.formatCompactKorean(value) : number(value, 0);
  const signedEok = (value, digits = 1) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const numeric = Number(value) / 1e8;
    const sign = numeric > 0 ? "+" : numeric < 0 ? "−" : "";
    return `${sign}${Math.abs(numeric).toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}억`;
  };

  let page = { watchlists: { lists: [] }, conditions: { conditions: [] }, table: [] };
  let selectedListId = "favorites";
  let conditions = [];
  let selectedSearch = null;
  let flashIdentity = null;
  let scannerResult = null;
  let scannerElapsed = 0;
  let sparklines = {};
  let selectedIdentity = null;
  let selectedDetail = null;
  let loadedChart = null;
  let priceChart = null;
  let candleSeries = null;
  let dynamicChartSeries = [];
  let chartLoadSequence = 0;
  let searchTimer = null;
  let sidebarSearchSequence = 0;

  const indicatorColors = {
    ma5: "#4a3aa7", ma20: "#2a78d6", ma60: "#eb6834",
    ma120: "#1baf7a", rsi14: "#8b4c9e", volume: "#8a847b",
  };
  const indicatorLabels = {
    ma5: "MA5", ma20: "MA20", ma60: "MA60", ma120: "MA120",
    rsi14: "RSI14", volume: "거래량",
  };
  const indicatorDefaults = {
    ma5: { enabled: true, placement: "overlay" },
    ma20: { enabled: true, placement: "overlay" },
    ma60: { enabled: true, placement: "overlay" },
    ma120: { enabled: true, placement: "overlay" },
    rsi14: { enabled: true, placement: "panel" },
    volume: { enabled: false, placement: "panel" },
  };
  let indicatorState = loadIndicatorState();

  function loadIndicatorState() {
    try {
      const saved = JSON.parse(localStorage.getItem("stock-web-stock-indicators-v1"));
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        return Object.fromEntries(Object.entries(indicatorDefaults).map(([key, fallback]) => [key, {
          enabled: Boolean(saved[key] ? saved[key].enabled : fallback.enabled),
          placement: saved[key] && ["overlay", "panel"].includes(saved[key].placement)
            ? saved[key].placement : fallback.placement,
        }]));
      }
    } catch (_error) { /* optional preference */ }
    return JSON.parse(JSON.stringify(indicatorDefaults));
  }

  function saveIndicatorState() {
    try { localStorage.setItem("stock-web-stock-indicators-v1", JSON.stringify(indicatorState)); }
    catch (_error) { /* optional preference */ }
  }

  function syncIndicatorMenu() {
    document.querySelectorAll("#stock-indicator-menu [data-indicator]").forEach((row) => {
      const state = indicatorState[row.dataset.indicator];
      row.querySelector('input[type="checkbox"]').checked = Boolean(state && state.enabled);
      row.querySelector("select").value = state ? state.placement : "overlay";
    });
  }

  function intervalLabel(key) {
    const interval = currentInterval();
    const suffix = interval === "week" ? "주봉" : interval === "month" ? "월봉" : "";
    return suffix ? `${indicatorLabels[key]} (${suffix})` : indicatorLabels[key];
  }

  function changeIndicator(key, changes) {
    if (!indicatorState[key]) return;
    const wasEnabled = indicatorState[key].enabled;
    indicatorState[key] = { ...indicatorState[key], ...changes };
    saveIndicatorState();
    syncIndicatorMenu();
    renderLoadedChart();
    if (!wasEnabled && indicatorState[key].enabled && selectedIdentity) {
      loadChart(selectedIdentity.symbol).catch((error) => {
        $("stocks-safety").textContent = `차트 지표를 불러오지 못했습니다. · ${error.message}`;
      });
    }
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }

  async function mutate(url, body, method = "POST") {
    return requestJson(url, {
      method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  }

  function currentList() {
    return (page.watchlists.lists || []).find((item) => item.list_id === selectedListId)
      || (page.watchlists.lists || [])[0];
  }

  function selectedWatchRow() {
    if (!selectedIdentity) return null;
    return (page.table || []).find((row) => row.symbol === selectedIdentity.symbol && row.market === selectedIdentity.market)
      || (page.table || []).find((row) => row.symbol === selectedIdentity.symbol) || null;
  }

  function selectedInCurrentList() {
    const list = currentList();
    return Boolean(list && selectedIdentity && (list.items || []).some((item) => item.symbol === selectedIdentity.symbol && item.market === selectedIdentity.market));
  }

  function formatPriceValue(value, market) {
    if (value === null || value === undefined) return "—";
    const digits = market === "US ETF" ? 2 : 0;
    return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  const price = (row) => !row.price_available ? "—" : formatPriceValue(row.price, row.market);

  function sparklineSvg(values) {
    const numeric = (values || []).map(Number).filter(Number.isFinite);
    if (numeric.length < 2) return '<span class="sidebar-spark-empty">—</span>';
    const width = 90, height = 24, low = Math.min(...numeric), high = Math.max(...numeric);
    const span = high - low || 1;
    const points = numeric.map((value, index) => `${(index / (numeric.length - 1) * width).toFixed(1)},${(height - 2 - (value - low) / span * (height - 4)).toFixed(1)}`).join(" ");
    const color = numeric[numeric.length - 1] > numeric[0] ? "#c0392b" : numeric[numeric.length - 1] < numeric[0] ? "#2b62c0" : "#1f1d1a";
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="최근 ${numeric.length}개 종가 추이"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.4" vector-effect="non-scaling-stroke"></polyline></svg>`;
  }

  function renderSidebar() {
    const rows = page.table || [];
    $("sidebar-count").textContent = `${rows.length}개`;
    $("watchlist-sidebar").innerHTML = rows.length ? rows.map((row) => {
      const isUsEtf = row.market === "US ETF";
      const name = isUsEtf ? row.symbol : row.name;
      const subtitle = isUsEtf ? `${row.name} · ${row.market}` : `${row.symbol} · ${row.market}`;
      const flags = row.condition_matches || [];
      const selected = selectedIdentity && selectedIdentity.symbol === row.symbol
        && selectedIdentity.market === row.market && row.list_id === selectedListId;
      return `<button type="button" role="listitem" class="watchlist-sidebar-item${selected ? " on" : ""}" data-symbol="${esc(row.symbol)}" data-market="${esc(row.market)}" data-list-id="${esc(row.list_id)}">
        <span class="sidebar-item-top"><span><b>${esc(name)}</b><small>${esc(subtitle)}</small></span><span class="sidebar-quote"><b>${price(row)}</b><small class="${tone(row.change_pct)}">${arrow(row.change_pct)} ${pct(row.change_pct)}</small></span></span>
        <span class="sidebar-item-bottom">${sparklineSvg(sparklines[row.symbol])}<span class="sidebar-flags">${flags.map((item) => `<i>${esc(item.name)}</i>`).join("")}</span></span>
      </button>`;
    }).join("") : '<div class="unavailable sidebar-empty">관심종목이 없습니다.<br>위 검색에서 상세를 열 수 있습니다.</div>';
  }

  function renderWatchlistEditor() {
    const lists = page.watchlists.lists || [];
    if (!lists.some((item) => item.list_id === selectedListId) && lists.length) selectedListId = lists[0].list_id;
    $("watchlist-select").innerHTML = lists.map((item) => `<option value="${esc(item.list_id)}">${esc(item.name)} (${(item.items || []).length})</option>`).join("");
    $("watchlist-select").value = selectedListId;
    const list = currentList();
    $("watchlist-name").value = list ? list.name : "";
    $("watchlist-items").innerHTML = list && list.items.length ? list.items.map((item, index) => `
      <div class="watchlist-edit-row${flashIdentity === `${item.market}:${item.symbol}` ? " flash-new" : ""}" data-market="${esc(item.market)}" data-symbol="${esc(item.symbol)}">
        <span><b>${esc(item.name)}</b><small>${esc(item.symbol)} · ${esc(item.market)}</small></span>
        <span class="watchlist-row-actions">
          <button class="text-button move-watch-item" data-offset="-1" aria-label="${esc(item.name)} 위로 이동" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="text-button move-watch-item" data-offset="1" aria-label="${esc(item.name)} 아래로 이동" ${index === list.items.length - 1 ? "disabled" : ""}>↓</button>
          <button class="text-button remove-watch-item" aria-label="${esc(item.name)} 관심목록에서 삭제">삭제</button>
        </span>
      </div>`).join("") : `<div class="unavailable">이 목록에는 아직 종목이 없습니다.</div>`;
  }

  function renderWatchlistTable() {
    const rows = page.table || [];
    const rsiHeader = $("watchlist-table-rows").closest("table").querySelector("thead th:nth-child(8)");
    if (rsiHeader) { rsiHeader.textContent = "RSI14"; rsiHeader.title = "Wilder 지수이동평균 방식"; }
    $("watchlist-table-rows").innerHTML = rows.length ? rows.map((row) => `<tr class="${flashIdentity === `${row.market}:${row.symbol}` ? "flash-new" : ""}">
      <td><button type="button" class="text-button open-stock-detail stock-name-button" title="${esc(row.name)}" data-symbol="${esc(row.symbol)}" data-market="${esc(row.market)}"><b>${esc(row.name)}</b></button>${row.flag ? `<span class="flag stocks-table-inline-flag">${esc(row.flag)}</span>` : ""}</td>
      <td class="num">${esc(row.symbol)}</td>
      <td class="num">${price(row)}${row.price_available ? "" : `<small>${esc(row.unavailable_reason)}</small>`}</td>
      <td class="num ${tone(row.change_pct)}">${pct(row.change_pct)}</td>
      <td class="num ${tone(row.ma5_pct)}">${pct(row.ma5_pct)}</td>
      <td class="num ${tone(row.ma20_pct)}">${pct(row.ma20_pct)}</td>
      <td class="num ${tone(row.ma60_pct)}">${pct(row.ma60_pct)}</td>
      <td class="num">${number(row.rsi14, 1)}</td>
      <td class="num ${tone(row.drawdown_pct)}">${pct(row.drawdown_pct)}</td>
      <td class="num">${row.volume20_multiple === null || row.volume20_multiple === undefined ? "—" : `${number(row.volume20_multiple, 2)}×`}</td>
      <td class="stocks-table-condition">${row.flag ? `<span class="flag">${esc(row.flag)}</span>` : "—"}</td>
      <td><button type="button" class="button chart-link open-stock-detail" data-symbol="${esc(row.symbol)}" data-market="${esc(row.market)}">상세</button></td>
    </tr>`).join("") : `<tr><td colspan="12" class="unavailable">관심종목이 없습니다.</td></tr>`;
  }

  const fieldOptions = [
    ["rsi14", "RSI14"], ["disp60_pct", "60일선 대비 %"], ["drawdown_pct", "52주 고점 대비 낙폭"],
    ["ma20_pct", "20일선 대비 %"], ["change_pct", "등락률"],
  ];
  function options(items, selected) {
    return items.map(([value, label]) => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`).join("");
  }

  function renderConditions() {
    $("condition-rows").innerHTML = conditions.length ? conditions.map((item, index) => `<tr data-index="${index}" data-id="${esc(item.id)}">
      <td><input data-field="name" aria-label="조건 이름" maxlength="60" value="${esc(item.name)}"></td>
      <td><select data-field="field" aria-label="조건 지표">${options(fieldOptions, item.field)}</select></td>
      <td><select data-field="op" aria-label="조건 연산자">${options([["<=", "≤"], [">=", "≥"]], item.op)}</select></td>
      <td><input class="num" data-field="value" aria-label="조건 값" type="number" step="any" value="${esc(item.value)}"></td>
      <td><select data-field="scope" aria-label="조건 적용 범위">${options([["watchlist", "관심종목"], ["universe", "전체시장"]], item.scope)}</select></td>
      <td><button type="button" class="text-button remove-condition" aria-label="${esc(item.name || "새 조건")} 삭제">삭제</button></td>
    </tr>`).join("") : `<tr><td colspan="6" class="unavailable">설정된 조건이 없습니다. 조건을 추가하면 도달 종목만 표시됩니다.</td></tr>`;
  }

  function collectConditions() {
    return [...$("condition-rows").querySelectorAll("tr[data-index]")].map((row) => ({
      id: row.dataset.id || uid(), name: row.querySelector('[data-field="name"]').value.trim(),
      field: row.querySelector('[data-field="field"]').value,
      op: row.querySelector('[data-field="op"]').value,
      value: Number(row.querySelector('[data-field="value"]').value),
      scope: row.querySelector('[data-field="scope"]').value,
    }));
  }

  function renderSearch(payload, hostId = "stock-search-results", detailMode = false) {
    const matches = payload.matches || [];
    $(hostId).innerHTML = matches.length ? matches.map((item) => `
      <button type="button" class="${detailMode ? "sidebar-search-result" : "search-result"}" data-market="${esc(item.market)}" data-symbol="${esc(item.symbol)}" data-name="${esc(item.name)}">
        <b>${esc(item.name)}</b><span>${esc(item.symbol)} · ${esc(item.market)} · ${esc(item.security_type)}</span>
      </button>`).join("") : `<div class="unavailable">${esc(payload.reason || "검색 결과가 없습니다.")}</div>`;
  }

  function renderSelectedSearch() {
    const list = currentList();
    $("add-selected-stock").disabled = !selectedSearch || !list;
    $("stock-selection").textContent = selectedSearch && list
      ? `선택: ${selectedSearch.name} ${selectedSearch.symbol} → 목록 '${list.name}'에 추가`
      : "검색 결과에서 종목을 선택하세요.";
  }

  async function runSearch() {
    const query = $("stock-search").value.trim();
    renderSearch(await requestJson(`/api/stocks/search?q=${encodeURIComponent(query)}`));
  }

  async function runSidebarSearch(sequence) {
    const query = $("sidebar-stock-search").value.trim();
    if (query.length < 2) { $("sidebar-search-results").innerHTML = ""; return; }
    const result = await requestJson(`/api/stocks/search?q=${encodeURIComponent(query)}`);
    if (sequence !== sidebarSearchSequence) return;
    renderSearch(result, "sidebar-search-results", true);
  }

  function basisLabel(detail) {
    const row = selectedWatchRow();
    const provisional = row && row.price_basis === "provisional";
    const asOf = (row && row.as_of) || detail.basis.as_of;
    if (!asOf) return "마감 기준 없음";
    return `${String(asOf).slice(5)} 마감${provisional || detail.basis.provisional ? " · 잠정" : ""}`;
  }

  function statTile(label, value, suffix = "", valueTone = "", explanation = "") {
    return `<div${explanation ? ` title="${esc(explanation)}"` : ""}><span>${label}</span><b class="num ${valueTone}">${value === "—" ? value : `${value}${suffix}`}</b></div>`;
  }

  function renderHeadline(detail) {
    const identity = detail.identity;
    const headline = detail.headline;
    const stats = detail.stats;
    const change = headline.change;
    const row = selectedWatchRow();
    const flags = row ? (row.condition_matches || []) : (detail.conditions || []);
    const actionLabel = selectedInCurrentList() ? "관심종목에서 제거" : "관심종목에 추가";
    $("stock-headline-card").innerHTML = `
      <div class="stock-headline-top">
        <div><h1>${esc(identity.market === "US ETF" ? identity.symbol : identity.name)}</h1><p>${identity.market === "US ETF" ? `${esc(identity.name)} · ` : ""}${esc(identity.symbol)} · ${esc(identity.market)} · ${esc(identity.security_type)}</p></div>
        <div class="stock-detail-actions"><button type="button" class="button primary" id="toggle-detail-watchlist">${actionLabel}</button><button type="button" class="button" id="edit-detail-conditions">조건 편집</button></div>
      </div>
      <div class="stock-headline-price">
        <b class="num">${headline.price_available ? formatPriceValue(headline.price, identity.market) : "—"}</b>
        <span class="num ${tone(change)}">${change === null || change === undefined ? "" : `${arrow(change)} ${formatPriceValue(Math.abs(change), identity.market)} · ${pct(headline.change_pct)}`}</span>
      </div>
      <div class="stock-basis-line">${esc(basisLabel(detail))}</div>
      <div class="stock-condition-chips">${flags.length ? flags.map((item) => `<span>${esc(item.name)}</span>`).join("") : '<span class="empty">도달 조건 없음</span>'}</div>
      <div class="stock-stat-row">
        ${statTile("RSI14", number(stats.rsi14, 1), "", "", "Wilder 지수이동평균 방식")}
        ${statTile("60일선 대비", pct(stats.disp60_pct), "", tone(stats.disp60_pct))}
        ${statTile("52주 고점 대비", pct(stats.drawdown_pct), "", tone(stats.drawdown_pct))}
        ${statTile("20일 거래량 배수", stats.volume20_multiple === null ? "—" : number(stats.volume20_multiple, 2), stats.volume20_multiple === null ? "" : "×")}
        ${statTile("시가총액", stats.market_cap === null ? "—" : compact(stats.market_cap))}
        ${statTile("시가배당률", plainPct(stats.dividend_yield_pct))}
      </div>`;
  }

  function renderCompany(company) {
    if (!company.available) { $("stock-company").innerHTML = `<div class="unavailable">${esc(company.message)}</div>`; return; }
    const rows = [
      ["시장", company.market], ["증권 유형", company.security_type], ["상장일", company.listing_date],
      ["발행주식수", company.issued_shares === null ? "—" : `${number(company.issued_shares, 0)}주`],
      ["액면가", company.par_value === null ? "—" : `${number(company.par_value, 0)}원`], ["ISIN", company.isin],
      ["업종", company.industry || company.industry_message],
    ];
    $("stock-company").innerHTML = `<dl class="stock-kv-list">${rows.map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value || "—")}</dd></div>`).join("")}</dl>`;
  }

  function renderTarget(target) {
    if (!target.available) { $("stock-target-price").innerHTML = `<div class="unavailable">${esc(target.message)}</div>`; return; }
    // Display rule (TARGET_PRICE_CONSENSUS.md): 참고 · 출처 · 기준일 · 표본 n명 · 현재가 대비 괴리율 — always show the source,
    // and date the gap's price separately from the consensus date.
    const gapBasis = target.price_as_of ? `현재가 ${esc(target.price_as_of)} 종가 기준` : "현재가 기준일 없음";
    $("stock-target-price").innerHTML = `<div class="target-price-value"><span>참고 · 평균 목표가</span><b class="num">${esc(target.currency)} ${number(target.target_mean, 2)}</b></div>
      <dl class="stock-kv-list"><div><dt>출처</dt><dd>${esc(target.source_label || target.source || "출처 미기록")}</dd></div><div><dt>컨센서스 기준일</dt><dd>${esc(target.as_of)}</dd></div><div><dt>표본</dt><dd>${number(target.analyst_count, 0)}명</dd></div><div><dt>괴리율</dt><dd class="num ${tone(target.upside_pct)}">${pct(target.upside_pct)} <small class="muted">(${gapBasis})</small></dd></div></dl>`;
  }

  function operatingBars(rows) {
    const ordered = [...rows].reverse();
    const values = ordered.map((row) => Number(row.operating_income || 0));
    const limit = Math.max(...values.map(Math.abs), 1);
    const barWidth = 34, gap = 12, width = ordered.length * (barWidth + gap) + 20, mid = 48;
    return `<svg class="fundamentals-bars" viewBox="0 0 ${width} 94" role="img" aria-label="분기별 영업이익 막대 차트"><line x1="4" x2="${width - 4}" y1="${mid}" y2="${mid}" class="fundamentals-zero"></line>${ordered.map((row, index) => {
      const value = Number(row.operating_income || 0); const height = Math.max(1, Math.abs(value) / limit * 36); const x = 12 + index * (barWidth + gap); const y = value >= 0 ? mid - height : mid;
      return `<rect x="${x}" y="${y}" width="${barWidth}" height="${height}" class="${value >= 0 ? "positive" : "negative"}"></rect><text x="${x + barWidth / 2}" y="89" text-anchor="middle">${esc(row.quarter.replace("년 ", "Q").replace("분기", ""))}</text>`;
    }).join("")}</svg>`;
  }

  function trillion(value) {
    return value === null || value === undefined ? "—" : `${number(Number(value) / 1e12, 1)}조`;
  }

  function renderFundamentals(fundamentals) {
    if (!fundamentals.available) { $("stock-fundamentals").innerHTML = `<div class="unavailable">${esc(fundamentals.message)}</div>`; return; }
    const rows = fundamentals.rows || [];
    $("stock-fundamentals").innerHTML = `<div class="fundamentals-overview">${operatingBars(rows)}<div class="fundamentals-health"><span>최근 4분기 흑자 여부 <b>${esc(fundamentals.profitability_label)}</b></span><span>매출 추세 <b>${esc(fundamentals.revenue_trend)}</b></span></div></div>
      <div class="data-table-wrap"><table class="data-table stock-fundamentals-table"><thead><tr><th>분기</th><th>매출</th><th>영업이익</th><th>순이익</th><th>영업이익률</th><th>부채비율</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(row.quarter)}${row.sanity_check_required ? '<small class="fundamentals-sanity-badge" title="순이익이 매출을 초과 — 공시값 확인 필요">확인 필요</small>' : ""}</td><td class="num">${trillion(row.revenue)}</td><td class="num ${tone(row.operating_income)}">${trillion(row.operating_income)}</td><td class="num ${tone(row.net_income)}">${trillion(row.net_income)}</td><td class="num">${plainPct(row.operating_margin_pct)}</td><td class="num">${plainPct(row.debt_ratio_pct)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderDividends(dividends) {
    if (!dividends.available) { $("stock-dividends").innerHTML = `<div class="unavailable">${esc(dividends.message)}</div>`; return; }
    $("stock-dividends").innerHTML = `<div class="dividend-summary"><span>최근 4분기 합계 <b class="num">${number(dividends.trailing_4q_sum, 0)}원</b></span><span>시가배당률 <b class="num">${plainPct(dividends.dividend_yield_pct)}</b></span><span>${esc(dividends.next_event_label)} <b>${esc(dividends.next_event_value)}</b></span></div>
      <div class="data-table-wrap"><table class="data-table stock-dividend-table"><thead><tr><th>기준일</th><th>지급일</th><th>구분</th><th>주당 배당</th></tr></thead><tbody>${dividends.rows.map((row) => `<tr><td>${esc(row.dividend_record_date || "—")}</td><td>${esc(row.cash_payment_date || "—")}</td><td>${esc(row.category)}</td><td class="num">${number(row.ordinary_dividend_amount, 0)}원</td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderInvestorFlows(flows) {
    const host = $("stock-investor-flows");
    if (!flows || flows.reason) {
      host.innerHTML = `<div class="unavailable">${esc((flows || {}).reason || "종목별 수급 데이터 미보존")}</div>`;
      return;
    }
    const summary = flows.summary_20d || {};
    host.innerHTML = `<div class="investor-flow-summary"><b>20일 누적</b><span>외국인 <strong class="num ${tone(summary.foreign)}">${signedEok(summary.foreign, 0)}</strong> · 기관 <strong class="num ${tone(summary.institution)}">${signedEok(summary.institution, 0)}</strong> · 개인 <strong class="num ${tone(summary.individual)}">${signedEok(summary.individual, 0)}</strong></span><small class="muted">기준일 ${esc(flows.as_of || "—")}</small></div>
      <div id="stock-investor-chart" class="stock-investor-chart"></div>
      <div class="data-table-wrap"><table class="data-table stock-investor-table"><thead><tr><th>일자</th><th>외국인</th><th>기관</th><th>개인</th><th>기타법인</th></tr></thead><tbody>${(flows.rows || []).map((row) => `<tr><td>${esc(row.date)}</td><td class="num ${tone(row.foreign_net)}">${signedEok(row.foreign_net)}</td><td class="num ${tone(row.institution_net)}">${signedEok(row.institution_net)}</td><td class="num ${tone(row.individual_net)}">${signedEok(row.individual_net)}</td><td class="num ${tone(row.other_corp_net)}">${signedEok(row.other_corp_net)}</td></tr>`).join("")}</tbody></table></div>`;
    const cumulative = flows.cumulative || {};
    const points = (values) => (cumulative.dates || []).map((date, index) => ({ t: date, v: values[index] }));
    if (window.SIChart) SIChart.renderLineChart($("stock-investor-chart"), [], {
      height: 220, ariaLabel: "20일 누적 투자자 순매수 차트", xMode: "index",
      axisFormatter: (value) => `${(Number(value) / 1e8).toFixed(0)}억`,
      valueFormatter: (value) => signedEok(value),
      emptyMessage: "투자자 수급 관측이 2개 이상이면 선이 표시됩니다.",
      series: [
        { key: "foreign", label: "외국인", color: "#c0392b", points: points(cumulative.foreign || []) },
        { key: "institution", label: "기관", color: "#2b62c0", points: points(cumulative.institution || []) },
        { key: "individual", label: "개인", color: "#a8621a", points: points(cumulative.individual || []) },
      ],
    });
  }

  function ensurePriceChart() {
    if (priceChart || !window.LightweightCharts) return;
    const host = $("stock-price-chart");
    host.innerHTML = "";
    priceChart = LightweightCharts.createChart(host, {
      layout: { background: { color: "#fff" }, textColor: "#6b6660", fontFamily: "IBM Plex Sans KR, system-ui" },
      grid: { vertLines: { color: "#f0ece5" }, horzLines: { color: "#e6e1d8" } },
      rightPriceScale: { borderColor: "#d9d3ca" }, timeScale: { borderColor: "#d9d3ca" }, crosshair: { mode: 1 }, autoSize: true,
    });
    const priceFormatter = (value) => formatPriceValue(value, (selectedIdentity && selectedIdentity.market) || (selectedDetail && selectedDetail.identity.market));
    candleSeries = priceChart.addCandlestickSeries({ upColor: "#c0392b", downColor: "#2b62c0", borderUpColor: "#c0392b", borderDownColor: "#2b62c0", wickUpColor: "#c0392b", wickDownColor: "#2b62c0", priceFormat: { type: "custom", formatter: priceFormatter } });
    if (window.ResizeObserver) {
      let pending = null;
      new ResizeObserver(() => {
        if (!priceChart) return;
        clearTimeout(pending);
        pending = setTimeout(() => { if (priceChart) priceChart.timeScale().fitContent(); }, 80);
      }).observe(host);
    }
  }

  function clearDynamicChartSeries() {
    if (!priceChart) return;
    dynamicChartSeries.forEach((series) => priceChart.removeSeries(series));
    dynamicChartSeries = [];
  }

  function movingAverage(candles, windowSize) {
    return candles.map((candle, index) => {
      if (index + 1 < windowSize) return null;
      const values = candles.slice(index + 1 - windowSize, index + 1).map((item) => Number(item.c));
      return { time: candle.t, value: values.reduce((sum, value) => sum + value, 0) / windowSize };
    }).filter(Boolean);
  }

  function fallbackIndicators(candles) {
    const result = {
      volume: candles.map((candle) => ({ time: candle.t, value: candle.v })),
    };
    [5, 20, 60, 120].forEach((windowSize) => {
      result[`ma${windowSize}`] = movingAverage(candles, windowSize);
    });
    const values = window.SIIndicators
      ? SIIndicators.rsiWilder(candles.map((candle) => Number(candle.c)), 14) : [];
    result.rsi14 = candles.map((candle, index) => values[index] === null || values[index] === undefined
      ? null : ({ time: candle.t, value: values[index] })).filter(Boolean);
    return result;
  }

  function cleanIndicatorPoints(points) {
    return (points || []).filter((point) => point && (point.t || point.time)
      && (point.v ?? point.value) !== null && (point.v ?? point.value) !== undefined
      && Number.isFinite(Number(point.v ?? point.value)))
      .map((point) => ({ time: point.t || point.time, value: Number(point.v ?? point.value) }));
  }

  function serverIndicators(payload) {
    const result = {};
    const source = payload.indicators || {};
    Object.entries(source).forEach(([name, points]) => {
      if (Array.isArray(points)) result[name] = cleanIndicatorPoints(points);
    });
    const legacyMa = payload.ma || {};
    Object.entries(legacyMa).forEach(([name, points]) => {
      if (!(name in result) && Array.isArray(points)) result[name] = cleanIndicatorPoints(points);
    });
    if (Array.isArray(payload.rsi14)) result.rsi14 = cleanIndicatorPoints(payload.rsi14);
    return result;
  }

  function panelMargins(index, height) {
    return { top: Math.max(0.04, 1 - (index + 1) * height), bottom: index * height };
  }

  function addIndicatorLine(options, points) {
    const series = priceChart.addLineSeries({
      lineWidth: 2, priceLineVisible: false, lastValueVisible: false, ...options,
    });
    series.setData(points);
    dynamicChartSeries.push(series);
    return series;
  }

  function addIndicatorHistogram(options, points, candleColors) {
    const series = priceChart.addHistogramSeries({
      priceLineVisible: false, lastValueVisible: false, ...options,
    });
    series.setData(points.map((point) => ({
      ...point, color: candleColors.get(point.time) || "rgba(107,102,96,.35)",
    })));
    dynamicChartSeries.push(series);
    return series;
  }

  function currentInterval() {
    const selected = document.querySelector("#stock-interval button.on");
    return selected ? selected.dataset.v : "day";
  }

  function syncIntervalControls() {
    const interval = currentInterval();
    const shortRange = document.querySelector("#stock-range button.on[data-v='3M'], #stock-range button.on[data-v='6M']");
    document.querySelectorAll("#stock-range button[data-v='3M'], #stock-range button[data-v='6M']").forEach((button) => {
      button.disabled = interval === "month";
      button.title = interval === "month" ? "월봉은 최소 1년 범위로 표시합니다." : "";
    });
    if (interval === "month" && shortRange) {
      shortRange.classList.remove("on");
      document.querySelector("#stock-range button[data-v='1Y']").classList.add("on");
    }
  }

  function renderLoadedChart() {
    const source = loadedChart && loadedChart.candles;
    if (!source || !source.length) {
      if (priceChart) {
        priceChart.remove(); priceChart = null; candleSeries = null; dynamicChartSeries = [];
      }
      $("stock-price-chart").innerHTML = `<div class="unavailable">${esc((loadedChart || {}).reason || "보존 데이터 없음")}</div>`;
      $("stock-chart-legend").innerHTML = "";
      return;
    }
    syncIntervalControls();
    const interval = currentInterval();
    const allCandles = source;
    const range = currentRange();
    const windowSizes = {
      day: { "3M": 63, "6M": 126, "1Y": 252, "3Y": 756 },
      week: { "3M": 13, "6M": 26, "1Y": 52, "3Y": 156 },
      month: { "3M": 3, "6M": 6, "1Y": 12, "3Y": 36 },
    };
    const count = (windowSizes[interval] || {})[range];
    const candles = count ? allCandles.slice(-count) : allCandles;
    const firstTime = candles.length ? candles[0].t : "";
    ensurePriceChart();
    if (priceChart) {
      clearDynamicChartSeries();
      candleSeries.setData(candles.map((item) => ({ time: item.t, open: item.o, high: item.h, low: item.l, close: item.c })));
      const values = { ...fallbackIndicators(allCandles), ...serverIndicators(loadedChart) };
      const enabled = Object.keys(indicatorState).filter((name) => indicatorState[name].enabled);
      const panels = enabled.filter((name) => indicatorState[name].placement === "panel");
      const panelHeight = panels.length ? Math.min(0.22, 0.60 / panels.length) : 0;
      priceChart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.04, bottom: panels.length * panelHeight + 0.02 },
      });
      const candleColors = new Map(candles.map((item) => [
        item.t, item.c >= item.o ? "rgba(192,57,43,.42)" : "rgba(43,98,192,.42)",
      ]));
      enabled.forEach((name) => {
        const placement = indicatorState[name].placement;
        const panelIndex = panels.indexOf(name);
        const scaleId = placement === "panel" ? `stock-${name}`
          : (name === "volume" || name === "rsi14") ? `stock-overlay-${name}` : undefined;
        const points = cleanIndicatorPoints(values[name]).filter((point) => point.time >= firstTime);
        let series;
        if (name === "volume") {
          series = addIndicatorHistogram({
            ...(scaleId ? { priceScaleId: scaleId } : {}),
            priceFormat: { type: "custom", formatter: compact },
          }, points, candleColors);
        } else {
          const formatter = name === "rsi14" ? (value) => Number(value).toFixed(0)
            : (value) => formatPriceValue(value, selectedIdentity && selectedIdentity.market);
          series = addIndicatorLine({
            color: indicatorColors[name], lineWidth: name === "ma5" ? 1 : 2,
            ...(scaleId ? { priceScaleId: scaleId } : {}),
            priceFormat: { type: "custom", formatter },
          }, points);
        }
        if (name === "rsi14") {
          [30, 70].forEach((guide) => series.createPriceLine({
            price: guide, color: "rgba(138,132,123,.65)", lineStyle: 2,
            lineWidth: 1, axisLabelVisible: true, title: String(guide),
          }));
        }
        if (placement === "panel") {
          priceChart.priceScale(scaleId).applyOptions({
            scaleMargins: panelMargins(panelIndex, panelHeight), borderVisible: false,
          });
        } else if (scaleId) {
          priceChart.priceScale(scaleId).applyOptions({
            visible: false,
            scaleMargins: name === "volume" ? { top: .72, bottom: .02 } : { top: .08, bottom: .08 },
          });
        }
      });
      priceChart.timeScale().fitContent();
      requestAnimationFrame(() => { if (priceChart) priceChart.timeScale().fitContent(); });
    } else {
      $("stock-price-chart").innerHTML = '<div class="unavailable">차트 라이브러리 로드 실패</div>';
    }
    $("stock-chart-legend").innerHTML = Object.keys(indicatorState)
      .filter((name) => indicatorState[name].enabled)
      .map((name) => `<span class="stock-indicator-label"><i style="background:${indicatorColors[name]}"></i>${esc(intervalLabel(name))} · ${indicatorState[name].placement === "panel" ? "아래" : "겹침"}<button type="button" data-remove-stock-indicator="${name}" aria-label="${esc(indicatorLabels[name])} 제거">×</button></span>`).join("");
    $("stock-chart-basis").textContent = selectedDetail ? basisLabel(selectedDetail) : (loadedChart.as_of || "마감 기준 없음");
  }

  function currentRange() {
    const selected = document.querySelector("#stock-range button.on");
    return selected ? selected.dataset.v : "6M";
  }

  async function loadChart(symbol) {
    const sequence = ++chartLoadSequence;
    const params = new URLSearchParams({
      symbol, range: "ALL", interval: currentInterval(),
      indicators: Object.keys(indicatorState).filter((name) => indicatorState[name].enabled).join(","),
    });
    const payload = await requestJson(`/api/chart?${params}`);
    if (sequence !== chartLoadSequence) return;
    loadedChart = payload;
    renderLoadedChart();
  }

  async function loadDetail(identity, replaceUrl = true) {
    if (identity.list_id && identity.list_id !== selectedListId) {
      selectedListId = identity.list_id; renderWatchlistEditor(); renderSelectedSearch();
    }
    selectedIdentity = { symbol: identity.symbol, market: identity.market || (identity.symbol.length === 6 ? "KOSPI" : "US ETF") };
    renderSidebar();
    $("stock-headline-card").innerHTML = '<div class="stock-detail-loading">종목 상세를 불러오는 중…</div>';
    if (replaceUrl) history.replaceState(null, "", `/stocks?symbol=${encodeURIComponent(selectedIdentity.symbol)}`);
    const [detail] = await Promise.all([
      requestJson(`/api/stock-detail?symbol=${encodeURIComponent(selectedIdentity.symbol)}&market=${encodeURIComponent(selectedIdentity.market)}`),
      loadChart(selectedIdentity.symbol),
    ]);
    selectedDetail = detail;
    selectedIdentity = { symbol: detail.identity.symbol, market: detail.identity.market };
    renderSidebar(); renderHeadline(detail); renderCompany(detail.company); renderTarget(detail.target_price);
    renderFundamentals(detail.fundamentals); renderDividends(detail.dividends);
    renderInvestorFlows(detail.investor_flows);
    $("stock-chart-basis").textContent = basisLabel(detail);
  }

  async function loadSparklines() {
    const symbols = [...new Set((page.table || []).map((row) => row.symbol))];
    if (!symbols.length) { sparklines = {}; renderSidebar(); return; }
    const result = await requestJson(`/api/stock-sparklines?symbols=${encodeURIComponent(symbols.join(","))}`);
    sparklines = result.sparklines || {};
    renderSidebar();
  }

  function renderAll() {
    conditions = (page.conditions.conditions || []).map((item) => ({ ...item }));
    renderWatchlistEditor(); renderWatchlistTable(); renderConditions(); renderSidebar();
    renderSelectedSearch();
    $("stocks-safety").textContent = page.note || "";
  }

  async function resolveInitialIdentity() {
    const requested = ($("stocks-page").dataset.initialSymbol || new URLSearchParams(window.location.search).get("symbol") || "").trim().toUpperCase();
    let row = requested ? (page.table || []).find((item) => item.symbol === requested) : null;
    if (row) return row;
    if (requested) {
      const result = await requestJson(`/api/stocks/search?q=${encodeURIComponent(requested)}`);
      const exact = (result.matches || []).find((item) => item.symbol === requested);
      if (exact) return exact;
      return { symbol: requested, market: /^\d{6}$/.test(requested) ? "KOSPI" : "US ETF" };
    }
    return (page.table || [])[0] || null;
  }

  async function refreshPage(message = "", preserveSelection = true) {
    page = await requestJson("/api/stocks");
    renderAll();
    await loadSparklines();
    $("watchlist-status").textContent = message;
    const identity = preserveSelection && selectedIdentity ? selectedIdentity : await resolveInitialIdentity();
    if (identity) await loadDetail(identity, true);
  }

  const wonEok = (value, digits = 1) => value === null || value === undefined
    ? "—" : `${number(Number(value) / 100000000, digits)}억원`;
  const plainPct = (value) => value === null || value === undefined ? "—" : `${number(value, 1)}%`;
  const healthValue = (row, key, formatter) => {
    if (!row.fundamentals_as_of) return '<span class="muted">미수집</span>';
    return row[key] === null || row[key] === undefined ? '<span class="muted">확인 불가</span>' : formatter(row[key]);
  };
  const fourQuarter = (value) => value ? "4/4 양수" : "음수 포함";
  const revenueTrend = (value) => ({ INCREASING: "증가", DECLINING: "감소", FLAT: "보합", MIXED: "혼조", UNAVAILABLE: "확인 불가" }[value] || "확인 불가");
  const valueTrap = (state) => ({ FLAGGED: ["가치 함정 후보", "amber"], NOT_FLAGGED: ["가치 함정 아님", "muted"], UNAVAILABLE: ["가치 함정 판정 불가", "muted"] }[state] || ["가치 함정 판정 불가", "muted"]);

  function renderScanner() {
    const result = scannerResult;
    if (!result) return;
    if (result.status !== "READY") {
      $("scanner-summary").textContent = `표시 불가 · ${result.reason || "입력 확인 필요"} · ${scannerElapsed.toFixed(2)}초 · ${result.liquidity_note || ""}`;
      $("scanner-rows").innerHTML = '<tr><td colspan="15" class="unavailable">후보를 계산할 수 없습니다.</td></tr>';
      return;
    }
    const fundamentalColumns = result.fundamental_columns || [];
    const fundamentalsOnly = $("scanner-fundamentals-only").checked;
    const rows = fundamentalsOnly ? result.candidates.filter((row) => Boolean(row.fundamentals_as_of)) : result.candidates;
    $("scanner-head").innerHTML = `<th>종목명</th><th>시장</th><th>코드</th><th>현재가</th><th>등락률</th><th>RSI14</th><th>60일선</th><th>52주 낙폭</th><th>20일 거래대금</th><th>시총</th><th class="scanner-debt">부채비율</th><th>영업이익 4Q</th><th>순이익 4Q</th><th class="scanner-revenue">매출 추세</th>${fundamentalColumns.includes("per") ? "<th>PER</th>" : ""}${fundamentalColumns.includes("pbr") ? "<th>PBR</th>" : ""}<th>관찰 근거</th>`;
    const coverage = result.fundamentals_coverage || { available: 0, total: result.count, as_of: null };
    const displayed = fundamentalsOnly ? ` · 재무 수집됨 ${rows.length}개 표시` : "";
    $("scanner-summary").textContent = `정식 종가 기준 (${String(result.as_of).slice(5)}) · 잠정 미포함 · ${result.scanned_instruments.toLocaleString("ko-KR")}개 확인 · ${result.count.toLocaleString("ko-KR")}개 후보${displayed} · ${result.rule} · ${result.liquidity_note} · 재무 ${coverage.available}/${coverage.total} 수집 · ${scannerElapsed.toFixed(2)}초 · ${result.fundamentals_note}`;
    $("scanner-rows").innerHTML = rows.length ? rows.map((row) => {
      const trap = valueTrap(row.value_trap_state);
      return `<tr><td><button type="button" class="text-button open-stock-detail" data-symbol="${esc(row.symbol)}" data-market="${esc(row.market)}"><b>${esc(row.name)}</b></button>${row.data_caution ? `<small class="amber">${esc(row.data_caution)}</small>` : ""}</td>
      <td>${esc(row.market)}</td><td class="num">${esc(row.symbol)}</td><td class="num">${number(row.price, 0)}</td><td class="num ${tone(row.change_pct)}">${pct(row.change_pct)}</td><td class="num">${number(row.rsi14, 1)}</td>
      <td class="num ${tone(row.disp60_pct)}">${pct(row.disp60_pct)}</td><td class="num ${tone(row.drawdown_pct)}">${pct(row.drawdown_pct)}</td><td class="num">${wonEok(row.avg_value_20d, 1)}</td><td class="num">${wonEok(row.market_cap, 1)}</td>
      <td class="num scanner-debt">${healthValue(row, "debt_ratio_pct", plainPct)}</td><td>${healthValue(row, "op_income_positive_4q", fourQuarter)}<small class="${trap[1]}">${trap[0]}</small></td><td>${healthValue(row, "net_income_positive_4q", fourQuarter)}</td><td class="scanner-revenue">${healthValue(row, "revenue_trend", revenueTrend)}</td>
      ${fundamentalColumns.includes("per") ? `<td class="num">${number(row.per, 2)}</td>` : ""}${fundamentalColumns.includes("pbr") ? `<td class="num">${number(row.pbr, 2)}</td>` : ""}<td>${esc(row.why)}</td></tr>`;
    }).join("") : `<tr><td colspan="${15 + fundamentalColumns.length}" class="unavailable">${fundamentalsOnly ? "재무가 수집된 후보가 없습니다." : "현재 조건에 도달한 종목이 없습니다."}</td></tr>`;
  }

  async function loadScanner() {
    const started = performance.now();
    $("scanner-summary").textContent = "보존 데이터를 읽는 중…";
    const minValue = Number($("scanner-min-value").value), minCap = Number($("scanner-min-cap").value);
    if (!Number.isFinite(minValue) || minValue < 0 || !Number.isFinite(minCap) || minCap < 0) throw new Error("유동성 기준은 0 이상의 숫자여야 합니다.");
    scannerResult = await requestJson(`/api/scanner?${new URLSearchParams({ min_value: String(minValue * 100000000), min_cap: String(minCap * 100000000) })}`);
    scannerElapsed = (performance.now() - started) / 1000; renderScanner();
  }

  document.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const picker = $("stock-indicator-picker");
    if (picker && !picker.contains(target)) {
      $("stock-indicator-menu").hidden = true;
      $("stock-indicator-picker-button").setAttribute("aria-expanded", "false");
    }
    try {
      const removeIndicator = target.closest("[data-remove-stock-indicator]");
      const sidebarItem = target.closest(".watchlist-sidebar-item, .sidebar-search-result, .open-stock-detail");
      if (removeIndicator) {
        changeIndicator(removeIndicator.dataset.removeStockIndicator, { enabled: false });
      } else if (sidebarItem) {
        if (sidebarItem.dataset.listId) {
          selectedListId = sidebarItem.dataset.listId; renderWatchlistEditor(); renderSelectedSearch();
        }
        $("sidebar-search-results").innerHTML = "";
        await loadDetail({ symbol: sidebarItem.dataset.symbol, market: sidebarItem.dataset.market });
      } else if (target.id === "run-stock-search") {
        await runSearch();
      } else if (target.closest(".search-result")) {
        const result = target.closest(".search-result");
        selectedSearch = { market: result.dataset.market, symbol: result.dataset.symbol, name: result.dataset.name };
        document.querySelectorAll(".search-result").forEach((item) => item.classList.toggle("on", item === result));
        renderSelectedSearch();
      } else if (target.id === "add-selected-stock" && selectedSearch) {
        const added = { ...selectedSearch };
        await mutate("/api/watchlist/items", { list_id: selectedListId, market: added.market, symbol: added.symbol });
        flashIdentity = `${added.market}:${added.symbol}`; selectedSearch = null; $("stock-search-results").innerHTML = "";
        await refreshPage("종목을 추가했습니다.");
        window.setTimeout(() => { document.querySelectorAll(".flash-new").forEach((row) => row.classList.remove("flash-new")); flashIdentity = null; }, 1900);
      } else if (target.id === "toggle-detail-watchlist" && selectedIdentity) {
        const remove = selectedInCurrentList();
        await mutate("/api/watchlist/items", { list_id: selectedListId, market: selectedIdentity.market, symbol: selectedIdentity.symbol }, remove ? "DELETE" : "POST");
        await refreshPage(remove ? "종목을 삭제했습니다." : "종목을 추가했습니다.");
      } else if (target.id === "edit-detail-conditions") {
        const section = [...document.querySelectorAll("details.stocks-management")].find((item) => item.querySelector("#condition-rows"));
        section.open = true; section.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (target.classList.contains("remove-watch-item")) {
        const row = target.closest(".watchlist-edit-row");
        if (!window.confirm("이 종목을 현재 관심목록에서 삭제할까요?")) return;
        await mutate("/api/watchlist/items", { list_id: selectedListId, market: row.dataset.market, symbol: row.dataset.symbol }, "DELETE");
        await refreshPage("종목을 삭제했습니다.");
      } else if (target.classList.contains("move-watch-item")) {
        const row = target.closest(".watchlist-edit-row");
        await mutate("/api/watchlist/items/move", { list_id: selectedListId, market: row.dataset.market, symbol: row.dataset.symbol, offset: Number(target.dataset.offset) });
        await refreshPage("순서를 변경했습니다.");
      } else if (target.id === "create-watchlist") {
        await mutate("/api/watchlists", { action: "create", name: $("new-watchlist-name").value.trim() });
        $("new-watchlist-name").value = ""; await refreshPage("관심목록을 만들었습니다.");
      } else if (target.id === "rename-watchlist") {
        await mutate("/api/watchlists", { action: "rename", list_id: selectedListId, name: $("watchlist-name").value.trim() });
        await refreshPage("목록 이름을 변경했습니다.");
      } else if (target.id === "add-condition") {
        conditions = collectConditions(); conditions.push({ id: uid(), name: "", field: "rsi14", op: "<=", value: 30, scope: "watchlist" }); renderConditions();
      } else if (target.classList.contains("remove-condition")) {
        conditions = collectConditions(); conditions.splice(Number(target.closest("tr").dataset.index), 1); renderConditions();
      } else if (target.id === "save-conditions") {
        $("condition-status").textContent = "저장 중…";
        await mutate("/api/watch-conditions", { schema_version: 1, conditions: collectConditions() });
        await refreshPage("조건을 반영했습니다."); $("condition-status").textContent = "저장했습니다.";
      } else if (target.id === "refresh-scanner") {
        await loadScanner();
      }
    } catch (error) { $("stocks-safety").textContent = `처리 실패 · ${error.message}`; }
  });

  document.addEventListener("DOMContentLoaded", async () => {
    syncIndicatorMenu();
    $("stock-indicator-picker-button").addEventListener("click", () => {
      const menu = $("stock-indicator-menu");
      menu.hidden = !menu.hidden;
      $("stock-indicator-picker-button").setAttribute("aria-expanded", String(!menu.hidden));
    });
    document.querySelectorAll("#stock-indicator-menu [data-indicator]").forEach((row) => {
      const key = row.dataset.indicator;
      row.querySelector('input[type="checkbox"]').addEventListener("change", (event) => {
        changeIndicator(key, { enabled: event.target.checked });
      });
      row.querySelector("select").addEventListener("change", (event) => {
        changeIndicator(key, { placement: event.target.value });
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || $("stock-indicator-menu").hidden) return;
      $("stock-indicator-menu").hidden = true;
      $("stock-indicator-picker-button").setAttribute("aria-expanded", "false");
      $("stock-indicator-picker-button").focus();
    });
    $("scanner-fundamentals-only").addEventListener("change", renderScanner);
    $("watchlist-select").addEventListener("change", () => { selectedListId = $("watchlist-select").value; renderWatchlistEditor(); renderSelectedSearch(); if (selectedDetail) renderHeadline(selectedDetail); });
    $("stock-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); runSearch().catch((error) => { $("stocks-safety").textContent = `검색 실패 · ${error.message}`; }); } });
    $("sidebar-stock-search").addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      sidebarSearchSequence += 1;
      const sequence = sidebarSearchSequence;
      if ($("sidebar-stock-search").value.trim().length < 2) { $("sidebar-search-results").innerHTML = ""; return; }
      searchTimer = window.setTimeout(() => runSidebarSearch(sequence).catch((error) => {
        if (sequence === sidebarSearchSequence) $("stocks-safety").textContent = `검색 실패 · ${error.message}`;
      }), 350);
    });
    document.querySelectorAll("#stock-range button").forEach((button) => button.addEventListener("click", () => { if (button.disabled) return; document.querySelectorAll("#stock-range button").forEach((item) => item.classList.remove("on")); button.classList.add("on"); renderLoadedChart(); }));
    document.querySelectorAll("#stock-interval button").forEach((button) => button.addEventListener("click", () => {
      document.querySelectorAll("#stock-interval button").forEach((item) => item.classList.remove("on"));
      button.classList.add("on"); syncIntervalControls();
      if (selectedIdentity) loadChart(selectedIdentity.symbol).catch((error) => {
        $("stocks-safety").textContent = `차트를 불러오지 못했습니다. · ${error.message}`;
      });
    }));
    syncIntervalControls();
    try { await refreshPage("", false); }
    catch (error) { $("stocks-safety").textContent = `종목 화면을 불러오지 못했습니다. · ${error.message}`; }
  });
})();
