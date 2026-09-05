"""Retained-data projections for the stock detail page.

The module is deliberately provider-free.  It reads the same local daily-price
datasets as the home chart and adds company, filing, dividend, and research
projections without promoting or rewriting any source data.
"""
from __future__ import annotations

from datetime import date
import math
from pathlib import Path
import re
import time
from typing import Any

import pandas as pd
import pyarrow.dataset as pads

from stock_data.gui.services import US_ETF_CHART_IDENTITIES
from stock_data.research.target_prices import TARGET_PRICE_CARD_TEXT, read_target_price_consensus
from stock_web.api import datasets as dsx
from stock_web.api.datasets import field
from stock_web.api.indicators import rsi_latest


DETAIL_CACHE_TTL_SECONDS = 60.0
_DETAIL_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, object]]] = {}
_KR_SYMBOL = re.compile(r"\d{6}")
_US_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-=]{0,19}")
_CASH_EVENT_TYPES = frozenset({"CASH_DIVIDEND", "현금배당", "CASH"})


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    result = str(value).strip()
    return result or None


def _date_text(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    parsed = pd.to_datetime(text, format="%Y%m%d" if re.fullmatch(r"\d{8}", text) else None, errors="coerce")
    return parsed.date().isoformat() if not pd.isna(parsed) else text


def _normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if not (_KR_SYMBOL.fullmatch(value) or _US_SYMBOL.fullmatch(value)):
        raise ValueError("종목 코드는 6자리 국내 코드 또는 지원되는 미국 티커여야 합니다.")
    return value


def _is_korean(symbol: str, market: str = "") -> bool:
    return bool(_KR_SYMBOL.fullmatch(symbol)) or market.strip().upper() in {"KR", "KRX", "KOSPI", "KOSDAQ"}


def _is_korean_etf(project_root: Path, symbol: str, identity: dict[str, object]) -> bool:
    security_type = str(identity.get("security_type") or "").upper()
    if "ETF" in security_type or "ETN" in security_type:
        return True
    frame = dsx.load(
        project_root,
        "data/normalized/kr_etf_master",
        filter_expr=(field("symbol") == symbol),
        partitioning=None,
    )
    return frame is not None and not frame.empty


def _investor_flows(
    project_root: Path, *, symbol: str, supported: bool,
) -> dict[str, object]:
    """Project a Korean stock's retained investor flow in raw won."""
    if not supported:
        return {"reason": "종목별 수급은 국내 주식만 보존"}
    root = project_root / "data/normalized/kr_equity_investor_flow_daily"
    if not root.is_dir():
        return {"reason": "종목별 수급 데이터 미보존"}
    required = [
        "date", "symbol", "foreign_net", "institution_net", "individual_net",
        "other_corp_net",
    ]
    try:
        dataset = pads.dataset(root, format="parquet", partitioning=None)
        columns = [*required]
        if "captured_at" in dataset.schema.names:
            columns.append("captured_at")
        frame = dataset.to_table(
            columns=columns,
            filter=pads.field("symbol") == symbol,
        ).to_pandas()
    except Exception:
        return {"reason": "종목별 수급 데이터를 읽을 수 없습니다."}
    if frame.empty or not set(required).issubset(frame.columns):
        return {"reason": "종목별 수급 데이터 미보존"}
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    value_columns = required[2:]
    for column in value_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", *value_columns])
    sort_columns = ["date"]
    if "captured_at" in frame.columns:
        frame["captured_at"] = pd.to_datetime(frame["captured_at"], utc=True, errors="coerce")
        sort_columns.append("captured_at")
    frame = (
        frame.sort_values(sort_columns, kind="stable")
        .drop_duplicates("date", keep="last")
        .tail(20)
        .reset_index(drop=True)
    )
    if frame.empty:
        return {"reason": "종목별 수급 데이터 미보존"}
    rows = [{
        "date": row["date"].date().isoformat(),
        "foreign_net": int(row["foreign_net"]),
        "institution_net": int(row["institution_net"]),
        "individual_net": int(row["individual_net"]),
        "other_corp_net": int(row["other_corp_net"]),
    } for _, row in frame.tail(10).iloc[::-1].iterrows()]
    return {
        "as_of": frame["date"].iloc[-1].date().isoformat(),
        "rows": rows,
        "cumulative": {
            "dates": [value.date().isoformat() for value in frame["date"]],
            "foreign": [int(value) for value in frame["foreign_net"].cumsum()],
            "institution": [int(value) for value in frame["institution_net"].cumsum()],
            "individual": [int(value) for value in frame["individual_net"].cumsum()],
        },
        "summary_20d": {
            "foreign": int(frame["foreign_net"].sum()),
            "institution": int(frame["institution_net"].sum()),
            "individual": int(frame["individual_net"].sum()),
        },
    }


def _load_ohlcv(project_root: Path, symbol: str) -> pd.DataFrame | None:
    """Copy the home chart's retained stock lookup without importing its private helper."""
    if _KR_SYMBOL.fullmatch(symbol):
        canonical = dsx.load(
            project_root,
            "data/normalized/kr_equity_price_daily",
            filter_expr=(field("symbol") == symbol),
        )
        provisional = dsx.load(
            project_root,
            "data/normalized/kr_equity_price_provisional_daily",
            filter_expr=(field("symbol") == symbol),
        )
        parts: list[pd.DataFrame] = []
        if canonical is not None and not canonical.empty:
            canonical = canonical.copy()
            canonical["provisional"] = False
            parts.append(canonical)
            if provisional is not None and not provisional.empty:
                latest = pd.to_datetime(canonical["date"], errors="coerce").max()
                provisional = provisional.loc[
                    pd.to_datetime(provisional["date"], errors="coerce") > latest
                ].copy()
        if provisional is not None and not provisional.empty:
            provisional = provisional.copy()
            provisional["provisional"] = True
            parts.append(provisional)
        if parts:
            frame = pd.concat(parts, ignore_index=True, sort=False)
        else:
            frame = dsx.load(
                project_root,
                "data/normalized/kr_etf_price_daily",
                filter_expr=(field("symbol") == symbol),
                partitioning=None,
            )
    else:
        from stock_web.api.symbol_resolver import global_equity_identity

        equity = global_equity_identity(symbol)
        frame = dsx.load(
            project_root,
            (
                "data/normalized/global_equity_price_daily"
                if equity is not None else "data/normalized/global_etf_price_daily"
            ),
            filter_expr=(field("symbol") == symbol),
            partitioning=None if equity is not None else "hive",
        )
    if frame is None or frame.empty or not {"date", "close"} <= set(frame.columns):
        return None
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result.dropna(subset=["date", "close"]).sort_values("date", kind="stable")
    result = result.drop_duplicates("date", keep="last").reset_index(drop=True)
    return result if not result.empty else None


def _master_row(project_root: Path, symbol: str, market: str) -> pd.Series | None:
    frame = dsx.load(
        project_root,
        "data/normalized/kr_equity_master",
        filter_expr=(field("symbol") == symbol),
        partitioning=None,
    )
    # A retained root can temporarily contain partitions written under two
    # compatible schema revisions.  Arrow's dataset-level schema follows the
    # first fragment, so recover optional v2 company fields from the matching
    # bounded fragment when they were not surfaced by that projection.
    if frame is not None and not frame.empty and not {"issued_shares", "par_value"} <= set(frame.columns):
        fragments = []
        for path in sorted((project_root / "data/normalized/kr_equity_master").rglob("*.parquet")):
            try:
                candidate = pd.read_parquet(path)
            except (OSError, ValueError):
                continue
            if "symbol" in candidate.columns:
                candidate = candidate.loc[candidate["symbol"].astype(str).eq(symbol)]
                if not candidate.empty:
                    fragments.append(candidate)
        if fragments:
            frame = pd.concat(fragments, ignore_index=True, sort=False)
    if frame is None or frame.empty:
        return None
    if market and "market" in frame.columns:
        exact = frame.loc[frame["market"].astype(str).str.upper().eq(market.upper())]
        if not exact.empty:
            frame = exact
    return frame.iloc[-1]


def _identity(project_root: Path, symbol: str, market: str) -> tuple[dict[str, object], pd.Series | None]:
    if _is_korean(symbol, market):
        row = _master_row(project_root, symbol, market)
        row_market = _text(row.get("market")) if row is not None else None
        security_type = _text(row.get("security_type_name")) if row is not None else None
        return ({
            "symbol": symbol,
            "name": (_text(row.get("name")) if row is not None else None) or symbol,
            "market": row_market or market or "KR",
            "security_type": security_type or "주식",
            "isin": _text(row.get("isin")) if row is not None else None,
            "currency": "KRW",
        }, row)
    from stock_web.api.symbol_resolver import global_equity_identity

    equity = global_equity_identity(symbol)
    if equity is not None:
        return ({
            "symbol": symbol,
            "name": equity["name"],
            "market": "US 주식",
            "security_type": equity["security_type"],
            "isin": None,
            "currency": equity["currency"],
            "exchange": equity["exchange"],
            "underlying_kr_symbol": equity.get("underlying_kr_symbol"),
            "underlying_url": (
                f"/stocks?symbol={equity['underlying_kr_symbol']}"
                if equity.get("underlying_kr_symbol") else None
            ),
        }, None)
    catalog = {item.symbol: item for item in US_ETF_CHART_IDENTITIES}
    item = catalog.get(symbol)
    return ({
        "symbol": symbol,
        "name": item.name if item is not None else symbol,
        "market": item.market if item is not None else (market or "US ETF"),
        "security_type": item.security_type if item is not None else "ETF",
        "isin": item.isin if item is not None else None,
        "currency": item.currency if item is not None else "USD",
    }, None)


def _price_projection(frame: pd.DataFrame | None) -> tuple[dict[str, object], dict[str, object]]:
    empty_headline = {
        "price_available": False, "price": None, "previous_close": None,
        "change": None, "change_pct": None, "as_of": None,
    }
    empty_stats = {
        "rsi14": None, "disp60_pct": None, "drawdown_pct": None,
        "ma20_pct": None, "volume20_multiple": None,
        "market_cap": None, "dividend_yield_pct": None,
    }
    if frame is None or frame.empty:
        return empty_headline, empty_stats
    close = frame["close"].astype(float)
    price = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) > 1 else None
    change = price - previous if previous is not None else None
    change_pct = change / previous * 100.0 if previous not in (None, 0) else None
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    high = float(close.tail(252).max()) if len(close) >= 20 else None
    volume_multiple = None
    if "volume" in frame.columns and len(frame) > 1:
        volume = pd.to_numeric(frame["volume"], errors="coerce")
        baseline = volume.iloc[-21:-1].dropna()
        current = _finite(volume.iloc[-1])
        if current is not None and len(baseline) and float(baseline.mean()) > 0:
            volume_multiple = current / float(baseline.mean())
    provisional = bool(frame.iloc[-1].get("provisional", False))
    headline = {
        "price_available": True,
        "price": price,
        "previous_close": previous,
        "change": change,
        "change_pct": change_pct,
        "as_of": frame.iloc[-1]["date"].date().isoformat(),
        "price_basis": "provisional" if provisional else "canonical",
    }
    stats = {
        **empty_stats,
        "rsi14": rsi_latest(close),
        "ma20_pct": (price / ma20 - 1.0) * 100.0 if ma20 else None,
        "disp60_pct": (price / ma60 - 1.0) * 100.0 if ma60 else None,
        "drawdown_pct": (price / high - 1.0) * 100.0 if high else None,
        "volume20_multiple": volume_multiple,
    }
    return headline, stats


