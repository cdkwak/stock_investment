from __future__ import annotations

import json
from pathlib import Path
import subprocess
from uuid import uuid4

from stock_web.app import create_app
from tests.unit.web import ASGITestClient


def _project_root() -> Path:
    root = Path(__file__).parents[3] / ".tmp/agents/research-tabs-20260906" / uuid4().hex
    root.mkdir(parents=True)
    return root


def test_research_html_contains_all_hash_tab_sections_and_questions() -> None:
    response = ASGITestClient(create_app(_project_root())).get(
        "/research", client_host="127.0.0.1",
    )

    assert response.status_code == 200
    for name, question in (
        ("timeline", "지금 어떤 상태이고 과거엔 어땠나"),
        ("rules", "후보들이 어떻게 하고 있나"),
        ("lab", "돌려본다"),
    ):
        assert f'href="#{name}"' in response.text
        assert f'id="research-panel-{name}"' in response.text
        assert question in response.text
    assert 'id="crisis-timeline"' in response.text
    assert 'id="result-cards-content"' in response.text
    assert "결과 카드 없음 — 후보 순위·임계 결정 뒤 생성" in response.text


def test_tabs_use_hash_only_and_knobs_stay_visibly_undecided() -> None:
    web = Path(__file__).parents[3] / "src/stock_web"
    template = (web / "templates/research.html").read_text(encoding="utf-8")
    script = (web / "static/research.js").read_text(encoding="utf-8")

    assert "window.location.hash" in script
    assert 'window.addEventListener("hashchange", sync)' in script
    assert "localStorage" not in script
    for label in (
        "낙폭 임계", "60일선 이격 임계", "최고 단계 레버리지 상품 비중",
        "단계 수", "기본 노출",
    ):
        row = template.split(f"<span>{label}</span>", 1)[1].split("</div>", 1)[0]
        assert "미정 · 사용자 결정 대기" in row
        assert "지금 정할 것.md" in row and "큐 사용자 결정 줄" in row
    assert "실효 노출 = 비중 × 배수" in template
    assert "최고 단계 레버리지 상품 비중 결정 뒤 계산" in template
    assert "compoundDefaults" not in script
    assert 'cachedList("drawdown_thresholds"), -.20' not in script
    assert 'cachedList("disp60_thresholds"), -.10' not in script


