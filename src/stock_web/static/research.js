/* Read-only rule-candidate leaderboard and forward-test renderer. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
  const finite = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));
  const pct = (value, digits = 1, signed = true) => finite(value) ? `${signed && Number(value) > 0 ? "+" : ""}${(Number(value) * 100).toFixed(digits)}%` : "—";
  const pctUnsigned = (value, digits = 1) => pct(value, digits, false);
  const number = (value, digits = 1) => finite(value) ? Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits }) : "—";
  const valueClass = (value) => !finite(value) ? "muted" : Number(value) > 0 ? "up" : Number(value) < 0 ? "down" : "muted";
  const sideLabels = { drawdown: "낙폭", overheat: "과열", hybrid: "혼합" };
  const basketLabels = { KR: "한국", US_TECH: "미국 기술주", SEMIS: "반도체", POOLED: "통합" };
  const statusLabels = { active: "운영", experimental: "실험", retired: "종료" };
  const actionLabels = { add: "추가", update: "변경", change: "변경", retire: "폐기", retired: "폐기", remove: "삭제" };
  const verdictLabels = { hit: "적중", miss: "미적중", none: "신호 없음" };
  const indicatorLabels = {
    drawdown252: "252일 낙폭", disp60: "60일 이격", rsi14: "RSI14", volidx_pct: "변동성지수 백분위(VIX/VKOSPI)",
  };
  const emptyMessage = "아직 평가 결과가 없습니다 · `scripts/research/run_rule_leaderboard.py` 실행 후 표시";
  let research = null;
  let selectedId = null;

  function emptyMarkup(message) {
    const parts = String(message || emptyMessage).split("`");
    return parts.map((part, index) => index % 2 ? `<code>${esc(part)}</code>` : esc(part)).join("");
  }

  function renderMeta(payload) {
    const version = String(payload.rules_version || "—");
    const fit = payload.fit_window || {}, holdout = payload.holdout_window || {};
    const rows = [
      ["규칙 버전", version === "—" ? version : version.slice(0, 8)],
      ["시도 횟수", `${number(payload.attempt_count, 0)}회`],
      ["평가 시각", payload.generated_at_display || "—"],
      ["적합 / 홀드아웃", `${fit.end || "—"} / ${holdout.start || "—"}`],
      ["경고", `${number(payload.warning_count, 0)}건`],
    ];
    $("research-meta").innerHTML = rows.map(([label, value]) => `<div class="research-meta-item"><span>${esc(label)}</span><b class="num">${esc(value)}</b></div>`).join("");
  }

  function result(candidate, split) {
    return (((candidate || {}).results || {})[split]) || {};
  }

  function renderLeaderboard(payload) {
    const candidates = payload.candidates || [];
    $("leaderboard-body").innerHTML = candidates.map((candidate) => {
      const holdout = result(candidate, "holdout"), fit = result(candidate, "fit");
      return `<tr data-candidate="${encodeURIComponent(candidate.id || "")}" tabindex="0" role="button" aria-label="${esc(candidate.name || candidate.id)} 상세 보기">
        <td class="rule-cell"><span class="rule-rank">${number(candidate.rank, 0)}</span><span class="rule-name">${esc(candidate.name || candidate.id)}</span><span class="rule-direction">${esc(candidate.direction_hint || "")}</span></td>
        <td>${number(holdout.n, 0)}</td>
        <td><b>${pct(holdout.mean_60)}</b> <span class="${valueClass(holdout.diff_60)}" title="기준 대비">(${pct(holdout.diff_60)})</span></td>
        <td>${esc(sideLabels[candidate.side] || candidate.side || "—")}</td>
        <td>${esc(basketLabels[candidate.basket] || candidate.basket || "—")}</td>
        <td><span class="research-status ${esc(candidate.status || "")}">${esc(statusLabels[candidate.status] || candidate.status || "—")}</span></td>
        <td>${pctUnsigned(holdout.hit_60, 0)}</td><td>${pctUnsigned(holdout.vol_60)}</td><td class="${valueClass(holdout.mdd_60)}">${pct(holdout.mdd_60)}</td>
        <td class="${valueClass(fit.diff_60)}">${pct(fit.diff_60)}</td>
        <td>${candidate.warn_small_sample ? '<span class="sample-warning" title="홀드아웃 표본 15 미만">⚠</span>' : ""}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="11"><div class="unavailable">${emptyMarkup(payload.message)}</div></td></tr>`;
    document.querySelectorAll("#leaderboard-body tr[data-candidate]").forEach((row) => {
      const activate = () => selectCandidate(decodeURIComponent(row.dataset.candidate || ""));
      row.addEventListener("click", activate);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); }
      });
    });
  }

  function monotonicHint(levels) {
    const values = (levels || []).map((level) => finite((level.holdout || {}).mean_60) ? Number(level.holdout.mean_60) : null).filter((value) => value !== null);
    if (values.length < 2) return "레벨별 홀드아웃 관측이 2개 이상이면 단조성을 확인합니다.";
    const increasing = values.every((value, index) => index === 0 || value >= values[index - 1]);
    const decreasing = values.every((value, index) => index === 0 || value <= values[index - 1]);
    if (increasing) return "레벨이 높을수록 홀드아웃 60일 평균이 단조 증가합니다.";
    if (decreasing) return "레벨이 높을수록 홀드아웃 60일 평균이 단조 감소합니다.";
    return "레벨과 홀드아웃 60일 평균의 단조 관계가 뚜렷하지 않습니다.";
  }

  function renderLevelChart(levels) {
    const host = $("level-chart");
    const points = (levels || []).map((level) => ({
      level: level.level,
      value: (level.holdout || {}).mean_60,
    })).filter((point) => finite(point.value));
    if (!points.length) {
      host.innerHTML = '<div class="unavailable">홀드아웃 레벨 관측이 없습니다.</div>';
      return;
    }
    const width = Math.max(240, points.length * 72);
    const height = 132, top = 24, bottom = 36, side = 10;
    const values = points.map((point) => Number(point.value));
    const minimum = Math.min(0, ...values), maximum = Math.max(0, ...values);
    const span = maximum - minimum || 1;
    const plotHeight = height - top - bottom, plotWidth = width - side * 2;
    const y = (value) => top + ((maximum - value) / span) * plotHeight;
    const baseline = y(0), slot = plotWidth / points.length;
    const bars = points.map((point, index) => {
      const value = Number(point.value), valueY = y(value);
      const barTop = Math.min(valueY, baseline), barHeight = Math.max(1, Math.abs(baseline - valueY));
      const barWidth = Math.min(38, slot * .56), x = side + index * slot + (slot - barWidth) / 2;
      const fill = value > 0 ? "#c0392b" : value < 0 ? "#2b62c0" : "#8b867e";
      const labelY = value >= 0 ? Math.max(12, barTop - 5) : Math.min(height - 18, barTop + barHeight + 13);
      return `<g aria-label="레벨 ${esc(point.level)}, 홀드아웃 60일 평균 ${esc(pct(value))}"><rect x="${x}" y="${barTop}" width="${barWidth}" height="${barHeight}" rx="2" fill="${fill}"></rect><text class="level-value" x="${x + barWidth / 2}" y="${labelY}" text-anchor="middle">${esc(pct(value))}</text><text class="level-label" x="${x + barWidth / 2}" y="${height - 5}" text-anchor="middle">${esc(point.level)}</text></g>`;
    }).join("");
    host.innerHTML = `<svg class="level-bar-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="레벨별 홀드아웃 60일 평균 막대 차트"><title>레벨별 홀드아웃 60일 평균</title><line class="level-zero-line" x1="${side}" x2="${width - side}" y1="${baseline}" y2="${baseline}"></line>${bars}</svg>`;
  }

  function currentMarkup(candidate) {
    const current = candidate.current;
    if (!current) return `<div class="research-current-strip"><div class="current-title">현재 상태</div><span class="muted">현재 평가가 없습니다.</span></div>`;
    const indicators = current.indicators || {}, analog = current.analog || {};
    const facts = Object.entries(indicators).map(([key, value]) => {
      const shown = key === "rsi14" ? number(value, 1) : key === "volidx_pct" ? pctUnsigned(value) : pct(value);
      return `<span>${esc(indicatorLabels[key] || key)} <b class="num">${shown}</b></span>`;
    }).join("");
    return `<div class="research-current-strip">
      <div class="current-title">현재 상태 · ${esc(current.date || "—")}</div>
      <div class="current-main"><b>${esc(candidate.name || candidate.id)}</b><span>점수 <b class="num">${number(current.score, 1)}</b></span><span><b class="num">${number(current.level, 0)}/${number(current.max_level, 0)}</b>단계</span><span>노출 <b class="num">${pctUnsigned(current.exposure, 0)}</b></span></div>
      <div class="current-facts">${facts || "지표 없음"}</div>
      <div class="current-analog">과거 같은 단계 n=${number(analog.n, 0)} → 60일 평균 <b class="num ${valueClass(analog.mean_60)}">${pct(analog.mean_60)}</b>, 상승확률 <b class="num">${pctUnsigned(analog.hit_60, 0)}</b></div>
    </div>`;
  }

  function renderDetail(candidate) {
    if (!candidate) {
      $("detail-content").innerHTML = '<div class="research-detail-empty">선택된 규칙이 없습니다.</div>';
      return;
    }
    $("detail-title").textContent = candidate.name || candidate.id;
    $("detail-reason").textContent = candidate.reason || "";
    const cycles = (candidate.cycles || []).map((cycle) => `<tr><td>${esc(cycle.label || cycle.id)}</td><td>${number(cycle.signals, 0)}</td><td>${esc(cycle.first_signal || "—")}</td><td class="${valueClass(cycle.mean_60)}">${pct(cycle.mean_60)}</td><td class="verdict-${esc(cycle.verdict || "none")}">${esc(verdictLabels[cycle.verdict] || cycle.verdict || "신호 없음")}</td></tr>`).join("");
    const levels = candidate.levels || [];
    const levelRows = levels.map((level) => `<tr><td>${number(level.level, 0)}</td><td>${number((level.fit || {}).n, 0)} / ${pct((level.fit || {}).mean_60)}</td><td>${number((level.holdout || {}).n, 0)} / ${pct((level.holdout || {}).mean_60)}</td></tr>`).join("");
    $("detail-content").innerHTML = `<div class="detail-heading"><div><h2>${esc(candidate.name || candidate.id)}</h2><div class="detail-tags"><span class="research-status ${esc(candidate.status || "")}">${esc(statusLabels[candidate.status] || candidate.status)}</span><span class="research-status">${esc(sideLabels[candidate.side] || candidate.side)}</span><span class="research-status">${esc(basketLabels[candidate.basket] || candidate.basket)}</span></div></div><p class="detail-definition">${esc(candidate.definition_text)}</p></div>
      <div class="detail-grid">
        <div class="detail-block"><h3>사이클별 결과</h3><div class="research-table-wrap"><table class="research-table"><thead><tr><th>사이클</th><th>신호 수</th><th>첫 신호</th><th>60일 평균</th><th>판정</th></tr></thead><tbody>${cycles || '<tr><td colspan="5">사이클 결과 없음</td></tr>'}</tbody></table></div></div>
        <div class="detail-block"><h3>레벨별 결과</h3><div class="level-layout"><div class="research-table-wrap"><table class="research-table"><thead><tr><th>레벨</th><th>적합 n / 평균</th><th>홀드아웃 n / 평균</th></tr></thead><tbody>${levelRows || '<tr><td colspan="3">레벨 결과 없음</td></tr>'}</tbody></table></div><div><div class="level-chart" id="level-chart"></div><div class="level-hint">${esc(monotonicHint(levels))}</div></div></div></div>
        ${currentMarkup(candidate)}
      </div>`;
    renderLevelChart(levels);
  }

  function selectCandidate(candidateId) {
    selectedId = candidateId;
    document.querySelectorAll("#leaderboard-body tr[data-candidate]").forEach((row) => row.classList.toggle("selected", decodeURIComponent(row.dataset.candidate || "") === selectedId));
    renderDetail((research.candidates || []).find((candidate) => candidate.id === selectedId));
  }

  function renderForward(payload) {
    if (!payload || payload.status !== "READY") {
      $("forward-content").innerHTML = `<div class="unavailable">${emptyMarkup((payload || {}).message)}</div>`;
      return;
    }
    $("forward-content").innerHTML = (payload.groups || []).map((group, groupIndex) => `<details class="forward-version" ${groupIndex === 0 ? "open" : ""}><summary>규칙 버전 ${esc(String(group.rules_version || "미상").slice(0, 8))}<span>최근 신호 ${esc(group.newest_as_of || "—")}</span></summary>${(group.candidates || []).map((candidate) => {
      const summary = candidate.summary;
      const summaryLine = summary ? [20, 60, 90].map((horizon) => finite(summary[`mean_${horizon}`]) ? `${horizon}일 <b class="${valueClass(summary[`mean_${horizon}`])}">${pct(summary[`mean_${horizon}`])}</b> (n=${number(summary[`n_${horizon}`], 0)})` : `${horizon}일 대기`).join(" · ") : "실현 행이 5개 이상이면 평균을 표시합니다.";
      const candidateRows = candidate.rows || [];
      const rows = candidateRows.map((row) => `<tr><td>${esc(row.as_of || "—")}</td><td>${number(row.level, 0)}</td><td>${pctUnsigned(row.exposure, 0)}</td>${[20, 60, 90].map((horizon) => `<td class="${valueClass(row[`return_${horizon}`])}">${row[`status_${horizon}`] === "실현" ? pct(row[`return_${horizon}`]) : "대기"}</td>`).join("")}</tr>`).join("");
      return `<details class="forward-candidate" ${candidateRows.length > 3 ? "open" : ""}><summary class="forward-candidate-head"><span class="forward-candidate-summary"><span class="forward-candidate-identity"><b>${esc(candidate.name || candidate.candidate_id)}</b><span class="muted">${esc(basketLabels[candidate.basket] || candidate.basket || "—")} · ${number(candidateRows.length, 0)}행</span></span><span class="forward-summary-line">${summaryLine}</span></span></summary><div class="research-table-wrap"><table class="research-table forward-table"><thead><tr><th>as_of (기준 세션)</th><th>단계</th><th>노출</th><th>실현 20일</th><th>실현 60일</th><th>실현 90일</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
    }).join("")}</details>`).join("");
  }

  function renderHistory(history) {
    $("history-content").innerHTML = (history || []).length ? `<div class="history-list">${history.map((item) => `<div class="history-row"><time class="num">${esc(item.date || "—")}</time><span class="history-action">${esc(actionLabels[item.action] || item.action || "—")}</span><span class="history-id">${esc(item.id || "—")}</span><span class="history-reason">${esc(item.reason || "")}</span></div>`).join("")}</div>` : '<div class="unavailable">변경 기록이 없습니다.</div>';
  }

  async function boot() {
    let forward = null;
    try {
      const [researchResponse, forwardResponse] = await Promise.all([fetch("/api/research"), fetch("/api/research/forward")]);
      research = researchResponse.ok ? await researchResponse.json() : { status: "EMPTY", message: emptyMessage, candidates: [] };
      forward = forwardResponse.ok ? await forwardResponse.json() : { status: "EMPTY", message: emptyMessage, groups: [] };
    } catch (_error) {
      research = { status: "EMPTY", message: emptyMessage, candidates: [], history: [] };
      forward = { status: "EMPTY", message: emptyMessage, groups: [] };
    }
    renderMeta(research);
    renderLeaderboard(research);
    renderForward(forward);
    renderHistory(research.history);
    if (research.status !== "READY") {
      $("research-empty").hidden = false;
      $("research-empty").innerHTML = emptyMarkup(research.message);
      renderDetail(null);
      return;
    }
    $("research-empty").hidden = true;
    const first = (research.candidates || [])[0];
    if (first) selectCandidate(first.id);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
