/* Local account page: provider-free reads and loopback-only writes. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const money = (value) => value === null || value === undefined ? "—" : `₩${Math.round(Number(value)).toLocaleString("ko-KR")}`;
  const compactMoney = (value) => value === null || value === undefined ? "—" : `₩${(Number(value) / 1e8).toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}억`;
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
  let timelineChart = null;

  function sourceAsOf(rows, kinds) {
    return (rows || []).filter((row) => kinds.includes(row.kind)).map((row) => `${row.name} ${shortDate(row.as_of)}${row.included ? "" : " 제외"}`).join(" · ");
  }

  function renderSummary() {
    const summary = payload.summary || {};
    $("invest-total").textContent = compactMoney(summary.invest_total_krw);
    $("net-worth-total").textContent = compactMoney(summary.net_worth_krw);
    $("invest-asof").textContent = sourceAsOf(payload.rows, ["api", "manual"]) || "연결된 투자 자산 없음";
    const netWorthSources = sourceAsOf(payload.rows, ["asset", "liability"]);
    $("net-worth-asof").textContent = summary.net_worth_krw === null || summary.net_worth_krw === undefined
      ? "기타 자산·부채 스냅샷 없음"
      : netWorthSources || `부동산·예금 포함 · ${summary.net_worth_as_of || "기준일 미상"} 기준`;
  }

  function renderSourceRows() {
    const rows = payload.rows || [];
    $("account-source-rows").innerHTML = rows.length ? rows.map((row) => `<tr>
      <td><b>${esc(row.name)}</b><div class="muted source-note">${esc(row.note || "")}</div></td>
      <td class="num ${row.value_krw < 0 ? "down" : ""}">${money(row.value_krw)}</td>
      <td class="num">${money(row.cash_krw)}</td>
      <td class="num ${row.pnl_krw > 0 ? "up" : row.pnl_krw < 0 ? "down" : ""}">${money(row.pnl_krw)}</td>
      <td class="num">${esc(row.as_of || "—")}</td>
      <td><span class="chip ${row.included ? "" : "dashed"}">${row.included ? (row.partial ? "부분 포함" : "포함") : "제외"}</span></td>
    </tr>`).join("") : `<tr><td colspan="6" class="unavailable">표시할 계좌나 자산이 없습니다.</td></tr>`;
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
    if (timelineChart) { timelineChart.remove(); timelineChart = null; }
    if (!window.LightweightCharts || points.length < 2) {
      host.innerHTML = `<div class="unavailable">실제 순자산 관측이 2개 이상이면 선이 표시됩니다.${points.length === 1 ? ` · 현재 ${money(points[0].v)}` : ""}</div>`;
      return;
    }
    host.innerHTML = "";
    timelineChart = LightweightCharts.createChart(host, {
      layout: { background: { color: "#fff" }, textColor: "#6b6660", fontFamily: "IBM Plex Sans KR, system-ui" },
      grid: { vertLines: { visible: false }, horzLines: { color: "#e6e1d8" } },
      rightPriceScale: { borderColor: "#d9d3ca" }, timeScale: { borderColor: "#d9d3ca" }, autoSize: true,
    });
    const series = timelineChart.addLineSeries({ color: "#1f1d1a", lineWidth: 2, priceLineVisible: false });
    series.setData(points.map((point) => ({ time: point.t, value: point.v })));
    timelineChart.timeScale().fitContent();
  }

  function renderBreakdown() {
    const netWorth = payload.net_worth || {};
    $("breakdown-asof").textContent = netWorth.as_of ? `${netWorth.as_of} 기준` : "";
    $("net-worth-breakdown").innerHTML = (netWorth.breakdown || []).length ? netWorth.breakdown.map((row) => `<div class="breakdown-row">
      <span><i class="${row.kind}"></i>${esc(row.name)}${row.complete ? "" : ' <small class="muted">미완전</small>'}</span>
      <b class="num ${row.kind === "liability" ? "down" : ""}">${row.kind === "liability" ? "−" : ""}${money(row.value_krw)}</b>
    </div>`).join("") : `<div class="unavailable">저장된 자산·부채 구성이 없습니다.</div>`;
  }

  function hydrateState() {
    manualAccounts = ((payload.manual_accounts || {}).accounts || []).map((account) => ({
      ...account, positions: account.valued_positions || account.positions || [],
    }));
    const latest = (payload.net_worth || {}).latest;
    assetRows = latest ? latest.assets.map((row) => ({ ...row })) : [];
    liabilityRows = latest ? latest.liabilities.map((row) => ({ ...row })) : [];
    $("net-worth-date").value = latest ? latest.as_of_date : today();
  }

  function renderAll() {
    renderSummary(); renderSourceRows(); renderManualAccounts(); renderNetWorthForm(); renderTimeline(); renderBreakdown();
    $("account-safety").textContent = payload.safety_note || "";
  }

  async function refresh() {
    const response = await fetch("/api/account");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = await response.json();
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
    try { await refresh(); }
    catch (error) { $("account-safety").textContent = `계좌 화면을 불러오지 못했습니다. · ${error.message}`; }
  });
})();
