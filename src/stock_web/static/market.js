/* Market page renderer. Reads retained local API payloads only. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
  const fmt = (value, digits = 2) => value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : Number(value).toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (value, digits = 0) => value === null || value === undefined ? "—" : `${Number(value) > 0 ? "+" : ""}${fmt(value, digits)}`;
  const compact = (value) => window.SIChart ? SIChart.formatCompactKorean(value) : fmt(value, 0);
  const percentilePosition = (value) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const rank = Math.max(0, Math.min(100, Math.round(Number(value))));
    return `${fmt(rank, 0)}% (상위 ${fmt(100 - rank, 0)}%)`;
  };
  const pcr = (value) => window.SIChart ? SIChart.formatPcr(value) : fmt(value, 2);
  const flowAxis = (value) => Math.abs(Number(value)) >= 10000 ? `${fmt(Number(value) / 10000, 1)}조` : `${fmt(value, 0)}억`;
  const flowValue = (value) => Math.abs(Number(value)) >= 10000 ? `${signed(Number(value) / 10000, 1)}조원` : `${signed(Math.round(Number(value)), 0)}억원`;
  const unavailable = (reason) => `표시 불가 · ${esc(reason || "보존 데이터 없음")}`;
  const chartOptions = () => ({
    layout: { background: { color: "#fff" }, textColor: "#6b6660", fontFamily: "IBM Plex Sans KR, system-ui", fontSize: 10 },
    grid: { vertLines: { color: "#f0ece5" }, horzLines: { color: "#e6e1d8" } },
    rightPriceScale: { borderColor: "#d9d3ca" }, timeScale: { borderColor: "#d9d3ca", timeVisible: false },
    crosshair: { mode: 1 }, autoSize: true,
  });
  const pointData = (points) => (points || []).filter((point) => point.v !== null && point.v !== undefined).map((point) => ({ time: point.t, value: point.v }));
  const colors = { ma5: "#4a3aa7", ma20: "#2a78d6", ma60: "#eb6834", ma120: "#1baf7a", volume: "#8a847b", rsi14: "#8b4c9e" };
  const indicatorLabels = { ma5: "MA5", ma20: "MA20", ma60: "MA60", ma120: "MA120", volume: "거래량", rsi14: "RSI14" };
  const indicatorDefaults = {
    ma5: { enabled: true, placement: "overlay" }, ma20: { enabled: true, placement: "overlay" },
    ma60: { enabled: true, placement: "overlay" }, ma120: { enabled: true, placement: "overlay" },
    volume: { enabled: true, placement: "panel" }, rsi14: { enabled: false, placement: "panel" },
  };
  const chartIndicatorSuperset = Object.keys(indicatorDefaults).sort();
  const flowColors = { foreigner: "#4a3aa7", institution: "#2a78d6", individual: "#eb6834" };
  const lsFlowColors = { foreign: "#4a3aa7", institution: "#2a78d6", individual: "#eb6834", other: "#8a847b" };
  let indicatorState = loadIndicatorState();
  let mainChart, candleSeries, mainPayload, pagePayload;
  let dynamicSeries = [];
  const mainCache = new Map();
  const flowCache = new Map();
  const historyCache = new Map();
  const hiddenFlowSeries = { KOSPI: new Set(), KOSDAQ: new Set() };
  let selectedLsScope = "K2I_F_U";
  let showLsOther = false;
  const warnedLocalIndicators = new Set();

  function loadIndicatorState() {
    try {
      const saved = JSON.parse(localStorage.getItem("stock-web-market-indicators-v2"));
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        return Object.fromEntries(Object.entries(indicatorDefaults).map(([key, fallback]) => [key, {
          enabled: Boolean(saved[key] ? saved[key].enabled : fallback.enabled),
          placement: saved[key] && ["overlay", "panel"].includes(saved[key].placement) ? saved[key].placement : fallback.placement,
        }]));
      }
      const legacy = JSON.parse(localStorage.getItem("stock-web-market-indicators"));
      if (Array.isArray(legacy)) return Object.fromEntries(Object.entries(indicatorDefaults).map(([key, fallback]) => [key, { ...fallback, enabled: legacy.includes(key) }]));
    } catch (_error) { /* defaults below */ }
    return JSON.parse(JSON.stringify(indicatorDefaults));
  }

  function saveIndicatorState() {
    try { localStorage.setItem("stock-web-market-indicators-v2", JSON.stringify(indicatorState)); } catch (_error) { /* optional */ }
  }

  function syncIndicatorMenu() {
    document.querySelectorAll("#indicator-menu [data-indicator]").forEach((row) => {
      const state = indicatorState[row.dataset.indicator];
      row.querySelector('input[type="checkbox"]').checked = Boolean(state && state.enabled);
      row.querySelector("select").value = state ? state.placement : "overlay";
    });
  }

  function changeIndicator(key, changes) {
    if (!indicatorState[key]) return;
    indicatorState[key] = { ...indicatorState[key], ...changes };
    saveIndicatorState(); syncIndicatorMenu(); renderMainChart(mainPayload);
  }

  function selectedButtonValue(selector, fallback) {
    const button = document.querySelector(`${selector} button.on`);
    return button ? button.dataset.v : fallback;
  }

  function ensureMainChart() {
    if (mainChart || !window.LightweightCharts) return;
    mainChart = LightweightCharts.createChart($("market-chart"), chartOptions());
    candleSeries = mainChart.addCandlestickSeries({
      upColor: "#c0392b", downColor: "#2b62c0", borderUpColor: "#c0392b",
      borderDownColor: "#2b62c0", wickUpColor: "#c0392b", wickDownColor: "#2b62c0",
      priceFormat: { type: "custom", formatter: compact },
    });
  }

  function addLine(options, data) {
    const series = mainChart.addLineSeries({ lineWidth: 1, priceLineVisible: false, lastValueVisible: false, ...options });
    series.setData(pointData(data)); dynamicSeries.push(series); return series;
  }

  function addHistogram(options, data, colorBuilder) {
    const series = mainChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, ...options });
    series.setData((data || []).filter((point) => point.v !== null && point.v !== undefined).map((point) => ({
      time: point.t, value: point.v, color: colorBuilder ? colorBuilder(point.v, point) : options.color,
    })));
    dynamicSeries.push(series); return series;
  }

  function clearDynamicSeries() {
    if (!mainChart) return;
    dynamicSeries.forEach((series) => mainChart.removeSeries(series)); dynamicSeries = [];
  }

  function calculateIndicators(candles) {
    const close = candles.map((candle) => Number(candle.c));
    const result = { volume: candles.map((candle) => ({ t: candle.t, v: candle.v })) };
    [5, 20, 60, 120].forEach((windowSize) => {
      result[`ma${windowSize}`] = candles.slice(windowSize - 1).map((candle, offset) => ({
        t: candle.t, v: close.slice(offset, offset + windowSize).reduce((sum, value) => sum + value, 0) / windowSize,
      }));
    });
    const rsiValues = SIIndicators.rsiWilder(close, 14);
    result.rsi14 = candles.map((candle, index) => rsiValues[index] === null ? null : ({ t: candle.t, v: rsiValues[index] })).filter(Boolean);
    return result;
  }

  function serverIndicators(payload) {
    const source = (payload && payload.indicators) || {};
    const result = {};
    Object.entries(source).forEach(([name, points]) => {
      if (!Array.isArray(points) || !points.length) return;
      result[name] = points.filter((point) => point && point.v !== null && point.v !== undefined).map((point) => ({ t: point.t, v: Number(point.v) }));
    });
    return result;
  }

  function panelMargins(index, height) {
    return { top: Math.max(0.04, 1 - (index + 1) * height), bottom: index * height };
  }

  function renderMainChart(payload) {
    const stats = $("market-chart-stats"), legend = $("market-chart-legend");
    if (!payload || !payload.candles || !payload.candles.length) {
      if (mainChart) { clearDynamicSeries(); candleSeries.setData([]); }
      else $("market-chart").innerHTML = `<div class="unavailable">${unavailable(payload && payload.reason)}</div>`;
      stats.innerHTML = ""; legend.innerHTML = ""; return;
    }
    ensureMainChart();
    if (!mainChart) { $("market-chart").innerHTML = `<div class="unavailable">${unavailable("차트 라이브러리 로드 실패")}</div>`; return; }
    clearDynamicSeries();
    candleSeries.setData(payload.candles.map((candle) => ({ time: candle.t, open: candle.o, high: candle.h, low: candle.l, close: candle.c })));
    const enabled = Object.keys(indicatorState).filter((name) => indicatorState[name].enabled);
    const serverValues = serverIndicators(payload);
    const localValues = calculateIndicators(payload.candles);
    const values = { ...localValues, ...serverValues };
    const localFallbacks = new Set(enabled.filter((name) => !Object.prototype.hasOwnProperty.call(serverValues, name)));
    localFallbacks.forEach((name) => {
      if (warnedLocalIndicators.has(name)) return;
      console.warn(`[market] ${indicatorLabels[name]}: 서버 지표 없음; 보이는 봉만으로 계산하며 워밍업이 없습니다.`);
      warnedLocalIndicators.add(name);
    });
    const panels = enabled.filter((name) => indicatorState[name].placement === "panel");
    const height = panels.length ? Math.min(0.14, 0.58 / panels.length) : 0;
    mainChart.priceScale("right").applyOptions({ scaleMargins: { top: 0.04, bottom: panels.length * height + 0.02 } });
    const candleColors = new Map(payload.candles.map((candle) => [candle.t, candle.c >= candle.o ? "rgba(192,57,43,.42)" : "rgba(43,98,192,.42)"]));
    enabled.forEach((name) => {
      const placement = indicatorState[name].placement;
      const panelIndex = panels.indexOf(name);
      const scaleId = placement === "panel" ? `market-${name}` : name === "volume" || name === "rsi14" ? `market-overlay-${name}` : undefined;
      if (name === "volume") addHistogram({ priceScaleId: scaleId, priceFormat: { type: "custom", formatter: compact } }, values.volume, (_value, point) => candleColors.get(point.t) || "rgba(107,102,96,.35)");
      else addLine({ color: colors[name], priceFormat: { type: "custom", formatter: name === "rsi14" ? percent : compact }, ...(scaleId ? { priceScaleId: scaleId } : {}) }, values[name]);
      if (name === "rsi14" && values.rsi14.length) {
        const ends = [values.rsi14[0], values.rsi14[values.rsi14.length - 1]];
        [30, 70].forEach((guide) => addLine({ color: "rgba(138,132,123,.6)", lineStyle: 2, lineWidth: 1, priceFormat: { type: "custom", formatter: percent }, ...(scaleId ? { priceScaleId: scaleId } : {}) }, ends.map((point) => ({ t: point.t, v: guide }))));
      }
      if (placement === "panel") mainChart.priceScale(scaleId).applyOptions({ scaleMargins: panelMargins(panelIndex, height), borderVisible: false });
      else if (scaleId) mainChart.priceScale(scaleId).applyOptions({ visible: false, scaleMargins: name === "volume" ? { top: .72, bottom: .02 } : { top: .08, bottom: .08 } });
    });
    mainChart.timeScale().fitContent();
    const latestRsi = payload.stats && payload.stats.rsi14 !== null && payload.stats.rsi14 !== undefined && Number.isFinite(Number(payload.stats.rsi14)) ? Number(payload.stats.rsi14) : null;
    stats.innerHTML = `<span class="muted">기준일 <b class="num">${esc(payload.as_of)}</b></span>${latestRsi !== null ? `<span class="muted" data-explanation="rsi14">RSI14 <b class="num">${fmt(latestRsi, 1)}</b></span>` : ""}`;
    applyExplanations(pagePayload.explanations || {}, stats);
    legend.innerHTML = `<span class="market-symbol-label"><i style="background:#1f1d1a"></i>${esc(payload.symbol_name || payload.symbol)}</span>` + enabled.map((name) => {
      const fallbackBadge = localFallbacks.has(name) ? `<span class="market-indicator-fallback-badge">보이는 봉 계산 · 워밍업 없음</span>` : "";
      return `<span class="market-indicator-label"><i style="background:${colors[name]}"></i>${esc(indicatorLabels[name])} · ${indicatorState[name].placement === "panel" ? "아래" : "겹침"}${fallbackBadge}<button type="button" data-remove-indicator="${name}" aria-label="${esc(indicatorLabels[name])} 제거">×</button></span>`;
    }).join("");
    legend.querySelectorAll("[data-remove-indicator]").forEach((button) => button.addEventListener("click", () => changeIndicator(button.dataset.removeIndicator, { enabled: false })));
  }

  async function loadMainChart() {
    const symbol = $("market-chart-symbol").value || "KOSPI";
    const interval = selectedButtonValue("#market-chart-interval", "1d");
    const range = selectedButtonValue("#market-chart-range", "1Y");
    const requestedIndicators = chartIndicatorSuperset.join(",");
    const cacheKey = `${symbol}|${interval}|${range}|${requestedIndicators}`;
    if (mainCache.has(cacheKey)) { mainPayload = mainCache.get(cacheKey); renderMainChart(mainPayload); return; }
    $("market-chart-stats").textContent = "차트 확인 중";
    try {
      // Fetch the complete UI allowlist so later toggles keep full-history warm-up values.
      const params = new URLSearchParams({ symbol, interval, range, indicators: requestedIndicators });
      const response = await fetch(`/api/market/chart?${params}`);
      mainPayload = response.ok ? await response.json() : { reason: `HTTP ${response.status}` };
      if (response.ok) mainCache.set(cacheKey, mainPayload);
      renderMainChart(mainPayload);
    } catch (error) { mainPayload = { reason: String(error) }; renderMainChart(mainPayload); }
  }

  function rangeValue(cardId, fallback = "1Y") {
    return selectedButtonValue(`[data-range-card="${cardId}"]`, fallback);
  }

  function slicePoints(points, rangeKey) {
    const clean = (points || []).filter((point) => point && point.t && Number.isFinite(Number(point.v)));
    if (rangeKey === "ALL") return clean;
    if (rangeKey === "20D" || rangeKey === "60D") return clean.slice(-Number.parseInt(rangeKey, 10));
    if (!clean.length) return clean;
    const years = Number.parseInt(rangeKey, 10);
    const last = new Date(`${clean[clean.length - 1].t}T00:00:00Z`), start = new Date(last);
    start.setUTCFullYear(start.getUTCFullYear() - years);
    return clean.filter((point) => new Date(`${point.t}T00:00:00Z`) >= start);
  }

  function renderSvg(host, points, options) {
    if (!window.SIChart) { host.innerHTML = `<div class="unavailable">${unavailable("공용 SVG 차트 로드 실패")}</div>`; return; }
    SIChart.renderLineChart(host, points, options);
  }

  function renderLinePanel(chartId, metaId, view, kind, rangeCard) {
    const host = $(chartId), meta = $(metaId);
    if (!view || view.status !== "VALUE" || !view.series || !view.series.length) {
      host.innerHTML = `<div class="unavailable">${unavailable(view && view.reason)}</div>`; meta.textContent = ""; return;
    }
    const formatter = kind === "pcr" ? pcr : (value) => fmt(value, 2);
    meta.innerHTML = `<b class="num">${esc(kind === "pcr" ? pcr(view.value) : signed(view.value, 2))}</b><span>${esc(view.basis_label || `기준일 ${view.as_of || "—"}`)}</span>`;
    const points = slicePoints(view.series, rangeValue(rangeCard));
    renderSvg(host, points, { height: 150, ariaLabel: `${kind === "pcr" ? "PCR" : "Basis"} 추이`, axisFormatter: formatter, valueFormatter: formatter });
  }

  function historySection(range, name) {
    const sections = historyCache.get(range) || ((pagePayload && pagePayload.sections) || {});
    return sections[name] || {};
  }

  function cachePagePayload(payload) {
    if (!payload || !payload.sections) return;
    if (payload.flows_range && payload.sections.flows) flowCache.set(payload.flows_range, payload.sections.flows);
    if (payload.history_range) historyCache.set(payload.history_range, payload.sections);
  }

  // Cboe daily put/call ratios (private mode only). The payload always carries the reason
  // when the dataset is empty, so the panel never shows a bare "미표시".
  function renderCboePcr(view) {
    const panel = $("cboe-pcr-panel");
    if (!panel) return;
    if (!view) { panel.hidden = true; return; }
    panel.hidden = false;
    const meta = $("cboe-pcr-meta"), body = $("cboe-pcr-body");
    if (view.status !== "VALUE" || !(view.rows || []).length) {
      meta.innerHTML = `<span>${esc(view.scope_label || "")}</span>`;
      body.innerHTML = `<div class="unavailable">${unavailable(view.reason || "보존된 Cboe 일별 통계가 없습니다.")} · 수집 전(기계 URL 확인 대기)</div>`;
      return;
    }
    const asOf = (view.rows[0] || {}).date || "—";
    meta.innerHTML = `<b class="num">${esc(pcr((view.rows.find((row) => row.scope === "TOTAL") || {}).volume_pcr))}</b><span>거래소 합계 거래량 PCR · 기준일 ${esc(asOf)} · ${esc(view.scope_label || "")} · 재배포 금지</span>`;
    body.innerHTML = `<table class="market-table"><thead><tr><th>범위</th><th>거래량 PCR</th><th>미결제 PCR</th><th>콜 거래량</th><th>풋 거래량</th></tr></thead><tbody>${view.rows.map((row) => `<tr><td>${esc(row.label || row.scope)}</td><td class="num">${esc(pcr(row.volume_pcr))}</td><td class="num">${esc(pcr(row.oi_pcr))}</td><td class="num">${esc(fmt(row.call_volume, 0))}</td><td class="num">${esc(fmt(row.put_volume, 0))}</td></tr>`).join("")}</tbody></table>`;
  }

  function renderLsInvestorFlow(section) {
    const host = $("ls-flow-chart"), meta = $("ls-flow-meta"), selector = $("ls-flow-scope");
    const view = section && section.ls_futures_investors;
    const scopes = view && Array.isArray(view.available_scopes) ? view.available_scopes : [];
    if (!view || view.status !== "VALUE" || !scopes.length) {
      selector.innerHTML = '<option value="">보존 범위 없음</option>';
      selector.disabled = true;
      meta.textContent = "";
      host.innerHTML = `<div class="unavailable">${unavailable(view && view.reason)}</div>`;
      return;
    }
    selector.disabled = false;
    if (!scopes.some((item) => item.scope === selectedLsScope)) selectedLsScope = view.scope || scopes[0].scope;
    selector.innerHTML = scopes.map((item) => `<option value="${esc(item.scope)}">${esc(item.scope_label)}</option>`).join("");
    selector.value = selectedLsScope;
    const selected = scopes.find((item) => item.scope === selectedLsScope) || view;
    const rows = selected.rows || [];
    const definitions = [
      ["foreign", "외국인"], ["institution", "기관"], ["individual", "개인"], ["other", "기타법인"],
    ];
    const specs = definitions.filter(([key]) => key !== "other" || showLsOther).map(([key, label]) => ({
      key, label, color: lsFlowColors[key], type: "line",
      points: rows.map((row) => ({ t: row.date, v: row[key] })).filter((point) => point.t && Number.isFinite(Number(point.v))),
    }));
    meta.innerHTML = `<span>${esc(selected.scope_label || "")} · 기준일 ${esc(selected.as_of || "—")} · 단위: 계약<br><small>${esc(selected.basis_label || "LS t8462 · 당일 저녁 수집 · 순계약")}</small></span><span class="market-series-toggles market-ls-series-legend">${definitions.slice(0, 3).map(([key, label]) => `<span><i style="background:${lsFlowColors[key]}"></i>${label}</span>`).join("")}<button type="button" data-ls-other class="${showLsOther ? "" : "off"}"><i style="background:${lsFlowColors.other}"></i>기타법인</button></span>`;
    meta.querySelector("[data-ls-other]").addEventListener("click", () => { showLsOther = !showLsOther; renderLsInvestorFlow(section); });
    renderSvg(host, specs[0] ? specs[0].points : [], {
      height: 210, ariaLabel: `${selected.scope_label || "LS 파생"} 투자자 일별 순계약`, series: specs,
      axisFormatter: (value) => fmt(value, 0), valueFormatter: (value) => `${signed(Math.round(Number(value)), 0)}계약`,
    });
  }

  function renderDerivatives() {
    const section = ((pagePayload && pagePayload.sections) || {}).derivatives || {};
    const basis = historySection(rangeValue("basis"), "derivatives");
    const volumePcr = historySection(rangeValue("volume-pcr"), "derivatives");
    const oiPcr = historySection(rangeValue("oi-pcr"), "derivatives");
    $("derivatives-unavailable").textContent = section && section.status === "UNAVAILABLE" ? unavailable(section.reason) : "";
    renderCboePcr(section.cboe_pcr);
    renderLinePanel("basis-chart", "basis-meta", basis.basis, "basis", "basis");
    renderLinePanel("volume-pcr-chart", "volume-pcr-meta", volumePcr.pcr && volumePcr.pcr.volume, "pcr", "volume-pcr");
    renderLinePanel("oi-pcr-chart", "oi-pcr-meta", oiPcr.pcr && oiPcr.pcr.oi, "pcr", "oi-pcr");
    renderLsInvestorFlow(historySection(rangeValue("ls-flow"), "derivatives"));
    const wall = section && section.wall;
    $("wall-meta").textContent = wall && wall.status === "VALUE" ? (wall.basis_label || `기준일 ${wall.as_of || "—"}`) : "";
    $("wall-unavailable").textContent = wall && wall.status !== "VALUE" ? unavailable(wall.reason) : "";
    $("wall-rows").innerHTML = wall && wall.status === "VALUE" ? (wall.rows || []).map((row) => `<tr>
      <td>${esc(row.date)}</td><td>${esc(row.maturity_month || "—")}</td><td class="num">${fmt(row.underlying_price, 2)}</td>
      <td class="num up">${row.near_call_wall_strike === null || row.near_call_wall_strike === undefined ? (row.near_call_wall_status === "NO_NEAR_WINDOW_OI" ? "창 내 OI 없음" : '<span class="muted">미계산</span>') : fmt(row.near_call_wall_strike, 2)}</td><td class="num">${compact(row.near_call_wall_oi)}</td><td class="num">${row.near_call_wall_distance_pct === null || row.near_call_wall_distance_pct === undefined ? "—" : signed(row.near_call_wall_distance_pct, 1) + "%"}</td>
      <td class="num down">${row.near_put_wall_strike === null || row.near_put_wall_strike === undefined ? (row.near_put_wall_status === "NO_NEAR_WINDOW_OI" ? "창 내 OI 없음" : '<span class="muted">미계산</span>') : fmt(row.near_put_wall_strike, 2)}</td><td class="num">${compact(row.near_put_wall_oi)}</td><td class="num">${row.near_put_wall_distance_pct === null || row.near_put_wall_distance_pct === undefined ? "—" : signed(row.near_put_wall_distance_pct, 1) + "%"}</td>
      <td class="muted">${esc(row.near_wall_note || "±15% 창")}</td>
    </tr>`).join("") : "";
  }

  function flowSection(range) {
    return flowCache.get(range) || ((pagePayload && pagePayload.sections) || {}).flows || {};
  }

  async function loadFlowRange(range) {
    if (flowCache.has(range)) { rerenderSmallCharts(); return; }
    document.querySelectorAll(".market-flow-meta").forEach((node) => { node.textContent = "수급 이력 확인 중"; });
    try {
      const response = await fetch(`/api/market?flows_range=${encodeURIComponent(range)}`);
      const payload = response.ok ? await response.json() : null;
      if (!payload || payload.flows_range !== range || !payload.sections || !payload.sections.flows) {
        throw new Error(response.ok ? "요청 범위를 제공하지 못했습니다." : `HTTP ${response.status}`);
      }
      cachePagePayload(payload);
    } catch (error) {
      const reason = `표시 불가 · ${esc(String(error))}`;
      document.querySelectorAll(".market-flow-meta").forEach((node) => { node.textContent = reason; });
      return;
    }
    rerenderSmallCharts();
  }

  function renderFlowChart(hostId, metaId, market) {
    const host = $(hostId), meta = $(metaId), marketName = market && market.market;
    if (!market || market.status !== "VALUE") {
      host.innerHTML = `<div class="unavailable">${unavailable(market && market.reason)}</div>`; meta.textContent = ""; return;
    }
    const range = rangeValue(`${marketName.toLowerCase()}-flow`, "60D");
    const daily = document.querySelector(`[data-flow-mode="${marketName}"]`).classList.contains("on");
    const hidden = hiddenFlowSeries[marketName];
    const specs = Object.entries(market.series || {}).map(([key, item]) => {
      const points = daily ? item.daily_points : item.cumulative_points;
      return { key, label: item.label, color: flowColors[key], type: daily ? "bar" : "line", points: points || [], hidden: hidden.has(key) };
    });
    meta.innerHTML = `<span>기준일 ${esc(market.as_of || "—")} · ${daily ? "일별" : "선택 기간 누적"} · 단위: 억원</span><span class="market-series-toggles">${specs.map((spec) => `<button type="button" data-flow-series="${spec.key}" class="${spec.hidden ? "off" : ""}"><i style="background:${spec.color}"></i>${esc(spec.label)}</button>`).join("")}</span>`;
    meta.querySelectorAll("[data-flow-series]").forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.flowSeries;
      if (hidden.has(key)) hidden.delete(key); else hidden.add(key);
      renderFlowChart(hostId, metaId, market);
    }));
    const visible = specs.filter((spec) => !spec.hidden);
    renderSvg(host, visible[0] ? visible[0].points : [], {
      height: 210, ariaLabel: `${marketName} 투자자 ${daily ? "일별" : "누적"} 순매수`, series: visible,
      axisFormatter: flowAxis, valueFormatter: flowValue,
    });
  }

  function renderBalanceChart(chartId, metaId, view, color, rangeCard) {
    const host = $(chartId), meta = $(metaId);
    if (!view || view.status !== "VALUE" || !view.series || !view.series.length) {
      host.innerHTML = `<div class="unavailable">${unavailable(view && view.reason)}</div>`; meta.textContent = ""; return;
    }
    const latest = view.series[view.series.length - 1];
    meta.innerHTML = `<b class="num">${compact(latest.v)}원</b><span>기준일 ${esc(view.as_of || "—")}</span>`;
    const points = slicePoints(view.series, rangeValue(rangeCard));
    renderSvg(host, points, { height: 210, color, ariaLabel: `${rangeCard} 잔고 추이`, axisFormatter: compact, valueFormatter: (value) => `${compact(value)}원` });
  }

  function renderFlows() {
    const section = ((pagePayload && pagePayload.sections) || {}).flows || {};
    $("flows-unavailable").textContent = section && section.status === "UNAVAILABLE" ? unavailable(section.reason) : "";
    ["KOSPI", "KOSDAQ"].forEach((marketName) => {
      const range = rangeValue(`${marketName.toLowerCase()}-flow`, "60D");
      const ranged = flowSection(range);
      const market = ((ranged && ranged.markets) || []).find((item) => item.market === marketName);
      renderFlowChart(`${marketName.toLowerCase()}-flow-chart`, `${marketName.toLowerCase()}-flow-meta`, market);
    });
    const credit = historySection(rangeValue("credit"), "flows").credit;
    const lending = historySection(rangeValue("lending"), "flows").lending;
    renderBalanceChart("credit-chart", "credit-meta", credit, "#a8621a", "credit");
    if (lending) { $("lending-panel").hidden = false; renderBalanceChart("lending-chart", "lending-meta", lending, "#2a78d6", "lending"); }
    else $("lending-panel").hidden = true;
    const micro = section && section.microstructure;
    $("micro-unavailable").textContent = micro && micro.status !== "VALUE" ? unavailable(micro.reason) : "";
    const breadth = micro && micro.breadth;
    const lendingSummary = micro && micro.lending_summary;
    $("breadth-unavailable").textContent = breadth && breadth.status !== "VALUE" ? unavailable(breadth.reason) : "";
    $("lending-summary-unavailable").textContent = lendingSummary && lendingSummary.status !== "VALUE" ? unavailable(lendingSummary.reason) : "";
    $("breadth-rows").innerHTML = breadth && breadth.status === "VALUE" ? (breadth.rows || []).map((row) => `<tr>
      <td>${esc(row.market || "—")}</td><td>${esc(row.as_of || "—")}</td>
      <td class="num up">${fmt(row.advancing, 0)}</td><td class="num down">${fmt(row.declining, 0)}</td>
      <td class="num">${fmt(row.unchanged, 0)}</td><td class="num">${fmt(row.ad_ratio, 2)}</td>
    </tr>`).join("") : "";
    $("lending-summary-rows").innerHTML = lendingSummary && lendingSummary.status === "VALUE" ? (lendingSummary.rows || []).map((row) => `<tr>
      <td>${esc(row.market || "—")}</td><td>${esc(row.as_of || "—")}</td>
      <td class="num">${row.value !== undefined && row.value !== null ? compact(row.value) + "원" : "—"}</td>
      <td class="num ${Number(row.change_1d) > 0 ? "up" : Number(row.change_1d) < 0 ? "down" : ""}">${row.change_1d !== undefined && row.change_1d !== null ? signed(Math.round(row.change_1d / 1e8), 0) + "억원" : "—"}</td>
      <td class="num ${Number(row.change_5d) > 0 ? "up" : Number(row.change_5d) < 0 ? "down" : ""}">${row.change_5d !== undefined && row.change_5d !== null ? signed(Math.round(row.change_5d / 1e8), 0) + "억원" : "—"}</td>
    </tr>`).join("") : "";
  }

  function renderValuationChart(chartId, metaId, market, rangeCard) {
    const host = $(chartId), meta = $(metaId);
    if (!market || market.status !== "VALUE") {
      host.innerHTML = `<div class="unavailable">${unavailable(market && market.reason)}</div>`; meta.textContent = ""; return;
    }
    const current = market.current || {}, range = rangeValue(rangeCard);
    meta.innerHTML = `<span class="market-valuation-metric"><span>PER <b class="num">${fmt(current.per, 2)}</b></span><span>5년 백분위 ${percentilePosition(current.per_percentile)}</span></span><span class="market-valuation-metric"><span>PBR <b class="num">${fmt(current.pbr, 2)}</b></span><span>5년 백분위 ${percentilePosition(current.pbr_percentile)}</span></span><span>기준일 ${esc(current.t || "—")}</span>`;
    const series = market.series || {};
    const perPoints = slicePoints(series.per, range), pbrPoints = slicePoints(series.pbr, range);
    host.innerHTML = `<div class="market-valuation-panel"><span class="market-axis-name up">PER · 좌축</span><div data-valuation-series="per"></div></div><div class="market-valuation-panel"><span class="market-axis-name down">PBR · 우축</span><div data-valuation-series="pbr"></div></div>`;
    const nonNegativeAxis = (value) => fmt(Math.max(0, Number(value)), 2);
    renderSvg(host.querySelector('[data-valuation-series="per"]'), perPoints, { height: 120, color: "#c0392b", ariaLabel: `${market.market} 가중 PER`, axisFormatter: nonNegativeAxis, valueFormatter: (value) => fmt(value, 2) });
    renderSvg(host.querySelector('[data-valuation-series="pbr"]'), pbrPoints, { height: 120, color: "#2b62c0", ariaLabel: `${market.market} 가중 PBR`, axisFormatter: nonNegativeAxis, valueFormatter: (value) => fmt(value, 2) });
  }

  function renderValuation() {
    const section = ((pagePayload && pagePayload.sections) || {}).valuation || {};
    $("valuation-unavailable").textContent = section && section.status === "UNAVAILABLE" ? unavailable(section.reason) : "";
    const kospi = historySection(rangeValue("kospi-valuation"), "valuation");
    const kosdaq = historySection(rangeValue("kosdaq-valuation"), "valuation");
    const kospiMarket = ((kospi && kospi.markets) || []).find((market) => market.market === "KOSPI");
    const kosdaqMarket = ((kosdaq && kosdaq.markets) || []).find((market) => market.market === "KOSDAQ");
    renderValuationChart("kospi-valuation-chart", "kospi-valuation-meta", kospiMarket, "kospi-valuation");
    renderValuationChart("kosdaq-valuation-chart", "kosdaq-valuation-meta", kosdaqMarket, "kosdaq-valuation");
  }

  function applyExplanations(explanations, root = document) {
    root.querySelectorAll("[data-explanation]").forEach((node) => {
      const text = explanations[node.dataset.explanation];
      if (!text) return;
      node.title = text;
      if (!node.querySelector(":scope > .metric-info")) node.insertAdjacentHTML("beforeend", ` <span class="metric-info" tabindex="0" aria-label="${esc(text)}">ⓘ</span>`);
    });
  }

  function rerenderSmallCharts() {
    if (!pagePayload) return;
    renderDerivatives(); renderFlows(); renderValuation();
  }

  async function loadHistoryRange(range, cardId) {
    if (historyCache.has(range)) { rerenderSmallCharts(); return; }
    try {
      const response = await fetch(`/api/market?flows_range=${encodeURIComponent(range)}`);
      const payload = response.ok ? await response.json() : null;
      if (!payload || payload.history_range !== range || !payload.sections) {
        throw new Error(response.ok ? "요청 범위를 제공하지 못했습니다." : `HTTP ${response.status}`);
      }
      cachePagePayload(payload);
      rerenderSmallCharts();
    } catch (error) {
      const meta = $(`${cardId}-meta`);
      if (meta) meta.textContent = `표시 불가 · ${esc(String(error))}`;
    }
  }

  function wireControls() {
    syncIndicatorMenu();
    $("indicator-picker-button").addEventListener("click", () => {
      const menu = $("indicator-menu"); menu.hidden = !menu.hidden;
      $("indicator-picker-button").setAttribute("aria-expanded", String(!menu.hidden));
    });
    document.querySelectorAll("#indicator-menu [data-indicator]").forEach((row) => {
      const key = row.dataset.indicator;
      row.querySelector('input[type="checkbox"]').addEventListener("change", (event) => changeIndicator(key, { enabled: event.target.checked }));
      row.querySelector("select").addEventListener("change", (event) => changeIndicator(key, { placement: event.target.value }));
    });
    document.addEventListener("click", (event) => {
      if (!$("indicator-picker").contains(event.target)) { $("indicator-menu").hidden = true; $("indicator-picker-button").setAttribute("aria-expanded", "false"); }
    });
    ["#market-chart-interval", "#market-chart-range"].forEach((selector) => document.querySelectorAll(`${selector} button`).forEach((button) => button.addEventListener("click", () => {
      document.querySelectorAll(`${selector} button`).forEach((item) => item.classList.remove("on")); button.classList.add("on"); loadMainChart();
    })));
    document.querySelectorAll("[data-range-card] button").forEach((button) => button.addEventListener("click", () => {
      const group = button.closest("[data-range-card]"); group.querySelectorAll("button").forEach((item) => item.classList.remove("on")); button.classList.add("on");
      if (group.dataset.rangeCard.endsWith("-flow")) loadFlowRange(button.dataset.v); else loadHistoryRange(button.dataset.v, group.dataset.rangeCard);
    }));
    document.querySelectorAll("[data-flow-mode]").forEach((button) => button.addEventListener("click", () => { button.classList.toggle("on"); rerenderSmallCharts(); }));
    $("ls-flow-scope").addEventListener("change", (event) => { selectedLsScope = event.target.value; rerenderSmallCharts(); });
    $("market-chart-symbol").addEventListener("change", loadMainChart);
  }

  async function boot() {
    wireControls();
    try { const response = await fetch("/api/market"); pagePayload = response.ok ? await response.json() : { sections: {} }; }
    catch (_error) { pagePayload = { sections: {} }; }
    cachePagePayload(pagePayload);
    const symbols = pagePayload.chart_symbols || [];
    $("market-chart-symbol").innerHTML = symbols.length ? symbols.map((item) => `<option value="${esc(item.symbol)}">${esc(item.name)}</option>`).join("") : `<option value="KOSPI">KOSPI</option>`;
    applyExplanations(pagePayload.explanations || {}); rerenderSmallCharts(); loadMainChart();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