def _price_display(value: object, market: object) -> str | None:
    numeric = _finite(value)
    if numeric is None:
        return None
    digits = 2 if str(market) in {"US ETF", "US 주식"} else 0
    return f"{numeric:,.{digits}f}"


def _company(identity: dict[str, object], row: pd.Series | None) -> dict[str, object]:
    if row is None:
        return {"available": False, "message": "국내 종목 기업정보만 보존되어 있습니다."}
    return {
        "available": True,
        "market": identity["market"],
        "security_type": identity["security_type"],
        "listing_date": _date_text(row.get("listing_date")),
        "issued_shares": _finite(row.get("issued_shares")),
        "par_value": _finite(row.get("par_value")),
        "isin": identity.get("isin"),
        "industry": None,
        "industry_message": "출처 확보 후 표시",
    }


def _fundamental_source(project_root: Path, symbol: str) -> pd.DataFrame | None:
    frame = dsx.load(
        project_root,
        "data/normalized/kr_fundamentals_quarterly",
        filter_expr=(field("symbol") == symbol),
        partitioning=None,
    )
    if frame is None or frame.empty:
        return None
    work = frame.copy()
    work["period_end"] = pd.to_datetime(work["period_end"], errors="coerce")
    work = work.dropna(subset=["period_end"])
    if work.empty:
        return None
    # Use the orchestration helper when the retained frame carries its complete
    # contract; minimal historical/test projections intentionally fall back to
    # the equivalent receipt/scope selection below.
    try:
        from stock_data.orchestration.kr_fundamentals_quarterly import latest_fundamental_rows

        helper_result = latest_fundamental_rows(work, date.today())
        if not helper_result.empty:
            work = helper_result
    except (KeyError, TypeError, ValueError, RuntimeError):
        pass
    work["rcept_no"] = work["rcept_no"].astype(str)
    scope_rank = work["fs_div"].astype(str).str.upper().map({"CFS": 0, "OFS": 1}).fillna(2)
    work = work.assign(_scope_rank=scope_rank)
    # Newest filing within each period/scope, then CFS before OFS.
    work = work.sort_values(["period_end", "_scope_rank", "rcept_no"], kind="stable")
    work = work.drop_duplicates(["period_end", "fs_div"], keep="last")
    work = work.sort_values(["period_end", "_scope_rank"], kind="stable")
    work = work.drop_duplicates("period_end", keep="first")
    return work.sort_values("period_end", kind="stable").tail(6).reset_index(drop=True)


