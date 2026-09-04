/* Home page renderer. Everything is read-only; a missing section renders as 표시 불가. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const publicMode = typeof document !== "undefined" && document.body && document.body.dataset.public === "1";
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 1) => (v === null || v === undefined) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
  const cls = (v) => (v === null || v === undefined) ? "muted" : (v > 0 ? "up" : v < 0 ? "down" : "muted");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const asof = (value) => {
    if (!value) return "—";
    const text = String(value);
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text.slice(5);
    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) return text;
    const parts = Object.fromEntries(new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(parsed).map((part) => [part.type, part.value]));
    return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  };
  const unavailable = (why) => `<div class="unavailable">표시 불가${why ? " · " + esc(why) : ""}</div>`;

  function rsiWilder(closes, period = 14) {
    if (!Number.isInteger(period) || period < 1) throw new RangeError("period must be at least 1");
    const result = [];
    let previous = null, seedGains = [], seedLosses = [], averageGain = null, averageLoss = null;
    (closes || []).forEach((rawClose) => {
      const close = Number(rawClose);
      if (rawClose === null || rawClose === undefined || !Number.isFinite(close)) {
        result.push(null); previous = averageGain = averageLoss = null; seedGains = []; seedLosses = []; return;
      }
      if (previous === null) { result.push(null); previous = close; return; }
      const change = close - previous, gain = Math.max(change, 0), loss = Math.max(-change, 0);
      previous = close;
      if (averageGain === null || averageLoss === null) {
        seedGains.push(gain); seedLosses.push(loss);
        if (seedGains.length < period) { result.push(null); return; }
        averageGain = seedGains.reduce((sum, value) => sum + value, 0) / period;
        averageLoss = seedLosses.reduce((sum, value) => sum + value, 0) / period;
      } else {
        averageGain = (averageGain * (period - 1) + gain) / period;
        averageLoss = (averageLoss * (period - 1) + loss) / period;
      }
      result.push(averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss));
    });
    return result;
  }
  window.SIIndicators = { rsiWilder };

  function formatCompactKorean(value) {
    if (value === null || value === undefined || value === "") return "—";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    const absolute = Math.abs(numeric);
    let scaled = numeric, unit = "", digits = Number.isInteger(numeric) ? 0 : 1;
    if (absolute >= 1e12) { scaled = numeric / 1e12; unit = "조"; digits = 1; }
    else if (absolute >= 1e8) { scaled = numeric / 1e8; unit = "억"; digits = absolute >= 1e11 ? 0 : 1; }
    else if (absolute >= 1e4) { scaled = numeric / 1e4; unit = "만"; digits = 1; }
    return `${scaled.toLocaleString("ko-KR", { maximumFractionDigits: digits })}${unit}`;
  }

  function formatPercent(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : "—";
  }

  function formatSharePercent(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const numeric = Number(value);
    if (numeric > 0 && numeric < 0.05) return "<0.1%";
    return `${fmt(numeric, 0)}%`;
  }

  function signedEok(value, digits = 1) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const numeric = Number(value) / 1e8;
    const sign = numeric > 0 ? "+" : numeric < 0 ? "−" : "";
    return `${sign}${Math.abs(numeric).toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }

  function formatPcr(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value).toFixed(2) : "—";
  }

  function brokerReportedPnl(section, metric) {
    return (metric || {}).broker_reported_pnl_krw ?? (section || {}).broker_reported_pnl_krw;
  }

  function renderLineChart(host, rawPoints, options = {}) {
    if (!host) return;
    const normalize = (points) => (points || []).filter((point) => point && Number.isFinite(Number(point.v))).map((point) => ({ ...point, v: Number(point.v), ms: Date.parse(`${point.t}T00:00:00Z`) })).filter((point) => Number.isFinite(point.ms));
    const primary = normalize(rawPoints);
    let benchmark = normalize(options.benchmark);
    const specs = Array.isArray(options.series) && options.series.length
      ? options.series.map((spec, index) => ({
        key: spec.key || `series-${index}`, label: spec.label || "", color: spec.color || "#1f1d1a",
        type: spec.type === "bar" ? "bar" : "line", points: normalize(spec.points), hidden: Boolean(spec.hidden),
      })).filter((spec) => !spec.hidden)
      : [{ key: "value", label: options.valueLabel || "", color: options.color || "#1f1d1a", type: "line", points: primary, hidden: false }];
    const timePoints = specs.flatMap((spec) => spec.points);
    if (timePoints.length < 2) {
      host.innerHTML = `<div class="unavailable">${esc(options.emptyMessage || "관측이 2개 이상이면 선이 표시됩니다.")}</div>`;
      return;
    }
    const orderedTimes = [...new Map(timePoints.sort((a, b) => a.ms - b.ms).map((point) => [point.ms, point])).values()];
    const start = orderedTimes[0].ms, finish = orderedTimes[orderedTimes.length - 1].ms;
    benchmark = benchmark.filter((point) => point.ms >= start && point.ms <= finish);
    if (!options.series && benchmark.length && benchmark[0].v && primary[0] && options.rebaseBenchmark !== false) {
      const benchmarkScale = primary[0].v / benchmark[0].v;
      benchmark = benchmark.map((point) => ({ ...point, v: point.v * benchmarkScale }));
      specs.push({ key: "benchmark", label: options.benchmarkLabel || "", color: options.benchmarkColor || "#2b62c0", type: "line", points: benchmark, hidden: false });
    }
    const width = 640, height = Number(options.height || 220), left = 62, right = 16, top = 16, bottom = 30;
    const plotW = width - left - right, plotH = height - top - bottom;
    const allValues = specs.flatMap((spec) => spec.points.map((point) => point.v));
    if (specs.some((spec) => spec.type === "bar")) allValues.push(0);
    (options.guides || []).forEach((guide) => { if (Number.isFinite(Number(guide.value))) allValues.push(Number(guide.value)); });
    const rawMin = Math.min(...allValues), rawMax = Math.max(...allValues), padding = (rawMax - rawMin) * 0.08 || Math.max(Math.abs(rawMax) * 0.02, 1);
    const min = rawMin - padding, max = rawMax + padding;
    const span = finish - start || 1;
    const indexByTime = new Map(orderedTimes.map((point, index) => [point.ms, index]));
    const x = options.xMode === "index"
      ? (point) => left + (indexByTime.get(point.ms) || 0) / Math.max(orderedTimes.length - 1, 1) * plotW
      : (point) => left + (point.ms - start) / span * plotW;
    const y = (value) => top + (max - value) / (max - min) * plotH;
    const path = (series) => series.filter((point) => point.ms >= start && point.ms <= finish).map((point, index) => `${index ? "L" : "M"}${x(point).toFixed(2)} ${y(point.v).toFixed(2)}`).join(" ");
    const yTicks = Array.from({ length: 4 }, (_, index) => min + (max - min) * index / 3);
    const tickIndexes = [...new Set([0, Math.floor((orderedTimes.length - 1) / 2), orderedTimes.length - 1])];
    const axisFormatter = options.axisFormatter || ((value) => `${(value / 1e8).toFixed(2)}억`);
    const valueFormatter = options.valueFormatter || ((value) => `₩${Math.round(value).toLocaleString("ko-KR")}`);
    const barSpecs = specs.filter((spec) => spec.type === "bar");
    const step = plotW / Math.max(orderedTimes.length - 1, 1);
    const barWidth = Math.max(1, Math.min(10, step * .72) / Math.max(barSpecs.length, 1));
    host.innerHTML = `<svg class="si-line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(options.ariaLabel || "자산 추이")}">
      ${yTicks.map((value) => `<line x1="${left}" x2="${width - right}" y1="${y(value)}" y2="${y(value)}" class="si-grid"></line><text x="${left - 7}" y="${y(value) + 3}" text-anchor="end" class="si-axis-label">${esc(axisFormatter(value))}</text>`).join("")}
      <line x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}" class="si-axis"></line>
      <line x1="${left}" x2="${width - right}" y1="${height - bottom}" y2="${height - bottom}" class="si-axis"></line>
      ${tickIndexes.map((index) => `<text x="${x(orderedTimes[index])}" y="${height - 9}" text-anchor="${index === 0 ? "start" : index === orderedTimes.length - 1 ? "end" : "middle"}" class="si-axis-label">${esc(orderedTimes[index].t)}</text>`).join("")}
      ${(options.guides || []).map((guide) => `<line x1="${left}" x2="${width - right}" y1="${y(Number(guide.value))}" y2="${y(Number(guide.value))}" class="si-guide-line"></line><text x="${width - right - 3}" y="${y(Number(guide.value)) - 3}" text-anchor="end" class="si-guide-label">${esc(guide.label || guide.value)}</text>`).join("")}
      ${barSpecs.map((spec, seriesIndex) => spec.points.map((point) => { const zero = y(0), py = y(point.v), offset = (seriesIndex - (barSpecs.length - 1) / 2) * barWidth; return `<rect x="${x(point) + offset - barWidth / 2}" y="${Math.min(zero, py)}" width="${barWidth}" height="${Math.max(1, Math.abs(zero - py))}" fill="${esc(spec.color)}" class="si-series-bar"></rect>`; }).join("")).join("")}
      ${specs.filter((spec) => spec.type === "line" && spec.points.length > 1).map((spec, index) => `<path d="${path(spec.points)}" class="${index ? "si-benchmark-line" : "si-value-line"} si-series-line" style="stroke:${esc(spec.color)}"></path>`).join("")}
      ${primary.filter((point) => point.partial).map((point) => `<circle cx="${x(point)}" cy="${y(point.v)}" r="2.5" class="si-partial-point"></circle>`).join("")}
      <g class="si-hover" style="display:none"><line y1="${top}" y2="${height - bottom}" class="si-hover-line"></line><g class="si-tooltip"></g></g>
    </svg>`;
    const svg = host.querySelector("svg"), hover = svg.querySelector(".si-hover"), vertical = hover.querySelector("line"), tooltip = hover.querySelector(".si-tooltip");
    svg.addEventListener("pointermove", (event) => {
      const rect = svg.getBoundingClientRect();
      const targetX = (event.clientX - rect.left) / rect.width * width;
      const nearest = orderedTimes.reduce((best, point) => Math.abs(x(point) - targetX) < Math.abs(x(best) - targetX) ? point : best, orderedTimes[0]);
      const rows = specs.map((spec) => ({ spec, point: spec.points.find((point) => point.ms === nearest.ms) })).filter((item) => item.point);
      const px = x(nearest), boxWidth = 190, boxHeight = 22 + rows.length * 16;
      const tooltipX = Math.min(Math.max(px + 8, left + 2), width - right - boxWidth - 2), tooltipY = top + 2;
      hover.style.display = ""; vertical.setAttribute("x1", px); vertical.setAttribute("x2", px);
      tooltip.innerHTML = `<rect x="${tooltipX}" y="${tooltipY}" width="${boxWidth}" height="${boxHeight}" rx="4" class="si-tooltip-bg"></rect><text x="${tooltipX + 8}" y="${tooltipY + 14}" class="si-tooltip-date">${esc(nearest.t)}${nearest.partial ? " · 부분 관측" : ""}</text>${rows.map((item, index) => `<text x="${tooltipX + 8}" y="${tooltipY + 31 + index * 16}" class="si-tooltip-value" style="fill:${esc(item.spec.color)}">${esc(item.spec.label ? item.spec.label + " " : "")}${esc(valueFormatter(item.point.v, item.spec))}</text>`).join("")}`;
    });
    svg.addEventListener("pointerleave", () => { hover.style.display = "none"; });
  }
  window.SIChart = { renderLineChart };
  Object.assign(window.SIChart, { formatCompactKorean, formatPercent, formatPcr });

  function sparkline(values, w = 140, h = 22) {
    const numeric = (values || []).map((point) => typeof point === "object" ? Number(point.v) : Number(point)).filter(Number.isFinite);
    if (numeric.length < 2) return "";
    const lo = Math.min(...numeric), hi = Math.max(...numeric), pad = (hi - lo) * 0.1 || 1;
    const coordinates = numeric.map((v, i) => ({ x: 1 + (w - 2) * i / (numeric.length - 1), y: 1 + (h - 2) - (h - 2) * (v - lo + pad) / (hi - lo + 2 * pad) }));
    const points = coordinates.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
    const last = coordinates[coordinates.length - 1];
    const color = numeric[numeric.length - 1] > numeric[0] ? "#c0392b" : numeric[numeric.length - 1] < numeric[0] ? "#2b62c0" : "#1f1d1a";
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round"></polyline><circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="2.2" fill="${color}"></circle></svg>`;
  }

  // ---- regime -------------------------------------------------------------
  const placeholderEvidence = new Set(["근거 없음", "표시 불가", "수집 추가 필요"]);
  function regimeRow(row) {
    const item = Array.isArray(row) ? { label: row[0], value: row[1], hint: row[2] } : (row || {});
    const value = item.value ?? item.display ?? "—";
    return {
      label: String(item.label ?? item.name ?? "").replaceAll("순위", "백분위"),
      value,
      hint: item.hint || "",
      hidden: item.evidence === false && placeholderEvidence.has(String(value).trim()),
    };
  }
  function regimeRows(rows, rowClass = "") {
    const normalized = (rows || []).map(regimeRow), hidden = normalized.filter((row) => row.hidden).length;
    return normalized.filter((row) => !row.hidden).map((row) => `<div${rowClass ? ` class="${rowClass}"` : ""}><span>${esc(row.label)}</span><span class="num"><b>${esc(row.value)}</b>${row.hint ? ` <small>· ${esc(row.hint)}</small>` : ""}</span></div>`).join("") +
      (hidden ? `<div class="regime-hidden-note">근거 없는 지표 ${hidden}개 숨김</div>` : "");
  }
  function regimeCompact(rows) {
    // Collapsed band: one wrapped line of "label value" pairs so the card is not empty.
    const items = (rows || []).map(regimeRow).filter((row) => !row.hidden && !placeholderEvidence.has(String(row.value).trim()));
    if (!items.length) return `<span class="muted">근거 없음</span>`;
    return items.map((row) => `<span><span class="k">${esc(row.label)}</span> <b>${esc(row.value)}</b></span>`).join('<span class="sep">·</span>');
  }
  const regimeEvidenceStorageKey = "si.regime.evidence";
  function loadRegimeEvidenceOpen() {
    try { return localStorage.getItem(regimeEvidenceStorageKey) === "open"; }
    catch (_error) { return false; }
  }
  function applyRegimeEvidenceState(open, remember = false) {
    const section = $("regime"), strip = $("regime-evidence-strip");
    if (!section) return;
    section.dataset.expanded = open ? "true" : "false";
    if (strip) strip.hidden = !open;
    const toggle = $("regime-toggle");
    if (toggle) {
      toggle.textContent = open ? "근거 접기 ▴" : "근거 펼치기 ▾";
      toggle.setAttribute("aria-expanded", String(open));
    }
    if (remember) {
      try { localStorage.setItem(regimeEvidenceStorageKey, open ? "open" : "closed"); }
      catch (_error) { /* optional preference */ }
    }
  }
  function renderRegimeEvidenceStrip(sec) {
    const host = $("regime-evidence-strip");
    if (!host) return;
    const markets = Array.isArray(sec && sec.markets) ? sec.markets : [];
    const riskRows = sec && sec.risk && Array.isArray(sec.risk.rows)
      ? sec.risk.rows : ((markets[2] || {}).evidence || []);
    const evidenceCard = (title, rows) => `<div class="regime-evidence-card"><div class="t">${esc(title)}</div><div class="ev">${regimeRows(rows)}</div></div>`;
    void evidenceCard; void riskRows;
    host.innerHTML = `<div class="regime-evidence-card regime-rule-card"><div class="t">판정 규칙</div><div class="ev regime-rule-copy"><div>RSI14 &gt; 70 이고 추세선 위 = 과열 · RSI14 &lt; 30 이고 추세선 아래 = 침체 · 그 외 중립</div><div>글로벌 위험: 침체 신호 2개 이상 = 침체, 과열 신호 2개 이상 = 과열 (금리차 역전 · 금리차 1개월 −0.25%p · 10년물 1개월 −25bp · WTI 1개월 −10%)</div></div></div>`;
  }
  function renderRegime(sec) {
    const host = $("regime-cards");
    if (!sec || !sec.markets) { host.innerHTML = `<div class="regime-card">${unavailable("국면 근거 미계산")}</div>`; return; }
    host.innerHTML = sec.markets.map((m, index) => `
      <div class="regime-card">
        <div class="regime-title-line">
          <span class="t">${esc(m.title)}</span>
          <div class="temp"><b style="color:${m.hot ? "var(--amber-soft)" : "#f4f2ee"}">${esc(m.temperature)}</b><span>${esc(String(m.subtitle || "").replace(/^신호 (?=\d+\/3)/, "자료 "))}</span>${index === 0 ? '<button class="regime-toggle" id="regime-toggle" type="button" aria-expanded="false">근거 펼치기 ▾</button>' : ""}</div>
        </div>
        <div class="ev-compact">${regimeCompact(m.evidence)}</div>
        <div class="ev">${regimeRows(m.evidence)}</div>
      </div>`).join("");
    renderRegimeEvidenceStrip(sec);
    const r = sec.rules;
    const researchCurrent = (Array.isArray(sec.research_current) && sec.research_current.length ? sec.research_current : ["규칙 평가 없음"])
      .map((line) => `<div style="border-top:1px solid #4a463f;margin-top:5px;padding-top:5px;color:var(--amber-soft);font-size:10px">${esc(line)}</div>`).join("");
    $("rules").innerHTML = (r ? `
      <div class="t">내 규칙 기준 점검</div>
      ${regimeRows(r.rows, "row")}
      ${r.warning ? `<div class="warn">${esc(r.warning)}</div>` : ""}
      <div style="font-size:10px;color:#8a847b;margin-top:4px">${esc(r.source || "")}</div>` : `<div class="t">내 규칙</div><div style="color:#b5aea4;font-size:11px">규칙 값 미입력 · Obsidian "투자 규칙.md"의 [채우기] 값을 채우면 표시됩니다</div>`) + researchCurrent;
    applyRegimeEvidenceState($("regime").dataset.expanded === "true");
  }

  // ---- tiles ----------------------------------------------------------------
  function tileTooltip(tile) {
    const details = [tile.value !== undefined ? `값 ${tile.value}` : "", tile.change_label ? `등락 ${tile.change_label}` : "", tile.source_label, tile.source, tile.source_name, tile.sub_note, tile.note, tile.window, tile.as_of_label, tile.as_of ? `기준 ${asof(tile.as_of)}` : "", tile.source_as_of ? `출처 기준 ${asof(tile.source_as_of)}` : "", tile.close_date ? `마감 ${tile.close_date}` : "", tile.latest_intraday && tile.latest_intraday.time ? `장중 ${asof(tile.latest_intraday.time)}` : ""];
    return [...new Set(details.filter(Boolean).map(String))].join(" · ");
  }
  function renderTiles(tiles, onPick) {
    const host = $("tiles");
    host.innerHTML = (tiles || []).map((t) => {
      const interactive = Boolean(t.symbol);
      const tooltip = tileTooltip(t);
      const tag = t.source_label || (t.latest_intraday || String(t.window || "").includes("장중") ? "장중 09:00~15:30" : "30일");
      return `
      <div class="tile" data-symbol="${esc(t.symbol || "")}" title="${esc(tooltip)}" aria-label="${esc(`${t.name || "지표"}${tooltip ? ` · ${tooltip}` : ""}`)}" ${interactive ? 'role="button" tabindex="0"' : 'tabindex="0" aria-disabled="true"'}>
        <div class="n">${esc(t.name)}</div>
        <div class="v"><span class="headline-value"><b class="num">${esc(t.value ?? "—")}</b></span><span class="num ${cls(t.change_pct)}">${esc(t.change_label ?? pct(t.change_pct))}</span></div>
        <div class="ma">MA5 <span class="num ${cls(t.ma5_pct)}">${esc(t.ma5_label ?? pct(t.ma5_pct))}</span> · MA20 <span class="num ${cls(t.ma20_pct)}">${esc(t.ma20_label ?? pct(t.ma20_pct))}</span></div>
        ${t.spark ? `<div class="spark"><span class="spark-line">${sparkline(t.spark)}</span><small title="${esc(tooltip)}">${esc(tag)}</small></div>` : `<div class="note">${esc(t.note || "표시 불가")}</div>`}
        ${t.sub_note ? `<div class="tile-sub-note" title="${esc(t.sub_note)}">${esc(t.sub_note)}</div>` : ""}
      </div>`;
    }).join("");
    host.querySelectorAll(".tile[data-symbol]:not([data-symbol=''])").forEach((el) => {
      const pick = () => onPick(el.dataset.symbol);
      el.addEventListener("click", pick);
      el.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(); } });
    });
  }

  // ---- chart ----------------------------------------------------------------
  const homeIndicatorDefaults = { ma5: true, ma20: true, ma60: true, ma120: true, bollinger: false, rsi14: { enabled: false, placement: "panel" }, volume: true };
  const homeIndicatorColors = { ma5: "#4a3aa7", ma20: "#2a78d6", ma60: "#eb6834", ma120: "#1baf7a", bollinger: "#a8621a", rsi14: "#8b4c9e", volume: "#8a847b" };
  const homeIndicatorLabels = { ma5: "MA5", ma20: "MA20", ma60: "MA60", ma120: "MA120", bollinger: "Bollinger(20,2)", rsi14: "RSI14", volume: "거래량" };
  let homeIndicatorState = loadHomeIndicatorState();
  let chart, candleSeries, volSeries, rsiPanelSeries, rsiOverlaySeries, chartResizeObserver, chartResizeTimer, loadedChart = null, maSeries = {}, bollingerSeries = {}, rsiPanelGuides = [], rsiOverlayGuides = [];

  function defaultHomeIndicatorState() {
    return { ...homeIndicatorDefaults, rsi14: { ...homeIndicatorDefaults.rsi14 } };
  }
  function homeIndicatorEnabled(key) {
    const state = homeIndicatorState[key];
    return key === "rsi14" ? Boolean(state && state.enabled) : Boolean(state);
  }
  function homeRsiPlacement() {
    const state = homeIndicatorState.rsi14;
    return state && state.placement === "overlay" ? "overlay" : "panel";
  }

  function loadHomeIndicatorState() {
    if (typeof localStorage === "undefined") return defaultHomeIndicatorState();
    try {
      const saved = JSON.parse(localStorage.getItem("home.indicators"));
      if (Array.isArray(saved)) return Object.fromEntries(Object.keys(homeIndicatorDefaults).map((key) => [key, key === "rsi14" ? { enabled: saved.includes(key), placement: "panel" } : saved.includes(key)]));
      if (saved && typeof saved === "object") return Object.fromEntries(Object.entries(homeIndicatorDefaults).map(([key, fallback]) => {
        if (key !== "rsi14") return [key, typeof saved[key] === "boolean" ? saved[key] : fallback];
        if (typeof saved[key] === "boolean") return [key, { enabled: saved[key], placement: "panel" }];
        const state = saved[key];
        return [key, state && typeof state === "object" ? { enabled: Boolean(state.enabled), placement: state.placement === "overlay" ? "overlay" : "panel" } : { ...fallback }];
      }));
    } catch (_error) { /* keep defaults */ }
    return defaultHomeIndicatorState();
  }
  function saveHomeIndicatorState() {
    try { localStorage.setItem("home.indicators", JSON.stringify(homeIndicatorState)); } catch (_error) { /* optional preference */ }
  }
  function syncHomeIndicatorMenu() {
    document.querySelectorAll("#indicator-menu [data-indicator]").forEach((row) => {
      const input = row.querySelector('input[type="checkbox"]');
      if (!input) return;
      input.checked = row.dataset.indicator === "rsi14"
        ? homeIndicatorEnabled("rsi14") && homeRsiPlacement() === row.dataset.placement
        : homeIndicatorEnabled(row.dataset.indicator);
    });
  }
  function changeHomeIndicator(key, enabled, placement) {
    if (!(key in homeIndicatorState)) return;
    if (key === "rsi14") {
      if (enabled) homeIndicatorState.rsi14 = { enabled: true, placement: placement === "overlay" ? "overlay" : "panel" };
      else if (homeRsiPlacement() === placement) homeIndicatorState.rsi14 = { enabled: false, placement: homeRsiPlacement() };
    } else homeIndicatorState[key] = Boolean(enabled);
    saveHomeIndicatorState(); syncHomeIndicatorMenu(); renderChart();
  }
  function prepareHomeIndicatorMenu(menu) {
    const panelRow = menu.querySelector('[data-indicator="rsi14"]');
    if (!panelRow || menu.querySelector('[data-indicator="rsi14"][data-placement="overlay"]')) return;
    panelRow.dataset.placement = "panel";
    panelRow.innerHTML = '<span><input type="checkbox"> RSI14 · 아래</span>';
    const overlayRow = document.createElement("label");
    overlayRow.dataset.indicator = "rsi14";
    overlayRow.dataset.placement = "overlay";
    overlayRow.innerHTML = '<span><input type="checkbox"> RSI14 · 위(겹침)</span>';
    panelRow.insertAdjacentElement("afterend", overlayRow);
  }
  function wireHomeIndicatorPicker() {
    const picker = $("home-indicator-picker"), button = $("indicator-picker-button"), menu = $("indicator-menu");
    if (!picker || !button || !menu) return;
    prepareHomeIndicatorMenu(menu);
    syncHomeIndicatorMenu();
    button.addEventListener("click", () => { menu.hidden = !menu.hidden; button.setAttribute("aria-expanded", String(!menu.hidden)); });
    menu.querySelectorAll("[data-indicator]").forEach((row) => {
      const input = row.querySelector('input[type="checkbox"]');
      if (input) input.addEventListener("change", () => changeHomeIndicator(row.dataset.indicator, input.checked, row.dataset.placement));
    });
    document.addEventListener("click", (event) => {
      if (!picker.contains(event.target)) { menu.hidden = true; button.setAttribute("aria-expanded", "false"); }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menu.hidden) { menu.hidden = true; button.setAttribute("aria-expanded", "false"); button.focus(); }
    });
  }
  function aggregateCandles(candles, interval) {
    if (interval === "day") return candles;
    const groups = new Map();
    candles.forEach((candle) => {
      const observed = new Date(`${candle.t}T00:00:00Z`);
      let key;
      if (interval === "month") key = candle.t.slice(0, 7);
      else {
        const monday = new Date(observed); const weekday = (monday.getUTCDay() + 6) % 7;
        monday.setUTCDate(monday.getUTCDate() - weekday); key = monday.toISOString().slice(0, 10);
      }
      const group = groups.get(key);
      if (!group) groups.set(key, { ...candle });
      else { group.t = candle.t; group.h = Math.max(group.h, candle.h); group.l = Math.min(group.l, candle.l); group.c = candle.c; group.v = Number(group.v || 0) + Number(candle.v || 0); }
    });
    return [...groups.values()];
  }
  function movingAverage(candles, windowSize) {
    return candles.map((candle, index) => {
      if (index + 1 < windowSize) return null;
      const values = candles.slice(index + 1 - windowSize, index + 1).map((item) => Number(item.c));
      return { time: candle.t, value: values.reduce((sum, value) => sum + value, 0) / windowSize };
    }).filter(Boolean);
  }
  function bollinger(candles, windowSize = 20, multiplier = 2) {
    const bands = { middle: [], upper: [], lower: [] };
    candles.forEach((candle, index) => {
      if (index + 1 < windowSize) return;
      const values = candles.slice(index + 1 - windowSize, index + 1).map((item) => Number(item.c));
      const mean = values.reduce((sum, value) => sum + value, 0) / windowSize;
      const deviation = Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / windowSize);
      bands.middle.push({ time: candle.t, value: mean });
      bands.upper.push({ time: candle.t, value: mean + multiplier * deviation });
      bands.lower.push({ time: candle.t, value: mean - multiplier * deviation });
    });
    return bands;
  }
  function currentInterval() { const b = document.querySelector("#chart-interval button.on"); return b ? b.dataset.v : "day"; }
  function ensureChart() {
    if (chart || !window.LightweightCharts) return;
    const el = $("chart");
    el.innerHTML = "";
    chart = LightweightCharts.createChart(el, {
      layout: { background: { color: "#fff" }, textColor: "#6b6660", fontFamily: "IBM Plex Sans KR, system-ui" },
      grid: { vertLines: { color: "#f0ece5" }, horzLines: { color: "#e6e1d8" } },
      rightPriceScale: { borderColor: "#d9d3ca" }, timeScale: { borderColor: "#d9d3ca" },
      crosshair: { mode: 1 }, width: Math.max(1, el.clientWidth), height: Math.max(1, el.clientHeight),
    });
    const priceFormatter = (value) => {
      const symbol = String((loadedChart || {}).symbol || "").toUpperCase();
      const isKrw = /^\d{6}$/.test(symbol) || ["KOSPI", "KOSDAQ", "KOSPI200"].includes(symbol);
      return Number(value).toLocaleString("ko-KR", { minimumFractionDigits: isKrw ? 0 : 2, maximumFractionDigits: isKrw ? 0 : 2 });
    };
    candleSeries = chart.addCandlestickSeries({ upColor: "#c0392b", downColor: "#2b62c0", borderUpColor: "#c0392b", borderDownColor: "#2b62c0", wickUpColor: "#c0392b", wickDownColor: "#2b62c0", priceFormat: { type: "custom", formatter: priceFormatter } });
    volSeries = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", priceLineVisible: false, lastValueVisible: false });
    for (const key of ["ma5", "ma20", "ma60", "ma120"]) maSeries[key] = chart.addLineSeries({ color: homeIndicatorColors[key], lineWidth: key === "ma5" ? 1 : 2, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: priceFormatter } });
    bollingerSeries.middle = chart.addLineSeries({ color: "rgba(168,98,26,.65)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: priceFormatter } });
    bollingerSeries.upper = chart.addLineSeries({ color: homeIndicatorColors.bollinger, lineStyle: 2, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: priceFormatter } });
    bollingerSeries.lower = chart.addLineSeries({ color: homeIndicatorColors.bollinger, lineStyle: 2, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: priceFormatter } });
    rsiPanelSeries = chart.addLineSeries({ color: homeIndicatorColors.rsi14, lineWidth: 2, priceScaleId: "rsi-panel", priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "custom", formatter: (value) => Number(value).toFixed(0) } });
    rsiPanelGuides = [30, 70].map(() => chart.addLineSeries({ color: "rgba(138,132,123,.6)", lineStyle: 2, lineWidth: 1, priceScaleId: "rsi-panel", priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: (value) => Number(value).toFixed(0) } }));
    rsiOverlaySeries = chart.addLineSeries({ color: homeIndicatorColors.rsi14, lineWidth: 2, priceScaleId: "rsi", priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "custom", formatter: (value) => Number(value).toFixed(0) } });
    rsiOverlayGuides = [30, 70].map(() => chart.addLineSeries({ color: "#a8621a", lineStyle: 2, lineWidth: 1, priceScaleId: "rsi", priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: (value) => Number(value).toFixed(0) } }));
    if (window.ResizeObserver) {
      chartResizeObserver = new ResizeObserver(() => {
        clearTimeout(chartResizeTimer);
        chartResizeTimer = setTimeout(() => {
          if (!chart) return;
          const width = Math.max(1, Math.round(el.clientWidth));
          const height = Math.max(1, Math.round(el.clientHeight));
          chart.applyOptions({ height });
          chart.resize(width, height);
          chart.timeScale().fitContent();
        }, 80);
      });
      chartResizeObserver.observe(el);
    }
  }
  function renderChart(sec) {
    if (arguments.length) loadedChart = sec;
    sec = loadedChart;
    const stats = $("chart-stats"), legend = $("chart-legend");
    if (!sec || !sec.candles || !sec.candles.length) {
      if (chart) {
        candleSeries.setData([]); volSeries.setData([]); Object.values(maSeries).forEach((series) => series.setData([])); Object.values(bollingerSeries).forEach((series) => series.setData([])); rsiPanelSeries.setData([]); rsiOverlaySeries.setData([]); [...rsiPanelGuides, ...rsiOverlayGuides].forEach((series) => series.setData([]));
      } else $("chart").innerHTML = unavailable(sec && sec.reason);
      stats.innerHTML = ""; legend.innerHTML = ""; return;
    }
    ensureChart();
    if (!chart) { $("chart").innerHTML = unavailable("차트 라이브러리 로드 실패"); return; }
    const candles = aggregateCandles(sec.candles, currentInterval());
    candleSeries.setData(candles.map((c) => ({ time: c.t, open: c.o, high: c.h, low: c.l, close: c.c })));
    volSeries.setData(candles.map((c) => ({ time: c.t, value: c.v ?? 0, color: c.c >= c.o ? "rgba(192,57,43,.45)" : "rgba(43,98,192,.45)" })));
    volSeries.applyOptions({ visible: homeIndicatorEnabled("volume") });
    const rsiEnabled = homeIndicatorEnabled("rsi14"), rsiPlacement = homeRsiPlacement();
    const rsiInPanel = rsiEnabled && rsiPlacement === "panel";
    chart.priceScale("vol").applyOptions({ scaleMargins: rsiInPanel ? { top: 0.62, bottom: 0.27 } : { top: 0.82, bottom: 0 } });
    for (const key of Object.keys(maSeries)) {
      maSeries[key].setData(movingAverage(candles, Number(key.slice(2))));
      maSeries[key].applyOptions({ visible: homeIndicatorEnabled(key) });
    }
    const bands = bollinger(candles);
    Object.keys(bollingerSeries).forEach((key) => { bollingerSeries[key].setData(bands[key]); bollingerSeries[key].applyOptions({ visible: homeIndicatorEnabled("bollinger") }); });
    const rsiValues = SIIndicators.rsiWilder(candles.map((candle) => Number(candle.c)), 14);
    const rsiData = candles.map((candle, index) => rsiValues[index] === null ? null : { time: candle.t, value: rsiValues[index] }).filter(Boolean);
    rsiPanelSeries.setData(rsiData); rsiPanelSeries.applyOptions({ visible: rsiInPanel });
    rsiOverlaySeries.setData(rsiData); rsiOverlaySeries.applyOptions({ visible: rsiEnabled && rsiPlacement === "overlay" });
    const rsiEnds = rsiData.length ? [rsiData[0], rsiData[rsiData.length - 1]] : [];
    rsiPanelGuides.forEach((series, index) => { series.setData(rsiEnds.map((point) => ({ time: point.time, value: index ? 70 : 30 }))); series.applyOptions({ visible: rsiInPanel }); });
    rsiOverlayGuides.forEach((series, index) => { series.setData(rsiEnds.map((point) => ({ time: point.time, value: index ? 70 : 30 }))); series.applyOptions({ visible: rsiEnabled && rsiPlacement === "overlay" }); });
    chart.priceScale("right").applyOptions({ scaleMargins: rsiInPanel ? { top: 0.04, bottom: 0.27 } : { top: 0.04, bottom: 0.04 } });
    chart.priceScale("rsi-panel").applyOptions({ scaleMargins: { top: 0.78, bottom: 0.02 }, visible: false });
    chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.15, bottom: 0.25 }, visible: false });
    chart.timeScale().fitContent();
    requestAnimationFrame(() => { if (chart) chart.timeScale().fitContent(); });
    const s = sec.stats || {};
    stats.innerHTML = `
      <span class="num muted" title="Wilder 지수이동평균 방식">RSI14 <b>${s.rsi14 === undefined ? "—" : fmt(s.rsi14, 0)}</b></span>
      <span class="num muted">60일선 <b class="${cls(s.disp60_pct)}">${pct(s.disp60_pct)}</b></span>
      <span class="num muted">고점 대비 <b class="${cls(s.drawdown_pct)}">${pct(s.drawdown_pct)}</b></span>
      ${s.per !== undefined ? `<span class="badge">PER <b class="num">${fmt(s.per)}</b>${s.per_note ? " " + esc(s.per_note) : ""}</span>` : ""}
      ${s.pbr !== undefined ? `<span class="badge">PBR <b class="num">${fmt(s.pbr)}</b></span>` : ""}
      <span class="badge dashed">선행 PER · PBR — 소스 검증 전</span>`;
    const active = Object.keys(homeIndicatorState).filter(homeIndicatorEnabled);
    const latestRsi = rsiData.length ? rsiData[rsiData.length - 1].value : null;
    const indicatorLabel = (key) => key === "rsi14" ? `RSI14 · ${rsiPlacement === "overlay" ? "위" : "아래"}${latestRsi === null ? "" : ` ${fmt(latestRsi, 0)}`}` : homeIndicatorLabels[key];
    legend.innerHTML = `<span><i style="background:#1f1d1a"></i>${esc(sec.symbol_name || sec.symbol)}</span>` + active.map((key) => `<span class="home-indicator-label"><i style="background:${homeIndicatorColors[key]}"></i>${esc(indicatorLabel(key))}<button type="button" data-remove-home-indicator="${key}" aria-label="${esc(homeIndicatorLabels[key])} 제거">×</button></span>`).join("") + `<span class="muted">기준일 ${esc(sec.as_of || "")}</span>`;
    legend.querySelectorAll("[data-remove-home-indicator]").forEach((button) => button.addEventListener("click", () => changeHomeIndicator(button.dataset.removeHomeIndicator, false, button.dataset.removeHomeIndicator === "rsi14" ? rsiPlacement : undefined)));
  }

  // ---- watchlist --------------------------------------------------------------
  function watchlistName(row) {
    const symbol = String(row.symbol || ""), market = String(row.market || row.exchange || "");
    const isUs = /^(US|USA|NASDAQ|NYSE|AMEX)$/i.test(market) || /^[A-Z][A-Z0-9.-]{0,9}$/.test(symbol);
    return isUs && symbol ? symbol : (row.name || symbol || "—");
  }
  function watchlistInvestorCell(row, participant) {
    const investor = row.investor;
    if (!investor) return '<div class="r num muted watch-investor-cell">—</div>';
    const today = investor[`${participant}_1d`];
    const tooltip = `5일 ${signedEok(investor[`${participant}_5d`])}억 · 20일 ${signedEok(investor[`${participant}_20d`])}억 · 기준일 ${investor.as_of || "—"}`;
    return `<div class="r num ${cls(today)} watch-investor-cell" title="${esc(tooltip)}">${signedEok(today)}</div>`;
  }
  function watchlistInvestorMobile(row) {
    const investor = row.investor;
    if (!investor) return '<div class="watch-investor-mobile muted">외 — · 기 —</div>';
    return `<div class="watch-investor-mobile"><span class="${cls(investor.foreign_1d)}">외 ${signedEok(investor.foreign_1d, 0)}</span> · <span class="${cls(investor.institution_1d)}">기 ${signedEok(investor.institution_1d, 0)}</span></div>`;
  }
  function renderWatchlist(sec) {
    const host = $("watchlist");
    if (!sec || !sec.rows) { host.innerHTML = unavailable(sec && sec.reason); return; }
    const rows = publicMode
      ? [...sec.rows]
      : [...sec.rows].sort((left, right) => Number(Boolean(right.held)) - Number(Boolean(left.held)));
    $("watchlist-meta").textContent = publicMode
      ? `관심 ${sec.watch_count ?? rows.length}`
      : `보유 ${sec.held_count ?? 0} · 관심 ${sec.watch_count ?? 0}`;
    const isUs = (row) => ["US ETF", "US 주식"].includes(String(row.market || "")) || /^[A-Z][A-Z0-9.-]{0,9}$/.test(String(row.symbol || ""));
    const orderedRows = [...rows.filter((row) => !isUs(row)), ...rows.filter(isUs)];
    const live = sec.us_live && Array.isArray(sec.us_live.quotes) ? sec.us_live : null;
    const liveLine = live ? `<div class="us-live-quotes"><b>밤사이 미국</b><span class="muted">${esc(live.session_label || "")} · ${esc(live.as_of_label || "")}</span>${live.quotes.map((quote) => `<span class="quote"><b>${esc(quote.symbol)}</b> <span class="num">${quote.currency === "USD" ? "$" : `${esc(quote.currency)} `}${fmt(quote.last_price, 2)}</span> <span class="num ${cls(quote.change_pct)}">${pct(quote.change_pct)}</span></span>`).join("")}</div>` : "";
    let insertedLive = false;
    const renderedRows = orderedRows.map((r) => {
      const prefix = !insertedLive && liveLine && isUs(r) ? (insertedLive = true, liveLine) : "";
      return `${prefix}<div class="tr watch">
        <div title="${esc(r.name || r.symbol || "")}"><div>${esc(watchlistName(r))}</div>${watchlistInvestorMobile(r)}${r.flag ? `<div class="flag">조건 도달 · ${esc(r.flag)}</div>` : ""}</div>
        <div class="watch-status">${!publicMode && r.held ? '<span class="badge held-badge">보유</span>' : '<span class="muted">관심</span>'}</div>
        <div class="r num">${r.price ?? "—"}</div>
        <div class="r num ${cls(r.change_pct)}">${pct(r.change_pct)}</div>
        <div class="r num ${cls(r.drawdown_pct)}">${pct(r.drawdown_pct)}</div>
        <div class="r num">${r.rsi14 === null || r.rsi14 === undefined ? "—" : Math.round(r.rsi14)}</div>
        ${watchlistInvestorCell(r, "foreign")}
        ${watchlistInvestorCell(r, "institution")}
        ${watchlistInvestorCell(r, "individual")}
      </div>`;
    }).join("");
    host.innerHTML = `<div class="tr th watch"><div>종목</div><div>구분</div><div class="r">현재가</div><div class="r">등락</div><div class="r">고점 대비</div><div class="r">RSI14</div><div class="watch-investor-head"><small>순매수 억원 · 당일 (툴팁 5일·20일)</small><span>외국인</span><span>기관</span><span>개인</span></div></div>` + renderedRows;
  }

  // ---- account ------------------------------------------------------------------
  const signedKrw = (value) => value === null || value === undefined ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}₩${formatCompactKorean(Math.abs(value))}`;
  function renderAccount(sec, selectedWindow = null) {
    if (publicMode) {
      const card = $("account-card");
      if (card) card.hidden = true;
      return;
    }
    const host = $("account");
    const investTotal = sec && sec.invest_total_krw !== undefined ? sec.invest_total_krw : sec && sec.total_krw;
    if (!sec) { host.innerHTML = unavailable("계좌 요약 없음"); return; }
    selectedWindow = selectedWindow || sec.period_label || "3M";
    document.querySelectorAll("#account-range button").forEach((button) => button.classList.toggle("on", button.dataset.v === selectedWindow));
    const metric = (sec.return_metrics || {})[selectedWindow] || {};
    const brokerPnl = brokerReportedPnl(sec, metric);
    const startDate = metric.start_date;
    const chartHistory = startDate ? (sec.history || []).filter((point) => point.t >= startDate) : (sec.history || []);
    const chartBenchmark = startDate ? (sec.benchmark || []).filter((point) => point.t >= startDate) : (sec.benchmark || []);
    const summaryRows = Array.isArray(sec.summary_rows) ? sec.summary_rows : (sec.sources || []).map((source) => ({ label: source.name, as_of: source.as_of_label || asof(source.as_of), included: source.included, note: "" }));
    const recentCashflows = Array.isArray(sec.recent_cashflows) ? sec.recent_cashflows.slice(0, 5) : [];
    host.innerHTML = `
      <div class="acct-total"><span class="muted">투자 자산</span><b class="num">₩ ${formatCompactKorean(investTotal)}</b></div>
      <div class="acct-truth-lines">
        <span>총자산 변동 어제 <b class="num ${cls(sec.daily_true_change_krw)}">${signedKrw(sec.daily_true_change_krw)}</b> <small>(순입금 제외)</small></span>
        <span>이번 달 진짜 손익 <b class="num ${cls(sec.month_true_pnl_krw)}">${signedKrw(sec.month_true_pnl_krw)}</b></span>
        <span title="입출금 시점을 반영해 내가 실제 투입한 돈 대비 수익률입니다.">${esc(selectedWindow === "ALL" ? "전체" : selectedWindow)} 돈 가중(내 실제 수익률) <b class="num ${cls(metric.return_pct_modified_dietz)}">${pct(metric.return_pct_modified_dietz)}</b></span>
      </div>
      ${sec.net_worth_krw !== undefined && sec.net_worth_krw !== null ? `<div class="acct-net-worth"><span>순자산</span> <b class="num">₩${formatCompactKorean(sec.net_worth_krw)}</b> <small>(부동산·예금 포함, ${esc(sec.net_worth_as_of_label || asof(sec.net_worth_as_of))} 기준)</small></div>` : ""}
      <div class="acct-meta">
        ${metric.reason ? `<span class="muted">${esc(metric.reason)}</span>` : ""}
        <span title="입출금 영향을 잘라내고 운용 성과만 이어 붙인 수익률입니다.">시간 가중(운용 실력) <b class="num ${cls(metric.return_pct_twr)}">${pct(metric.return_pct_twr)}</b></span>
        <span>KOSPI 동기간 <b class="num ${cls(metric.kospi_return_pct)}">${pct(metric.kospi_return_pct)}</b></span>
        <span>증권사 표시 손익 <b class="num ${cls(brokerPnl)}">${signedKrw(brokerPnl)}</b></span>
        ${metric.partial ? '<span class="badge dashed">부분 관측 포함</span>' : ""}
        ${sec.effective_exposure_pct !== undefined ? `<span>실효 노출 <b class="num">${formatSharePercent(sec.effective_exposure_pct)}</b></span>` : ""}
        ${sec.leveraged_weight_pct !== undefined ? `<span>레버리지 명목 <b class="num">${formatSharePercent(sec.leveraged_weight_pct)}</b></span>` : ""}
        ${sec.cash_unknown ? '<span title="현금 미확인 계좌 포함">현금 <b class="num">—</b></span>' : sec.cash_pct !== undefined ? `<span>현금 <b class="num">${formatSharePercent(sec.cash_pct)}</b></span>` : ""}
        ${sec.short_treasury_pct !== undefined ? `<span>단기국채 <b class="num">${formatSharePercent(sec.short_treasury_pct)}</b></span>` : ""}
      </div>
      <div class="acct-meta">
        ${sec.usd_assets_usd !== undefined ? `<span>달러 자산 <b class="num">$${fmt(sec.usd_assets_usd, 0)} = ${formatCompactKorean(sec.usd_assets_krw)}원</b> (${fmt(sec.usdkrw, 2)}원 · ${esc(sec.usdkrw_as_of_label || asof(sec.usdkrw_as_of))})</span>` : `<span>달러 자산 —</span>`}
        ${sec.fx_effect_pct !== undefined ? `<span>환율 효과 어제 <b class="num ${cls(sec.fx_effect_pct)}">${pct(sec.fx_effect_pct)}</b></span>` : ""}
        ${sec.equity_effect_pct !== undefined ? `<span>주식 효과 <b class="num ${cls(sec.equity_effect_pct)}">${pct(sec.equity_effect_pct)}</b></span>` : ""}
      </div>
      <div id="acct-chart" class="acct-chart"></div>
      <div class="acct-foot">${esc(sec.footnote || "계좌 규모 변화 · 점선은 KOSPI 비교")}</div>
      ${(sec.exposure_unverified || []).length ? `<div class="acct-foot">배수 미확인(1배 처리): ${esc(sec.exposure_unverified.join(", "))}</div>` : ""}
      <div class="account-detail-grid">
        <section><div class="account-detail-title">계좌별</div><div class="account-compact-list">${summaryRows.length ? summaryRows.map((row) => `<div class="account-compact-row" title="${esc(row.note || "")}"><span><b>${esc(row.label || "—")}</b> ${row.included === false ? '<small class="muted">제외</small>' : '<small class="badge">포함</small>'}</span><span class="num muted">${esc(row.as_of || "—")}</span></div>`).join("") : '<span class="muted">—</span>'}</div></section>
        <section><div class="account-detail-title">최근 입출금</div><div class="account-compact-list">${recentCashflows.length ? recentCashflows.map((row) => `<div class="account-compact-row"><span title="${esc(row.label || "")}"><b>${esc(row.label || "입출금")}</b> <small class="num muted">${esc(row.date || "—")}</small></span><span class="num ${cls(row.amount_krw)}">${signedKrw(row.amount_krw)}</span></div>`).join("") : '<span class="muted">—</span>'}</div></section>
      </div>`;
    renderLineChart($("acct-chart"), chartHistory, { benchmark: chartBenchmark, height: 150, ariaLabel: "총자산과 KOSPI 동기간 추이" });
  }

  // ---- bottom cards ----------------------------------------------------------
  const signed = (v) => (v === null || v === undefined) ? '<span class="muted">—</span>' : `<span class="${cls(v)}">${v > 0 ? "+" : ""}${fmt(v, 0)}</span>`;
  function shortBasisDate(value) {
    const text = String(value || "");
    const iso = text.match(/\d{4}-(\d{2}-\d{2})/);
    if (iso) return iso[1];
    const short = text.match(/(?:^|\D)(\d{2}-\d{2})(?:\D|$)/);
    return short ? short[1] : "";
  }
  function renderFlows(sec, krCloseAsOf) {
    const host = $("flows");
    if (!sec) { host.innerHTML = unavailable("보존 데이터 없음"); return; }
    const rows = Array.isArray(sec.rows) ? sec.rows : [];
    const creditMeta = sec.credit || sec.credit_balance || (sec.lending && sec.lending.credit) || {};
    const balances = (Array.isArray(sec.balances) ? sec.balances : []).filter((row) => row.name !== "대차잔고").map((row) => String(row.name || "").includes("신용") ? { ...creditMeta, ...row, as_of: row.as_of || creditMeta.as_of, lag_note: row.lag_note || creditMeta.lag_note } : { ...row });
    if (sec.lending) {
      const lending = { name: "대차잔고", value: sec.lending.balance_amount === null || sec.lending.balance_amount === undefined ? "—" : `${formatCompactKorean(sec.lending.balance_amount)}원`, d1_pct: sec.lending.d1_pct, d5_pct: sec.lending.d5_pct, d20_pct: null, spark: sec.lending.trend_20d || [], as_of: sec.lending.as_of, lag_note: sec.lending.lag_note };
      const creditIndex = balances.findIndex((row) => String(row.name || "").includes("신용"));
      balances.splice(creditIndex >= 0 ? creditIndex + 1 : balances.length, 0, lending);
    }
    if (!rows.length && !balances.length) { host.innerHTML = unavailable(sec.reason || "보존 데이터 없음"); return; }
    const flowAsOf = shortBasisDate(sec.as_of), closeAsOf = shortBasisDate(krCloseAsOf);
    const flowBasis = flowAsOf && closeAsOf && flowAsOf !== closeAsOf ? `<small class="flow-as-of muted">${esc(flowAsOf)} 기준</small>` : "";
    host.innerHTML = (rows.length ? `<div class="table"><div class="tr th flow"><div>순매수 (억원) ${flowBasis}</div><div class="r">오늘</div><div class="r">5일</div><div class="r">20일</div></div>` +
      rows.map((r) => `<div class="tr flow"><div class="muted">${esc(r.name)}</div><div class="r num">${signed(r.today)}</div><div class="r num">${signed(r.d5)}</div><div class="r num">${signed(r.d20)}</div></div>`).join("") + `</div>` : "") +
      (balances.length ? `<div class="table balance-table"><div class="tr th bal"><div>잔고</div><div>현재 · 1년 위치</div><div class="r">1일</div><div class="r">5일</div><div class="r">20일</div><div>20일 추세</div></div>` +
        balances.map((b) => {
          const embeddedBasis = String(b.value ?? "").match(/\((\d{2}-\d{2})\)\s*$/);
          const basis = shortBasisDate(b.as_of) || (embeddedBasis ? embeddedBasis[1] : "");
          const value = String(b.value ?? "—").replace(/\s*\(\d{2}-\d{2}\)\s*$/, "");
          const lagNote = b.lag_note || (String(b.name || "").includes("신용") ? "KOFIA 신용잔고는 2거래일 뒤 발표" : String(b.name || "").includes("대차") ? "공공데이터포털 대차잔고는 1거래일 뒤 발표" : "");
          const tooltip = [basis ? `${basis} 기준` : "", lagNote].filter(Boolean).join(" · ");
          return `<div class="tr bal" title="${esc(tooltip)}"><div class="muted">${esc(b.name)}</div><div class="num"><span class="balance-value">${esc(value)}${basis ? ` <small class="balance-as-of muted">· ${esc(basis)} 기준</small>` : ""}</span> <small class="${b.hot ? "up" : "muted"}">${esc(b.position || "")}</small></div><div class="r num ${cls(b.d1_pct)}">${pct(b.d1_pct)}</div><div class="r num ${cls(b.d5_pct)}">${pct(b.d5_pct)}</div><div class="r num ${cls(b.d20_pct)}">${pct(b.d20_pct)}</div><div>${sparkline(b.spark || [], 70, 18)}</div></div>`;
        }).join("") + `</div>` : "");
  }
  function renderDerivatives(sec) {
    const host = $("derivatives");
    if (!sec || !sec.groups) { host.innerHTML = unavailable(sec && sec.reason); return; }
    host.innerHTML = sec.groups.map((g) => `<div class="sub">${esc(g.title)}</div>` + g.rows.map((r) => `<div class="kv"><span>${esc(r[0])}</span><span class="num">${esc(r[1])}</span></div>`).join("")).join("") + `<div class="muted" style="font-size:10px;margin-top:6px">콜 월·풋 월·옵션 분포는 시장 페이지에서 ▸</div>`;
  }
  function renderSchedule(sec) {
    const host = $("schedule");
    const events = sec && Array.isArray(sec.events) ? sec.events.map((item) => ({ time: item.time, text: item.text })) : sec && Array.isArray(sec.items) ? sec.items.map((item) => ({ time: item.when, text: item.what, importance: item.importance })) : [];
    host.innerHTML = (events.length ? events.map((item) => `<div class="schedule-row"><span class="num">${esc(item.time || "—")}</span><span title="${esc(item.text || "")}">${esc(item.text || "—")}</span>${item.importance ? `<span class="dots muted">${[1, 2, 3].map((level) => `<i class="${level <= item.importance ? "on" : ""}"></i>`).join("")}</span>` : ""}</div>`).join("") : '<div class="muted">오늘 일정 없음</div>') + (sec && sec.note ? `<div class="schedule-note">${esc(sec.note)}</div>` : "");
  }
  function renderBrief(schedule, legacy) {
    const host = $("brief");
    const briefs = schedule && Array.isArray(schedule.briefs) ? schedule.briefs.filter((item) => item && (item.title || item.body)) : [];
    const latest = briefs.slice().sort((left, right) => String(left.time || "").localeCompare(String(right.time || ""))).at(-1);
    if (latest) {
      $("brief-meta").textContent = latest.time || "";
      host.innerHTML = `<div class="brief-title">${esc(latest.title || (latest.kind === "close" ? "장 마감" : "아침"))}</div><div class="brief-body collapsed">${esc(latest.body || "—")}</div><button type="button" class="text-button brief-toggle" aria-expanded="false">전체 보기 ▾</button>`;
      const toggle = host.querySelector(".brief-toggle"), body = host.querySelector(".brief-body");
      toggle.addEventListener("click", () => { const collapsed = body.classList.toggle("collapsed"); toggle.textContent = collapsed ? "전체 보기 ▾" : "접기 ▴"; toggle.setAttribute("aria-expanded", String(!collapsed)); });
      requestAnimationFrame(() => { toggle.hidden = body.scrollHeight <= body.clientHeight + 1; });
      return;
    }
    if (legacy && Array.isArray(legacy.lines) && legacy.lines.length) {
      $("brief-meta").textContent = legacy.meta || "";
      host.innerHTML = `<div class="brief-body">${legacy.lines.map((line) => `· ${esc(line)}`).join("\n")}</div>`;
      return;
    }
    $("brief-meta").textContent = "";
    host.innerHTML = '<div class="muted">브리핑 없음 · 07:30/16:10 생성</div>';
  }
  function renderChanges(sec) {
    const host = $("changes");
    const data = sec || {};
    const counts = data.counts || {};
    const ruleChanges = Array.isArray(data.rule_changes) ? data.rule_changes : [];
    const entries = Array.isArray(data.condition_entries) ? data.condition_entries : [];
    const exits = Array.isArray(data.condition_exits) ? data.condition_exits : [];
    const highs = Array.isArray(data.new_highs_52w_list) ? data.new_highs_52w_list : [];
    const lows = Array.isArray(data.new_lows_52w_list) ? data.new_lows_52w_list : [];
    const spikes = Array.isArray(data.volume_spikes) ? data.volume_spikes : [];
    const ruleCount = counts.rule_changes ?? ruleChanges.length;
    const entryCount = counts.condition_entries ?? entries.length;
    const exitCount = counts.condition_exits ?? exits.length;
    const highCount = counts.new_highs_52w ?? data.new_highs_52w ?? 0;
    const lowCount = counts.new_lows_52w ?? data.new_lows_52w ?? 0;
    const spikeCount = counts.volume_spikes ?? spikes.length;
    const chip = (panel, label, total) => `<button type="button" class="change-chip${total ? "" : " zero"}" data-change-panel="${panel}" aria-expanded="false">${label}</button>`;
    const empty = '<span class="muted">변화 없음</span>';
    const conditionRows = [
      ...entries.map((item) => `<li><span class="change-direction on">진입</span>${esc(item.display || `${item.symbol || ""} ${item.name || ""}`)}</li>`),
      ...exits.map((item) => `<li><span class="change-direction off">이탈</span>${esc(item.display || `${item.symbol || ""} ${item.name || ""}`)}</li>`),
    ].join("");
    const highLowRows = [
      ...highs.map((item) => `<li><span class="change-direction on">신고가</span>${esc(item.display || item.symbol || "")}</li>`),
      ...lows.map((item) => `<li><span class="change-direction off">신저가</span>${esc(item.display || item.symbol || "")}</li>`),
    ].join("");
    host.innerHTML = `<div class="changes-heading"><b>오늘 달라진 것</b><span class="muted">${esc(data.as_of ? `${data.as_of} 기준` : "표시 가능한 변화 없음")}</span></div>` +
      `<div class="changes-chips">${chip("rules", `규칙 단계 변화 ${ruleCount}`, ruleCount)}${chip("conditions", `조건 진입 ${entryCount} · 이탈 ${exitCount}`, entryCount + exitCount)}${chip("highlow", `52주 신고가 ${highCount} · 신저가 ${lowCount}`, highCount + lowCount)}${chip("volume", `거래량 급증 ${spikeCount}`, spikeCount)}</div>` +
      `<div class="change-detail" data-change-detail="rules" hidden>${ruleChanges.length ? `<ul>${ruleChanges.map((item) => `<li><b>${esc(item.rule || "규칙")}</b> ${esc(item.from_level || "—")} → ${esc(item.to_level || "—")}</li>`).join("")}</ul>` : empty}</div>` +
      `<div class="change-detail" data-change-detail="conditions" hidden>${conditionRows ? `<ul>${conditionRows}</ul>` : empty}</div>` +
      `<div class="change-detail" data-change-detail="highlow" hidden>${highLowRows ? `<ul>${highLowRows}</ul>` : empty}</div>` +
      `<div class="change-detail" data-change-detail="volume" hidden>${spikes.length ? `<ul>${spikes.map((item) => `<li><b>${esc(item.display || item.symbol || "")}</b> 20일 평균의 ${fmt(item.ratio, 2)}배</li>`).join("")}</ul>` : empty}</div>` +
      `<a class="changes-link" href="/stocks">전체 목록은 종목 페이지에서 ▸</a>`;
    host.querySelectorAll("[data-change-panel]").forEach((button) => button.addEventListener("click", () => {
      const target = button.dataset.changePanel;
      const detail = host.querySelector(`[data-change-detail="${target}"]`);
      const opening = detail && detail.hidden;
      host.querySelectorAll("[data-change-detail]").forEach((item) => { item.hidden = true; });
      host.querySelectorAll("[data-change-panel]").forEach((item) => item.setAttribute("aria-expanded", "false"));
      if (detail && opening) { detail.hidden = false; button.setAttribute("aria-expanded", "true"); }
    }));
  }
  function renderSummaryStrip(d) {
    const f = d.flows && d.flows.rows ? d.flows.rows[0] : null;
    const dv = d.derivatives && d.derivatives.groups ? d.derivatives.groups[0] : null;
    const groups = [];
    if (f) groups.push(`<span class="summary-group"><b>수급</b><span>외국인 오늘 ${signed(f.today)}억원 · 5일 ${signed(f.d5)}억원</span></span>`);
    if (dv && dv.rows && dv.rows.length) groups.push(`<span class="summary-group"><b>파생</b><span>${dv.rows.slice(0, 2).map((r) => `${esc(r[0])} ${esc(r[1])}`).join(" · ")}</span></span>`);
    const scheduleEvents = d.schedule && Array.isArray(d.schedule.events) ? d.schedule.events.map((item) => ({ when: item.time, what: item.text })) : d.schedule && Array.isArray(d.schedule.items) ? d.schedule.items : [];
    if (scheduleEvents.length) groups.push(`<span class="summary-group"><b>일정</b><span>${scheduleEvents.slice(0, 2).map((i) => `${esc(i.when)} ${esc(i.what)}`).join(" · ")}</span></span>`);
    const more = [];
    if ((d.schedule && d.schedule.briefs && d.schedule.briefs.length) || (d.brief && d.brief.lines && d.brief.lines.length)) more.push("브리핑");
    if (d.changes && d.changes.counts) {
      const c = d.changes.counts;
      more.push(`오늘 변화 규칙 ${c.rule_changes || 0} · 조건 ${c.condition_entries || 0}/${c.condition_exits || 0}`);
    }
    if (more.length) groups.push(`<span class="summary-group ml">${more.join(" · ")} · <a href="/stocks">자세히 ▸</a></span>`);
    $("summary-strip").innerHTML = groups.length ? groups.join('<span class="summary-separator">|</span>') : '<span class="muted">표시할 요약이 없습니다.</span>';
  }
  function renderHealth(h) {
    const chip = $("health-chip");
    const text = !h || h.reason
      ? (h && h.reason ? `데이터 갱신 상태 미확인 · ${h.reason}` : "데이터 갱신 상태 미확인")
      : `데이터 갱신: ${(h.labels && h.labels.current) || "정시"} ${h.current ?? 0} · ${(h.labels && h.labels.late) || "지연"} ${h.lag ?? 0} · ${(h.labels && h.labels.failed) || "실패"} ${h.fail ?? 0} ▸`;
    chip.textContent = text;
    chip.title = text;
  }

  // ---- boot -----------------------------------------------------------------------
  let payload = null;
  let homeToastTimer = null;
  function showHomeToast(message, error = false) {
    const toast = $("home-toast");
    if (!toast) return;
    clearTimeout(homeToastTimer);
    toast.textContent = message;
    toast.classList.toggle("error", error);
    toast.hidden = false;
    homeToastTimer = setTimeout(() => { toast.hidden = true; }, 3200);
  }
  async function saveJournalNote() {
    const input = $("journal-note"), button = $("save-journal-note");
    const note = String((input || {}).value || "").trim();
    if (!note) { showHomeToast("판단을 한 줄 적어 주세요.", true); return; }
    if (/(?:[₩$]\s*\d)|(?:\d[\d,.]*\s*(?:원|만원|억|천만|달러|불))/.test(note)) { showHomeToast("금액은 적지 않습니다", true); return; }
    button.disabled = true;
    try {
      const response = await fetch("/api/journal/note", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: note }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      input.value = "";
      showHomeToast("오늘 판단을 저장했습니다.");
    } catch (error) { showHomeToast(error.message || "저장하지 못했습니다.", true); }
    finally { button.disabled = false; }
  }
  async function loadChart(symbol, range) {
    try {
      const r = await fetch(`/api/chart?symbol=${encodeURIComponent(symbol)}&range=${encodeURIComponent(range)}`);
      renderChart(r.ok ? await r.json() : { reason: `HTTP ${r.status}` });
    } catch (e) { renderChart({ reason: String(e) }); }
    document.querySelectorAll(".tile").forEach((el) => el.classList.toggle("on", el.dataset.symbol === symbol));
  }
  function currentRange() { const b = document.querySelector("#chart-range button.on"); return b ? b.dataset.v : "6M"; }
  async function boot() {
    applyRegimeEvidenceState(loadRegimeEvidenceOpen());
    wireHomeIndicatorPicker();
    $("regime").addEventListener("click", (event) => {
      if (!event.target.closest(".regime-title-line, .regime-toggle")) return;
      applyRegimeEvidenceState($("regime").dataset.expanded !== "true", true);
    });
    $("tiles-more").addEventListener("click", () => { const t = $("tiles"); t.classList.toggle("collapsed"); $("tiles-more").textContent = t.classList.contains("collapsed") ? "지표 더 보기 ▾" : "지표 접기 ▴"; });
    $("tiles").classList.add("collapsed");
    document.querySelectorAll("#chart-interval button").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#chart-interval button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); renderChart(); }));
    document.querySelectorAll("#chart-range button").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#chart-range button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); loadChart($("chart-symbol").value, b.dataset.v); }));
    document.querySelectorAll("#account-range button").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#account-range button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); renderAccount(((payload || {}).sections || {}).account, b.dataset.v); }));
    $("chart-symbol").addEventListener("change", () => loadChart($("chart-symbol").value, currentRange()));
    if ($("save-journal-note")) $("save-journal-note").addEventListener("click", saveJournalNote);
    try {
      const r = await fetch("/api/home"); payload = await r.json();
    } catch (e) { payload = { sections: {} }; }
    const s = payload.sections || {};
    $("as-of").textContent = payload.as_of_label || "";
    renderHealth(s.health);
    renderRegime(s.regime);
    renderTiles(s.tiles, (sym) => { $("chart-symbol").value = sym; loadChart(sym, currentRange()); });
    $("sectors").innerHTML = s.sectors ? `<span><b>업종</b></span>` + s.sectors.map((x) => `<span class="muted">${esc(x.name)} <span class="num ${cls(x.change_pct)}">${pct(x.change_pct)}</span></span>`).join("") : "";
    const symbols = (s.chart_symbols || []);
    const requestedSymbol = (($("home-page") || {}).dataset || {}).initialSymbol || new URLSearchParams(window.location.search).get("symbol") || "";
    if (requestedSymbol && !symbols.some((item) => item.symbol === requestedSymbol)) {
      const watch = ((s.watchlist || {}).rows || []).find((item) => item.symbol === requestedSymbol);
      symbols.push({ symbol: requestedSymbol, name: watch ? `${watch.name} · ${requestedSymbol}` : requestedSymbol });
    }
    $("chart-symbol").innerHTML = symbols.map((x) => `<option value="${esc(x.symbol)}">${esc(x.name)}</option>`).join("") || `<option value="">차트 없음</option>`;
    if (symbols.length) {
      const initial = requestedSymbol && symbols.some((item) => item.symbol === requestedSymbol) ? requestedSymbol : symbols[0].symbol;
      $("chart-symbol").value = initial;
      loadChart(initial, currentRange());
    } else renderChart(null);
    renderWatchlist(s.watchlist); renderAccount(s.account); renderFlows(s.flows, payload.as_of || payload.as_of_label); renderDerivatives(s.derivatives);
    renderSchedule(s.schedule); renderBrief(s.schedule, s.brief); renderChanges(s.changes); renderSummaryStrip(s);
  }
  function enforcePublicUi(root = document) {
    if (!publicMode) return;
    root.querySelectorAll("[data-private-account], .held-badge").forEach((element) => { element.hidden = true; });
    const watchlistTitle = document.querySelector("#watchlist-card .card-head b");
    if (watchlistTitle) watchlistTitle.textContent = "관심종목";
    ["toggle-detail-watchlist", "edit-detail-conditions"].forEach((id) => {
      const button = $(id);
      if (button) button.hidden = true;
    });
  }
  function enforceGlobalEquityHeadline(root = document) {
    const card = root.querySelector && root.querySelector("#stock-headline-card");
    if (!card) return;
    const title = card.querySelector("h1"), subtitle = card.querySelector(".stock-headline-top p");
    if (!title || !subtitle || title.textContent.trim() !== "SK하이닉스(ADR)" || !subtitle.textContent.includes("SKHY")) return;
    title.innerHTML = 'SK하이닉스(ADR) · NASDAQ · 원주 <a href="/stocks?symbol=000660">000660</a>';
    subtitle.textContent = "SKHY · US 주식 · ADR";
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { aggregateCandles, brokerReportedPnl, formatCompactKorean, formatSharePercent, rsiWilder, signedEok };
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", () => {
    if (publicMode) {
      enforcePublicUi();
      new MutationObserver(() => enforcePublicUi()).observe(document.body, { childList: true, subtree: true });
    }
    if ($("stocks-page")) {
      enforceGlobalEquityHeadline();
      new MutationObserver(() => enforceGlobalEquityHeadline()).observe($("stock-headline-card"), { childList: true, subtree: true });
    }
    if ($("home-page")) boot();
  });
})();
