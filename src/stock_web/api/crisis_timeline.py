"""Read-only retained-data payloads for the Research crisis timeline."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.gui.query import LocalParquetQuery


class CrisisTimelineInputError(ValueError):
    """Raised when a timeline query does not name a supported view."""


# One authoritative crisis table feeds both API modes and every UI button.
CRISIS_WINDOWS: dict[str, dict[str, object]] = {
    "1987": {
        "start": "1987-08-01", "end": "1988-06-30",
        "mode_a_countries": ("US",), "mode_b_label": None,
    },
    "1997": {
        "start": "1997-06-01", "end": "1999-06-30",
        "mode_a_countries": ("KR",), "mode_b_label": "1997 (한국)",
    },
    "2000": {
        "start": "2000-03-01", "end": "2003-03-31",
        "mode_a_countries": ("US", "KR"), "mode_b_label": "2000 (동시)",
    },
    "2008": {
        "start": "2007-10-01", "end": "2009-06-30",
        "mode_a_countries": ("US", "KR"), "mode_b_label": "2008 (동시)",
    },
    "2011": {
        "start": "2011-04-01", "end": "2012-09-30",
        "mode_a_countries": (), "mode_b_label": "2011 (유럽)",
    },
    "2020": {
        "start": "2020-02-01", "end": "2020-08-31",
        "mode_a_countries": ("US", "KR"), "mode_b_label": "2020 (동시)",
    },
    "2022": {
        "start": "2022-01-01", "end": "2022-12-31",
        "mode_a_countries": ("US", "KR"), "mode_b_label": "2022 (동시)",
    },
    "2025": {
        "start": "2025-01-01", "end": None,
        "mode_a_countries": ("US", "KR"), "mode_b_label": None,
    },
    "1990": {
        "start": "1989-12-01", "end": "2012-12-31",
        "mode_a_countries": (), "mode_b_label": "1990 (일본 · 20년)",
        "duration_note": "일본 1990 위기는 1989-12-01부터 2012-12-31까지 20년 이상을 봅니다.",
    },
}

MODE_A_ORDER = {
    "US": ("1987", "2000", "2008", "2020", "2022", "2025"),
    "KR": ("1997", "2000", "2008", "2020", "2022", "2025"),
}
MODE_B_ORDER = ("2000", "2008", "2020", "2022", "1997", "2011", "1990")

INDEX_DEFINITIONS: dict[str, dict[str, str]] = {
    "KOSPI": {
        "label": "KOSPI", "index_kind": "price", "retained_from": "1975-01-04",
    },
    "NASDAQ100": {
        "label": "NASDAQ100", "index_kind": "price", "retained_from": "1985-10-01",
    },
    "SP500": {
        "label": "S&P 500", "index_kind": "price", "retained_from": "1928-01-03",
    },
    "NIKKEI225": {
        "label": "NIKKEI225", "index_kind": "price", "retained_from": "1985-01-02",
    },
    "EURO_STOXX50": {
        "label": "EURO STOXX 50", "index_kind": "price", "retained_from": "2007-04-02",
    },
    # One European index for every window (vault 2026-09-05: switching indices per window is a
    # series change). CAC 40 is a PRICE index retained from 1990-03-01 (STOXX Europe 600 on Yahoo
    # starts 2004-04-26); the legend carries the real name, never just "유럽".
    "CAC40": {
        "label": "CAC 40 (프랑스)", "index_kind": "price", "retained_from": "1990-03-01",
    },
    "DAX": {
        "label": "DAX", "index_kind": "total_return", "retained_from": "1987-12-30",
        "kind_disclosure": "DAX는 총수익지수(배당 포함) — 다른 지수와 기준이 다름",
    },
}

_US_YIELDS = (
    ("dgs2", "미국 2Y", "1976-06-01", False),
    ("dgs10", "미국 10Y", "1962-01-02", True),
    ("dgs30", "미국 30Y", "1977-02-15", False),
)
_KR_YIELDS = (
    ("3Y", "한국 3Y", "1998-11-13", False),
    ("10Y", "한국 10Y", "2000-12-18", True),
)
# 1997 외환위기 has no 국고채 line (3Y starts 1998-11). The representative market rate then was
# the 3-year AA− corporate bond; the call rate shows the liquidity squeeze. Separate series,
# separate lines, default off — never spliced into the treasury lines (vault 2026-09-05).
_KR_MARKET_RATES = (
    ("CORP_BOND_3Y_AA_MINUS", "kr_corp_bond_3y", "회사채 3년(AA−)", "1995-01-03"),
    ("CALL_RATE_OVERNIGHT", "kr_call_rate", "콜금리 1일", "1995-01-03"),
)

_CACHE_LOCK = Lock()
_PAYLOAD_CACHE: dict[
    tuple[str, str, str, str, str],
    tuple[tuple[Path, ...], tuple[tuple[Path, int, int], ...], dict[str, object]],
] = {}


def _today_kst() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


def _input_signature(
    paths: tuple[Path, ...],
) -> tuple[tuple[Path, int, int], ...] | None:
    """Return the house mtime/size signature for every retained input."""

    signature: list[tuple[Path, int, int]] = []
    try:
        for path in paths:
            stat = path.stat()
            signature.append((path, stat.st_mtime_ns, stat.st_size))
    except OSError:
        return None
    return tuple(signature)


def _watched_paths(query: LocalParquetQuery) -> tuple[Path, ...]:
    watched: list[Path] = []
    for path in dict.fromkeys(Path(item) for item in query.files_read):
        watched.extend((path, path.parent, path.parent.parent))
    return tuple(dict.fromkeys(watched))


def _window(crisis: str, today: str) -> tuple[str, str]:
    item = CRISIS_WINDOWS[crisis]
    return str(item["start"]), str(item["end"] or today)


HOLDOUT_START = "2016-01-01"
HOLDOUT_NOTE = "홀드아웃 구간 — 신호 설계 중 참고 금지"


def _window_rows(ids: tuple[str, ...], today: str, *, mode: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for crisis_id in ids:
        item = CRISIS_WINDOWS[crisis_id]
        start, end = _window(crisis_id, today)
        rows.append({
            "id": crisis_id,
            "label": (
                str(item["mode_b_label"]) if mode == "B"
                else ("2025~" if crisis_id == "2025" else crisis_id)
            ),
            "start": start,
            "end": end,
            "duration_note": item.get("duration_note"),
            # Windows inside the research hold-out (2016-01-01~) are marked, not blocked and not
            # counted: looking at the market shape here while designing a signal is fitting the
            # hold-out (vault 8b60835). The screen itself reads market data only.
            "holdout_note": HOLDOUT_NOTE if start >= HOLDOUT_START else None,
        })
    return rows


def _series_frame(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
    if frame.empty or not {"date", value_column} <= set(frame.columns):
        return pd.DataFrame(columns=["date", "value"])
    work = frame[["date", value_column]].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["value"] = pd.to_numeric(work[value_column], errors="coerce")
    work = work.dropna(subset=["date", "value"])
    work = work[work["value"] > 0]
    return (
        work.sort_values("date", kind="stable")
        .drop_duplicates("date", keep="last")[["date", "value"]]
        .reset_index(drop=True)
    )


def _slice(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if frame.empty or (start is None and end is None):
        return frame.copy()
    result = frame
    if start is not None:
        result = result[result["date"] >= pd.Timestamp(start)]
    if end is not None:
        result = result[result["date"] <= pd.Timestamp(end)]
    return result.reset_index(drop=True)


def _weekly_last(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    weekly = (
        frame.set_index("date")["value"]
        .resample("W-FRI").last().dropna().reset_index()
    )
    if weekly.empty or weekly.iloc[0]["date"] != frame.iloc[0]["date"]:
        weekly = pd.concat([frame.iloc[[0]], weekly], ignore_index=True)
    if weekly.iloc[-1]["date"] != frame.iloc[-1]["date"]:
        weekly = pd.concat([weekly, frame.iloc[[-1]]], ignore_index=True)
    return weekly.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _points(frame: pd.DataFrame, *, normalize: bool = False) -> list[dict[str, object]]:
    if frame.empty:
        return []
    factor = 100.0 / float(frame.iloc[0]["value"]) if normalize else 1.0
    return [
        {"time": row.date.date().isoformat(), "value": round(float(row.value) * factor, 8)}
        for row in frame.itertuples(index=False)
    ]


def _missing_reason(label: str, retained_from: str) -> str:
    return f"{label}: 선택한 구간에 보존 데이터 없음 (retained from {retained_from})"


# Price-index basis is decided by the data contract, not by this module (vault + review
# 2026-09-05: "규칙을 문서에 적는 것과 데이터 구조에 박는 것은 다르다"). KOSPI comes from
# kr_index_daily (a KRX price index) and is declared here; every other equity line must be in
# GLOBAL_INDEX_REGISTRY, otherwise it is refused rather than silently passed.
_LOCAL_INDEX_BASIS = {"KOSPI": "PRICE"}
_COUNTRY_BY_SYMBOL = {
    "KOSPI": "한국", "NASDAQ100": "미국", "SP500": "미국", "NIKKEI225": "일본",
    "EURO_STOXX50": "유럽", "CAC40": "프랑스(유럽)", "DAX": "유럽(독일)",
}


def _basis_for(symbol: str) -> str:
    if symbol in _LOCAL_INDEX_BASIS:
        return _LOCAL_INDEX_BASIS[symbol]
    from stock_data.contracts.global_market import index_basis

    try:
        return index_basis(symbol)
    except KeyError as error:
        raise CrisisTimelineInputError(
            f"주가 계열 {symbol}의 index_basis가 계약에 없어 그릴 수 없습니다"
        ) from error


def _assert_one_price_basis(symbols: tuple[str, ...]) -> str:
    bases = {_basis_for(symbol) for symbol in symbols}
    return_bases = {basis for basis in bases if basis in {"PRICE", "TOTAL_RETURN"}}
    if len(return_bases) > 1:
        raise CrisisTimelineInputError(
            "가격지수와 총수익지수를 한 정규화 축에 섞을 수 없습니다: "
            + ", ".join(f"{symbol}={_basis_for(symbol)}" for symbol in symbols)
        )
    return next(iter(return_bases)) if return_bases else "NOT_APPLICABLE"


def _data_kind_caption(lines: list[dict[str, object]]) -> str:
    selected = [item for item in lines if item["data"]]
    if not selected:
        return "이 구간에 그릴 수 있는 지수가 없음"
    bases = {_basis_for(str(item["symbol"])) for item in selected}
    if bases == {"PRICE"}:
        return "전부 가격지수 기준(배당 제외)"
    total = [str(item["label"]) for item in selected if _basis_for(str(item["symbol"])) == "TOTAL_RETURN"]
    if total:
        return " · ".join(f"{label}는 총수익지수(배당 포함) — 다른 지수와 기준이 다름" for label in total)
    return "지수 기준을 확인할 수 없음"


def _starts_late(first_time: str, window_start: str, *, tolerance_days: int = 10) -> bool:
    """True when a line's first observation is well past the window start (holidays such as
    2000-03-01 삼일절 shift the first KOSPI print by a day; that is not a data gap)."""
    from datetime import date

    return (date.fromisoformat(first_time) - date.fromisoformat(window_start)).days > tolerance_days


def _drawn_countries_note(lines: list[dict[str, object]], requested: tuple[str, ...]) -> str:
    drawn = [
        _COUNTRY_BY_SYMBOL.get(str(item["symbol"]), str(item["symbol"]))
        for item in lines if item["data"]
    ]
    absent = [
        _COUNTRY_BY_SYMBOL.get(symbol, symbol) for symbol in requested
        if not any(str(item["symbol"]) == symbol and item["data"] for item in lines)
    ]
    note = "이 창에 데이터가 있는 나라: " + (" · ".join(dict.fromkeys(drawn)) or "없음")
    if absent:
        note += " (미포함: " + " · ".join(dict.fromkeys(absent)) + ")"
    return note


def _index_frame(
    query: LocalParquetQuery, symbol: str, *, start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    definition = INDEX_DEFINITIONS[symbol]
    if symbol == "KOSPI":
        raw = query.read(
            "normalized/kr_index_daily", columns=["date", "market", "close"],
            filters={"market": ("KOSPI",)}, start=start, end=end,
        )
    else:
        raw = query.read(
            "normalized/global_index_price_daily",
            columns=["date", "symbol", "close"], filters={"symbol": (symbol,)},
            start=start, end=end,
        )
    result = _series_frame(raw, "close")
    result.attrs.update(definition)
    return result


def _kr_yield_frames(
    query: LocalParquetQuery, *, start: str | None = None, end: str | None = None,
) -> dict[str, pd.DataFrame]:
    bok = query.read(
        "normalized/bok_ecos_kr_treasury_yield_source_observation",
        columns=["date", "tenor", "yield_percent"], filters={"tenor": ("3Y", "10Y")},
        start=start, end=end,
    )
    toss = query.read(
        "normalized/kr_treasury_yield_daily",
        columns=["date", "instrument", "close"],
        filters={"instrument": ("KR_BOND_3Y", "KR_BOND_10Y")},
        start=start, end=end,
    )
    results: dict[str, pd.DataFrame] = {}
    for tenor, _label, _retained, _default in _KR_YIELDS:
        bok_part = bok[bok.get("tenor", pd.Series(dtype=str)).astype(str).eq(tenor)] if not bok.empty else bok
        bok_frame = _series_frame(bok_part, "yield_percent")
        instrument = f"KR_BOND_{tenor}"
        toss_part = toss[toss.get("instrument", pd.Series(dtype=str)).astype(str).eq(instrument)] if not toss.empty else toss
        toss_frame = _series_frame(toss_part, "close")
        if bok_frame.empty:
            combined = toss_frame
        elif toss_frame.empty:
            combined = bok_frame
        else:
            # Preserve the official BOK history; extend only beyond its last retained date.
            combined = pd.concat([
                bok_frame,
                toss_frame[toss_frame["date"] > bok_frame.iloc[-1]["date"]],
            ], ignore_index=True)
        results[tenor] = combined.sort_values("date").reset_index(drop=True)
    return results


def _line(
    *, series_id: str, label: str, symbol: str, index_kind: str | None,
    axis: str, default_visible: bool, frame: pd.DataFrame, retained_from: str,
    normalize: bool = False, source: str, window_start: str | None = None,
) -> tuple[dict[str, object], str | None]:
    missing = _missing_reason(label, retained_from) if frame.empty else None
    partial_note: str | None = None
    legend_suffix: str | None = None
    if not frame.empty and window_start is not None:
        first_time = pd.Timestamp(frame.iloc[0]["date"]).date().isoformat()
        if _starts_late(first_time, window_start):
            partial_note = (
                f"{label}: {first_time}부터만 표시 — 구간 시작 {window_start}에는 "
                f"보존 데이터 없음(retained from {retained_from})"
            )
            if normalize:
                partial_note += " · 자기 첫 관측일 = 100"
                legend_suffix = f"({first_time} = 100)"
    return ({
        "id": series_id,
        "label": label,
        "symbol": symbol,
        "measure": "price_index" if index_kind else "yield_percent",
        "index_kind": index_kind,
        "axis": axis,
        "unit": "구간 시작 = 100" if normalize else ("%" if axis == "right" else "index"),
        "default_visible": default_visible,
        "retained_from": retained_from,
        "source": source,
        "missing_reason": missing,
        "partial_note": partial_note,
        "legend_suffix": legend_suffix,
        "data": _points(frame, normalize=normalize),
    }, missing or partial_note)


def _mode_a(
    query: LocalParquetQuery, *, country: str, crisis: str | None,
    index_choice: str, today: str,
) -> dict[str, object]:
    window_ids = MODE_A_ORDER[country]
    if crisis is not None and crisis not in window_ids:
        raise CrisisTimelineInputError(f"{country}에서 지원하지 않는 위기: {crisis}")
    start, end = _window(crisis, today) if crisis else (None, None)
    index_symbol = index_choice if country == "US" else "KOSPI"
    index_frame = _index_frame(query, index_symbol, start=start, end=end)
    frames: list[tuple[dict[str, object], str | None]] = []
    definition = INDEX_DEFINITIONS[index_symbol]
    frames.append(_line(
        series_id=index_symbol.lower(), label=definition["label"], symbol=index_symbol,
        index_kind=definition["index_kind"], axis="left", default_visible=True,
        frame=index_frame, retained_from=definition["retained_from"], normalize=False,
        source="retained normalized price index", window_start=start,
    ))
    if country == "US":
        raw = query.read(
            "normalized/fred_treasury_yield_daily",
            columns=["date", "dgs2", "dgs10", "dgs30"], start=start, end=end,
        )
        for column, label, retained_from, default_visible in _US_YIELDS:
            frames.append(_line(
                series_id=column, label=label, symbol=column.upper(), index_kind=None,
                axis="right", default_visible=default_visible,
                frame=_series_frame(raw, column), retained_from=retained_from,
                normalize=False, source="FRED", window_start=start,
            ))
    else:
        kr_yields = _kr_yield_frames(query, start=start, end=end)
        for tenor, label, retained_from, default_visible in _KR_YIELDS:
            frames.append(_line(
                series_id=f"kr{tenor.lower()}", label=label, symbol=f"KR_{tenor}",
                index_kind=None, axis="right", default_visible=default_visible,
                frame=kr_yields[tenor],
                retained_from=retained_from, normalize=False,
                source="BOK ECOS · BOK 마지막 관측 이후 Toss 연장", window_start=start,
            ))
        market_rates = query.read(
            "normalized/bok_ecos_kr_market_rate_daily",
            columns=["date", "series", "rate_percent"], start=start, end=end,
        )
        for series_key, series_id, label, retained_from in _KR_MARKET_RATES:
            part = (
                market_rates[market_rates["series"].astype(str).eq(series_key)]
                if not market_rates.empty and "series" in market_rates else market_rates
            )
            frames.append(_line(
                series_id=series_id, label=label, symbol=series_key, index_kind=None,
                axis="right", default_visible=False, frame=_series_frame(part, "rate_percent"),
                retained_from=retained_from, normalize=False,
                source="BOK ECOS 817Y002 · 국고채와 별도 계열(이어 붙이지 않음)", window_start=start,
            ))
    series = [item[0] for item in frames]
    missing = [item[1] for item in frames if item[1]]
    all_dates = [point["time"] for item in series for point in item["data"]]
    if crisis is None and all_dates:
        span_years = (pd.Timestamp(max(all_dates)) - pd.Timestamp(min(all_dates))).days / 365.25
        if span_years > 15:
            for item in series:
                data = pd.DataFrame(item["data"])
                if data.empty:
                    continue
                data = data.rename(columns={"time": "date"})
                data["date"] = pd.to_datetime(data["date"])
                item["data"] = _points(_weekly_last(data[["date", "value"]]))
            resolution = "weekly_last"
        else:
            resolution = "daily"
    else:
        resolution = "daily"
    all_dates = [point["time"] for item in series for point in item["data"]]
    return {
        "mode": "A",
        "question": "주식이 크게 빠진 구간마다, 만기별 금리는 어떻게 움직였나?",
        "country": country,
        "index_choice": index_symbol,
        "selected_crisis": crisis or "ALL",
        "windows": _window_rows(window_ids, today, mode="A"),
        "selected_window": ({"id": crisis, "start": start, "end": end, "holdout_note": (HOLDOUT_NOTE if start >= HOLDOUT_START else None)} if crisis else None),
        "axis": {"left": "가격지수 · 로그", "left_scale": "logarithmic", "right": "국채 금리 (%)", "right_scale": "linear"},
        "normalization_caption": "원지수 · 금리(%)",
        "data_kind_caption": "가격지수(배당 미포함)와 국채 금리(%)를 서로 다른 축에 표시",
        "legend_note": "가격지수(좌축 · 로그) · 국채 금리(우축 · %)",
        "resolution": resolution,
        "resolution_caption": (
            "전체 보존 이력은 payload를 줄이기 위해 주별 마지막 관측값으로 표시합니다. 위기 버튼은 일별 관측값을 불러옵니다."
            if resolution == "weekly_last" else "선택한 위기 구간은 일별 관측값으로 표시합니다."
        ),
        "date_range": {"start": min(all_dates) if all_dates else start, "end": max(all_dates) if all_dates else end},
        "series": series,
        "missing_notes": missing,
    }


def _mode_b(
    query: LocalParquetQuery, *, crisis: str, index_choice: str, today: str,
) -> dict[str, object]:
    if crisis not in MODE_B_ORDER:
        raise CrisisTimelineInputError(f"Mode B에서 지원하지 않는 위기: {crisis}")
    start, end = _window(crisis, today)
    requested = ("KOSPI", index_choice, "NIKKEI225", "CAC40")
    # One basis for the whole normalised axis (refuses e.g. a total-return DAX).
    _assert_one_price_basis(requested)
    prepared: list[tuple[str, pd.DataFrame]] = [
        (symbol, _index_frame(query, symbol, start=start, end=end)) for symbol in requested
    ]
    # No substitution when a country lacks data (vault decision 2026-09-05: DAX is a
    # total-return index and swapping series inside one line is a series change). The line
    # is simply absent or starts late, and the caption says so.
    missing_notes: list[str] = []

    lines: list[dict[str, object]] = []
    for symbol, frame in prepared:
        definition = INDEX_DEFINITIONS[symbol]
        line, missing = _line(
            series_id=symbol.lower(), label=definition["label"], symbol=symbol,
            index_kind=definition["index_kind"], axis="left", default_visible=True,
            frame=frame, retained_from=definition["retained_from"], normalize=True,
            source="retained normalized price index", window_start=start,
        )
        lines.append(line)
        if missing:
            missing_notes.append(missing)
    data_kind_caption = _data_kind_caption(lines)
    drawn_note = _drawn_countries_note(lines, requested)
    selected = CRISIS_WINDOWS[crisis]
    all_dates = [point["time"] for item in lines for point in item["data"]]
    return {
        "mode": "B",
        "question": "같은 위기에 여러 나라는 각각 어떻게 움직였나?",
        "country": None,
        "drawn_note": drawn_note,
        "index_choice": index_choice,
        "selected_crisis": crisis,
        "windows": _window_rows(MODE_B_ORDER, today, mode="B"),
        "selected_window": {"id": crisis, "start": start, "end": end, "holdout_note": HOLDOUT_NOTE if start >= HOLDOUT_START else None},
        "axis": {"left": "구간 시작 = 100", "left_scale": "linear", "right": None, "right_scale": None},
        "normalization_caption": "구간 시작 = 100",
        "data_kind_caption": data_kind_caption,
        "legend_note": f"{drawn_note} · {data_kind_caption}",
        "duration_note": selected.get("duration_note"),
        "resolution": "daily",
        "resolution_caption": "실제 날짜 기준 · 선택한 위기 구간의 일별 관측값",
        "date_range": {"start": min(all_dates) if all_dates else start, "end": max(all_dates) if all_dates else end},
        "series": lines,
        "missing_notes": list(dict.fromkeys(missing_notes)),
    }


def build_crisis_timeline_payload(
    project_root: Path, *, mode: str = "A", country: str = "US",
    crisis: str | None = None, index_choice: str = "NASDAQ100",
) -> dict[str, object]:
    """Build a cached, account-independent timeline from retained Parquet data."""

    root = Path(project_root).resolve()
    normalized_mode = str(mode or "A").strip().upper()
    normalized_country = str(country or "US").strip().upper()
    normalized_index = str(index_choice or "NASDAQ100").strip().upper()
    normalized_crisis = str(crisis or "").strip().replace("~", "") or None
    if normalized_mode not in {"A", "B"}:
        raise CrisisTimelineInputError("mode는 A 또는 B여야 합니다.")
    if normalized_country not in MODE_A_ORDER:
        raise CrisisTimelineInputError("country는 US 또는 KR이어야 합니다.")
    if normalized_index not in {"NASDAQ100", "SP500"}:
        raise CrisisTimelineInputError("index_choice는 NASDAQ100 또는 SP500이어야 합니다.")
    if normalized_mode == "B" and normalized_crisis is None:
        normalized_crisis = "2008"
    today = _today_kst()
    cache_key = (
        str(root), normalized_mode, normalized_country,
        normalized_crisis or "ALL", normalized_index + ":" + today,
    )
    with _CACHE_LOCK:
        cached = _PAYLOAD_CACHE.get(cache_key)
        if cached is not None:
            paths, signature, payload = cached
            if _input_signature(paths) == signature:
                return deepcopy(payload)
            _PAYLOAD_CACHE.pop(cache_key, None)

    query = LocalParquetQuery(root / "data")
    payload = (
        _mode_a(
            query, country=normalized_country, crisis=normalized_crisis,
            index_choice=normalized_index, today=today,
        )
        if normalized_mode == "A" else
        _mode_b(query, crisis=normalized_crisis or "2008", index_choice=normalized_index, today=today)
    )
    payload.update({
        "schema_version": 1,
        "privacy": "시장 데이터만 사용 · 계좌·보유·개인 식별 데이터 없음",
    })
    paths = _watched_paths(query)
    signature = _input_signature(paths)
    if paths and signature is not None:
        with _CACHE_LOCK:
            _PAYLOAD_CACHE[cache_key] = (paths, signature, deepcopy(payload))
    return payload