def _quarter_label(row: pd.Series) -> str:
    report_quarter = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
    observed = pd.Timestamp(row["period_end"])
    year = int(row.get("bsns_year") or observed.year)
    quarter = report_quarter.get(str(row.get("reprt_code")), int(observed.quarter))
    return f"{year}년 {quarter}분기"


def _trend(values: list[float]) -> str:
    if len(values) < 4:
        return "확인 불가"
    values = values[-4:]
    up = [right > left for left, right in zip(values, values[1:])]
    down = [right < left for left, right in zip(values, values[1:])]
    if all(not value for value in up + down):
        return "보합"
    if not any(down):
        return "증가"
    if not any(up):
        return "감소"
    return "혼조"


def _fundamentals(project_root: Path, symbol: str, korean: bool) -> dict[str, object]:
    if not korean:
        return {"available": False, "message": "미국 ETF 재무 데이터 미보존"}
    frame = _fundamental_source(project_root, symbol)
    if frame is None or frame.empty:
        return {"available": False, "message": "OpenDART 미수집 · 수집 후 표시", "rows": []}
    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        revenue = _finite(row.get("revenue"))
        operating = _finite(row.get("operating_income"))
        net_income = _finite(row.get("net_income"))
        margin = operating / revenue * 100.0 if revenue not in (None, 0) and operating is not None else None
        rows.append({
            "quarter": _quarter_label(row),
            "period_end": _date_text(row["period_end"]),
            "fs_div": _text(row.get("fs_div")),
            "rcept_no": _text(row.get("rcept_no")),
            "revenue": revenue,
            "operating_income": operating,
            "net_income": net_income,
            "operating_margin_pct": margin,
            "debt_ratio_pct": _finite(row.get("debt_ratio_pct")),
            "sanity_check_required": bool(
                revenue is not None
                and (
                    (net_income is not None and net_income > revenue)
                    or (operating is not None and operating > revenue)
                )
            ),
        })
    recent = rows[-4:]
    op_values = [item["operating_income"] for item in recent]
    revenue_values = [item["revenue"] for item in recent]
    profitable = (
        all(value is not None and value > 0 for value in op_values)
        if len(recent) == 4 else None
    )
    trend = _trend([float(value) for value in revenue_values if value is not None])
    return {
        "available": True,
        "rows": list(reversed(rows)),
        "profitable_last_4q": profitable,
        "profitability_label": (
            "최근 4분기 모두 흑자" if profitable is True
            else "최근 4분기 적자 포함" if profitable is False
            else "최근 4분기 확인 불가"
        ),
        "revenue_trend": trend,
    }


