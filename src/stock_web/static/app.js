/* Home page renderer. Everything is read-only; a missing section renders as 표시 불가. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 1) => (v === null || v === undefined) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
  const cls = (v) => (v === null || v === undefined) ? "muted" : (v > 0 ? "up" : v < 0 ? "down" : "muted");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const intradayValue = (value) => Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 2 });
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

  function formatPcr(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value).toFixed(2) : "—";
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
    const x = (point) => left + (point.ms - start) / span * plotW;
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
    if (!values || values.length < 2) return "";
    const numeric = values.map((point) => typeof point === "object" ? Number(point.v) : Number(point));
    const lo = Math.min(...numeric), hi = Math.max(...numeric), pad = (hi - lo) * 0.1 || 1;
    const pts = numeric.map((v, i) => `${(1 + (w - 2) * i / (numeric.length - 1)).toFixed(1)},${(1 + (h - 2) - (h - 2) * (v - lo + pad) / (hi - lo + 2 * pad)).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="#1f1d1a" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round"></polyline></svg>`;
  }

  // ---- regime -------------------------------------------------------------
  function renderRegime(sec) {
    const host = $("regime-cards");
    if (!sec || !sec.markets) { host.innerHTML = `<div class="regime-card">${unavailable("국면 근거 미계산")}</div>`; return; }
    host.innerHTML = sec.markets.map((m, index) => `
      <div class="regime-card">
        <div class="regime-title-line">
          <span class="t">${esc(m.title)}</span>
          <div class="temp"><b style="color:${m.hot ? "var(--amber-soft)" : "#f4f2ee"}">${esc(m.temperature)}</b><span>${esc(m.subtitle || "")}</span>${index === 0 ? '<button class="regime-toggle" id="regime-toggle" type="button" aria-expanded="false">근거 펼치기 ▾</button>' : ""}</div>
        </div>
        <div class="ev">${(m.evidence || []).map((e) => `<div><span>${esc(e[0])}</span><span class="num">${esc(e[1])}</span></div>`).join("")}</div>
      </div>`).join("");
    const r = sec.rules;
    $("rules").innerHTML = r ? `
      <div class="t">내 규칙 기준 점검</div>
      ${(r.rows || []).map((x) => `<div class="row"><span>${esc(x[0])}</span><span class="num"><b>${esc(x[1])}</b> <span style="color:#b5aea4">${esc(x[2] || "")}</span></span></div>`).join("")}
      ${r.warning ? `<div class="warn">${esc(r.warning)}</div>` : ""}
      <div style="font-size:10px;color:#8a847b;margin-top:4px">${esc(r.source || "")}</div>` : `<div class="t">내 규칙</div><div style="color:#b5aea4;font-size:11px">규칙 값 미입력 · Obsidian "투자 규칙.md"의 [채우기] 값을 채우면 표시됩니다</div>`;
  }

  // ---- tiles ----------------------------------------------------------------
  function renderTiles(tiles, onPick) {
    const host = $("tiles");
    host.innerHTML = (tiles || []).map((t) => `
      <div class="tile" data-symbol="${esc(t.symbol || "")}">
        <div class="n">${esc(t.name)}</div>
        <div class="v"><span class="headline-value"><b class="num">${t.value ?? "—"}</b>${t.latest_intraday ? `<small class="muted">장중 <span class="num">${intradayValue(t.latest_intraday.value)}</span> · ${asof(t.latest_intraday.time).slice(-5)}${t.close_change_pct !== undefined && t.close_change_pct !== null ? ` · 마감${t.close_date ? " " + t.close_date : ""} <span class="num ${cls(t.close_change_pct)}">${pct(t.close_change_pct)}</span>` : ""}</small>` : ""}</span><span class="num ${cls(t.change_pct)}">${t.change_label ?? pct(t.change_pct)}</span></div>
        <div class="ma"><span>5일 <span class="num ${cls(t.ma5_pct)}">${pct(t.ma5_pct)}</span></span><span>20일 <span class="num ${cls(t.ma20_pct)}">${pct(t.ma20_pct)}</span></span></div>
        ${t.spark ? `<div class="spark">${sparkline(t.spark)}<small>${esc(t.window || "")}</small></div>` : `<div class="note">${esc(t.note || "표시 불가")}</div>`}
        ${t.sub_note ? `<div class="tile-sub-note">${esc(t.sub_note)}</div>` : ""}
      </div>`).join("");
    host.querySelectorAll(".tile").forEach((el) => el.addEventListener("click", () => el.dataset.symbol && onPick(el.dataset.symbol)));
  }

  // ---- chart ----------------------------------------------------------------
  let chart, candleSeries, volSeries, maSeries = {};
  function ensureChart() {
    if (chart || !window.LightweightCharts) return;
    const el = $("chart");
    chart = LightweightCharts.createChart(el, {
      layout: { background: { color: "#fff" }, textColor: "#6b6660", fontFamily: "IBM Plex Sans KR, system-ui" },
      grid: { vertLines: { color: "#f0ece5" }, horzLines: { color: "#e6e1d8" } },
      rightPriceScale: { borderColor: "#d9d3ca" }, timeScale: { borderColor: "#d9d3ca" },
      crosshair: { mode: 1 }, autoSize: true,
    });
    candleSeries = chart.addCandlestickSeries({ upColor: "#c0392b", downColor: "#2b62c0", borderUpColor: "#c0392b", borderDownColor: "#2b62c0", wickUpColor: "#c0392b", wickDownColor: "#2b62c0" });
    volSeries = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const colors = { ma5: "#4a3aa7", ma20: "#2a78d6", ma60: "#eb6834", ma120: "#1baf7a" };
    for (const k of Object.keys(colors)) maSeries[k] = chart.addLineSeries({ color: colors[k], lineWidth: k === "ma5" ? 1 : 2, priceLineVisible: false, lastValueVisible: false });
  }
  function renderChart(sec) {
    const stats = $("chart-stats"), legend = $("chart-legend");
    if (!sec || !sec.candles || !sec.candles.length) { $("chart").innerHTML = unavailable(sec && sec.reason); stats.innerHTML = ""; return; }
    ensureChart();
    if (!chart) { $("chart").innerHTML = unavailable("차트 라이브러리 로드 실패"); return; }
    candleSeries.setData(sec.candles.map((c) => ({ time: c.t, open: c.o, high: c.h, low: c.l, close: c.c })));
    volSeries.setData(sec.candles.map((c) => ({ time: c.t, value: c.v ?? 0, color: c.c >= c.o ? "rgba(192,57,43,.45)" : "rgba(43,98,192,.45)" })));
    for (const k of Object.keys(maSeries)) maSeries[k].setData((sec.ma && sec.ma[k]) ? sec.ma[k].filter((p) => p.v !== null).map((p) => ({ time: p.t, value: p.v })) : []);
    chart.timeScale().fitContent();
    const s = sec.stats || {};
    stats.innerHTML = `
      <span class="num muted">RSI14 <b>${s.rsi14 === undefined ? "—" : fmt(s.rsi14, 0)}</b></span>
      <span class="num muted">60일선 <b class="${cls(s.disp60_pct)}">${pct(s.disp60_pct)}</b></span>
      <span class="num muted">고점 대비 <b class="${cls(s.drawdown_pct)}">${pct(s.drawdown_pct)}</b></span>
      ${s.per !== undefined ? `<span class="badge">PER <b class="num">${fmt(s.per)}</b>${s.per_note ? " " + esc(s.per_note) : ""}</span>` : ""}
      ${s.pbr !== undefined ? `<span class="badge">PBR <b class="num">${fmt(s.pbr)}</b></span>` : ""}
      <span class="badge dashed">선행 PER · PBR — 소스 검증 전</span>`;
    legend.innerHTML = `<span><i style="background:#1f1d1a"></i>${esc(sec.symbol_name || sec.symbol)}</span><span><i style="background:#4a3aa7"></i>MA5</span><span><i style="background:#2a78d6"></i>MA20</span><span><i style="background:#eb6834"></i>MA60</span><span><i style="background:#1baf7a"></i>MA120</span><span class="muted">거래량 (아래) · 기준일 ${esc(sec.as_of || "")}</span>`;
  }

  // ---- watchlist --------------------------------------------------------------
  function renderWatchlist(sec) {
    const host = $("watchlist");
    if (!sec || !sec.rows) { host.innerHTML = unavailable(sec && sec.reason); return; }
    $("watchlist-meta").textContent = `보유 ${sec.held_count ?? 0} · 관심 ${sec.watch_count ?? 0} · 당일 순매수 억원`;
    host.innerHTML = `<div class="tr th watch"><div>종목</div><div>보유</div><div class="r">현재가</div><div class="r">등락</div><div class="r">고점 대비</div><div class="r">RSI</div><div class="r">외국인</div><div class="r">기관</div><div class="r">개인</div></div>` +
      sec.rows.map((r) => `<div class="tr watch">
        <div><div>${esc(r.name)}</div>${r.flag ? `<div class="flag">조건 도달 · ${esc(r.flag)}</div>` : ""}</div>
        <div class="${r.held ? "" : "muted"}" style="font-size:10.5px">${r.held ? "보유 " + pct(r.weight_pct, 0).replace("+", "") : "관심"}</div>
        <div class="r num">${r.price ?? "—"}</div>
        <div class="r num ${cls(r.change_pct)}">${pct(r.change_pct)}</div>
        <div class="r num ${cls(r.drawdown_pct)}">${pct(r.drawdown_pct)}</div>
        <div class="r num">${r.rsi14 === null || r.rsi14 === undefined ? "—" : Math.round(r.rsi14)}</div>
        <div class="r num ${cls(r.flow_foreign)}">${r.flow_foreign ?? "—"}</div>
        <div class="r num ${cls(r.flow_inst)}">${r.flow_inst ?? "—"}</div>
        <div class="r num ${cls(r.flow_indiv)}">${r.flow_indiv ?? "—"}</div>
      </div>`).join("");
  }

  // ---- account ------------------------------------------------------------------
  const signedKrw = (value) => value === null || value === undefined ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}₩${fmt(Math.abs(value) / 1e4, 0)}만`;
  function renderAccount(sec, selectedWindow = "3M") {
    const host = $("account");
    const investTotal = sec && sec.invest_total_krw !== undefined ? sec.invest_total_krw : sec && sec.total_krw;
    if (!sec || investTotal === undefined) { host.innerHTML = unavailable(sec && sec.reason); return; }
    const metric = (sec.return_metrics || {})[selectedWindow] || {};
    const startDate = metric.start_date;
    const chartHistory = startDate ? (sec.history || []).filter((point) => point.t >= startDate) : (sec.history || []);
    const chartBenchmark = startDate ? (sec.benchmark || []).filter((point) => point.t >= startDate) : (sec.benchmark || []);
    host.innerHTML = `
      <div class="acct-total"><span class="muted">투자 자산</span><b class="num">₩ ${fmt(investTotal / 1e8, 2)}억</b></div>
      <div class="acct-truth-lines">
        <span>총자산 변동 어제 <b class="num ${cls(sec.daily_true_change_krw)}">${signedKrw(sec.daily_true_change_krw)}</b> <small>(순입금 제외)</small></span>
        <span>이번 달 진짜 손익 <b class="num ${cls(sec.month_true_pnl_krw)}">${signedKrw(sec.month_true_pnl_krw)}</b></span>
        <span title="입출금 시점을 반영해 내가 실제 투입한 돈 대비 수익률입니다.">${esc(selectedWindow === "ALL" ? "전체" : selectedWindow)} 돈 가중(내 실제 수익률) <b class="num ${cls(metric.return_pct_modified_dietz)}">${pct(metric.return_pct_modified_dietz)}</b></span>
      </div>
      ${sec.net_worth_krw !== undefined && sec.net_worth_krw !== null ? `<div class="acct-net-worth"><span>순자산</span> <b class="num">₩${fmt(sec.net_worth_krw / 1e8, 2)}억</b> <small>(부동산·예금 포함, ${esc(sec.net_worth_as_of_label || asof(sec.net_worth_as_of))} 기준)</small></div>` : ""}
      <div class="acct-meta">
        ${metric.reason ? `<span class="muted">${esc(metric.reason)}</span>` : ""}
        <span title="입출금 영향을 잘라내고 운용 성과만 이어 붙인 수익률입니다.">시간 가중(운용 실력) <b class="num ${cls(metric.return_pct_twr)}">${pct(metric.return_pct_twr)}</b></span>
        <span>KOSPI 동기간 <b class="num ${cls(metric.kospi_return_pct)}">${pct(metric.kospi_return_pct)}</b></span>
        <span>증권사 표시 손익 <b class="num ${cls(metric.broker_reported_pnl_krw)}">${signedKrw(metric.broker_reported_pnl_krw)}</b></span>
        ${metric.partial ? '<span class="badge dashed">부분 관측 포함</span>' : ""}
        ${sec.effective_exposure_pct !== undefined ? `<span>실효 노출 <b class="num">${fmt(sec.effective_exposure_pct, 0)}%</b></span>` : ""}
        ${sec.leveraged_weight_pct !== undefined ? `<span>레버리지 명목 <b class="num">${fmt(sec.leveraged_weight_pct, 0)}%</b></span>` : ""}
        ${sec.cash_pct !== undefined ? `<span>현금 <b class="num">${fmt(sec.cash_pct, 0)}%</b></span>` : ""}
        ${sec.short_treasury_pct !== undefined ? `<span>단기국채 <b class="num">${fmt(sec.short_treasury_pct, 0)}%</b></span>` : ""}
      </div>
      <div class="acct-meta">
        ${sec.usd_assets_usd !== undefined ? `<span>달러 자산 <b class="num">$${fmt(sec.usd_assets_usd, 0)} = ${fmt(sec.usd_assets_krw / 1e8, 2)}억</b> (${fmt(sec.usdkrw, 2)}원 · ${esc(sec.usdkrw_as_of_label || asof(sec.usdkrw_as_of))})</span>` : `<span>달러 자산 —</span>`}
        ${sec.fx_effect_pct !== undefined ? `<span>환율 효과 어제 <b class="num ${cls(sec.fx_effect_pct)}">${pct(sec.fx_effect_pct)}</b></span>` : ""}
        ${sec.equity_effect_pct !== undefined ? `<span>주식 효과 <b class="num ${cls(sec.equity_effect_pct)}">${pct(sec.equity_effect_pct)}</b></span>` : ""}
      </div>
      <div id="acct-chart" class="acct-chart"></div>
      <div class="acct-foot">${esc(sec.footnote || "계좌 규모 변화 · 점선은 KOSPI 비교")}</div>
      ${(sec.sources || []).length ? `<div class="acct-foot">${sec.sources.map((source) => `${esc(source.name)} ${esc(source.as_of_label || asof(source.as_of))}${source.included ? "" : " 제외"}`).join(" · ")}</div>` : ""}
      ${(sec.exposure_unverified || []).length ? `<div class="acct-foot">배수 미확인(1배 처리): ${esc(sec.exposure_unverified.join(", "))}</div>` : ""}`;
    renderLineChart($("acct-chart"), chartHistory, { benchmark: chartBenchmark, height: 150, ariaLabel: "총자산과 KOSPI 동기간 추이" });
  }

  // ---- bottom cards ----------------------------------------------------------
  const signed = (v) => (v === null || v === undefined) ? '<span class="muted">—</span>' : `<span class="${cls(v)}">${v > 0 ? "+" : ""}${fmt(v, 0)}</span>`;
  function renderFlows(sec) {
    const host = $("flows");
    if (!sec || !sec.rows) { host.innerHTML = unavailable(sec && sec.reason); return; }
    host.innerHTML = `<div class="table"><div class="tr th flow"><div>순매수 (억원)</div><div class="r">오늘</div><div class="r">5일</div><div class="r">20일</div></div>` +
      sec.rows.map((r) => `<div class="tr flow"><div class="muted">${esc(r.name)}</div><div class="r num">${signed(r.today)}</div><div class="r num">${signed(r.d5)}</div><div class="r num">${signed(r.d20)}</div></div>`).join("") + `</div>` +
      ((sec.balances || []).length ? `<div class="table"><div class="tr th bal"><div>잔고</div><div>현재 · 1년 위치</div><div class="r">5일</div><div class="r">20일</div><div>20일 추세</div></div>` +
        sec.balances.map((b) => `<div class="tr bal"><div class="muted">${esc(b.name)}</div><div class="num">${esc(b.value)} <small class="${b.hot ? "up" : "muted"}">${esc(b.position || "")}</small></div><div class="r num ${cls(b.d5_pct)}">${pct(b.d5_pct)}</div><div class="r num ${cls(b.d20_pct)}">${pct(b.d20_pct)}</div><div>${sparkline(b.spark || [], 70, 18)}</div></div>`).join("") + `</div>` : "");
  }
  function renderDerivatives(sec) {
    const host = $("derivatives");
    if (!sec || !sec.groups) { host.innerHTML = unavailable(sec && sec.reason); return; }
    host.innerHTML = sec.groups.map((g) => `<div class="sub">${esc(g.title)}</div>` + g.rows.map((r) => `<div class="kv"><span>${esc(r[0])}</span><span class="num">${esc(r[1])}</span></div>`).join("")).join("") + `<div class="muted" style="font-size:10px;margin-top:6px">콜 월·풋 월·옵션 분포는 시장 페이지에서 ▸</div>`;
  }
  function renderSchedule(sec) {
    const host = $("schedule");
    if (!sec || !sec.items) { host.innerHTML = unavailable(sec && sec.reason); return; }
    host.innerHTML = sec.items.map((it) => `<div class="kv"><span class="num" style="width:44px">${esc(it.when)}</span><span style="flex-grow:1;color:var(--ink)">${esc(it.what)}</span><span class="dots muted" style="font-size:10px">${[1, 2, 3].map((k) => `<i class="${k <= (it.importance || 0) ? "on" : ""}"></i>`).join("")}${["", "참고", "보통", "중요"][it.importance || 0] || ""}</span></div>`).join("");
  }
  function renderBrief(sec) {
    const host = $("brief");
    if (!sec || !sec.lines) { host.innerHTML = unavailable(sec && sec.reason); return; }
    $("brief-meta").textContent = sec.meta || "";
    host.innerHTML = sec.lines.map((l) => `<div style="font-size:12px;line-height:1.55">· ${esc(l)}</div>`).join("");
  }
  function renderScanner(sec) {
    const host = $("scanner");
    if (!sec) { host.innerHTML = `<b>과매도 스캐너</b><span class="muted">표시 불가</span>`; return; }
    host.innerHTML = `<b>과매도 스캐너</b><span class="muted">${esc(sec.as_of || "")} 기준 후보 <b style="color:var(--ink)">${sec.count ?? 0}</b>개 · ${esc(sec.rule || "")}</span>` +
      `<span style="display:flex;gap:10px">${(sec.top || []).map((t) => `<span>${esc(t.name)} <span class="num down">${esc(t.why)}</span></span>`).join("")}</span><span class="ml">전체 목록은 종목 페이지에서 ▸</span>`;
  }
  function renderSummaryStrip(d) {
    const f = d.flows && d.flows.rows ? d.flows.rows[0] : null;
    const dv = d.derivatives && d.derivatives.groups ? d.derivatives.groups[0] : null;
    const groups = [];
    if (f) groups.push(`<span class="summary-group"><b>수급</b><span>외국인 오늘 ${signed(f.today)} · 5일 ${signed(f.d5)}</span></span>`);
    if (dv && dv.rows && dv.rows.length) groups.push(`<span class="summary-group"><b>파생</b><span>${dv.rows.slice(0, 2).map((r) => `${esc(r[0])} ${esc(r[1])}`).join(" · ")}</span></span>`);
    if (d.schedule && d.schedule.items && d.schedule.items.length) groups.push(`<span class="summary-group"><b>일정</b><span>${d.schedule.items.slice(0, 2).map((i) => `${esc(i.when)} ${esc(i.what)}`).join(" · ")}</span></span>`);
    const more = [];
    if (d.brief && d.brief.lines && d.brief.lines.length) more.push("브리핑");
    if (d.scanner && d.scanner.count !== undefined) more.push(`스캐너 ${d.scanner.count}개`);
    if (more.length) groups.push(`<span class="summary-group ml">${more.join(" · ")} · 자세히 ▸</span>`);
    $("summary-strip").innerHTML = groups.length ? groups.join('<span class="summary-separator">|</span>') : '<span class="muted">표시할 요약이 없습니다.</span>';
  }
  function renderHealth(h) {
    const chip = $("health-chip");
    if (!h || h.reason) { chip.textContent = h && h.reason ? `데이터 갱신 상태 미확인 · ${h.reason}` : "데이터 갱신 상태 미확인"; return; }
    chip.textContent = `데이터 갱신: 정상 ${h.current ?? 0} · 지연 ${h.lag ?? 0} · 실패 ${h.fail ?? 0} ▸`;
  }

  // ---- boot -----------------------------------------------------------------------
  let payload = null;
  async function loadChart(symbol, range) {
    try {
      const r = await fetch(`/api/chart?symbol=${encodeURIComponent(symbol)}&range=${encodeURIComponent(range)}`);
      renderChart(r.ok ? await r.json() : { reason: `HTTP ${r.status}` });
    } catch (e) { renderChart({ reason: String(e) }); }
    document.querySelectorAll(".tile").forEach((el) => el.classList.toggle("on", el.dataset.symbol === symbol));
  }
  function currentRange() { const b = document.querySelector("#chart-range button.on"); return b ? b.dataset.v : "6M"; }
  async function boot() {
    $("regime").addEventListener("click", (event) => {
      if (!event.target.closest(".regime-title-line, .regime-toggle")) return;
      const sec = $("regime"); const open = getComputedStyle(sec.querySelector(".ev") || sec).display !== "none";
      sec.dataset.expanded = open ? "false" : "true";
      const toggle = $("regime-toggle");
      if (toggle) {
        toggle.textContent = open ? "근거 펼치기 ▾" : "근거 접기 ▴";
        toggle.setAttribute("aria-expanded", String(!open));
      }
    });
    $("tiles-more").addEventListener("click", () => { const t = $("tiles"); t.classList.toggle("collapsed"); $("tiles-more").textContent = t.classList.contains("collapsed") ? "지표 더 보기 ▾" : "지표 접기 ▴"; });
    $("tiles").classList.add("collapsed");
    document.querySelectorAll("#chart-range button").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#chart-range button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); loadChart($("chart-symbol").value, b.dataset.v); }));
    document.querySelectorAll("#account-range button").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#account-range button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); renderAccount(((payload || {}).sections || {}).account, b.dataset.v); }));
    $("chart-symbol").addEventListener("change", () => loadChart($("chart-symbol").value, currentRange()));
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
    renderWatchlist(s.watchlist); renderAccount(s.account); renderFlows(s.flows); renderDerivatives(s.derivatives);
    renderSchedule(s.schedule); renderBrief(s.brief); renderScanner(s.scanner); renderSummaryStrip(s);
  }
  document.addEventListener("DOMContentLoaded", () => { if ($("home-page")) boot(); });
})();
