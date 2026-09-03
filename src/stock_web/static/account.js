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

  let payload = null;
  let manualAccounts = [];
  let assetRows = [];
  let liabilityRows = [];
  let selectedReturnWindow = "3M";
  let returnPeriodHydrated = false;
  let journalPayload = { events: [], summary: {}, gaps: [] };
  let selectedJournalDays = 90;

  function sourceAsOf(rows, kinds) {
    return (rows || []).filter((row) => kinds.includes(row.kind)).map((row) => `${row.name} ${row.as_of_label || shortDate(row.as_of)}${row.included ? "" : " 제외"}`).join(" · ");
  }

  function renderSummary() {
    const summary = payload.summary || {};
    $("invest-total").textContent = compactMoney(summary.invest_total_krw);
    $("net-worth-total").textContent = compactMoney(summary.net_worth_krw);
    $("invest-asof").textContent = sourceAsOf(payload.rows, ["api", "manual"]) || "연결된 투자 자산 없음";
    const netWorthSources = sourceAsOf(payload.rows, ["asset", "liability"]);
    $("net-worth-asof").textContent = summary.net_worth_krw === null || summary.net_worth_krw === undefined
      ? "기타 자산·부채 스냅샷 없음"
      : netWorthSources || `부동산·예금 포함 · ${summary.net_worth_as_of_label || "기준일 미상"} 기준`;
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
    $("return-metrics").innerHTML = metric.reason ? `<div class="unavailable">${esc(metric.reason)}</div>` : `
      <div class="return-metric"><span>${esc(label)} 진짜 손익</span><b class="num ${valueClass(metric.true_pnl_krw)}">${signedMoney(metric.true_pnl_krw)}</b></div>
      <div class="return-metric"><span>순입출금</span><b class="num ${valueClass(metric.net_flows_krw)}">${signedMoney(metric.net_flows_krw)}</b></div>
      <div class="return-metric" title="입출금 시점을 반영해 내가 실제 투입한 돈 대비 수익률입니다."><span>돈 가중(내 실제 수익률)</span><b class="num ${valueClass(metric.return_pct_modified_dietz)}">${pct(metric.return_pct_modified_dietz)}</b></div>
      <div class="return-metric" title="입출금 영향을 잘라내고 운용 성과만 이어 붙인 수익률입니다."><span>시간 가중(운용 실력)</span><b class="num ${valueClass(metric.return_pct_twr)}">${pct(metric.return_pct_twr)}</b></div>
      <div class="return-metric"><span>KOSPI 동기간</span><b class="num ${valueClass(metric.kospi_return_pct)}">${pct(metric.kospi_return_pct)}</b></div>
      <div class="return-metric"><span>증권사 표시 손익</span><b class="num ${valueClass(metric.broker_reported_pnl_krw)}">${signedMoney(metric.broker_reported_pnl_krw)}</b></div>
      ${metric.partial ? '<div class="return-metric"><span>관측 품질</span><b class="badge dashed">부분 관측 포함</b></div>' : ""}`;
    const history = payload.total_asset_history || [];
    const benchmark = payload.benchmark || [];
    const shownHistory = metric.start_date ? history.filter((point) => point.t >= metric.start_date) : history;
    const shownBenchmark = metric.start_date ? benchmark.filter((point) => point.t >= metric.start_date) : benchmark;
    const chartLabels = payload.chart_labels || {};
    window.SIChart.renderLineChart($("total-asset-chart"), shownHistory, {
      benchmark: shownBenchmark, ariaLabel: "총 투자자산과 KOSPI 동기간 추이",
      valueLabel: chartLabels.primary || "총자산",
      benchmarkLabel: chartLabels.benchmark || "KOSPI (시작값 맞춤)",
      emptyMessage: "총 투자자산 관측이 2개 이상이면 선이 표시됩니다.",
    });
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
    return ({ BUY: "매수", SELL: "매도", DIVIDEND: "배당 추정", "DIVIDEND?": "추정(미확인)" })[side] || side;
  }

  function currencySummary(values) {
    const entries = Object.entries(values || {});
    return entries.length ? entries.map(([currency, value]) => nativeMoney(value, currency)).join(" · ") : "—";
  }

  function resetJournalForm() {
    $("journal-date").value = today();
    $("journal-account").value = "미래에셋";
    $("journal-symbol").value = "";
    $("journal-name").value = "";
    $("journal-side").value = "BUY";
    $("journal-currency").value = "KRW";
    $("journal-quantity").value = "";
    $("journal-price").value = "";
    $("journal-memo").value = "";
  }

  function renderJournal() {
    const events = journalPayload.events || [];
    $("journal-rows").innerHTML = events.length ? events.map((entry) => {
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
      <div class="form-grid manual-account-fields">
        <label class="field"><span>계좌 이름</span><input data-field="label" value="${esc(account.label || "")}" placeholder="예: 미래에셋"></label>
        <label class="field"><span>통화</span><select data-field="currency"><option value="KRW" ${account.currency !== "USD" ? "selected" : ""}>KRW</option><option value="USD" ${account.currency === "USD" ? "selected" : ""}>USD</option></select></label>
        <label class="field"><span>현금</span><input data-field="cash" type="number" min="0" step="any" value="${esc(account.cash ?? 0)}"></label>
        <label class="field"><span>기준일</span><input data-field="snapshot_date" type="date" value="${esc(account.snapshot_date || today())}"></label>
      </div>
      <div class="holding-head"><span>보유 종목</span><button type="button" class="button add-holding">종목 추가</button></div>
      <div class="holding-rows">${positions.map((position, positionIndex) => `<div class="holding-row" data-position-index="${positionIndex}">
        <label class="field"><span>종목코드·티커</span><input data-field="ticker" value="${esc(position.ticker || "")}"></label>
        <label class="field"><span>종목명</span><input data-field="name" value="${esc(position.name || "")}"></label>
        <label class="field"><span>수량</span><input data-field="quantity" type="number" min="0" step="any" value="${esc(position.quantity ?? "")}"></label>
        <label class="field"><span>평균단가</span><input data-field="average_cost" type="number" min="0" step="any" value="${esc(position.average_cost ?? "")}"></label>
        <label class="field"><span>수동 현재가</span><input data-field="manual_price" type="number" min="0" step="any" value="${esc(position.manual_price ?? "")}" placeholder="선택"></label>
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
    return `<div class="net-worth-input-row" data-kind="${kind}" data-index="${index}">
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

  function hydrateState() {
    hydrateReturnPeriod();
    manualAccounts = ((payload.manual_accounts || {}).accounts || []).map((account) => ({
      ...account, positions: account.valued_positions || account.positions || [],
    }));
    const latest = (payload.net_worth || {}).latest;
    assetRows = latest ? latest.assets.map((row) => ({ ...row })) : [];
    liabilityRows = latest ? latest.liabilities.map((row) => ({ ...row })) : [];
    $("net-worth-date").value = latest ? latest.as_of_date : today();
  }

  function renderAll() {
    renderSummary(); renderSourceRows(); renderPerformance(); renderCashFlows(); renderJournal(); renderManualAccounts(); renderNetWorthForm(); renderTimeline(); renderBreakdown();
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
    $(statusHost).textContent = "저장 중…";
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    $(statusHost).textContent = "저장했습니다.";
    await refresh();
  }

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.id === "add-manual-account") {
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
    } else if (target.closest("#return-range button")) {
      document.querySelectorAll("#return-range button").forEach((button) => button.classList.remove("on"));
      target.classList.add("on"); selectedReturnWindow = target.dataset.v; renderPerformance();
    } else if (target.closest("#journal-range button")) {
      document.querySelectorAll("#journal-range button").forEach((button) => button.classList.remove("on"));
      target.classList.add("on"); selectedJournalDays = Number(target.dataset.days);
      loadJournal().catch((error) => { $("journal-status").textContent = `조회 실패 · ${error.message}`; });
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
          const response = await fetch("/api/cash-flows", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: target.dataset.flowId }) });
          const result = await response.json(); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
          $("cash-flow-status").textContent = "삭제했습니다."; resetCashFlowForm(); await refresh();
        } catch (error) { $("cash-flow-status").textContent = `삭제 실패 · ${error.message}`; }
      })();
    } else if (target.id === "cancel-cash-flow") {
      resetCashFlowForm(); $("cash-flow-status").textContent = "";
    } else if (target.classList.contains("delete-journal-entry")) {
      (async () => {
        try {
          const response = await fetch("/api/trade-journal/manual", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: target.dataset.entryId }) });
          const result = await response.json(); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
          $("journal-status").textContent = "삭제했습니다."; await loadJournal();
        } catch (error) { $("journal-status").textContent = `삭제 실패 · ${error.message}`; }
      })();
    }
  });

  document.addEventListener("DOMContentLoaded", async () => {
    $("save-manual-accounts").addEventListener("click", async () => {
      try { await postJson("/api/manual/accounts", { schema_version: 1, accounts: collectManualAccounts() }, "manual-status"); }
      catch (error) { $("manual-status").textContent = `저장 실패 · ${error.message}`; }
    });
    $("save-net-worth").addEventListener("click", async () => {
      try { await postJson("/api/net-worth", { as_of_date: $("net-worth-date").value, assets: collectNetWorthRows("asset"), liabilities: collectNetWorthRows("liability") }, "net-worth-status"); }
      catch (error) { $("net-worth-status").textContent = `저장 실패 · ${error.message}`; }
    });
    $("save-cash-flow").addEventListener("click", async () => {
      try {
        const body = { date: $("cash-flow-date").value, amount_krw: Number($("cash-flow-amount").value), account: $("cash-flow-account").value.trim(), memo: $("cash-flow-memo").value.trim() };
        if ($("cash-flow-id").value) body.id = $("cash-flow-id").value;
        await postJson("/api/cash-flows", body, "cash-flow-status"); resetCashFlowForm();
      } catch (error) { $("cash-flow-status").textContent = `저장 실패 · ${error.message}`; }
    });
    $("save-journal-entry").addEventListener("click", async () => {
      try {
        $("journal-status").textContent = "저장 중…";
        const body = {
          date: $("journal-date").value, account_label: $("journal-account").value.trim(),
          symbol: $("journal-symbol").value.trim(), name: $("journal-name").value.trim(),
          side: $("journal-side").value, quantity: Number($("journal-quantity").value),
          price: Number($("journal-price").value), currency: $("journal-currency").value,
          memo: $("journal-memo").value.trim(),
        };
        const response = await fetch("/api/trade-journal/manual", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const result = await response.json(); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        $("journal-status").textContent = "저장했습니다."; resetJournalForm(); await loadJournal();
      } catch (error) { $("journal-status").textContent = `저장 실패 · ${error.message}`; }
    });
    resetCashFlowForm();
    resetJournalForm();
    try { await refresh(); }
    catch (error) { $("account-safety").textContent = `계좌 화면을 불러오지 못했습니다. · ${error.message}`; }
  });
})();