def _next_quarter_end(observed: pd.Timestamp) -> str:
    return (observed.normalize() + pd.offsets.QuarterEnd()).date().isoformat()


def _dividends(
    project_root: Path, *, isin: str | None, korean: bool, price: float | None,
) -> dict[str, object]:
    if not korean:
        return {"available": False, "message": "배당 데이터 미보존"}
    if not isin:
        return {"available": False, "message": "배당 식별자 미수집", "rows": []}
    frame = dsx.load(
        project_root,
        "data/normalized/kr_equity_dividend",
        filter_expr=(field("isin") == isin),
        partitioning=None,
    )
    if frame is None or frame.empty:
        return {"available": False, "message": "현금배당 미수집 · 수집 후 표시", "rows": []}
    work = frame.copy()
    event = work.get("event_type", pd.Series("", index=work.index)).astype(str).str.upper()
    work = work.loc[event.isin(_CASH_EVENT_TYPES) | event.str.contains("현금", na=False)].copy()
    if work.empty:
        return {"available": False, "message": "현금배당 미수집 · 수집 후 표시", "rows": []}
    work["_record_date"] = work["dividend_record_date"].map(_date_text).map(
        lambda value: pd.to_datetime(value, errors="coerce")
    )
    work = work.dropna(subset=["_record_date"]).sort_values("_record_date", kind="stable").tail(4)
    rows = []
    for _, row in work.iloc[::-1].iterrows():
        rows.append({
            "dividend_record_date": _date_text(row.get("dividend_record_date")),
            "cash_payment_date": _date_text(row.get("cash_payment_date")),
            "category": _text(row.get("security_type")) or "현금배당",
            "ordinary_dividend_amount": _finite(row.get("ordinary_dividend_amount")),
        })
    amounts = [item["ordinary_dividend_amount"] for item in rows]
    trailing = sum(float(value) for value in amounts if value is not None)
    dividend_yield = trailing / price * 100.0 if price and trailing else None
    latest = rows[0]
    latest_record_date = str(latest["dividend_record_date"])
    if latest["cash_payment_date"] is None:
        next_event_label = "지급 예정"
        next_event_value = f"기준일 {latest_record_date}, 지급일 미공시"
        next_payment_label = f"{next_event_label} ({next_event_value})"
    else:
        next_event_label = "다음 기준일 (예상)"
        next_event_value = _next_quarter_end(pd.Timestamp(latest_record_date))
        next_payment_label = f"{next_event_label} {next_event_value}"
    return {
        "available": True,
        "rows": rows,
        "trailing_4q_sum": trailing,
        "dividend_yield_pct": dividend_yield,
        "next_event_label": next_event_label,
        "next_event_value": next_event_value,
        "next_payment_label": next_payment_label,
    }


