/* Local stocks page: provider-free reads and loopback-only preference writes. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const number = (value, digits = 1) => value === null || value === undefined
    ? "—" : Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const price = (row) => !row.price_available ? "—" : Number(row.price).toLocaleString("ko-KR", {
    maximumFractionDigits: row.market === "US ETF" ? 2 : 0,
  });
  const pct = (value, digits = 1) => value === null || value === undefined
    ? "—" : `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}%`;
  const tone = (value) => value === null || value === undefined ? "muted" : value > 0 ? "up" : value < 0 ? "down" : "muted";
  const uid = () => window.crypto && crypto.randomUUID ? crypto.randomUUID() : `condition-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  let page = { watchlists: { lists: [] }, conditions: { conditions: [] }, table: [] };
  let selectedListId = "favorites";
  let conditions = [];

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

  function renderWatchlistEditor() {
    const lists = page.watchlists.lists || [];
    if (!lists.some((item) => item.list_id === selectedListId) && lists.length) selectedListId = lists[0].list_id;
    $("watchlist-select").innerHTML = lists.map((item) => `<option value="${esc(item.list_id)}">${esc(item.name)} (${(item.items || []).length})</option>`).join("");
    $("watchlist-select").value = selectedListId;
    const list = currentList();
    $("watchlist-name").value = list ? list.name : "";
    $("watchlist-items").innerHTML = list && list.items.length ? list.items.map((item, index) => `
      <div class="watchlist-edit-row" data-market="${esc(item.market)}" data-symbol="${esc(item.symbol)}">
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
    $("watchlist-table-rows").innerHTML = rows.length ? rows.map((row) => `<tr>
      <td><b>${esc(row.name)}</b><small>${esc(row.list_name)}</small></td>
      <td class="num">${esc(row.symbol)}</td>
      <td class="num">${price(row)}${row.price_available ? "" : `<small>${esc(row.unavailable_reason)}</small>`}</td>
      <td class="num ${tone(row.change_pct)}">${pct(row.change_pct)}</td>
      <td class="num ${tone(row.ma5_pct)}">${pct(row.ma5_pct)}</td>
      <td class="num ${tone(row.ma20_pct)}">${pct(row.ma20_pct)}</td>
      <td class="num ${tone(row.ma60_pct)}">${pct(row.ma60_pct)}</td>
      <td class="num">${number(row.rsi14, 1)}</td>
      <td class="num ${tone(row.drawdown_pct)}">${pct(row.drawdown_pct)}</td>
      <td class="num">${row.volume20_multiple === null || row.volume20_multiple === undefined ? "—" : `${number(row.volume20_multiple, 2)}×`}</td>
      <td>${row.flag ? `<span class="flag">${esc(row.flag)}</span>` : "—"}</td>
      <td><a class="button chart-link" href="/?symbol=${encodeURIComponent(row.symbol)}">차트</a></td>
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

  function renderAll() {
    conditions = (page.conditions.conditions || []).map((item) => ({ ...item }));
    renderWatchlistEditor(); renderWatchlistTable(); renderConditions();
    $("stocks-safety").textContent = page.note || "";
  }

  async function refreshPage(message = "") {
    page = await requestJson("/api/stocks");
    renderAll();
    $("watchlist-status").textContent = message;
  }

  function renderSearch(payload) {
    const matches = payload.matches || [];
    $("stock-search-results").innerHTML = matches.length ? matches.map((item) => `
      <button type="button" class="search-result" data-market="${esc(item.market)}" data-symbol="${esc(item.symbol)}">
        <b>${esc(item.name)}</b><span>${esc(item.symbol)} · ${esc(item.market)} · ${esc(item.security_type)}</span>
      </button>`).join("") : `<div class="unavailable">${esc(payload.reason || "검색 결과가 없습니다.")}</div>`;
  }

  async function runSearch() {
    const query = $("stock-search").value.trim();
    renderSearch(await requestJson(`/api/stocks/search?q=${encodeURIComponent(query)}`));
  }

  async function loadScanner() {
    const started = performance.now();
    $("scanner-summary").textContent = "보존 데이터를 읽는 중…";
    const result = await requestJson("/api/scanner");
    const elapsed = (performance.now() - started) / 1000;
    if (result.status !== "READY") {
      $("scanner-summary").textContent = `표시 불가 · ${result.reason || "입력 확인 필요"} · ${elapsed.toFixed(2)}초`;
      $("scanner-rows").innerHTML = `<tr><td colspan="9" class="unavailable">후보를 계산할 수 없습니다.</td></tr>`;
      return;
    }
    const fundamentalColumns = result.fundamental_columns || [];
    $("scanner-head").innerHTML = `<th>종목명</th><th>시장</th><th>코드</th><th>현재가</th><th>등락률</th><th>RSI14</th><th>60일선</th><th>52주 낙폭</th>${fundamentalColumns.includes("per") ? "<th>PER</th>" : ""}${fundamentalColumns.includes("pbr") ? "<th>PBR</th>" : ""}<th>관찰 근거</th>`;
    $("scanner-summary").textContent = `${result.as_of} · ${result.scanned_instruments.toLocaleString("ko-KR")}개 확인 · ${result.count.toLocaleString("ko-KR")}개 후보 · ${result.rule} · ${elapsed.toFixed(2)}초 · ${result.fundamentals_note}`;
    $("scanner-rows").innerHTML = result.candidates.length ? result.candidates.map((row) => `<tr>
      <td><a href="/?symbol=${encodeURIComponent(row.symbol)}"><b>${esc(row.name)}</b></a>${row.data_caution ? `<small class="amber">${esc(row.data_caution)}</small>` : ""}</td>
      <td>${esc(row.market)}</td><td class="num">${esc(row.symbol)}</td><td class="num">${number(row.price, 0)}</td>
      <td class="num ${tone(row.change_pct)}">${pct(row.change_pct)}</td><td class="num">${number(row.rsi14, 1)}</td>
      <td class="num ${tone(row.disp60_pct)}">${pct(row.disp60_pct)}</td><td class="num ${tone(row.drawdown_pct)}">${pct(row.drawdown_pct)}</td>
      ${fundamentalColumns.includes("per") ? `<td class="num">${number(row.per, 2)}</td>` : ""}${fundamentalColumns.includes("pbr") ? `<td class="num">${number(row.pbr, 2)}</td>` : ""}
      <td>${esc(row.why)}</td>
    </tr>`).join("") : `<tr><td colspan="${9 + fundamentalColumns.length}" class="unavailable">현재 조건에 도달한 종목이 없습니다.</td></tr>`;
  }

  document.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    try {
      if (target.id === "run-stock-search") {
        await runSearch();
      } else if (target.closest(".search-result")) {
        const result = target.closest(".search-result");
        await mutate("/api/watchlist/items", { list_id: selectedListId, market: result.dataset.market, symbol: result.dataset.symbol });
        $("stock-search-results").innerHTML = "";
        await refreshPage("종목을 추가했습니다.");
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
        $("new-watchlist-name").value = "";
        await refreshPage("관심목록을 만들었습니다.");
      } else if (target.id === "rename-watchlist") {
        await mutate("/api/watchlists", { action: "rename", list_id: selectedListId, name: $("watchlist-name").value.trim() });
        await refreshPage("목록 이름을 변경했습니다.");
      } else if (target.id === "add-condition") {
        conditions = collectConditions();
        conditions.push({ id: uid(), name: "", field: "rsi14", op: "<=", value: 30, scope: "watchlist" });
        renderConditions();
      } else if (target.classList.contains("remove-condition")) {
        conditions = collectConditions();
        conditions.splice(Number(target.closest("tr").dataset.index), 1);
        renderConditions();
      } else if (target.id === "save-conditions") {
        $("condition-status").textContent = "저장 중…";
        await mutate("/api/watch-conditions", { schema_version: 1, conditions: collectConditions() });
        await refreshPage("조건을 반영했습니다.");
        $("condition-status").textContent = "저장했습니다.";
      } else if (target.id === "refresh-scanner") {
        await loadScanner();
      }
    } catch (error) {
      $("stocks-safety").textContent = `처리 실패 · ${error.message}`;
    }
  });

  document.addEventListener("DOMContentLoaded", async () => {
    $("watchlist-select").addEventListener("change", () => {
      selectedListId = $("watchlist-select").value; renderWatchlistEditor();
    });
    $("stock-search").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); runSearch().catch((error) => { $("stocks-safety").textContent = `검색 실패 · ${error.message}`; }); }
    });
    try { await refreshPage(); }
    catch (error) { $("stocks-safety").textContent = `종목 화면을 불러오지 못했습니다. · ${error.message}`; }
  });
})();
