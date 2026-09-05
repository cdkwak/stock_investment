from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from stock_web.api.crisis_timeline import CRISIS_WINDOWS, build_crisis_timeline_payload
from stock_web.app import create_app
from tests.unit.web import ASGITestClient


def _root() -> Path:
    root = (
        Path(__file__).parents[3]
        / ".tmp/agents/crisis-timeline-20260905/fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    return root


def _write(root: Path, relative: str, frame: pd.DataFrame) -> Path:
    path = root / "data" / relative / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def _fixtures(root: Path, *, include_nikkei: bool = True) -> None:
    global_rows: list[dict[str, object]] = []
    values = {
        "NASDAQ100": 1000.0,
        "SP500": 800.0,
        "NIKKEI225": 20000.0,
        "EURO_STOXX50": 4000.0,
        "DAX": 5000.0,
    }
    periods = {
        "NASDAQ100": [("1985-10-01", "1985-10-01"), ("2000-03-01", "2003-03-31"), ("2007-10-01", "2009-06-30"), ("2026-09-01", "2026-09-01")],
        "SP500": [("1928-01-03", "1928-01-03"), ("2000-03-01", "2003-03-31"), ("2007-10-01", "2009-06-30"), ("2026-09-01", "2026-09-01")],
        "NIKKEI225": [("2000-03-01", "2003-03-31"), ("2007-10-01", "2009-06-30")],
        "EURO_STOXX50": [("2007-04-02", "2009-06-30")],
        "DAX": [("2000-03-01", "2003-03-31")],
    }
    if not include_nikkei:
        periods.pop("NIKKEI225")
    for symbol, spans in periods.items():
        ordinal = 0
        for start, end in spans:
            for observed in pd.bdate_range(start, end):
                global_rows.append({
                    "date": observed, "symbol": symbol,
                    "close": values[symbol] + ordinal * 3.0,
                })
                ordinal += 1
    _write(root, "normalized/global_index_price_daily", pd.DataFrame(global_rows))

    kospi_dates = pd.bdate_range("2000-03-01", "2009-06-30")
    _write(root, "normalized/kr_index_daily", pd.DataFrame({
        "date": kospi_dates, "market": "KOSPI",
        "close": [500.0 + index for index in range(len(kospi_dates))],
    }))
    fred_dates = pd.date_range("1962-01-02", "2026-09-03", periods=900)
    _write(root, "normalized/fred_treasury_yield_daily", pd.DataFrame({
        "date": fred_dates,
        "dgs2": [2.0 + index / 1000 for index in range(len(fred_dates))],
        "dgs10": [3.0 + index / 1000 for index in range(len(fred_dates))],
        "dgs30": [4.0 + index / 1000 for index in range(len(fred_dates))],
    }))
    bok_rows = []
    for tenor, base in (("3Y", 3.0), ("10Y", 4.0)):
        for index, observed in enumerate(pd.date_range("1998-11-13", "2026-08-13", periods=400)):
            bok_rows.append({"date": observed, "tenor": tenor, "yield_percent": base + index / 1000})
    _write(
        root, "normalized/bok_ecos_kr_treasury_yield_source_observation",
        pd.DataFrame(bok_rows),
    )
    toss_rows = []
    for instrument, base in (("KR_BOND_3Y", 3.1), ("KR_BOND_10Y", 4.1)):
        for index, observed in enumerate(pd.bdate_range("2026-08-14", "2026-09-03")):
            toss_rows.append({"date": observed, "instrument": instrument, "close": base + index / 1000})
    _write(root, "normalized/kr_treasury_yield_daily", pd.DataFrame(toss_rows))


def test_crisis_table_has_the_requested_real_date_windows() -> None:
    expected = {
        "1987": ("1987-08-01", "1988-06-30"),
        "1997": ("1997-06-01", "1999-06-30"),
        "2000": ("2000-03-01", "2003-03-31"),
        "2008": ("2007-10-01", "2009-06-30"),
        "2011": ("2011-04-01", "2012-09-30"),
        "2020": ("2020-02-01", "2020-08-31"),
        "2022": ("2022-01-01", "2022-12-31"),
        "2025": ("2025-01-01", None),
        "1990": ("1989-12-01", "2012-12-31"),
    }
    assert {
        key: (str(value["start"]), value["end"])
        for key, value in CRISIS_WINDOWS.items()
    } == expected
    assert CRISIS_WINDOWS["1990"]["mode_b_label"] == "1990 (일본 · 20년)"


def test_mode_a_shape_axes_defaults_and_resolution() -> None:
    root = _root()
    _fixtures(root)

    payload = build_crisis_timeline_payload(
        root, mode="A", country="US", index_choice="NASDAQ100",
    )

    assert payload["question"] == "주식이 크게 빠진 구간마다, 만기별 금리는 어떻게 움직였나?"
    assert payload["axis"] == {
        "left": "가격지수 · 로그", "left_scale": "logarithmic",
        "right": "국채 금리 (%)", "right_scale": "linear",
    }
    assert payload["resolution"] == "weekly_last"
    assert [line["label"] for line in payload["series"]] == [
        "NASDAQ100", "미국 2Y", "미국 10Y", "미국 30Y",
    ]
    assert [line["label"] for line in payload["series"] if line["default_visible"]] == [
        "NASDAQ100", "미국 10Y",
    ]
    assert len(payload["series"]) == 4
    assert payload["privacy"] == "시장 데이터만 사용 · 계좌·보유·개인 식별 데이터 없음"


def test_mode_b_normalizes_each_available_line_at_its_first_window_day() -> None:
    root = _root()
    _fixtures(root)

    payload = build_crisis_timeline_payload(
        root, mode="B", crisis="2008", index_choice="SP500",
    )

    assert payload["question"] == "같은 위기에 여러 나라는 각각 어떻게 움직였나?"
    assert payload["drawn_note"] == "이 창에 그려진 나라: 한국 · 미국 · 일본 · 유럽"
    assert payload["normalization_caption"] == "구간 시작 = 100"
    assert payload["data_kind_caption"] == "전부 가격지수 기준(배당 제외)"
    assert [line["symbol"] for line in payload["series"]] == [
        "KOSPI", "SP500", "NIKKEI225", "EURO_STOXX50",
    ]
    assert all(line["data"][0]["value"] == pytest.approx(100.0) for line in payload["series"])
    assert all("2007-10-01" <= line["data"][0]["time"] <= "2009-06-30" for line in payload["series"])


def test_mode_b_never_substitutes_dax_and_names_the_absent_country() -> None:
    """Vault decision 2026-09-05: a country without data is absent and said so; the DAX
    performance (total-return) index is never swapped in for EURO STOXX 50."""
    root = _root()
    _fixtures(root)

    payload = build_crisis_timeline_payload(
        root, mode="B", crisis="2000", index_choice="NASDAQ100",
    )

    assert [line["symbol"] for line in payload["series"]] == ["KOSPI", "NASDAQ100", "NIKKEI225", "EURO_STOXX50"]
    assert "DAX" not in {line["symbol"] for line in payload["series"]}
    euro = payload["series"][-1]
    assert euro["data"] == []
    assert euro["missing_reason"] == "EURO STOXX 50: 선택한 구간에 보존 데이터 없음 (retained from 2007-04-02)"
    assert payload["data_kind_caption"] == "전부 가격지수 기준(배당 제외)"
    assert payload["drawn_note"] == "이 창에 그려진 나라: 한국 · 미국 · 일본 (미포함: 유럽)"
    assert payload["legend_note"].startswith("이 창에 그려진 나라: 한국 · 미국 · 일본 (미포함: 유럽) · 전부 가격지수")


def test_mode_b_partial_line_says_where_it_starts() -> None:
    """A line that begins inside the window is drawn from its first day (=100) and the
    caption says the window start had no data — a series is never patched or swapped."""
    root = _root()
    _fixtures(root)
    from stock_web.api import crisis_timeline

    payload = build_crisis_timeline_payload(root, mode="B", crisis="1990", index_choice="SP500")
    euro = next(line for line in payload["series"] if line["symbol"] == "EURO_STOXX50")
    assert euro["data"][0]["time"] == "2007-04-02" and euro["data"][0]["value"] == pytest.approx(100.0)
    assert any(note.startswith("EURO STOXX 50: 2007-04-02부터만 표시 — 구간 시작 1989-12-01") for note in payload["missing_notes"])


def test_mode_b_refuses_to_mix_price_and_total_return_bases() -> None:
    from stock_web.api import crisis_timeline

    with pytest.raises(crisis_timeline.CrisisTimelineInputError, match="DAX=TOTAL_RETURN"):
        crisis_timeline._assert_one_price_basis(("KOSPI", "SP500", "DAX"))
    with pytest.raises(crisis_timeline.CrisisTimelineInputError, match="index_basis가 계약에 없어"):
        crisis_timeline._basis_for("MYSTERY_INDEX")
    assert crisis_timeline._assert_one_price_basis(("KOSPI", "NASDAQ100", "NIKKEI225", "EURO_STOXX50")) == "PRICE"


def test_mode_b_names_a_missing_line_and_its_retained_start() -> None:
    root = _root()
    _fixtures(root, include_nikkei=False)

    payload = build_crisis_timeline_payload(
        root, mode="B", crisis="2008", index_choice="NASDAQ100",
    )

    nikkei = next(line for line in payload["series"] if line["symbol"] == "NIKKEI225")
    assert nikkei["data"] == []
    assert nikkei["missing_reason"] == (
        "NIKKEI225: 선택한 구간에 보존 데이터 없음 (retained from 1985-01-02)"
    )
    assert nikkei["missing_reason"] in payload["missing_notes"]


def test_router_serves_timeline_to_guests_and_rejects_bad_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root()
    _fixtures(root)
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    client = ASGITestClient(create_app(root))

    page = client.get("/research", client_host="127.0.0.1")
    response = client.get(
        "/api/research/crisis-timeline?mode=B&crisis=2008&index_choice=NASDAQ100",
        client_host="127.0.0.1",
    )
    invalid = client.get(
        "/api/research/crisis-timeline?mode=C", client_host="127.0.0.1",
    )

    assert page.status_code == 200
    assert 'id="crisis-timeline"' in page.text
    assert response.status_code == 200 and response.json()["mode"] == "B"
    assert response.json()["privacy"].endswith("개인 식별 데이터 없음")
    assert invalid.status_code == 400


def test_payload_cache_invalidates_when_a_retained_input_changes() -> None:
    root = _root()
    _fixtures(root)
    first = build_crisis_timeline_payload(
        root, mode="A", country="US", crisis="2008", index_choice="NASDAQ100",
    )
    path = root / "data/normalized/global_index_price_daily/data.parquet"
    frame = pd.read_parquet(path)
    mask = frame["symbol"].eq("NASDAQ100") & frame["date"].eq(pd.Timestamp("2007-10-01"))
    frame.loc[mask, "close"] = 7777.0
    frame.to_parquet(path, index=False)

    second = build_crisis_timeline_payload(
        root, mode="A", country="US", crisis="2008", index_choice="NASDAQ100",
    )

    first_line = first["series"][0]["data"]
    second_line = second["series"][0]["data"]
    assert first_line[0]["value"] != second_line[0]["value"]
    assert second_line[0] == {"time": "2007-10-01", "value": 7777.0}
