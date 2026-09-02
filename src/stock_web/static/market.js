/* Market page renderer. Reads retained local API payloads only. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
  const fmt = (value, digits = 2) => value === null || value === undefined || Number.isNaN(Number(value))
    ? "—" : Number(value).toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (value, digits = 0) => value === null || value === undefined ? "—" : `${Number(value) > 0 ? "+" : ""}${fmt(value, digits)}`;
  const unavailable = (reason) => `표시 불가 · ${esc(reason || "보존 데이터 없음")}`;
  const chartOptions = () => ({
    layout: { background: { color: "#fff" }, textColor: "#6b6660", fontFamily: "IBM Plex Sans KR, system-ui", fontSize: 10 },
    grid: { vertLines: { color: "#f0ece5" }, horzLines: { color: "#e6e1d8" } },
    rightPriceScale: { borderColor: "#d9d3ca" },
    timeScale: { borderColor: "#d9d3ca", timeVisible: false },
    crosshair: { mode: 1 }, autoSize: true,
  });
  const pointData = (points) => (points || []).filter((point) => point.v !== null && point.v !== undefined).map((point) => ({ time: point.t, value: point.v }));

  const indicatorLabels = {
    ma5: "MA5", ma20: "MA20", ma60: "MA60", ma120: "MA120",
    bollinger: "볼린저 (20, 2)", volume: "거래량", rsi14: "RSI14",
    macd: "MACD (12, 26, 9)", stochastic: "스토캐스틱 (14, 3)",
  };
  const allowedIndicators = Object.keys(indicatorLabels);
  const defaultIndicators = ["ma5", "ma20", "ma60", "ma120", "volume"];
  const lineColors = { ma5: "#4a3aa7", ma20: "#2a78d6", ma60: "#eb6834", ma120: "#1baf7a" };
  let activeIndicators = loadIndicatorState();
  let mainChart, candleSeries;
  let dynamicSeries = [];
  const smallCharts = [];

  function loadIndicatorState() {
    try {
      const saved = JSON.parse(localStorage.getItem("stock-web-market-indicators"));
      if (Array.isArray(saved)) {
        return [...new Set(saved.filter((item) => allowedIndicators.includes(item)))];
      }
    } catch (_error) { /* default below */ }
    return [...defaultIndicators];
  }

  function saveIndicatorState() {
    try { localStorage.setItem("stock-web-market-indicators", JSON.stringify(activeIndicators)); } catch (_error) { /* storage is optional */ }
  }

  function syncIndicatorMenu() {
    document.querySelectorAll("#indicator-menu input[type=checkbox]").forEach((input) => {
      input.checked = activeIndicators.includes(input.value);
    });
  }

  function setIndicator(value, enabled) {
    activeIndicators = enabled
      ? [...new Set([...activeIndicators, value])]
      : activeIndicators.filter((item) => item !== value);
    saveIndicatorState();
    syncIndicatorMenu();
    loadMainChart();
  }

  function selectedButtonValue(selector, fallback) {
    const button = document.querySelector(`${selector} button.on`);
    return button ? button.dataset.v : fallback;
  }

  function ensureMainChart() {
    if (mainChart || !window.LightweightCharts) return;
    mainChart = LightweightCharts.createChart($("market-chart"), chartOptions());
    candleSeries = mainChart.addCandlestickSeries({
      upColor: "#fff", downColor: "#2b62c0", borderUpColor: "#c0392b",
      borderDownColor: "#2b62c0", wickUpColor: "#c0392b", wickDownColor: "#2b62c0",
    });
  }

  function addLine(options, data) {
    const series = mainChart.addLineSeries({
      lineWidth: 1, priceLineVisible: false, lastValueVisible: false, ...options,
    });
    series.setData(pointData(data));
    dynamicSeries.push(series);
    return series;
  }

  function addHistogram(options, data, colorBuilder) {
    const series = mainChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, ...options });
    series.setData((data || []).filter((point) => point.v !== null && point.v !== undefined).map((point) => ({
      time: point.t, value: point.v, color: colorBuilder ? colorBuilder(point.v, point) : options.color,
    })));
    dynamicSeries.push(series);
    return series;
  }

  function clearDynamicSeries() {
    if (!mainChart) return;
    dynamicSeries.forEach((series) => mainChart.removeSeries(series));
    dynamicSeries = [];
  }

  function panelMargins(index, height) {
    return { top: Math.max(0.04, 1 - (index + 1) * height), bottom: index * height };
  }

  function renderMainChart(payload) {
    const stats = $("market-chart-stats");
    const legend = $("market-chart-legend");
    if (!payload || !payload.candles || !payload.candles.length) {
      if (mainChart) { clearDynamicSeries(); candleSeries.setData([]); }
      else $("market-chart").innerHTML = `<div class="unavailable">${unavailable(payload && payload.reason)}</div>`;
      stats.innerHTML = "";
      legend.innerHTML = "";
      return;
    }
    ensureMainChart();
    if (!mainChart) {
      $("market-chart").innerHTML = `<div class="unavailable">${unavailable("차트 라이브러리 로드 실패")}</div>`;
      return;
    }
    clearDynamicSeries();
    candleSeries.setData(payload.candles.map((candle) => ({
      time: candle.t, open: candle.o, high: candle.h, low: candle.l, close: candle.c,
    })));
    const values = payload.indicators || {};
    Object.keys(lineColors).forEach((name) => {
      if (activeIndicators.includes(name) && values[name]) addLine({ color: lineColors[name] }, values[name]);
    });
    if (activeIndicators.includes("bollinger") && values.bollinger) {
      addLine({ color: "#a8621a", lineStyle: 2 }, values.bollinger.upper);
      addLine({ color: "#b5aea4", lineStyle: 2 }, values.bollinger.middle);
      addLine({ color: "#a8621a", lineStyle: 2 }, values.bollinger.lower);
    }

    const lower = ["volume", "rsi14", "macd", "stochastic"].filter((name) => activeIndicators.includes(name) && values[name]);
    const candleColors = new Map(payload.candles.map((candle) => [
      candle.t, candle.c >= candle.o ? "rgba(192,57,43,.42)" : "rgba(43,98,192,.42)",
    ]));
    const height = lower.length ? Math.min(0.15, 0.62 / lower.length) : 0;
    mainChart.priceScale("right").applyOptions({ scaleMargins: { top: 0.04, bottom: lower.length * height + 0.02 } });
    lower.forEach((name, index) => {
      const scaleId = `market-${name}`;
      if (name === "volume") {
        addHistogram({ priceScaleId: scaleId, priceFormat: { type: "volume" } }, values.volume,
          (_value, point) => candleColors.get(point.t) || "rgba(107,102,96,.35)");
      } else if (name === "rsi14") {
        addLine({ priceScaleId: scaleId, color: "#4a3aa7" }, values.rsi14);
      } else if (name === "macd") {
        addLine({ priceScaleId: scaleId, color: "#2a78d6" }, values.macd.macd);
        addLine({ priceScaleId: scaleId, color: "#eb6834" }, values.macd.signal);
        addHistogram({ priceScaleId: scaleId }, values.macd.histogram,
          (value) => value >= 0 ? "rgba(192,57,43,.38)" : "rgba(43,98,192,.38)");
      } else {
        addLine({ priceScaleId: scaleId, color: "#2a78d6" }, values.stochastic.k);
        addLine({ priceScaleId: scaleId, color: "#eb6834" }, values.stochastic.d);
      }
      mainChart.priceScale(scaleId).applyOptions({ scaleMargins: panelMargins(index, height), borderVisible: false });
    });
    mainChart.timeScale().fitContent();
    stats.innerHTML = `<span class="muted">기준일 <b class="num">${esc(payload.as_of)}</b></span>${payload.stats && payload.stats.rsi14 !== null ? `<span class="muted">RSI14 <b class="num">${fmt(payload.stats.rsi14, 1)}</b></span>` : ""}`;
    legend.innerHTML = `<span class="market-symbol-label"><i style="background:#1f1d1a"></i>${esc(payload.symbol_name || payload.symbol)}</span>` +
      activeIndicators.map((name) => `<span class="market-indicator-label"><i style="background:${lineColors[name] || "#6b6660"}"></i>${esc(indicatorLabels[name])}<button type="button" data-remove-indicator="${name}" aria-label="${esc(indicatorLabels[name])} 제거">×</button></span>`).join("");
    legend.querySelectorAll("[data-remove-indicator]").forEach((button) => button.addEventListener("click", () => setIndicator(button.dataset.removeIndicator, false)));
  }

  async function loadMainChart() {
    const symbol = $("market-chart-symbol").value || "KOSPI";
    const interval = selectedButtonValue("#market-chart-interval", "1d");
    const range = selectedButtonValue("#market-chart-range", "6M");
    const params = new URLSearchParams({ symbol, interval, range, indicators: activeIndicators.join(",") });
    $("market-chart-stats").textContent = "차트 확인 중";
    try {
      const response = await fetch(`/api/market/chart?${params}`);
      renderMainChart(response.ok ? await response.json() : { reason: `HTTP ${response.status}` });
    } catch (error) { renderMainChart({ reason: String(error) }); }
  }

  function makeSmallChart(host, options = {}) {
    if (!window.LightweightCharts || !host) return null;
    const chart = LightweightCharts.createChart(host, { ...chartOptions(), ...options });
    smallCharts.push(chart);
    return chart;
  }

  function renderLinePanel(chartId, metaId, view, seriesSpecs) {
    const host = $(chartId), meta = $(metaId);
    if (!view || view.status !== "VALUE" || !view.series || !view.series.length) {
      host.innerHTML = `<div class="unavailable">${unavailable(view && view.reason)}</div>`;
      meta.textContent = "";
      return;
    }
    meta.innerHTML = `<b class="num">${esc(view.display_value || "")}</b><span>${esc(view.as_of || "")}</span>`;
    const chart = makeSmallChart(host);
    if (!chart) { host.innerHTML = `<div class="unavailable">${unavailable("차트 라이브러리 로드 실패")}</div>`; return; }
    (seriesSpecs || [{ color: "#1f1d1a", data: view.series }]).forEach((spec) => {
      const line = chart.addLineSeries({ color: spec.color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      line.setData(pointData(spec.data));
    });
    chart.timeScale().fitContent();
  }

  function renderDerivatives(section) {
    $("derivatives-unavailable").textContent = section && section.status === "UNAVAILABLE" ? unavailable(section.reason) : "";
    renderLinePanel("basis-chart", "basis-meta", section && section.basis);
    renderLinePanel("volume-pcr-chart", "volume-pcr-meta", section && section.pcr && section.pcr.volume);
    renderLinePanel("oi-pcr-chart", "oi-pcr-meta", section && section.pcr && section.pcr.oi);
    const ls = section && section.ls_flow;
    $("ls-flow").innerHTML = ls && ls.status === "VALUE"
      ? `<b class="num ${Number(ls.value) > 0 ? "up" : Number(ls.value) < 0 ? "down" : ""}">${esc(ls.display_value)}</b><span>${esc(ls.as_of || "")}</span><small>${esc(ls.warning || "Raw descriptive only")}</small>`
      : `<div class="unavailable">${unavailable(ls && ls.reason)}</div>`;
    const wall = section && section.wall;
    $("wall-unavailable").textContent = wall && wall.status !== "VALUE" ? unavailable(wall.reason) : "";
    $("wall-rows").innerHTML = wall && wall.status === "VALUE" ? (wall.rows || []).map((row) => `<tr>
      <td>${esc(row.date)}</td><td>${esc(row.maturity_month || "—")}</td><td class="num">${fmt(row.underlying_price)}</td>
      <td class="num up">${fmt(row.call_wall_strike)}</td><td class="num">${fmt(row.call_wall_oi, 0)}</td><td class="num">${row.call_wall_distance_pct === null || row.call_wall_distance_pct === undefined ? "—" : signed(row.call_wall_distance_pct, 1) + "%"}</td>
      <td class="num down">${fmt(row.put_wall_strike)}</td><td class="num">${fmt(row.put_wall_oi, 0)}</td><td class="num">${row.put_wall_distance_pct === null || row.put_wall_distance_pct === undefined ? "—" : signed(row.put_wall_distance_pct, 1) + "%"}</td>
    </tr>`).join("") : "";
  }

  function renderFlowChart(hostId, metaId, market) {
    const host = $(hostId), meta = $(metaId);
    if (!market || market.status !== "VALUE") {
      host.innerHTML = `<div class="unavailable">${unavailable(market && market.reason)}</div>`;
      meta.textContent = "";
      return;
    }
    meta.innerHTML = `<span><i class="legend-dot foreign"></i>외국인</span><span><i class="legend-dot institution"></i>기관</span><span><i class="legend-dot individual"></i>개인</span>`;
    const chart = makeSmallChart(host);
    if (!chart) return;
    const opacity = { foreigner: ".62", institution: ".42", individual: ".25" };
    Object.entries(market.series || {}).forEach(([key, item]) => {
      const series = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      series.setData((item.points || []).filter((point) => point.v !== null).map((point) => ({
        time: point.t, value: point.v,
        color: Number(point.v) >= 0 ? `rgba(192,57,43,${opacity[key] || ".4"})` : `rgba(43,98,192,${opacity[key] || ".4"})`,
      })));
    });
    chart.timeScale().fitContent();
  }

  function renderBalanceChart(chartId, metaId, view, color) {
    const host = $(chartId), meta = $(metaId);
    if (!view || view.status !== "VALUE" || !view.series || !view.series.length) {
      host.innerHTML = `<div class="unavailable">${unavailable(view && view.reason)}</div>`;
      meta.textContent = "";
      return;
    }
    const latest = view.series[view.series.length - 1];
    meta.innerHTML = `<b class="num">${fmt(latest.v / 1e12, 1)}조원</b><span>${esc(view.as_of || "")}</span>`;
    const chart = makeSmallChart(host);
    if (!chart) return;
    const line = chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    line.setData(pointData(view.series));
    chart.timeScale().fitContent();
  }

  function renderFlows(section) {
    $("flows-unavailable").textContent = section && section.status === "UNAVAILABLE" ? unavailable(section.reason) : "";
    const byMarket = Object.fromEntries(((section && section.markets) || []).map((market) => [market.market, market]));
    renderFlowChart("kospi-flow-chart", "kospi-flow-meta", byMarket.KOSPI);
    renderFlowChart("kosdaq-flow-chart", "kosdaq-flow-meta", byMarket.KOSDAQ);
    renderBalanceChart("credit-chart", "credit-meta", section && section.credit, "#a8621a");
    if (section && section.lending) {
      $("lending-panel").hidden = false;
      renderBalanceChart("lending-chart", "lending-meta", section.lending, "#2a78d6");
    } else $("lending-panel").hidden = true;
    const micro = section && section.microstructure;
    $("micro-unavailable").textContent = micro && micro.status !== "VALUE" ? unavailable(micro.reason) : "";
    $("micro-rows").innerHTML = micro && micro.status === "VALUE" ? (micro.rows || []).map((row) => `<tr>
      <td>${esc(row.name)}</td><td>${esc(row.market || "—")}</td><td>${esc(row.as_of || "—")}</td>
      <td class="num">${row.advancing !== undefined ? fmt(row.advancing, 0) : row.value !== undefined ? fmt(row.value / 1e12, 2) + "조원" : "—"}</td>
      <td class="num">${row.declining !== undefined ? fmt(row.declining, 0) : row.change_1d !== undefined ? signed(row.change_1d / 1e8, 0) + "억원" : "—"}</td>
      <td class="num">${row.unchanged !== undefined ? fmt(row.unchanged, 0) : row.change_5d !== undefined ? signed(row.change_5d / 1e8, 0) + "억원" : "—"}</td>
      <td class="num">${fmt(row.ad_ratio)}</td>
    </tr>`).join("") : "";
  }

  function renderValuationChart(chartId, metaId, market) {
    const host = $(chartId), meta = $(metaId);
    if (!market || market.status !== "VALUE") {
      host.innerHTML = `<div class="unavailable">${unavailable(market && market.reason)}</div>`;
      meta.textContent = "";
      return;
    }
    const current = market.current || {};
    meta.innerHTML = `<span>PER <b class="num">${fmt(current.per)}</b> · 5년 백분위 ${fmt(current.per_percentile, 0)}%</span><span>PBR <b class="num">${fmt(current.pbr)}</b> · 5년 백분위 ${fmt(current.pbr_percentile, 0)}%</span>`;
    const chart = makeSmallChart(host);
    if (!chart) return;
    const per = chart.addLineSeries({ color: "#c0392b", lineWidth: 2, priceScaleId: "per", priceLineVisible: false, lastValueVisible: false });
    const pbr = chart.addLineSeries({ color: "#2b62c0", lineWidth: 2, priceScaleId: "pbr", priceLineVisible: false, lastValueVisible: false });
    per.setData(pointData(market.per)); pbr.setData(pointData(market.pbr));
    if (current.t) {
      per.setMarkers([{ time: current.t, position: "inBar", color: "#c0392b", shape: "circle", text: "현재 PER" }]);
      pbr.setMarkers([{ time: current.t, position: "inBar", color: "#2b62c0", shape: "circle", text: "현재 PBR" }]);
    }
    chart.priceScale("per").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.08 } });
    chart.priceScale("pbr").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.08 }, visible: false });
    chart.timeScale().fitContent();
  }

  function renderValuation(section) {
    $("valuation-unavailable").textContent = section && section.status === "UNAVAILABLE" ? unavailable(section.reason) : "";
    const byMarket = Object.fromEntries(((section && section.markets) || []).map((market) => [market.market, market]));
    renderValuationChart("kospi-valuation-chart", "kospi-valuation-meta", byMarket.KOSPI);
    renderValuationChart("kosdaq-valuation-chart", "kosdaq-valuation-meta", byMarket.KOSDAQ);
  }

  function wireControls() {
    syncIndicatorMenu();
    $("indicator-picker-button").addEventListener("click", () => {
      const menu = $("indicator-menu");
      menu.hidden = !menu.hidden;
      $("indicator-picker-button").setAttribute("aria-expanded", String(!menu.hidden));
    });
    document.querySelectorAll("#indicator-menu input[type=checkbox]").forEach((input) => input.addEventListener("change", () => setIndicator(input.value, input.checked)));
    document.addEventListener("click", (event) => {
      if (!$("indicator-picker").contains(event.target)) {
        $("indicator-menu").hidden = true;
        $("indicator-picker-button").setAttribute("aria-expanded", "false");
      }
    });
    ["#market-chart-interval", "#market-chart-range"].forEach((selector) => {
      document.querySelectorAll(`${selector} button`).forEach((button) => button.addEventListener("click", () => {
        document.querySelectorAll(`${selector} button`).forEach((item) => item.classList.remove("on"));
        button.classList.add("on"); loadMainChart();
      }));
    });
    $("market-chart-symbol").addEventListener("change", loadMainChart);
  }

  async function boot() {
    wireControls();
    let payload;
    try {
      const response = await fetch("/api/market");
      payload = response.ok ? await response.json() : { sections: {} };
    } catch (_error) { payload = { sections: {} }; }
    const symbols = payload.chart_symbols || [];
    $("market-chart-symbol").innerHTML = symbols.length
      ? symbols.map((item) => `<option value="${esc(item.symbol)}">${esc(item.name)}</option>`).join("")
      : `<option value="KOSPI">KOSPI</option>`;
    const sections = payload.sections || {};
    renderDerivatives(sections.derivatives || { status: "UNAVAILABLE", reason: "파생 상세를 읽을 수 없습니다." });
    renderFlows(sections.flows || { status: "UNAVAILABLE", reason: "수급·잔고 상세를 읽을 수 없습니다." });
    renderValuation(sections.valuation || { status: "UNAVAILABLE", reason: "밸류에이션을 읽을 수 없습니다." });
    loadMainChart();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
