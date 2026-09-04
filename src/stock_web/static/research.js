/* Retained-data rule experiment, candidate leaderboard, and forward-test renderer. */
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
  let lastExperiment = null;

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

  function sampleText(metrics) {
    const base = `n=${number(metrics.n, 0)} (독립 사건 ${number(metrics.independent_events, 0)})`;
    const simultaneous = metrics.simultaneous_n ?? metrics.simultaneous_count;
    const unique = metrics.unique_n ?? metrics.unique_count;
    return finite(simultaneous) && finite(unique)
      ? `${base} · 동시 ${number(simultaneous, 0)} / 고유 ${number(unique, 0)}`
      : base;
  }

  function renderLeaderboard(payload) {
    const candidates = payload.candidates || [];
    $("leaderboard-body").innerHTML = candidates.map((candidate) => {
      const holdout = result(candidate, "holdout"), fit = result(candidate, "fit");
      const compound = candidate.compound_ladder || {};
      const underlyings = compound.status === "matched"
        ? (Array.isArray(compound.underlyings) && compound.underlyings.length ? compound.underlyings : [compound])
        : [];
      // Every underlying of the basket is shown (KR = KOSPI and KOSPI200): the weaker one must not disappear.
      const wealth = underlyings.length
        ? underlyings.map((item) => `${esc(item.underlying || "")} ${multipleText(item.holdout_relative_to_baseline)}`).join("<br>")
        : esc("미계산");
      const wealthTitle = underlyings.length
        ? `${compound.combination_label || ""} · ` + underlyings.map((item) => `${item.underlying || ""} 내 규칙 ${multipleText(item.holdout_final_wealth_multiple)} / 기준선 ${multipleText(item.holdout_baseline_final_wealth_multiple)}`).join(" · ")
        : "일치하는 복리 grid 행 없음";
      const plateau = underlyings.length
        ? underlyings.map((item) => `${esc(item.underlying || "")} ${esc(item.plateau_verdict || "")}`).join("<br>")
        : esc("미계산");
      const sampleTitle = `독립 사건 = 90일 이상 떨어진 신호 묶음 (바스켓 합산) · 명명 사이클 ${number(holdout.cycles_with_signal, 0)}/9 · 사이클 밖 신호 ${number(holdout.signals_outside_cycles, 0)}건`;
      return `<tr data-candidate="${encodeURIComponent(candidate.id || "")}" tabindex="0" role="button" aria-label="${esc(candidate.name || candidate.id)} 상세 보기">
        <td class="rule-cell"><span class="rule-rank">${number(candidate.rank, 0)}</span><span class="rule-name">${esc(candidate.name || candidate.id)}</span><span class="rule-direction">${esc(candidate.direction_hint || "")}</span></td>
        <td title="${esc(sampleTitle)}">${esc(sampleText(holdout))}</td>
        <td><b>${pct(holdout.mean_60)}</b> <span class="${valueClass(holdout.diff_60)}" title="기준 대비">(${pct(holdout.diff_60)})</span></td>
        <td class="wealth-cell" title="${esc(wealthTitle)}">${wealth}</td>
        <td class="plateau-cell">${plateau}</td>
        <td>${esc(sideLabels[candidate.side] || candidate.side || "—")}</td>
        <td>${esc(basketLabels[candidate.basket] || candidate.basket || "—")}</td>
        <td><span class="research-status ${esc(candidate.status || "")}">${esc(statusLabels[candidate.status] || candidate.status || "—")}</span></td>
        <td>${pctUnsigned(holdout.hit_60, 0)}</td><td>${pctUnsigned(holdout.vol_60)}</td><td class="${valueClass(holdout.mdd_60)}">${pct(holdout.mdd_60)}</td>
        <td class="${valueClass(fit.diff_60)}">${pct(fit.diff_60)}</td>
        <td>${candidate.warn_small_sample ? '<span class="sample-warning" title="홀드아웃 표본 15 미만">⚠</span>' : ""}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="13"><div class="unavailable">${emptyMarkup(payload.message)}</div></td></tr>`;
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

  function renderLevelChart(levels, hostId = "level-chart") {
    const host = $(hostId);
    if (!host) return;
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

  function splitSummaryMarkup(candidate, horizon = 60) {
    const rows = ["fit", "holdout"].map((split) => {
      const metrics = result(candidate, split);
      return `<tr><td>${split === "fit" ? "적합" : "홀드아웃"}</td><td>${esc(sampleText(metrics))}</td><td class="${valueClass(metrics[`mean_${horizon}`])}">${pct(metrics[`mean_${horizon}`])}</td><td class="${valueClass(metrics.diff_60)}">${pct(metrics.diff_60)}</td><td>${pctUnsigned(metrics.hit_60, 0)}</td><td>${pctUnsigned(metrics.vol_60)}</td><td class="${valueClass(metrics.mdd_60)}">${pct(metrics.mdd_60)}</td></tr>`;
    }).join("");
    return `<div class="detail-block result-summary-block"><h3>적합 / 홀드아웃 요약 · ${number(horizon, 0)}일 평균 선택</h3><div class="research-table-wrap"><table class="research-table"><thead><tr><th>구간</th><th>n</th><th>${number(horizon, 0)}일 평균</th><th>60일 기준 대비</th><th>상승확률</th><th>변동성</th><th>최대낙폭</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }

  function detailMarkup(candidate, chartId, horizon = 60) {
    const cycles = (candidate.cycles || []).map((cycle) => `<tr><td>${esc(cycle.label || cycle.id)}</td><td>${number(cycle.signals, 0)}</td><td>${esc(cycle.first_signal || "—")}</td><td class="${valueClass(cycle.mean_60)}">${pct(cycle.mean_60)}</td><td class="verdict-${esc(cycle.verdict || "none")}">${esc(verdictLabels[cycle.verdict] || cycle.verdict || "신호 없음")}</td></tr>`).join("");
    const levels = candidate.levels || [];
    const levelRows = levels.map((level) => `<tr><td>${number(level.level, 0)}</td><td>${number((level.fit || {}).n, 0)} / ${pct((level.fit || {}).mean_60)}</td><td>${number((level.holdout || {}).n, 0)} / ${pct((level.holdout || {}).mean_60)}</td></tr>`).join("");
    return `<div class="detail-heading"><div><h2>${esc(candidate.name || candidate.id)}</h2><div class="detail-tags"><span class="research-status ${esc(candidate.status || "")}">${esc(statusLabels[candidate.status] || candidate.status)}</span><span class="research-status">${esc(sideLabels[candidate.side] || candidate.side)}</span><span class="research-status">${esc(basketLabels[candidate.basket] || candidate.basket)}</span></div></div><p class="detail-definition">${esc(candidate.definition_text)}</p></div>
      <div class="detail-grid">
        ${splitSummaryMarkup(candidate, horizon)}
        <div class="detail-block"><h3>사이클별 결과</h3><div class="research-table-wrap"><table class="research-table"><thead><tr><th>사이클</th><th>신호 수</th><th>첫 신호</th><th>60일 평균</th><th>판정</th></tr></thead><tbody>${cycles || '<tr><td colspan="5">사이클 결과 없음</td></tr>'}</tbody></table></div></div>
        <div class="detail-block"><h3>레벨별 결과</h3><div class="level-layout"><div class="research-table-wrap"><table class="research-table"><thead><tr><th>레벨</th><th>적합 n / 평균</th><th>홀드아웃 n / 평균</th></tr></thead><tbody>${levelRows || '<tr><td colspan="3">레벨 결과 없음</td></tr>'}</tbody></table></div><div><div class="level-chart" id="${esc(chartId)}"></div><div class="level-hint">${esc(monotonicHint(levels))}</div></div></div></div>
        ${currentMarkup(candidate)}
      </div>`;
  }

  function renderDetail(candidate) {
    if (!candidate) {
      $("detail-content").innerHTML = '<div class="research-detail-empty">선택된 규칙이 없습니다.</div>';
      return;
    }
    $("detail-title").textContent = candidate.name || candidate.id;
    $("detail-reason").textContent = candidate.reason || "";
    $("detail-content").innerHTML = detailMarkup(candidate, "level-chart", 60);
    renderLevelChart(candidate.levels || [], "level-chart");
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

  function experimentSide() {
    return (document.querySelector('input[name="experiment-side"]:checked') || {}).value || "drawdown";
  }

  function experimentRows() {
    return [...document.querySelectorAll(".experiment-indicator-row")];
  }

  function syncExperimentControls() {
    const type = $("experiment-type").value;
    const needsLadder = type !== "vol_target";
    const needsTarget = type !== "ladder";
    document.querySelector(".experiment-level-field").hidden = !needsLadder;
    document.querySelector(".experiment-target-field").hidden = !needsTarget;
    $("experiment-indicators").hidden = !needsLadder;
    const selected = experimentRows().filter((row) => row.querySelector('input[type="checkbox"]').checked);
    $("experiment-levels").value = String(Math.max(1, selected.length));
    const operator = experimentSide() === "drawdown" ? "≤" : "≥";
    experimentRows().forEach((row) => {
      const checked = row.querySelector('input[type="checkbox"]').checked;
      row.classList.toggle("disabled", !checked);
      row.querySelector(".experiment-operator").textContent = operator;
    });
  }

  function setExperimentSide(side) {
    const input = document.querySelector(`input[name="experiment-side"][value="${side}"]`);
    if (input) input.checked = true;
  }

  function setIndicator(key, checked, value) {
    const row = document.querySelector(`.experiment-indicator-row[data-indicator="${key}"]`);
    if (!row) return;
    row.querySelector('input[type="checkbox"]').checked = checked;
    if (value !== undefined) {
      row.querySelector(".experiment-range").value = String(value);
      row.querySelector(".experiment-number").value = String(value);
    }
  }

  function invalidateExperiment() {
    if (!lastExperiment) return;
    lastExperiment = null;
    $("experiment-register").disabled = true;
    $("experiment-register").title = "다시 평가하세요";
    $("experiment-status").textContent = "조건이 바뀌었습니다. 다시 평가해 주세요.";
  }

  function applyPreset(name) {
    invalidateExperiment();
    experimentRows().forEach((row) => setIndicator(row.dataset.indicator, false));
    setExperimentSide("drawdown");
    $("experiment-basket").value = "KR";
    if (name === "watchlist") {
      $("experiment-type").value = "ladder";
      setIndicator("rsi14", true, 30);
      setIndicator("disp60", true, -10);
      setIndicator("drawdown252", true, -30);
    } else if (name === "drawdown-2") {
      $("experiment-type").value = "ladder";
      setIndicator("drawdown252", true, -20);
      setIndicator("disp60", true, -10);
    } else {
      $("experiment-type").value = "vol_target";
      $("experiment-target-vol").value = "0.15";
    }
    syncExperimentControls();
  }

  function experimentQuery() {
    const side = experimentSide(), type = $("experiment-type").value;
    const params = new URLSearchParams({
      side, basket: $("experiment-basket").value, type,
      target_vol: $("experiment-target-vol").value,
      horizon: $("experiment-horizon").value,
    });
    if (type !== "vol_target") {
      const selected = experimentRows().filter((row) => row.querySelector('input[type="checkbox"]').checked);
      params.set("levels", String(selected.length));
      selected.forEach((row) => {
        const key = row.dataset.indicator;
        const shown = Number(row.querySelector(".experiment-number").value);
        const threshold = key === "rsi14" ? shown : shown / 100;
        params.append("ind", `${key}:${side === "drawdown" ? "<=" : ">="}:${threshold}`);
      });
    }
    return params;
  }

  function renderExperiment(candidate) {
    $("experiment-result").hidden = false;
    $("experiment-result-definition").textContent = candidate.definition_text || "";
    $("experiment-result-content").innerHTML = detailMarkup(
      candidate, "experiment-level-chart", Number(candidate.horizon) || 60,
    );
    renderLevelChart(candidate.levels || [], "experiment-level-chart");
  }

  async function readError(response) {
    try {
      const payload = await response.json();
      return payload.error || `요청 실패 (${response.status})`;
    } catch (_error) {
      return `요청 실패 (${response.status})`;
    }
  }

  async function evaluateExperiment() {
    const button = $("experiment-evaluate");
    button.disabled = true;
    $("experiment-status").textContent = "retained 데이터로 평가 중…";
    try {
      const response = await fetch(`/api/research/experiment?${experimentQuery().toString()}`);
      if (!response.ok) throw new Error(await readError(response));
      lastExperiment = await response.json();
      renderExperiment(lastExperiment);
      $("experiment-session").textContent = `이번 세션 실험 ${number(lastExperiment.experiment_count, 0)}회`;
      const register = $("experiment-register");
      register.disabled = !lastExperiment.can_register;
      register.title = lastExperiment.can_register ? "실험 규칙을 후보로 등록" : "PC에서만";
      $("experiment-status").textContent = lastExperiment.can_register ? "평가 완료 · 아직 저장되지 않았습니다." : "평가 완료 · 후보 등록은 PC에서만 가능합니다.";
    } catch (error) {
      lastExperiment = null;
      $("experiment-register").disabled = true;
      $("experiment-register").title = "먼저 평가하세요";
      $("experiment-status").textContent = error.message || "평가하지 못했습니다.";
    } finally {
      button.disabled = false;
    }
  }

  async function refreshResearch(candidateId) {
    const response = await fetch("/api/research");
    if (!response.ok) throw new Error(await readError(response));
    research = await response.json();
    renderMeta(research);
    renderLeaderboard(research);
    renderHistory(research.history);
    const selected = (research.candidates || []).find((candidate) => candidate.id === candidateId);
    if (selected) selectCandidate(candidateId);
  }

  async function pollForRulesVersion(expectedVersion, candidateId) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const response = await fetch("/api/research");
      if (!response.ok) continue;
      const payload = await response.json();
      if (payload.rules_version === expectedVersion) {
        research = payload;
        renderMeta(research);
        renderLeaderboard(research);
        renderHistory(research.history);
        if ((research.candidates || []).some((candidate) => candidate.id === candidateId)) selectCandidate(candidateId);
        return true;
      }
    }
    return false;
  }

  async function confirmRegistration() {
    const name = $("experiment-name").value.trim();
    const reason = $("experiment-reason").value.trim();
    if (!lastExperiment || !name || !reason) {
      $("experiment-register-dialog").querySelector("form").reportValidity();
      return;
    }
    const button = $("experiment-register-confirm");
    button.disabled = true;
    $("experiment-status").textContent = "후보 등록 및 순위표 재생성 중 · 최대 60초…";
    try {
      const response = await fetch("/api/research/candidates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name, reason, side: lastExperiment.side, basket: lastExperiment.basket,
          definition: lastExperiment.definition,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const saved = await response.json();
      $("experiment-register-dialog").close();
      $("experiment-status").textContent = saved.status === "queued" ? "후보 등록 완료 · 순위표 재생성 대기 중…" : "후보 등록과 순위표 갱신 완료";
      if (saved.status === "queued") {
        const ready = await pollForRulesVersion(saved.rules_version, saved.candidate_id);
        $("experiment-status").textContent = ready ? "순위표 갱신 완료" : "후보는 등록됐습니다. 순위표 갱신 상태를 다시 확인해 주세요.";
      } else {
        await refreshResearch(saved.candidate_id);
      }
      $("experiment-register").disabled = true;
      $("experiment-register").title = "이미 후보로 등록했습니다";
    } catch (error) {
      $("experiment-status").textContent = error.message || "후보를 등록하지 못했습니다.";
    } finally {
      button.disabled = false;
    }
  }

  function bindExperiment() {
    experimentRows().forEach((row) => {
      const range = row.querySelector(".experiment-range");
      const numeric = row.querySelector(".experiment-number");
      range.addEventListener("input", () => { numeric.value = range.value; invalidateExperiment(); });
      numeric.addEventListener("input", () => {
        const minimum = Number(numeric.min), maximum = Number(numeric.max);
        if (Number.isFinite(Number(numeric.value))) range.value = String(Math.min(maximum, Math.max(minimum, Number(numeric.value))));
        invalidateExperiment();
      });
      row.querySelector('input[type="checkbox"]').addEventListener("change", () => { invalidateExperiment(); syncExperimentControls(); });
    });
    document.querySelectorAll('input[name="experiment-side"]').forEach((input) => input.addEventListener("change", () => { invalidateExperiment(); syncExperimentControls(); }));
    $("experiment-type").addEventListener("change", () => { invalidateExperiment(); syncExperimentControls(); });
    for (const id of ["experiment-basket", "experiment-target-vol", "experiment-horizon"]) {
      $(id).addEventListener("change", invalidateExperiment);
    }
    document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => applyPreset(button.dataset.preset)));
    $("experiment-evaluate").addEventListener("click", evaluateExperiment);
    $("experiment-register").addEventListener("click", () => {
      if (!lastExperiment || !lastExperiment.can_register) return;
      $("experiment-name").value = "";
      $("experiment-reason").value = "";
      $("experiment-register-dialog").showModal();
    });
    $("experiment-register-confirm").addEventListener("click", confirmRegistration);
    syncExperimentControls();
  }

  const holdoutViewState = { persistent: 0, session: 0 };

  function updateHoldoutCounters(payload = {}) {
    if (finite(payload.persistent_views)) holdoutViewState.persistent = Math.max(holdoutViewState.persistent, Number(payload.persistent_views));
    if (finite(payload.session_views)) holdoutViewState.session = Number(payload.session_views);
    const text = `홀드아웃 열람 ${number(holdoutViewState.persistent, 0)}회 (이 세션 ${number(holdoutViewState.session, 0)}회)`;
    if ($("crisis-view-count")) $("crisis-view-count").textContent = text;
    if ($("compound-view-count")) $("compound-view-count").textContent = text;
  }

  const crisisState = { payload: null, revealed: false, preset: "tlt", basis: "hold_start" };
  const crisisPalette = ["#315f8a", "#8a6a2f", "#4d7c59", "#76558a", "#2b7a78", "#9a5b45", "#5d6d7e", "#8f7b52", "#41729f", "#6f8f72", "#695b8f", "#3c7f86", "#936b55", "#58718a"];

  function crisisMode() {
    return (document.querySelector('input[name="crisis-mode"]:checked') || {}).value || "asset";
  }

  function crisisBasis() {
    return ($("crisis-basis") || {}).value || crisisState.basis;
  }

  function crisisBasisLabel() {
    const basis = crisisBasis();
    const row = (((crisisState.payload || {}).normalisations) || []).find((item) => item.id === basis);
    return (row || {}).label || (basis === "signal" ? "신호일 = 100" : "보유시작 = 100");
  }

  function crisisMedian(paths) {
    if (!paths.length) return [];
    return paths[0].map((_value, index) => {
      const values = paths.map((path) => path[index]).filter(finite).map(Number);
      if (!values.length) return null;
      values.sort((a, b) => a - b);
      const middle = Math.floor(values.length / 2);
      return values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2;
    });
  }

  function crisisPath(values, x, y, step = false) {
    let open = false;
    let prior = null;
    return (values || []).map((value, index) => {
      if (!finite(value)) { open = false; prior = null; return ""; }
      const command = open ? "L" : "M";
      const segment = step && open
        ? `L${x(index).toFixed(2)} ${y(Number(prior)).toFixed(2)} L${x(index).toFixed(2)} ${y(Number(value)).toFixed(2)}`
        : `${command}${x(index).toFixed(2)} ${y(Number(value)).toFixed(2)}`;
      open = true;
      prior = value;
      return segment;
    }).filter(Boolean).join(" ");
  }

  function crisisLineSet() {
    const payload = crisisState.payload || {};
    const episodes = payload.episodes || [];
    const visible = episodes.filter((episode) => crisisState.revealed || !episode.is_holdout);
    const hidden = episodes.filter((episode) => !crisisState.revealed && episode.is_holdout).map((episode) => `${episode.label} · 숨김`);
    const specs = [];
    if (crisisState.preset === "ladder") {
      const item = (((((payload.ladder || {})[crisisBasis()] || {}).KR || {}).KOSPI)) || {};
      const signalDates = item.signal_dates || {};
      const cycles = Object.keys(signalDates);
      const shown = cycles.filter((cycle) => crisisState.revealed || String(signalDates[cycle]) < "2016-01-01");
      const gated = cycles.filter((cycle) => !crisisState.revealed && String(signalDates[cycle]) >= "2016-01-01");
      const paths = shown.map((cycle) => item[cycle]).filter(Array.isArray);
      const worst = shown.reduce((winner, cycle) => {
        const values = (item[cycle] || []).slice(60).filter(finite).map(Number);
        const score = values.length ? Math.min(...values) : Infinity;
        return !winner || score < winner.score ? { cycle, score } : winner;
      }, null);
      shown.forEach((cycle, index) => specs.push({
        label: cycle === (worst || {}).cycle ? `WORST · ${cycle}` : cycle,
        values: item[cycle] || [], dates: (item.dates || {})[cycle] || [],
        color: cycle === (worst || {}).cycle ? "#b3342b" : crisisPalette[index % crisisPalette.length],
        width: cycle === (worst || {}).cycle ? 3 : 1.15,
      }));
      shown.forEach((cycle, index) => specs.push({
        label: `${cycle} · LEVEL`, values: (item.levels || {})[cycle] || [],
        dates: (item.dates || {})[cycle] || [], color: crisisPalette[index % crisisPalette.length],
        width: .85, dash: "2 3", axis: "right", unit: "단계", step: true,
      }));
      if (paths.length) specs.push({ label: "중앙 경로", values: crisisMedian(paths), dates: [], color: "#171612", width: 3.2 });
      return { specs, hidden: gated.map((cycle) => `${cycle} · 숨김`), rightAxis: true, rightDomain: [0, 2], rightUnit: "단계", title: "KOSPI 낙폭 사다리" };
    }
    if (crisisState.preset === "equity-yield") {
      visible.forEach((episode, index) => {
        const color = crisisPalette[index % crisisPalette.length];
        specs.push({ label: `${episode.label} · 주식`, values: ((((payload.series || {})[crisisBasis()] || {})[episode.id] || {}).equity_reference) || [], dates: (payload.dates || {})[episode.id] || [], color, width: 1.35 });
        specs.push({ label: `${episode.label} · 10Y`, values: (payload.yields || {})[episode.id] || [], dates: (payload.dates || {})[episode.id] || [], color, width: 1.1, dash: "5 3", axis: "right", unit: "%" });
      });
      return { specs, hidden, rightAxis: true, title: "주식 100 기준 / 10년 금리(우축)" };
    }
    if (crisisMode() === "episode") {
      const episode = episodes.find((item) => item.id === $("crisis-episode").value) || episodes[0];
      if (!episode || (episode.is_holdout && !crisisState.revealed)) {
        return { specs, hidden: episode ? [`${episode.label} · 숨김`] : hidden, rightAxis: false, title: "한 위기 × 여러 자산" };
      }
      (payload.assets || []).forEach((asset, index) => {
        const equity = asset.id === "equity_reference";
        specs.push({
          label: asset.label, values: ((((payload.series || {})[crisisBasis()] || {})[episode.id] || {})[asset.id]) || [],
          dates: (payload.dates || {})[episode.id] || [],
          color: equity ? "#171612" : crisisPalette[index % crisisPalette.length], width: equity ? 3.2 : 1.15,
        });
      });
      return { specs, hidden, rightAxis: false, title: `${episode.label} · 여러 자산` };
    }
    const assetId = $("crisis-asset").value;
    const asset = (payload.assets || []).find((item) => item.id === assetId) || {};
    const candidates = visible.map((episode) => ({
      episode, path: (((((payload.series || {})[crisisBasis()] || {})[episode.id] || {})[assetId])) || [],
      mark: ((((payload.signal_values || {})[episode.id]) || {})[assetId]),
    })).filter((item) => item.path.some(finite));
    const worst = candidates.filter((item) => finite(item.mark)).reduce((winner, item) => !winner || Number(item.mark) < Number(winner.mark) ? item : winner, null);
    candidates.forEach((item, index) => {
      const isWorst = worst && item.episode.id === worst.episode.id;
      specs.push({
        label: isWorst ? `WORST@T ${Number(item.mark).toFixed(1)} · ${item.episode.label}` : item.episode.label,
        values: item.path, dates: (payload.dates || {})[item.episode.id] || [],
        color: isWorst ? "#b3342b" : crisisPalette[index % crisisPalette.length], width: isWorst ? 3 : 1.15,
      });
    });
    if (candidates.length) specs.push({ label: "중앙 경로", values: crisisMedian(candidates.map((item) => item.path)), dates: [], color: "#171612", width: 3.2 });
    return { specs, hidden, rightAxis: false, title: asset.label || assetId };
  }

  function renderCrisisChart(lineSet) {
    const host = $("crisis-chart"), legend = $("crisis-legend");
    const specs = lineSet.specs.filter((spec) => (spec.values || []).some(finite));
    legend.innerHTML = specs.map((spec) => `<div class="crisis-legend-row" style="color:${esc(spec.color)}"><span class="crisis-legend-swatch"></span><span>${esc(spec.label)}</span></div>`).join("")
      + lineSet.hidden.map((label) => `<div class="crisis-legend-row hidden"><span class="crisis-legend-swatch"></span><span>${esc(label)}</span></div>`).join("");
    if (!specs.length) {
      host.innerHTML = '<div class="unavailable">표시할 경로가 없습니다. 홀드아웃 위기는 먼저 열람 기록을 남겨야 합니다.</div>';
      return;
    }
    const width = 1000, height = 390, left = 74, right = lineSet.rightAxis ? 64 : 22, top = 24, bottom = 38;
    const plotW = width - left - right, plotH = height - top - bottom;
    const start = Number((crisisState.payload || {}).offset_start ?? -60);
    const end = Number((crisisState.payload || {}).offset_end ?? 250);
    const xOffset = (offset) => left + (offset - start) / (end - start) * plotW;
    const xIndex = (index) => xOffset(start + index);
    const primaryValues = specs.filter((spec) => spec.axis !== "right").flatMap((spec) => spec.values.filter(finite).map(Number));
    const rightValues = specs.filter((spec) => spec.axis === "right").flatMap((spec) => spec.values.filter(finite).map(Number));
    primaryValues.push(100);
    const pMin = Math.min(...primaryValues), pMax = Math.max(...primaryValues), pPad = (pMax - pMin) * .07 || 2;
    const yMin = pMin - pPad, yMax = pMax + pPad;
    const y = (value) => top + (yMax - value) / (yMax - yMin) * plotH;
    const rMin0 = lineSet.rightDomain ? Number(lineSet.rightDomain[0]) : (rightValues.length ? Math.min(...rightValues) : 0);
    const rMax0 = lineSet.rightDomain ? Number(lineSet.rightDomain[1]) : (rightValues.length ? Math.max(...rightValues) : 1);
    const rPad = lineSet.rightDomain ? 0 : ((rMax0 - rMin0) * .07 || .2), rMin = rMin0 - rPad, rMax = rMax0 + rPad;
    const ry = (value) => top + (rMax - value) / (rMax - rMin) * plotH;
    const yTicks = Array.from({ length: 5 }, (_, index) => yMin + (yMax - yMin) * index / 4);
    const rTicks = Array.from({ length: 5 }, (_, index) => rMin + (rMax - rMin) * index / 4);
    const xTicks = [-60, 0, 20, 60, 120, 250];
    host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(lineSet.title)} 위기 신호 정렬 차트"><title>${esc(lineSet.title)} · x=0 첫 level-2 신호</title>
      <rect class="crisis-check-band" x="${xOffset(19)}" y="${top}" width="${Math.max(2, xOffset(21) - xOffset(19))}" height="${plotH}"></rect>
      <rect class="crisis-check-band" x="${xOffset(59)}" y="${top}" width="${Math.max(2, xOffset(61) - xOffset(59))}" height="${plotH}"></rect>
      ${yTicks.map((value) => `<line class="crisis-grid" x1="${left}" x2="${width - right}" y1="${y(value)}" y2="${y(value)}"></line><text class="crisis-axis-label" x="${left - 7}" y="${y(value) + 3}" text-anchor="end">${value.toFixed(0)}</text>`).join("")}
      <line class="crisis-base-line" x1="${left}" x2="${width - right}" y1="${y(100)}" y2="${y(100)}"></line>
      <line class="crisis-signal-line" x1="${xOffset(0)}" x2="${xOffset(0)}" y1="${top}" y2="${height - bottom}"></line>
      <line class="crisis-axis" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}"></line><line class="crisis-axis" x1="${left}" x2="${width - right}" y1="${height - bottom}" y2="${height - bottom}"></line>
      <text class="crisis-axis-label crisis-y-title" x="${-(top + plotH / 2)}" y="14" transform="rotate(-90)" text-anchor="middle">${esc(lineSet.yAxisLabel)}</text>
      ${xTicks.map((value) => `<text class="crisis-axis-label" x="${xOffset(value)}" y="${height - 12}" text-anchor="middle">${value > 0 ? "+" : ""}${value}</text>`).join("")}
      <text class="crisis-axis-label" x="${xOffset(20)}" y="${top + 11}" text-anchor="middle">+20</text><text class="crisis-axis-label" x="${xOffset(60)}" y="${top + 11}" text-anchor="middle">+60</text>
      ${lineSet.rightAxis ? `<line class="crisis-axis" x1="${width - right}" x2="${width - right}" y1="${top}" y2="${height - bottom}"></line>${rTicks.map((value) => `<text class="crisis-axis-label" x="${width - right + 7}" y="${ry(value) + 3}" text-anchor="start">${value.toFixed(lineSet.rightDomain ? 1 : 1)}${esc(lineSet.rightUnit || "%")}</text>`).join("")}` : ""}
      ${specs.map((spec) => `<path class="crisis-series-line" d="${crisisPath(spec.values, xIndex, spec.axis === "right" ? ry : y, spec.step)}" style="stroke:${esc(spec.color)};stroke-width:${Number(spec.width || 1.2)};${spec.dash ? `stroke-dasharray:${esc(spec.dash)};` : ""}"></path>`).join("")}
      <line class="crisis-hover-line" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}" visibility="hidden"></line><rect class="crisis-hitbox" x="${left}" y="${top}" width="${plotW}" height="${plotH}"></rect>
    </svg><div class="crisis-tooltip" hidden></div>`;
    const svg = host.querySelector("svg"), hitbox = svg.querySelector(".crisis-hitbox"), hover = svg.querySelector(".crisis-hover-line"), tooltip = host.querySelector(".crisis-tooltip");
    hitbox.addEventListener("pointermove", (event) => {
      const rect = svg.getBoundingClientRect();
      const svgX = (event.clientX - rect.left) / rect.width * width;
      const offset = Math.max(start, Math.min(end, Math.round(start + (svgX - left) / plotW * (end - start))));
      const index = offset - start, px = xOffset(offset);
      const rows = specs.map((spec) => ({ spec, value: spec.values[index], date: (spec.dates || [])[index] })).filter((row) => finite(row.value));
      hover.setAttribute("x1", px); hover.setAttribute("x2", px); hover.setAttribute("visibility", "visible");
      tooltip.innerHTML = `<b>${offset > 0 ? "+" : ""}${offset} 세션</b>${rows.map((row) => `<span class="crisis-tooltip-row" style="color:${esc(row.spec.color)}"><i class="crisis-tooltip-dot"></i><span>${esc(row.spec.label)} <em>${esc(row.date || "중앙")}</em></span><strong>${Number(row.value).toFixed(2)}${row.spec.unit || ""}</strong></span>`).join("")}`;
      tooltip.hidden = false;
      tooltip.style.left = `${Math.min(Math.max(8, event.clientX - rect.left + 12), Math.max(8, host.clientWidth - 370))}px`;
      tooltip.style.top = `${Math.min(Math.max(8, event.clientY - rect.top + 12), Math.max(8, host.clientHeight - 180))}px`;
    });
    hitbox.addEventListener("pointerleave", () => { hover.setAttribute("visibility", "hidden"); tooltip.hidden = true; });
  }

  function renderCrisis() {
    if (!crisisState.payload) return;
    const mode = crisisMode();
    $("crisis-asset-field").hidden = mode !== "asset" || crisisState.preset === "ladder";
    $("crisis-episode-field").hidden = mode !== "episode" || crisisState.preset === "ladder" || crisisState.preset === "equity-yield";
    document.querySelectorAll("[data-crisis-preset]").forEach((button) => button.classList.toggle("active", button.dataset.crisisPreset === crisisState.preset));
    const lineSet = crisisLineSet();
    const basisLabel = crisisBasisLabel();
    lineSet.title = `${lineSet.title} · ${basisLabel}`;
    lineSet.yAxisLabel = basisLabel;
    $("crisis-status").textContent = `${lineSet.title} · −60~+250 세션`;
    $("crisis-basis-caption").textContent = crisisBasis() === "hold_start"
      ? "보유시작 = 100 · 핵심 실탄 표의 valuation 열(T−60 또는 마지막 level-0일부터)과 일치합니다."
      : "신호일 = 100 · 기존 위기 겹쳐보기의 신호일 재기준화 값과 일치합니다.";
    renderCrisisChart(lineSet);
  }

  function setCrisisPreset(name) {
    crisisState.preset = name;
    const assetMode = document.querySelector('input[name="crisis-mode"][value="asset"]');
    if (assetMode) assetMode.checked = true;
    if (name === "tlt") $("crisis-asset").value = "tlt";
    if (name === "reit") $("crisis-asset").value = "reit_vnq";
    if (name === "equity-yield") $("crisis-asset").value = "equity_reference";
    renderCrisis();
  }

  async function revealCrisisHoldout() {
    const button = $("crisis-holdout");
    button.disabled = true;
    try {
      const response = await fetch("/api/research/compound/holdout-view", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "crisis_overlay", mode: crisisMode(), asset: $("crisis-asset").value, episode: $("crisis-episode").value, preset: crisisState.preset }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json();
      crisisState.revealed = true;
      updateHoldoutCounters(payload);
      button.textContent = "홀드아웃 표시 중";
      renderCrisis();
    } catch (error) {
      button.disabled = false;
      $("crisis-status").textContent = error.message || "홀드아웃 열람을 기록하지 못했습니다.";
    }
  }

  async function initCrisisOverlay() {
    if (!$("crisis-overlay")) return;
    $("crisis-command").textContent = ".venv\\Scripts\\python.exe scripts/research/run_crisis_overlay.py --project-root .";
    $("crisis-basis").addEventListener("change", () => { crisisState.basis = crisisBasis(); renderCrisis(); });
    document.querySelectorAll('[name="crisis-mode"]').forEach((input) => input.addEventListener("change", () => { crisisState.preset = ""; renderCrisis(); }));
    $("crisis-asset").addEventListener("change", () => { crisisState.preset = ""; renderCrisis(); });
    $("crisis-episode").addEventListener("change", () => { crisisState.preset = ""; renderCrisis(); });
    document.querySelectorAll("[data-crisis-preset]").forEach((button) => button.addEventListener("click", () => setCrisisPreset(button.dataset.crisisPreset)));
    $("crisis-holdout").addEventListener("click", revealCrisisHoldout);
    try {
      const response = await fetch("/api/research/crisis-overlay");
      if (!response.ok) throw new Error(await readError(response));
      crisisState.payload = await response.json();
      $("crisis-asset").innerHTML = (crisisState.payload.assets || []).map((asset) => `<option value="${esc(asset.id)}">${esc(asset.label)}</option>`).join("");
      $("crisis-episode").innerHTML = (crisisState.payload.episodes || []).map((episode) => `<option value="${esc(episode.id)}">${esc(episode.label)}${episode.is_holdout ? " · 홀드아웃" : ""}</option>`).join("");
      updateHoldoutCounters({ persistent_views: crisisState.payload.holdout_views || 0 });
      if ((crisisState.payload.assets || []).some((asset) => asset.id === "tlt")) $("crisis-asset").value = "tlt";
      setCrisisPreset("tlt");
    } catch (error) {
      $("crisis-status").textContent = error.message || "미계산";
      $("crisis-chart").innerHTML = `<div class="unavailable">${emptyMarkup(error.message || "미계산")}</div>`;
    }
  }

  const compoundState = {
    catalog: [], cache: new Map(), payload: null, frame: 0,
    holdoutVisible: false, sessionViews: 0, pollTimer: 0, wasRunning: false,
  };
  const compoundDefaults = {
    drawdown_threshold: [-.10, -.15, -.20, -.25, -.30, -.35],
    disp60_threshold: [-.05, -.10, -.15], levels: [1, 2, 3, 4],
    leverage_multiple: [1, 2, 3], base_exposure: [0, 1],
    exit: ["a", "b60", "b120", "c", "d"], cost_enabled: [false, true],
  };
  let compoundToastTimer = 0;

  function compoundToast(message, error = false) {
    const host = $("compound-toast");
    if (!host) return;
    clearTimeout(compoundToastTimer);
    host.textContent = message;
    host.classList.toggle("error", error);
    host.hidden = false;
    compoundToastTimer = setTimeout(() => { host.hidden = true; }, 4200);
  }

  function compoundEntry() {
    const value = $("compound-basket").value;
    return compoundState.catalog.find((item) => `${item.basket}|${item.product}` === value) || null;
  }

  function cachedList(name) {
    return (((compoundState.payload || {}).cached_values || {})[name]) || [];
  }

  function sliderValue(id, values) {
    return values[Number($(id).value)] ?? values[0];
  }

  function compoundCombination() {
    const entry = compoundEntry();
    if (!entry) return null;
    return {
      basket: entry.basket, product: entry.product,
      drawdown_threshold: sliderValue("compound-drawdown", cachedList("drawdown_thresholds")),
      disp60_threshold: sliderValue("compound-disp60", cachedList("disp60_thresholds")),
      levels: Number($("compound-levels").value),
      leverage_multiple: Number($("compound-multiple").value),
      exit: $("compound-exit").value, cost_enabled: $("compound-cost").checked,
      product_variant: $("compound-product").value,
    };
  }

  function compoundMetric(row, variant, split) {
    if (!row) return null;
    if (variant === "actual_adjusted") return ((row.actual_product_basis || {})[split]) || null;
    return row[split] || null;
  }

  function compoundRow(combination) {
    if (!combination) return null;
    const rows = (compoundState.payload || {}).rows || [];
    const row = rows.find((item) => item.base_exposure === 1
      && Number(item.drawdown_threshold) === Number(combination.drawdown_threshold)
      && Number(item.disp60_threshold) === Number(combination.disp60_threshold)
      && Number(item.levels) === combination.levels
      && Number(item.leverage_multiple) === combination.leverage_multiple
      && item.exit === combination.exit
      && Boolean(item.cost_enabled) === combination.cost_enabled) || null;
    return combination.product_variant === "actual_adjusted" && !(row || {}).actual_product_basis ? null : row;
  }

  function multipleText(value, digits = 2) {
    return finite(value) ? `${Number(value).toFixed(digits)}배` : "—";
  }

  function compoundProductLabel(variant) {
    return ({ index_1x: "지수 1x", synthetic_2x: "합성 2x", synthetic_3x: "합성 3x", actual_adjusted: "실제 상품 보정" })[variant] || variant;
  }

  function renderCompoundCurve(row, fit, variant) {
    const host = $("compound-equity-chart");
    const baseline = (compoundState.payload || {}).baseline || {};
    const finish = fit && fit.end ? String(fit.end) : "9999-12-31";
    const series = (items) => (items || []).filter((point) => point.date <= finish).map((point) => ({ t: point.date, v: point.wealth }));
    const mine = variant === "actual_adjusted" ? [] : series(row && row.equity_curve_weekly);
    const base = series(baseline.equity_curve_weekly);
    if (mine.length < 2 || base.length < 2 || !window.SIChart) {
      host.innerHTML = '<div class="unavailable">이 조합은 이름 붙은 자산 곡선이 캐시에 없습니다.</div>';
      return;
    }
    window.SIChart.renderLineChart(host, [], {
      height: 220, ariaLabel: "FIT 기간 내 규칙과 기준선 자산 곡선",
      series: [
        { key: "mine", label: "내 규칙", color: "#c07a20", points: mine },
        { key: "baseline", label: "기준선", color: "#2b62c0", points: base },
      ],
      axisFormatter: (value) => `${Number(value).toFixed(1)}x`,
      valueFormatter: (value) => `${Number(value).toFixed(2)}배`,
    });
  }

  function renderCompoundCycles(row, variant) {
    const cycles = variant === "actual_adjusted" ? [] : ((row && row.cycles) || []);
    $("compound-cycle-body").innerHTML = cycles.length ? cycles.map((cycle) => `<tr><td>${esc(cycle.entry_date || "—")}</td><td>${number(cycle.max_level_reached, 0)}</td><td>${esc(cycle.exit_date || "open")}</td><td class="${valueClass(cycle.contribution_to_wealth)}">${pct(cycle.contribution_to_wealth)}</td></tr>`).join("") : '<tr><td colspan="4"><div class="unavailable">이 조합의 사이클 상세는 캐시에 없습니다.</div></td></tr>';
  }

  function heatMetric(row, variant) {
    return compoundMetric(row, variant, "fit");
  }

  function renderCompoundHeatmap(combination) {
    const mode = $("compound-heatmap-axes").value;
    const definitions = {
      threshold_x_levels: { x: "drawdown_threshold", y: "levels", fixed: { disp60_threshold: -.10, leverage_multiple: 2 } },
      levels_x_multiple: { x: "levels", y: "leverage_multiple", fixed: { drawdown_threshold: -.20, disp60_threshold: -.10 } },
      threshold_x_multiple: { x: "drawdown_threshold", y: "leverage_multiple", fixed: { disp60_threshold: -.10, levels: 2 } },
    };
    const definition = definitions[mode];
    const rows = ((compoundState.payload || {}).rows || []).filter((row) => row.base_exposure === 1 && row.exit === "a" && row.cost_enabled === true
      && Object.entries(definition.fixed).every(([key, value]) => Number(row[key]) === Number(value))
      && finite((heatMetric(row, combination.product_variant) || {}).relative_to_baseline));
    const host = $("compound-heatmap");
    if (!rows.length) {
      host.innerHTML = '<div class="unavailable">미계산 조합 · 이 surface가 캐시에 없습니다.</div>';
      $("compound-plateau-verdict").textContent = "판정할 이웃 셀이 없습니다.";
      return;
    }
    const xs = [...new Set(rows.map((row) => row[definition.x]))].sort((a, b) => Number(a) - Number(b));
    const ys = [...new Set(rows.map((row) => row[definition.y]))].sort((a, b) => Number(a) - Number(b));
    const best = rows.reduce((winner, row) => Number(heatMetric(row, combination.product_variant).relative_to_baseline) > Number(heatMetric(winner, combination.product_variant).relative_to_baseline) ? row : winner, rows[0]);
    const values = rows.map((row) => Number(heatMetric(row, combination.product_variant).relative_to_baseline));
    const low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
    const cellW = 86, cellH = 46, left = 70, top = 22;
    const width = left + xs.length * cellW + 6, height = top + ys.length * cellH + 30;
    const label = (key, value) => key === "drawdown_threshold" ? `${Math.round(Number(value) * 100)}%` : String(value);
    const cells = rows.map((row) => {
      const xi = xs.indexOf(row[definition.x]), yi = ys.indexOf(row[definition.y]);
      const value = Number(heatMetric(row, combination.product_variant).relative_to_baseline);
      const ratio = (value - low) / span;
      const fill = `hsl(${35 + ratio * 85} 48% ${88 - ratio * 24}%)`;
      const current = Number(row[definition.x]) === Number(combination[definition.x]) && Number(row[definition.y]) === Number(combination[definition.y]);
      const isBest = row === best;
      return `<g><rect x="${left + xi * cellW}" y="${top + yi * cellH}" width="${cellW - 3}" height="${cellH - 3}" rx="3" fill="${fill}"></rect>${current ? `<rect class="heat-current" x="${left + xi * cellW + 1.5}" y="${top + yi * cellH + 1.5}" width="${cellW - 6}" height="${cellH - 6}" rx="3"></rect>` : ""}<text class="heat-value" x="${left + xi * cellW + (cellW - 3) / 2}" y="${top + yi * cellH + 27}" text-anchor="middle">${value.toFixed(2)}x</text>${isBest ? `<text class="heat-best" x="${left + xi * cellW + cellW - 14}" y="${top + yi * cellH + 15}" text-anchor="middle">★</text>` : ""}</g>`;
    }).join("");
    host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="FIT 기준선 대비 고원 지도"><title>별은 최적 셀, 굵은 테두리는 현재 조합</title>${xs.map((value, index) => `<text x="${left + index * cellW + (cellW - 3) / 2}" y="${height - 8}" text-anchor="middle">${esc(label(definition.x, value))}</text>`).join("")}${ys.map((value, index) => `<text x="${left - 8}" y="${top + index * cellH + 27}" text-anchor="end">${esc(label(definition.y, value))}</text>`).join("")}${cells}</svg>`;
    const bestXi = xs.indexOf(best[definition.x]), bestYi = ys.indexOf(best[definition.y]);
    const neighbours = rows.filter((row) => row !== best
      && Math.abs(xs.indexOf(row[definition.x]) - bestXi) <= 1
      && Math.abs(ys.indexOf(row[definition.y]) - bestYi) <= 1);
    const bestValue = Number(heatMetric(best, combination.product_variant).relative_to_baseline);
    const neighbourMean = neighbours.length ? neighbours.reduce((sum, row) => sum + Number(heatMetric(row, combination.product_variant).relative_to_baseline), 0) / neighbours.length : NaN;
    const sharp = bestValue > 1 && Number.isFinite(neighbourMean) && bestValue - neighbourMean > .25 * (bestValue - 1);
    $("compound-plateau-verdict").textContent = `${sharp ? "뾰족한 봉우리" : "넓은 고원"} · 최적 ${bestValue.toFixed(2)}x${Number.isFinite(neighbourMean) ? ` / 이웃 평균 ${neighbourMean.toFixed(2)}x` : " / 이웃 없음"}`;
  }

  function renderCompound() {
    compoundState.frame = 0;
    const combination = compoundCombination();
    if (!combination) return;
    $("compound-drawdown-value").textContent = finite(combination.drawdown_threshold) ? pct(combination.drawdown_threshold, 0, false) : "—";
    $("compound-disp60-value").textContent = finite(combination.disp60_threshold) ? pct(combination.disp60_threshold, 0, false) : "—";
    const row = compoundRow(combination);
    if (!row) {
      $("compound-headline").innerHTML = "<span>FIT · ~2015</span><b>미계산 조합</b>";
      $("compound-fit-metrics").innerHTML = '<div class="unavailable">선택한 모든 값이 일치하는 cached grid row가 없습니다.</div>';
      $("compound-holdout-output").hidden = true;
      $("compound-knob-note").textContent = "미계산 조합";
      renderCompoundExitCompare(combination);
      renderCompoundCurve(null, null, combination.product_variant);
      renderCompoundCycles(null, combination.product_variant);
      renderCompoundHeatmap(combination);
      return;
    }
    const fit = compoundMetric(row, combination.product_variant, "fit");
    const relative = Number(fit.relative_to_baseline);
    $("compound-headline").innerHTML = `<span>FIT · ${esc(fit.start || "—")}~${esc(fit.end || "2015")}</span><b>기준선 ${multipleText(fit.baseline_final_wealth_multiple)} · 내 규칙 ${multipleText(fit.final_wealth_multiple)} · ${pct(relative - 1, 0)}</b>`;
    $("compound-fit-metrics").innerHTML = `<div class="compound-metric"><span>최종 금액 / 기준선</span><b>${multipleText(relative)}</b></div><div class="compound-metric"><span>CAGR</span><b>${pct(fit.cagr)}</b></div><div class="compound-metric"><span>최대낙폭</span><b>${pct(fit.max_drawdown)}</b></div>`;
    $("compound-knob-note").textContent = `${row.underlying || combination.product} · ${compoundProductLabel(combination.product_variant)} · ${combination.cost_enabled ? "거래비용 포함" : "거래비용 제외"} · cached row`;
    if (compoundState.holdoutVisible) renderCompoundHoldout(row, combination);
    else $("compound-holdout-output").hidden = true;
    renderCompoundExitCompare(combination);
    renderCompoundCurve(row, fit, combination.product_variant);
    renderCompoundCycles(row, combination.product_variant);
    renderCompoundHeatmap(combination);
  }

  function scheduleCompoundRender(resetHoldout = true) {
    if (resetHoldout) compoundState.holdoutVisible = false;
    if (compoundState.frame) return;
    // requestAnimationFrame never fires in a hidden/background tab, which left the panel
    // stuck on "그리드를 불러오는 중…"; fall back to a macrotask there.
    if (document.hidden || typeof requestAnimationFrame !== "function") {
      compoundState.frame = -1;
      setTimeout(renderCompound, 0);
      return;
    }
    compoundState.frame = requestAnimationFrame(renderCompound);
  }

  const compoundExitLabels = { a: "a 점수 역주행", b60: "b60 시간 분할", b120: "b120 시간 분할", c: "c 목표수익 분할", d: "d 안 팔기" };

  // 05:05 review: final multiple ranked alone always crowns "안 팔기"; the MDD trade-off
  // only shows when every exit sits on its own line with both numbers side by side.
  function renderCompoundExitCompare(combination) {
    const host = $("compound-exit-compare");
    if (!host) return;
    const exits = ["a", "b60", "b120", "c", "d"];
    const showHoldout = compoundState.holdoutVisible;
    const rows = exits.map((exit) => {
      const row = compoundRow({ ...combination, exit });
      const fit = compoundMetric(row, combination.product_variant, "fit");
      const holdout = showHoldout ? compoundMetric(row, combination.product_variant, "holdout") : null;
      const current = exit === combination.exit;
      const cell = (metric) => metric
        ? `<td>${multipleText(metric.relative_to_baseline)}</td><td class="${valueClass(metric.max_drawdown)}">${pct(metric.max_drawdown)}</td>`
        : "<td>미계산</td><td>—</td>";
      return `<tr class="${current ? "current" : ""}"><td>${esc(compoundExitLabels[exit] || exit)}${current ? ' <span class="muted">(선택)</span>' : ""}</td>${cell(fit)}${showHoldout ? cell(holdout) : ""}</tr>`;
    }).join("");
    host.innerHTML = `<div class="compound-subhead"><b>출구 5개 나란히 · 최종배수와 최대낙폭을 같은 줄에</b><span class="muted">같은 신호·배율·비용, 출구만 다름${showHoldout ? "" : " · 홀드아웃 열은 '홀드아웃 보기' 뒤에"}</span></div><div class="research-table-wrap"><table class="research-table compound-exit-table"><thead><tr><th>출구</th><th>FIT 최종/기준선</th><th>FIT MDD</th>${showHoldout ? "<th>홀드아웃 최종/기준선</th><th>홀드아웃 MDD</th>" : ""}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderCompoundHoldout(row, combination) {
    const metric = compoundMetric(row, combination.product_variant, "holdout");
    const full = compoundMetric(row, combination.product_variant, "full");
    const host = $("compound-holdout-output");
    if (!metric) { host.hidden = true; return; }
    host.hidden = false;
    host.innerHTML = `<h3>홀드아웃 · ${esc(metric.start || "2016")}~</h3><p>기준선 ${multipleText(metric.baseline_final_wealth_multiple)} · 내 규칙 ${multipleText(metric.final_wealth_multiple)} · ${pct(Number(metric.relative_to_baseline) - 1, 0)} · CAGR ${pct(metric.cagr)} · MDD ${pct(metric.max_drawdown)}</p>${full ? `<p class="muted">전체 · 내 규칙 ${multipleText(full.final_wealth_multiple)} / 기준선 ${multipleText(full.baseline_final_wealth_multiple)} · CAGR ${pct(full.cagr)} · MDD ${pct(full.max_drawdown)}</p>` : ""}`;
  }

  function setCompoundSlider(id, values, preferred) {
    const slider = $(id);
    slider.min = "0";
    slider.max = String(Math.max(values.length - 1, 0));
    const exact = values.findIndex((value) => Number(value) === Number(preferred));
    slider.value = String(exact >= 0 ? exact : Math.max(values.length - 1, 0));
    slider.disabled = values.length < 2;
  }

  async function loadCompoundGrid() {
    const entry = compoundEntry();
    if (!entry) return;
    const key = `${entry.basket}|${entry.product}`;
    $("compound-knob-note").textContent = "그리드를 불러오는 중…";
    try {
      let payload = compoundState.cache.get(key);
      if (!payload) {
        const response = await fetch(`/api/research/compound/grid?basket=${encodeURIComponent(entry.basket)}&product=${encodeURIComponent(entry.product)}`);
        if (!response.ok) throw new Error(await readError(response));
        payload = await response.json();
        compoundState.cache.set(key, payload);
      }
      compoundState.payload = payload;
      setCompoundSlider("compound-drawdown", cachedList("drawdown_thresholds"), -.20);
      setCompoundSlider("compound-disp60", cachedList("disp60_thresholds"), -.10);
      compoundState.holdoutVisible = false;
      compoundState.frame = 0;
      renderCompound();
    } catch (error) {
      compoundState.payload = null;
      $("compound-knob-note").textContent = error.message || "그리드를 읽지 못했습니다.";
      compoundToast(error.message || "그리드를 읽지 못했습니다.", true);
    }
  }

  function syncCompoundProduct() {
    const variant = $("compound-product").value;
    const fixed = { index_1x: 1, synthetic_2x: 2, synthetic_3x: 3 }[variant];
    if (fixed) $("compound-multiple").value = String(fixed);
    $("compound-multiple").disabled = false;
  }

  async function revealCompoundHoldout() {
    const combination = compoundCombination(), row = compoundRow(combination);
    if (!row) { compoundToast("미계산 조합은 홀드아웃을 열 수 없습니다.", true); return; }
    const button = $("compound-holdout");
    button.disabled = true;
    try {
      const response = await fetch("/api/research/compound/holdout-view", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(combination),
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json();
      compoundState.holdoutVisible = true;
      compoundState.sessionViews = payload.session_views;
      updateHoldoutCounters(payload);
      renderCompoundHoldout(row, combination);
    } catch (error) {
      compoundToast(error.message || "홀드아웃 열람을 기록하지 못했습니다.", true);
    } finally { button.disabled = false; }
  }

  async function registerCompoundCandidate() {
    const combination = compoundCombination();
    if (!compoundRow(combination)) { compoundToast("미계산 조합은 후보로 등록할 수 없습니다.", true); return; }
    if (combination.basket === "FOREIGN") { compoundToast("FOREIGN은 현재 포워드 테스트 바스켓 계약 밖입니다.", true); return; }
    const entry = compoundEntry(), button = $("compound-register");
    button.disabled = true;
    const body = {
      name: `복리 사다리 ${entry.underlying} ${Math.round(combination.drawdown_threshold * 100)}%/${Math.round(combination.disp60_threshold * 100)}%`,
      reason: `compound ladder UI · ${combination.levels}분할`,
      compound: combination,
    };
    try {
      const response = await fetch("/api/research/candidates", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await readError(response));
      compoundToast("후보 등록 완료 · 포워드 테스트 레인이 자동으로 이어받습니다.");
      await refreshResearch();
    } catch (error) {
      compoundToast(error.message || "후보를 등록하지 못했습니다.", true);
    } finally { button.disabled = false; }
  }

  function compoundRunPayload() {
    const baskets = [...document.querySelectorAll('input[name="compound-run-basket"]:checked')].map((input) => input.value);
    return {
      baskets, product: $("compound-run-product").value,
      cost_enabled: $("compound-cost").checked,
      ranges: {
        drawdown_threshold: $("compound-run-drawdowns").value.trim(),
        disp60_threshold: $("compound-run-disp60").value.trim(),
        levels: $("compound-run-levels").value.trim(),
        leverage_multiple: $("compound-run-multiples").value.trim(),
      },
    };
  }

  function compoundCommand() {
    const payload = compoundRunPayload();
    const values = (value, fallback, integer = false) => value ? value.split(",").map((item) => integer ? Number.parseInt(item.trim(), 10) : Number(item.trim())) : fallback;
    const grid = {
      drawdown_threshold: values(payload.ranges.drawdown_threshold, compoundDefaults.drawdown_threshold),
      disp60_threshold: values(payload.ranges.disp60_threshold, compoundDefaults.disp60_threshold),
      levels: values(payload.ranges.levels, compoundDefaults.levels, true),
      leverage_multiple: values(payload.ranges.leverage_multiple, compoundDefaults.leverage_multiple, true),
      base_exposure: [0, 1], exit: compoundDefaults.exit, cost_enabled: [payload.cost_enabled],
    };
    const pyValue = (value) => typeof value === "string" ? `'${value}'` : typeof value === "boolean" ? (value ? "True" : "False") : String(value);
    const pyTuple = (items) => `(${items.map(pyValue).join(",")}${items.length === 1 ? "," : ""})`;
    const python = `{${Object.entries(grid).map(([key, items]) => `'${key}':${pyTuple(items)}`).join(",")}}`;
    const baskets = `(${payload.baskets.map((item) => `'${item}'`).join(",")}${payload.baskets.length === 1 ? "," : ""})`;
    return `.venv\\Scripts\\python.exe -c "from pathlib import Path; from scripts.research import run_compound_backtest as m; m.FULL_GRID=${python}; m.run(Path('.'), ${baskets}, quick=False)"`;
  }

  function updateCompoundCommand() { $("compound-command").textContent = compoundCommand(); }

  async function pollCompoundRun() {
    clearTimeout(compoundState.pollTimer);
    try {
      const response = await fetch("/api/research/compound/run");
      if (!response.ok) throw new Error(await readError(response));
      const status = await response.json();
      $("compound-run-log").textContent = (status.progress_lines || []).join("\n") || "아직 실행 기록이 없습니다.";
      $("compound-run-status").textContent = status.running ? `실행 중 · ${status.started_at || ""}` : status.last_error ? `실패 · ${status.last_error}` : status.last_finished_at ? `완료 · ${status.last_finished_at}` : "대기";
      $("compound-run-start").disabled = status.running;
      if (compoundState.wasRunning && !status.running && !status.last_error) {
        const entry = compoundEntry();
        if (entry) compoundState.cache.delete(`${entry.basket}|${entry.product}`);
        await loadCompoundGrid();
        compoundToast("계산 완료 · 새 grid를 다시 불러왔습니다.");
      }
      compoundState.wasRunning = status.running;
      if (status.running) compoundState.pollTimer = setTimeout(pollCompoundRun, 2000);
    } catch (error) {
      $("compound-run-status").textContent = error.message || "실행 상태를 읽지 못했습니다.";
    }
  }

  async function startCompoundRun() {
    const button = $("compound-run-start");
    button.disabled = true;
    try {
      const response = await fetch("/api/research/compound/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(compoundRunPayload()),
      });
      if (!response.ok) throw new Error(await readError(response));
      compoundState.wasRunning = true;
      compoundToast("계산을 시작했습니다. retained data만 사용합니다.");
      pollCompoundRun();
    } catch (error) {
      compoundToast(error.message || "계산을 시작하지 못했습니다.", true);
      button.disabled = false;
    }
  }

  function bindCompound() {
    ["compound-drawdown", "compound-disp60"].forEach((id) => $(id).addEventListener("input", () => scheduleCompoundRender()));
    ["compound-levels", "compound-exit", "compound-heatmap-axes"].forEach((id) => $(id).addEventListener("change", () => scheduleCompoundRender()));
    $("compound-cost").addEventListener("change", () => { scheduleCompoundRender(); updateCompoundCommand(); });
    $("compound-product").addEventListener("change", () => { syncCompoundProduct(); scheduleCompoundRender(); updateCompoundCommand(); });
    $("compound-multiple").addEventListener("change", () => {
      if ($("compound-product").value !== "actual_adjusted") {
        $("compound-product").value = ({ 1: "index_1x", 2: "synthetic_2x", 3: "synthetic_3x" })[$("compound-multiple").value];
      }
      scheduleCompoundRender();
    });
    $("compound-basket").addEventListener("change", loadCompoundGrid);
    $("compound-holdout").addEventListener("click", revealCompoundHoldout);
    $("compound-register").addEventListener("click", registerCompoundCandidate);
    $("compound-run-start").addEventListener("click", startCompoundRun);
    document.querySelectorAll("#compound-run input, #compound-run select").forEach((input) => input.addEventListener("input", updateCompoundCommand));
    syncCompoundProduct();
  }

  async function initCompound() {
    if (!$("compound-lab")) return;
    bindCompound();
    try {
      const response = await fetch("/api/research/compound/grid");
      if (!response.ok) throw new Error(await readError(response));
      const catalog = await response.json();
      compoundState.catalog = catalog.catalog || [];
      $("compound-basket").innerHTML = compoundState.catalog.map((item) => `<option value="${esc(`${item.basket}|${item.product}`)}">${esc(item.label)}</option>`).join("");
      const views = Number(catalog.holdout_views || 0);
      updateHoldoutCounters({ persistent_views: views });
      if (!compoundState.catalog.length) throw new Error("미계산 조합 · compound grid가 없습니다.");
      updateCompoundCommand();
      await Promise.all([loadCompoundGrid(), pollCompoundRun()]);
    } catch (error) {
      $("compound-knob-note").textContent = error.message || "파라미터 실험을 시작하지 못했습니다.";
    }
  }

  async function boot() {
    bindExperiment();
    initCompound();
    initCrisisOverlay();
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