def test_hash_tab_switch_and_payload_status_count_execute_in_javascript() -> None:
    script_path = Path(__file__).parents[3] / "src/stock_web/static/research.js"
    node_program = f"""
const fs = require('fs');
function node(name) {{
  return {{ hidden: false, dataset: {{ researchPanel: name }}, attrs: {{}}, classList: {{ toggle(key, on) {{ this[key] = on; }} }}, getAttribute(key) {{ return key === 'href' ? `#${{name}}` : this.attrs[key]; }}, setAttribute(key, value) {{ this.attrs[key] = value; }}, tabIndex: 0, textContent: '' }};
}}
const panels = ['timeline', 'rules', 'lab'].map(node);
const tabs = ['timeline', 'rules', 'lab'].map(node);
const status = node('status');
global.window = {{ location: {{ hash: '' }}, addEventListener() {{}} }};
global.document = {{
  addEventListener() {{}},
  getElementById(id) {{ return id === 'research-tab-rules-status' ? status : null; }},
  querySelectorAll(selector) {{ return selector === '[data-research-panel]' ? panels : selector === '.research-tab' ? tabs : []; }},
}};
eval(fs.readFileSync({json.dumps(str(script_path))}, 'utf8'));
window.__researchTabsTest.setResearchTab('rules');
if (panels[1].hidden || !panels[0].hidden || tabs[1].attrs['aria-selected'] !== 'true') throw new Error('rules tab did not activate');
window.__researchTabsTest.setResearchTab('unknown');
if (panels[0].hidden || !panels[1].hidden || tabs[0].attrs['aria-selected'] !== 'true') throw new Error('invalid hash did not fail to timeline');
window.__researchTabsTest.renderTabStatus({{ tab_status: {{ rules: {{ candidate_count: 8, adopted_count: 0 }} }} }});
if (status.hidden || status.textContent !== '(후보 8 · 채택 0)') throw new Error('status count did not render');
window.__researchTabsTest.renderTabStatus({{}});
if (!status.hidden || status.textContent !== '') throw new Error('missing status did not stay plain');
console.log('HASH_TABS_OK');
"""
    completed = subprocess.run(
        ["node", "-e", node_program], check=False, capture_output=True, text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert "HASH_TABS_OK" in completed.stdout


def test_result_card_renderer_reads_required_fields_and_fails_loud() -> None:
    script_path = Path(__file__).parents[3] / "src/stock_web/static/research.js"
    rows = []
    for index, (label, sessions) in enumerate(
        (("1개월", 21), ("3개월", 63), ("6개월", 126), ("12개월", 252)),
        start=1,
    ):
        rows.append({
            "mean_return": index / 100,
            "median_return": (index + 0.5) / 100,
            "win_rate": .50 + index / 100,
            "events_total": 11,
            "events_independent": 7,
            "events_mature": 10 - index,
            "label": label,
            "horizon_sessions": sessions,
        })
    card = {
        "claim": "합성 카드 전체필드",
        "events_total": 11,
        "events_independent": 7,
        "table": rows,
        "average_path": [
            {"offset_sessions": -252, "mean_index": 91.0, "events": 5},
            {"offset_sessions": 0, "mean_index": 100.0, "events": 7},
            {"offset_sessions": 252, "mean_index": 113.0, "events": 6},
        ],
    }
    sell = {
        **card,
        "side": "sell",
        "table": [{
            "mean_realized_volatility": .21,
            "median_realized_volatility": .20,
            "mean_max_drawdown": -.14,
            "median_max_drawdown": -.12,
            "events_total": 11,
            "events_independent": 7,
            "events_mature": 8,
            "label": label,
            "horizon_sessions": sessions,
        } for label, sessions in (("1개월", 21), ("3개월", 63), ("6개월", 126), ("12개월", 252))],
    }
    node_program = f"""
const fs = require('fs');
global.window = {{ location: {{ hash: '' }}, addEventListener() {{}} }};
global.document = {{ addEventListener() {{}}, getElementById() {{ return null; }}, querySelectorAll() {{ return []; }} }};
eval(fs.readFileSync({json.dumps(str(script_path))}, 'utf8'));
const api = window.__researchResultCardTest;
const card = {json.dumps(card, ensure_ascii=False)};
const markup = api.resultCardMarkup(card, 0);
for (const expected of ['합성 카드 전체필드', '총 사건 <b>11</b>', '독립 사건 <b>7</b>', '1개월', '12개월', '+1.0%', '+1.5%', '51.0%', '경로별 사건 수 5~7', 'result-path-line']) {{
  if (!markup.includes(expected)) throw new Error(`missing rendered field: ${{expected}}`);
}}
const broken = JSON.parse(JSON.stringify(card));
delete broken.table[0].median_return;
const errorMarkup = api.resultCardMarkup(broken, 1);
if (!errorMarkup.includes('결과 카드 오류') || !errorMarkup.includes('median_return')) throw new Error('missing explicit median error');
const sellMarkup = api.resultCardMarkup({json.dumps(sell, ensure_ascii=False)}, 2);
const sellHead = sellMarkup.split('<thead>')[1].split('</thead>')[0];
if (!(sellHead.indexOf('평균 변동성') < sellHead.indexOf('평균 최대낙폭') && sellHead.indexOf('평균 최대낙폭') < sellHead.indexOf('총 사건'))) throw new Error('sell columns are not risk-first');
console.log('RESULT_CARD_RENDERER_OK');
"""

    completed = subprocess.run(
        ["node", "-e", node_program], check=False, capture_output=True, text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert "RESULT_CARD_RENDERER_OK" in completed.stdout


def test_legacy_banner_is_payload_driven_and_exact() -> None:
    web = Path(__file__).parents[3] / "src/stock_web"
    script = (web / "static/research.js").read_text(encoding="utf-8")

    assert "if (!(payload || {}).legacy_numbers)" in script
    assert "payload.legacy_reason" in script
    assert (
        "재구축 전 수치 — 폐기 예정 · 판단에 참고 금지 "
        "(2026-09-06 재구축 스펙)"
    ) in script