def _target_price(
    project_root: Path, *, symbol: str, korean: bool, price: float | None,
) -> dict[str, object]:
    """Card payload from the retained consensus rows; the wording follows the row's status.

    Korean securities are collected through the same Yahoo path since 2026-09-05 (the
    former "국내 출처 없음" sentence was wrong), so ``korean`` only picks the fallback
    currency. Missing rows mean the collector has not run for this symbol — say so.
    """

    not_collected = {"available": False, "status": "NOT_COLLECTED",
                     "message": TARGET_PRICE_CARD_TEXT.get("NOT_COLLECTED", "미수집 · 수집기 미실행")}
    root = project_root / "data/normalized/research_target_price_consensus"
    try:
        frame = read_target_price_consensus(root)
    except (FileNotFoundError, OSError, ValueError):
        return not_collected
    rows = frame.loc[frame["symbol"].astype(str).str.upper().eq(symbol)].copy()
    if rows.empty:
        return not_collected
    rows["_date"] = pd.to_datetime(rows["date"], errors="coerce")
    if "retrieved_at" in rows.columns:
        rows["_retrieved"] = pd.to_datetime(rows["retrieved_at"], utc=True, errors="coerce")
        rows = rows.sort_values(["_date", "_retrieved"], kind="stable")
    else:
        rows = rows.sort_values("_date", kind="stable")
    row = rows.iloc[-1]
    status = _text(row.get("status")) if "status" in rows.columns else None
    mean = _finite(row.get("target_mean"))
    count = _finite(row.get("analyst_count"))
    if mean is None or count in (None, 0):
        if status and status != "AVAILABLE":
            return {
                "available": False, "status": status,
                "message": TARGET_PRICE_CARD_TEXT.get(status, status),
                "as_of": _date_text(row.get("date")),
            }
        return {"available": False, "status": "NO_COVERAGE",
                "message": TARGET_PRICE_CARD_TEXT.get("NO_COVERAGE", "커버리지 없음"),
                "as_of": _date_text(row.get("date"))}
    return {
        "available": True,
        "status": "AVAILABLE",
        "target_mean": mean,
        "analyst_count": int(count),
        "as_of": _date_text(row.get("date")),
        "upside_pct": (mean / price - 1.0) * 100.0 if price else None,
        "currency": _text(row.get("currency")) or ("KRW" if korean else "USD"),
        "source": _text(row.get("source")) or None,
    }


