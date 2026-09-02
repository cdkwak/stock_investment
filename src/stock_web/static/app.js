/* Home page renderer. Everything is read-only; a missing section renders as 표시 불가. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 1) => (v === null || v === undefined) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
  const cls = (v) => (v === null || v === undefined) ? "muted" : (v > 0 ? "up" : v < 0 ? "down" : "muted");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const unavailable = (why) => `<div class="unavailable">표시 불가${why ? " · " + esc(why) : ""}</div>`;

  function sparkline(values, w = 140, h = 22) {
    if (!values || values.length < 2) return "";
    const lo = Math.min(...values), hi = Math.max(...values), pad = (hi - lo) * 0.1 || 1;
    const pts = values.map((v, i) => `${(1 + (w - 2) * i / (values.length - 1)).toFixed(1)},${(1 + (h - 2) - (h - 2) * (v - lo + pad) / (hi - lo + 2 * pad)).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="#1f1d1a" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round"></polyline></svg>`;
  }

  // ---- regime -------------------------------------------------------------
  function renderRegime(sec) {
    const host = $("regime-cards");
    if (!sec || !sec.markets) { host.innerHTML = `<div class="regime-card">${unavailable("국면 근거 미계산")}</div>`; return; }
    host.innerHTML = sec.markets.map((m) => `
      <div class="regime-card">
        <div class="t">${esc(m.title)}</div>
        <div class="temp"><b style="color:${m.hot ? "var(--amber-soft)" : "#f4f2ee"}">${esc(m.temperature)}</b><span>${esc(m.subtitle || "")}</span></div>
        <div class="ev">${(m.evidence || []).map((e) => `<div><span>${esc(e[0])}</span><span class="num">${esc(e[1])}</span></div>`).join("")}</div>
      </div>`).join("");
    const r = sec.rules;
    $("rules").innerHTML = r ? `
      <div class="t">내 규칙 기준 점검</div>
      ${(r.rows || []).map((x) => `<div class="row"><span>${esc(x[0])}</span><span class="num"><b>${esc(x[1])}</b> <span style="color:#b5aea4">${esc(x[2] || "")}</span></span></div>`).join("")}
      ${r.warning ? `<div class="warn">${esc(r.warning)}</div>` : ""}
      <div style="font-size:10px;color:#8a847b;margin-top:4px">${esc(r.source || "")}</div>` : `<div class="t">내 규칙</div><div style="color:#b5aea4;font-size:11px">규칙 파일 없음 · Obsidian 30_규칙/투자 규칙.md</div>`;
  }

  // ---- tiles ----------------------------------------------------------------
  function renderTiles(tiles, onPick) {
    const host = $("tiles");
    host.innerHTML = (tiles || []).map((t) => `
      <div class="tile" data-symbol="${esc(t.symbol || "")}">
        <div class="n">${esc(t.name)}</div>
        <div class="v"><b class="num">${t.value ?? "—"}</b><span class="num ${cls(t.change_pct)}">${t.change_label ?? pct(t.change_pct)}</span></div>
        <div class="ma"><span>5일 <span class="num ${cls(t.ma5_pct)}">${pct(t.ma5_pct)}</span></span><span>20일 <span class="num ${cls(t.ma20_pct)}">${pct(t.ma20_pct)}</span></span></div>
        ${t.spark ? `<div class="spark">${sparkline(t.spark)}<small>${esc(t.window || "")}</small></div>` : `<div class="note">${esc(t.note || "표시 불가")}</div>`}
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
    candleSeries = chart.addCandlestickSeries({ upColor: "#fff", downColor: "#2b62c0", borderUpColor: "#c0392b", borderDownColor: "#2b62c0", wickUpColor: "#c0392b", wickDownColor: "#2b62c0" });
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
  let acctChart, acctSeries, acctBench;
  function renderAccount(sec) {
    const host = $("account");
    if (!sec || sec.total_krw === undefined) { host.innerHTML = unavailable(sec && sec.reason); return; }
    host.innerHTML = `
      <div class="acct-total"><b class="num">₩ ${fmt(sec.total_krw / 1e8, 2)}억</b><span class="num ${cls(sec.day_change_pct)}">어제 ${pct(sec.day_change_pct)}${sec.day_change_krw !== undefined ? ` (${sec.day_change_krw >= 0 ? "+" : ""}${fmt(sec.day_change_krw / 1e4, 0)}만)` : ""}</span></div>
      <div class="acct-meta">
        ${sec.period_pct !== undefined ? `<span>${esc(sec.period_label || "기간")} <b class="num ${cls(sec.period_pct)}">${pct(sec.period_pct)}</b></span>` : ""}
        ${sec.kospi_period_pct !== undefined ? `<span>KOSPI 동기간 <b class="num ${cls(sec.kospi_period_pct)}">${pct(sec.kospi_period_pct)}</b></span>` : ""}
        ${sec.ytd_pct !== undefined ? `<span>연초 <b class="num ${cls(sec.ytd_pct)}">${pct(sec.ytd_pct)}</b></span>` : ""}
        ${sec.effective_exposure_pct !== undefined ? `<span>실효 노출 <b class="num">${fmt(sec.effective_exposure_pct, 0)}%</b></span>` : ""}
        ${sec.leveraged_weight_pct !== undefined ? `<span>레버리지 명목 <b class="num">${fmt(sec.leveraged_weight_pct, 0)}%</b></span>` : ""}
        ${sec.cash_pct !== undefined ? `<span>현금 <b class="num">${fmt(sec.cash_pct, 0)}%</b></span>` : ""}
        ${sec.short_treasury_pct !== undefined ? `<span>단기국채 <b class="num">${fmt(sec.short_treasury_pct, 0)}%</b></span>` : ""}
      </div>
      <div class="acct-meta">
        ${sec.usd_assets_usd !== undefined ? `<span>달러 자산 <b class="num">$${fmt(sec.usd_assets_usd, 0)} = ${fmt(sec.usd_assets_krw / 1e8, 2)}억</b> (${fmt(sec.usdkrw, 2)}원 · ${esc(sec.usdkrw_as_of || "기준일 미상")})</span>` : `<span>달러 자산 —</span>`}
        ${sec.fx_effect_pct !== undefined ? `<span>환율 효과 어제 <b class="num ${cls(sec.fx_effect_pct)}">${pct(sec.fx_effect_pct)}</b></span>` : ""}
        ${sec.equity_effect_pct !== undefined ? `<span>주식 효과 <b class="num ${cls(sec.equity_effect_pct)}">${pct(sec.equity_effect_pct)}</b></span>` : ""}
      </div>
      <div id="acct-chart" class="acct-chart"></div>
      <div class="acct-foot">${esc(sec.footnote || "계좌 규모 변화 · 점선은 KOSPI 비교")}</div>
      ${(sec.exposure_unverified || []).length ? `<div class="acct-foot">배수 미확인(1배 처리): ${esc(sec.exposure_unverified.join(", "))}</div>` : ""}`;
    if (window.LightweightCharts && sec.history && sec.history.length > 1) {
      acctChart = LightweightCharts.createChart($("acct-chart"), { layout: { background: { color: "#fff" }, textColor: "#6b6660" }, grid: { vertLines: { visible: false }, horzLines: { color: "#e6e1d8" } }, rightPriceScale: { borderColor: "#d9d3ca" }, timeScale: { borderColor: "#d9d3ca" }, autoSize: true, handleScroll: false, handleScale: false });
      acctSeries = acctChart.addLineSeries({ color: "#1f1d1a", lineWidth: 2, priceLineVisible: false });
      acctSeries.setData(sec.history.map((p) => ({ time: p.t, value: p.v })));
      if (sec.benchmark) { acctBench = acctChart.addLineSeries({ color: "#b5aea4", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }); acctBench.setData(sec.benchmark.map((p) => ({ time: p.t, value: p.v }))); }
      acctChart.timeScale().fitContent();
    }
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
    $("summary-strip").innerHTML = `<b>수급</b><span>${f ? `외국인 오늘 ${signed(f.today)} · 5일 ${signed(f.d5)}` : '<span class="muted">—</span>'}</span>` +
      `<span style="color:var(--line)">|</span><b>파생</b><span>${dv ? dv.rows.slice(0, 2).map((r) => `${esc(r[0])} ${esc(r[1])}`).join(" · ") : '<span class="muted">—</span>'}</span>` +
      `<span style="color:var(--line)">|</span><b>일정</b><span>${d.schedule && d.schedule.items ? d.schedule.items.slice(0, 2).map((i) => `${esc(i.when)} ${esc(i.what)}`).join(" · ") : '<span class="muted">—</span>'}</span>` +
      `<span class="ml">브리핑 · 스캐너 ${d.scanner && d.scanner.count !== undefined ? d.scanner.count + "개" : "—"} · 자세히 ▸</span>`;
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
    $("regime-toggle").addEventListener("click", () => {
      const sec = $("regime"); const open = getComputedStyle(sec.querySelector(".ev") || sec).display !== "none";
      sec.dataset.expanded = open ? "false" : "true"; $("regime-toggle").textContent = open ? "근거 펼치기 ▾" : "근거 접기 ▴";
    });
    $("tiles-more").addEventListener("click", () => { const t = $("tiles"); t.classList.toggle("collapsed"); $("tiles-more").textContent = t.classList.contains("collapsed") ? "지표 더 보기 ▾" : "지표 접기 ▴"; });
    $("tiles").classList.add("collapsed");
    document.querySelectorAll("#chart-range button").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#chart-range button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); loadChart($("chart-symbol").value, b.dataset.v); }));
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
    $("chart-symbol").innerHTML = symbols.map((x) => `<option value="${esc(x.symbol)}">${esc(x.name)}</option>`).join("") || `<option value="">차트 없음</option>`;
    if (symbols.length) loadChart(symbols[0].symbol, currentRange()); else renderChart(null);
    renderWatchlist(s.watchlist); renderAccount(s.account); renderFlows(s.flows); renderDerivatives(s.derivatives);
    renderSchedule(s.schedule); renderBrief(s.brief); renderScanner(s.scanner); renderSummaryStrip(s);
  }
  document.addEventListener("DOMContentLoaded", boot);
})();