def _condition_matches(project_root: Path, metrics: dict[str, object]) -> list[dict[str, object]]:
    from stock_web.api.stocks_page import evaluate_conditions, load_conditions

    conditions = list(load_conditions(project_root).get("conditions", []))
    return evaluate_conditions(metrics, conditions, scope="watchlist")


def build_stock_detail_payload(
    project_root: Path, *, symbol: str, market: str = "", public_mode: bool = False,
) -> dict[str, object]:
    root = Path(project_root).resolve()
    normalized_symbol = _normalize_symbol(symbol)
    normalized_market = str(market or "").strip()
    cache_key = (str(root), normalized_symbol, normalized_market.upper(), bool(public_mode))
    cached = _DETAIL_CACHE.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < DETAIL_CACHE_TTL_SECONDS:
        return cached[1]

    identity, master = _identity(root, normalized_symbol, normalized_market)
    korean = _is_korean(normalized_symbol, str(identity["market"]))
    frame = _load_ohlcv(root, normalized_symbol)
    headline, stats = _price_projection(frame)
    headline["price_display"] = _price_display(headline.get("price"), identity["market"])
    history_sessions = 0 if frame is None else len(frame)
    disp60_reason = (
        "상장 60일 미만"
        if identity["market"] == "US 주식" and 0 < history_sessions < 60
        else (f"자료 {history_sessions}일치" if 0 < history_sessions < 60 else None)
    )
    stats["disp60_reason"] = disp60_reason
    stats["disp60_display"] = (
        f"{float(stats['disp60_pct']):+.1f}%"
        if stats.get("disp60_pct") is not None
        else (f"— ({disp60_reason})" if disp60_reason else "—")
    )
    company = _company(identity, master)
    issued_shares = company.get("issued_shares") if company.get("available") else None
    if headline["price"] is not None and issued_shares is not None:
        stats["market_cap"] = float(headline["price"]) * float(issued_shares)
    dividends = _dividends(
        root, isin=identity.get("isin"), korean=korean, price=headline.get("price"),
    )
    investor_flows = _investor_flows(
        root,
        symbol=normalized_symbol,
        supported=korean and not _is_korean_etf(root, normalized_symbol, identity),
    )
    stats["dividend_yield_pct"] = dividends.get("dividend_yield_pct")
    # Guest/public mode never reads the user's private watch-condition file (PUBLIC_MODE.md):
    # the stocks page already blanks conditions there; this route must too (audit 2026-09-05).
    conditions = [] if public_mode else _condition_matches(
        root, {**stats, "change_pct": headline.get("change_pct")},
    )
    as_of = headline.get("as_of")
    provisional = headline.get("price_basis") == "provisional"
    basis_label = f"{str(as_of)[5:]} 마감" if as_of else "마감 기준 없음"
    if provisional:
        basis_label += " · 잠정"
    payload = {
        "identity": identity,
        "headline": headline,
        "stats": stats,
        "company": company,
        "fundamentals": _fundamentals(root, normalized_symbol, korean),
        "dividends": dividends,
        "investor_flows": investor_flows,
        "target_price": _target_price(
            root, symbol=normalized_symbol, korean=korean, price=headline.get("price"),
        ),
        "basis": {
            "as_of": as_of,
            "price_basis": headline.get("price_basis"),
            "provisional": provisional,
            "label": basis_label,
        },
        "conditions": conditions,
    }
    _DETAIL_CACHE[cache_key] = (now, payload)
    return payload


def build_stock_sparklines(project_root: Path, *, symbols: str) -> dict[str, object]:
    requested = []
    for raw in str(symbols or "").split(","):
        if not raw.strip():
            continue
        symbol = _normalize_symbol(raw)
        if symbol not in requested:
            requested.append(symbol)
    if len(requested) > 200:
        raise ValueError("스파크라인은 한 번에 최대 200개 종목까지 요청할 수 있습니다.")
    result: dict[str, list[float]] = {}
    root = Path(project_root).resolve()
    for symbol in requested:
        frame = _load_ohlcv(root, symbol)
        result[symbol] = [] if frame is None else [float(value) for value in frame["close"].tail(30)]
    return {"sparklines": result}


__all__ = [
    "DETAIL_CACHE_TTL_SECONDS", "build_stock_detail_payload", "build_stock_sparklines",
]
