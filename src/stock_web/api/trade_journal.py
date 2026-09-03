"""Snapshot-derived, explicitly estimated trade journal for the local account UI.

Landing account snapshots are immutable inputs.  This module never calls a
provider and only writes the local derivation cache or explicit manual entries
under ``artifacts/local_user``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Iterable, Mapping
from uuid import uuid4

import pandas as pd

from stock_web.api import datasets
from stock_web.api.account_page import (
    AccountInputError,
    _atomic_json_replace,
    load_cash_flows,
)


TOSS_LANDING = Path("data/landing/tossinvest/account_snapshot")
KB_LANDING = Path("data/landing/kbsec/account_snapshot")
CACHE_PATH = Path("artifacts/local_user/trade_journal_cache.json")
MANUAL_PATH = Path("artifacts/local_user/trade_journal_manual.json")

_CACHE_SCHEMA_VERSION = 1
_MANUAL_SCHEMA_VERSION = 1
_KST = timezone(timedelta(hours=9))
_QUANTITY_QUANTUM = Decimal("0.000001")
_MANUAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SYMBOL = re.compile(r"[A-Za-z0-9.^_-]{1,24}\Z")
_ACCOUNT_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){9,13}\d(?!\d)")
_SOURCE_LABELS = {"toss_self": "Toss", "kb_self": "KB"}
_SOURCE_ALIASES = {
    "toss_self": {"toss", "토스", "토스증권"},
    "kb_self": {"kb", "kb증권", "케이비", "케이비증권"},
}


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _number(value: Decimal | None, *, quantity: bool = False) -> float | None:
    if value is None:
        return None
    if quantity:
        value = value.quantize(_QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)
    return float(value)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for pattern in ("%Y%m%dT%H%M%S%z", "%Y%m%d-%H%M%S%z"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_from_name(path: Path) -> datetime | None:
    match = re.search(r"(20\d{6}[T_-]?\d{6}Z?)", path.name)
    if match is None:
        return None
    compact = re.sub(r"[_-]", "T", match.group(1))
    if "T" not in compact:
        compact = f"{compact[:8]}T{compact[8:]}"
    return _parse_timestamp(compact)


def _position_map(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        return {}
    accumulated: dict[str, dict[str, object]] = {}
    for raw in raw_positions:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        quantity = _decimal(raw.get("quantity"))
        if not symbol or quantity is None or quantity <= 0:
            continue
        currency = str(raw.get("currency") or "KRW").strip().upper()
        if currency not in {"KRW", "USD"}:
            continue
        average = _decimal(raw.get("average_purchase_price"))
        if average is None:
            average = _decimal(raw.get("average_cost"))
        last = _decimal(raw.get("last_price"))
        if last is None:
            last = _decimal(raw.get("current_price"))
        current = accumulated.setdefault(symbol, {
            "symbol": symbol,
            "name": str(raw.get("name") or symbol).strip()[:80],
            "currency": currency,
            "market_country": str(raw.get("market_country") or "").strip().upper(),
            "quantity": Decimal("0"),
            "cost": Decimal("0"),
            "cost_quantity": Decimal("0"),
            "last_price": None,
        })
        current["quantity"] = Decimal(str(current["quantity"])) + quantity
        if average is not None and average >= 0:
            current["cost"] = Decimal(str(current["cost"])) + average * quantity
            current["cost_quantity"] = Decimal(str(current["cost_quantity"])) + quantity
        if last is not None and last >= 0:
            current["last_price"] = last
        if not current["name"] and raw.get("name"):
            current["name"] = str(raw["name"]).strip()[:80]
    result: dict[str, dict[str, object]] = {}
    for symbol, item in accumulated.items():
        quantity = Decimal(str(item["quantity"]))
        cost_quantity = Decimal(str(item["cost_quantity"]))
        average = (
            Decimal(str(item["cost"])) / cost_quantity
            if cost_quantity == quantity and quantity > 0 else None
        )
        result[symbol] = {
            "symbol": symbol,
            "name": item["name"],
            "currency": item["currency"],
            "market_country": item["market_country"],
            "quantity": _number(quantity, quantity=True),
            "average_price": _number(average),
            "last_price": _number(item["last_price"]),
        }
    return result


def _cash_map(payload: Mapping[str, object], source: str) -> dict[str, float]:
    result: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    if source == "toss_self":
        rows = payload.get("buying_power")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                currency = str(row.get("currency") or "KRW").upper()
                amount = _decimal(row.get("cash_buying_power"))
                if currency in {"KRW", "USD"} and amount is not None:
                    result[currency] += amount
    else:
        raw_cash = payload.get("cash_balance")
        if isinstance(raw_cash, list):
            for row in raw_cash:
                if isinstance(row, Mapping):
                    currency = str(row.get("currency") or "KRW").upper()
                    amount = _decimal(row.get("cash_balance") or row.get("amount"))
                    if currency in {"KRW", "USD"} and amount is not None:
                        result[currency] += amount
        elif isinstance(raw_cash, Mapping):
            for raw_currency, raw_amount in raw_cash.items():
                currency = str(raw_currency).upper()
                amount = _decimal(raw_amount)
                if currency in {"KRW", "USD"} and amount is not None:
                    result[currency] += amount
        else:
            amount = _decimal(raw_cash)
            if amount is not None:
                result["KRW"] = amount
    return {currency: float(amount) for currency, amount in result.items()}


def _load_daily_snapshots(
    project_root: Path, relative: Path, source: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    root = project_root / relative
    newest: dict[str, tuple[datetime, dict[str, object]]] = {}
    issues: list[dict[str, object]] = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            issues.append({
                "source": source, "type": "unreadable_snapshot",
                "file": path.name, "reason": "스냅샷 JSON을 읽을 수 없어 제외했습니다.",
            })
            continue
        if not isinstance(payload, Mapping):
            continue
        # Landing captures wrap the provider body: {capture_kind, payload_sha256, schema_version,
        # snapshot: {...positions, collected_at...}} (verified live 2026-09-03 for Toss and KB).
        if isinstance(payload.get("snapshot"), Mapping) and "positions" not in payload:
            payload = payload["snapshot"]
        observed = _parse_timestamp(payload.get("collected_at")) or _timestamp_from_name(path)
        if observed is None:
            issues.append({
                "source": source, "type": "undated_snapshot",
                "file": path.name, "reason": "수집 시각을 확인할 수 없어 제외했습니다.",
            })
            continue
        kst_date = observed.astimezone(_KST).date().isoformat()
        snapshot = {
            "date": kst_date,
            "collected_at": observed.isoformat(),
            "positions": _position_map(payload),
            "cash": _cash_map(payload, source),
        }
        current = newest.get(kst_date)
        if current is None or observed > current[0]:
            newest[kst_date] = (observed, snapshot)
    return [newest[key][1] for key in sorted(newest)], issues


def _event_id(source: str, day: str, symbol: str, side: str) -> str:
    digest = hashlib.sha256(f"{source}|{day}|{symbol}|{side}".encode()).hexdigest()[:20]
    return f"derived_{digest}"


def _trade_event(
    source: str, previous: Mapping[str, object], current: Mapping[str, object],
    symbol: str,
) -> dict[str, object] | None:
    before = (previous["positions"] if isinstance(previous.get("positions"), Mapping) else {}).get(symbol)
    after = (current["positions"] if isinstance(current.get("positions"), Mapping) else {}).get(symbol)
    q0 = _decimal(before.get("quantity")) if isinstance(before, Mapping) else Decimal("0")
    q1 = _decimal(after.get("quantity")) if isinstance(after, Mapping) else Decimal("0")
    if q0 is None or q1 is None or q0 == q1:
        return None
    side = "BUY" if q1 > q0 else "SELL"
    delta = abs(q1 - q0)
    observed = after if isinstance(after, Mapping) else before
    assert isinstance(observed, Mapping)
    price: Decimal | None = None
    price_basis = "last_price"
    basis: str
    if side == "BUY":
        avg1 = _decimal(after.get("average_price")) if isinstance(after, Mapping) else None
        if q0 == 0 and avg1 is not None and avg1 > 0:
            price = avg1
            price_basis = "average_purchase_price"
            basis = "신규 보유 수량과 당일 평균매입단가로 계산"
        else:
            avg0 = _decimal(before.get("average_price")) if isinstance(before, Mapping) else None
            inferred = (
                (avg1 * q1 - avg0 * q0) / (q1 - q0)
                if avg0 is not None and avg1 is not None and q1 > q0 else None
            )
            if inferred is not None and inferred > 0:
                price = inferred
                price_basis = "average_cost_delta"
                basis = "전일·당일 수량과 평균매입단가의 원가 차이로 역산"
            else:
                price = _decimal(after.get("last_price")) if isinstance(after, Mapping) else None
                basis = "평균단가 원가 차이가 일관되지 않아 당일 현재가 사용"
    else:
        if after is None:
            price = _decimal(before.get("last_price")) if isinstance(before, Mapping) else None
            basis = "종목이 사라져 전일 스냅샷 현재가 사용"
        else:
            price = _decimal(after.get("last_price"))
            basis = "당일 스냅샷 현재가 사용"
    if price is None or price < 0:
        return None
    avg0 = _decimal(before.get("average_price")) if isinstance(before, Mapping) else None
    realized = (price - avg0) * delta if side == "SELL" and avg0 is not None else None
    day = str(current["date"])
    return {
        "id": _event_id(source, day, symbol, side),
        "date": day,
        "source": source,
        "account_label": _SOURCE_LABELS[source],
        "symbol": symbol,
        "name": str(observed.get("name") or symbol),
        "side": side,
        "quantity": _number(delta, quantity=True),
        "price": _number(price),
        "currency": str(observed.get("currency") or "KRW"),
        "amount": _number(delta * price),
        "realized_pnl_est": _number(realized),
        "price_basis": price_basis,
        "basis": basis,
        "snapshot_dates": [str(previous["date"]), day],
        "recurring_like": False,
        "estimated": True,
        "origin": "snapshot_diff",
    }


def _build_derivation(project_root: Path) -> dict[str, object]:
    snapshots_by_source: dict[str, list[dict[str, object]]] = {}
    gaps: list[dict[str, object]] = []
    for source, relative in (("toss_self", TOSS_LANDING), ("kb_self", KB_LANDING)):
        snapshots, issues = _load_daily_snapshots(project_root, relative, source)
        snapshots_by_source[source] = snapshots
        gaps.extend(issues)

    events: list[dict[str, object]] = []
    pairs: list[dict[str, object]] = []
    snapshot_days: dict[str, list[str]] = {}
    for source, snapshots in snapshots_by_source.items():
        snapshot_days[source] = [str(snapshot["date"]) for snapshot in snapshots]
        for previous, current in zip(snapshots, snapshots[1:]):
            d0 = date.fromisoformat(str(previous["date"]))
            d1 = date.fromisoformat(str(current["date"]))
            if d1 - d0 != timedelta(days=1):
                missing = [
                    (d0 + timedelta(days=offset)).isoformat()
                    for offset in range(1, (d1 - d0).days)
                ]
                gaps.append({
                    "source": source, "type": "missing_snapshot_days",
                    "from_date": d0.isoformat(), "to_date": d1.isoformat(),
                    "missing_dates": missing,
                    "reason": "중간 일자 스냅샷이 없어 두 관측 사이를 추정하지 않았습니다.",
                })
                continue
            symbols = set(previous["positions"]) | set(current["positions"])
            pair_events = [
                event for symbol in sorted(symbols)
                if (event := _trade_event(source, previous, current, symbol)) is not None
            ]
            events.extend(pair_events)
            pairs.append({
                "source": source,
                "d0": previous["date"], "d1": current["date"],
                "cash0": previous["cash"], "cash1": current["cash"],
                "held0": previous["positions"],
                "trade_events": pair_events,
            })
    return {
        "events": events, "pairs": pairs, "gaps": gaps,
        "snapshot_days": snapshot_days,
    }


def _snapshot_file_key(project_root: Path) -> list[str]:
    files: list[str] = []
    for relative in (TOSS_LANDING, KB_LANDING):
        root = project_root / relative
        if root.is_dir():
            files.extend(
                str(path.relative_to(project_root)).replace("\\", "/")
                for path in root.glob("*.json")
            )
    return sorted(set(files))


def _load_or_build_derivation(project_root: Path) -> dict[str, object]:
    file_key = _snapshot_file_key(project_root)
    cache_path = project_root / CACHE_PATH
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                isinstance(cached, Mapping)
                and cached.get("schema_version") == _CACHE_SCHEMA_VERSION
                and cached.get("snapshot_files") == file_key
                and isinstance(cached.get("derivation"), Mapping)
            ):
                return dict(cached["derivation"])
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    derivation = _build_derivation(project_root)
    try:
        _atomic_json_replace(cache_path, {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "snapshot_files": file_key,
            "derivation": derivation,
        })
    except OSError:
        # A read-only local launch may still show the journal; only caching is lost.
        pass
    return derivation


def _latest_usd_krw(project_root: Path) -> float | None:
    frame = datasets.load(
        project_root, "data/normalized/fred_usd_fx_daily",
        columns=["date", "dexkous"],
    )
    if frame is None or frame.empty:
        return None
    values = pd.to_numeric(frame["dexkous"], errors="coerce").dropna()
    if values.empty or float(values.iloc[-1]) <= 0:
        return None
    return float(values.iloc[-1])


def _tag_recurring(
    project_root: Path, events: list[dict[str, object]], snapshot_days: Mapping[str, object],
) -> list[dict[str, object]]:
    fx = _latest_usd_krw(project_root)
    buys_by_symbol: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in events:
        if event.get("side") == "BUY":
            buys_by_symbol[(str(event["source"]), str(event["symbol"]))].add(str(event["date"]))
    result: list[dict[str, object]] = []
    for raw_event in events:
        event = dict(raw_event)
        recurring = False
        if event.get("side") == "BUY":
            amount = float(event.get("amount") or 0)
            currency = str(event.get("currency"))
            krw_amount = amount if currency == "KRW" else amount * fx if fx is not None else None
            small = krw_amount is not None and krw_amount < 100_000
            days = [str(day) for day in snapshot_days.get(str(event["source"]), [])]
            try:
                index = days.index(str(event["date"]))
            except ValueError:
                index = -1
            window = set(days[max(0, index - 4):index + 1]) if index >= 0 else set()
            frequent = len(window & buys_by_symbol[(str(event["source"]), str(event["symbol"]))]) >= 3
            recurring = small or frequent
        event["recurring_like"] = recurring
        result.append(event)
    return result


def derive_trade_events(project_root: Path) -> list[dict[str, object]]:
    """Return grouped BUY/SELL estimates derived from consecutive KST snapshots."""

    derivation = _load_or_build_derivation(Path(project_root))
    events = [dict(event) for event in derivation.get("events", [])]
    return _tag_recurring(Path(project_root), events, derivation.get("snapshot_days", {}))


def _canonical_dividend_date(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).date().isoformat()
    digits = re.sub(r"\D", "", str(value))
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _dividend_rows(project_root: Path) -> list[dict[str, object]]:
    master = datasets.load(
        project_root, "data/normalized/kr_equity_master", columns=["symbol", "isin"],
    )
    dividends = datasets.load(
        project_root, "data/normalized/kr_equity_dividend",
        columns=[
            "isin", "company", "event_type", "dividend_record_date",
            "cash_payment_date", "ordinary_dividend_amount",
        ],
    )
    if master is None or master.empty or dividends is None or dividends.empty:
        return []
    symbol_by_isin = {
        str(row.isin): str(row.symbol).zfill(6)
        for row in master[["symbol", "isin"]].dropna().itertuples(index=False)
    }
    rows: list[dict[str, object]] = []
    for row in dividends.itertuples(index=False):
        symbol = symbol_by_isin.get(str(row.isin))
        payment_date = _canonical_dividend_date(row.cash_payment_date)
        amount = _decimal(row.ordinary_dividend_amount)
        if symbol and payment_date and amount is not None and amount > 0:
            company = symbol if pd.isna(row.company) else str(row.company)
            rows.append({
                "symbol": symbol,
                "name": company,
                "payment_date": payment_date,
                "amount_per_share": float(amount),
            })
    return rows


def _flow_matches_source(account: object, source: str) -> bool:
    normalized = re.sub(r"\s+", "", str(account)).casefold()
    return normalized in _SOURCE_ALIASES[source]


def _cash_flow_for_pair(
    flows: Iterable[Mapping[str, object]], source: str, d0: str, d1: str,
) -> Decimal:
    total = Decimal("0")
    for flow in flows:
        flow_date = str(flow.get("date") or "")
        if d0 < flow_date <= d1 and _flow_matches_source(flow.get("account"), source):
            amount = _decimal(flow.get("amount_krw"))
            if amount is not None:
                total += amount
    return total


def _derive_dividends(project_root: Path, derivation: Mapping[str, object]) -> list[dict[str, object]]:
    references = _dividend_rows(project_root)
    flows = load_cash_flows(project_root)["entries"]
    events: list[dict[str, object]] = []
    for pair in derivation.get("pairs", []):
        if not isinstance(pair, Mapping):
            continue
        source, d0, d1 = str(pair["source"]), str(pair["d0"]), str(pair["d1"])
        cash0 = pair.get("cash0") if isinstance(pair.get("cash0"), Mapping) else {}
        cash1 = pair.get("cash1") if isinstance(pair.get("cash1"), Mapping) else {}
        currencies = set(cash0) | set(cash1)
        for currency in sorted(currencies):
            before = _decimal(cash0.get(currency))
            after = _decimal(cash1.get(currency))
            if before is None or after is None:
                continue
            net_trade_cash = Decimal("0")
            for trade in pair.get("trade_events", []):
                if not isinstance(trade, Mapping) or trade.get("currency") != currency:
                    continue
                amount = _decimal(trade.get("amount")) or Decimal("0")
                net_trade_cash += amount if trade.get("side") == "SELL" else -amount
            ledger_flow = (
                _cash_flow_for_pair(flows, source, d0, d1)
                if currency == "KRW" else Decimal("0")
            )
            residual = after - before - net_trade_cash - ledger_flow
            threshold = Decimal("1000") if currency == "KRW" else Decimal("1")
            if residual < threshold:
                continue
            held0 = pair.get("held0") if isinstance(pair.get("held0"), Mapping) else {}
            matches: list[tuple[dict[str, object], Decimal, Mapping[str, object]]] = []
            if currency == "KRW":
                for reference in references:
                    payment = str(reference["payment_date"])
                    held = held0.get(str(reference["symbol"]))
                    quantity = _decimal(held.get("quantity")) if isinstance(held, Mapping) else None
                    if d0 < payment <= d1 and quantity is not None and quantity > 0:
                        matches.append((reference, quantity, held))
            if matches:
                for reference, quantity, held in matches:
                    expected = quantity * Decimal(str(reference["amount_per_share"]))
                    symbol = str(reference["symbol"])
                    events.append({
                        "id": _event_id(source, d1, symbol, "DIVIDEND"),
                        "date": d1, "source": source,
                        "account_label": _SOURCE_LABELS[source],
                        "symbol": symbol,
                        "name": str(held.get("name") or reference["name"]),
                        "side": "DIVIDEND", "quantity": _number(quantity, quantity=True),
                        "price": float(reference["amount_per_share"]), "currency": "KRW",
                        "amount": _number(expected), "expected_amount": _number(expected),
                        "observed_cash_residual": _number(residual),
                        "realized_pnl_est": None, "price_basis": "dividend_reference",
                        "basis": "현금 잔액 증가와 국내 배당 지급일을 대조한 세전 추정",
                        "snapshot_dates": [d0, d1], "recurring_like": False,
                        "estimated": True, "origin": "cash_residual",
                    })
            else:
                symbol = "CASH_RESIDUAL"
                events.append({
                    "id": _event_id(source, d1, f"{symbol}:{currency}", "DIVIDEND?"),
                    "date": d1, "source": source,
                    "account_label": _SOURCE_LABELS[source],
                    "symbol": "", "name": "미확인 현금 증가",
                    "side": "DIVIDEND?", "quantity": None, "price": None,
                    "currency": currency, "amount": _number(residual),
                    "expected_amount": None, "observed_cash_residual": _number(residual),
                    "realized_pnl_est": None, "price_basis": "cash_residual",
                    "basis": "매매와 등록 입출금을 제외한 현금 증가 · 배당 여부 미확인",
                    "snapshot_dates": [d0, d1], "recurring_like": False,
                    "estimated": True, "origin": "cash_residual",
                })
    return events


def _manual_text(value: object, field: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise AccountInputError(f"{field} 값이 올바르지 않습니다.")
    text = value.strip()
    if (required and not text) or len(text) > maximum or _ACCOUNT_NUMBER.search(text):
        raise AccountInputError(f"{field} 값이 비어 있거나 식별자처럼 보입니다.")
    return text


def _manual_positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccountInputError(f"{field} 값이 숫자가 아닙니다.")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise AccountInputError(f"{field} 값의 범위가 올바르지 않습니다.")
    return number


def _manual_entry(payload: object, *, allow_missing_id: bool) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise AccountInputError("수동 매매일지 항목은 JSON 객체여야 합니다.")
    required = {"date", "account_label", "symbol", "name", "side", "quantity", "price", "currency", "memo"}
    if not required.issubset(payload) or not set(payload).issubset(required | {"id"}):
        raise AccountInputError("수동 매매일지 형식이 올바르지 않습니다.")
    raw_id = payload.get("id")
    if allow_missing_id and (raw_id is None or raw_id == ""):
        entry_id = f"manual_{uuid4().hex}"
    elif isinstance(raw_id, str) and _MANUAL_ID.fullmatch(raw_id):
        entry_id = raw_id
    else:
        raise AccountInputError("수동 매매일지 ID가 올바르지 않습니다.")
    raw_date = payload.get("date")
    try:
        canonical_date = date.fromisoformat(str(raw_date)).isoformat()
    except ValueError as error:
        raise AccountInputError("수동 매매일지 날짜가 올바르지 않습니다.") from error
    if canonical_date != raw_date:
        raise AccountInputError("수동 매매일지 날짜가 올바르지 않습니다.")
    symbol = _manual_text(payload.get("symbol"), "종목코드", 24).upper()
    if _SYMBOL.fullmatch(symbol) is None:
        raise AccountInputError("종목코드 또는 티커가 올바르지 않습니다.")
    side = str(payload.get("side") or "").upper()
    if side not in {"BUY", "SELL", "DIVIDEND"}:
        raise AccountInputError("매매 구분이 올바르지 않습니다.")
    currency = str(payload.get("currency") or "").upper()
    if currency not in {"KRW", "USD"}:
        raise AccountInputError("통화는 KRW 또는 USD여야 합니다.")
    account_label = _manual_text(payload.get("account_label"), "계좌", 60)
    normalized_label = re.sub(r"\s+", "", account_label).casefold()
    if any(normalized_label in aliases for aliases in _SOURCE_ALIASES.values()):
        raise AccountInputError("Toss와 KB는 스냅샷에서 자동 추정하므로 수동 입력할 수 없습니다.")
    return {
        "id": entry_id, "date": canonical_date,
        "account_label": account_label,
        "symbol": symbol, "name": _manual_text(payload.get("name"), "종목명", 80),
        "side": side,
        "quantity": round(_manual_positive_number(payload.get("quantity"), "수량"), 6),
        "price": _manual_positive_number(payload.get("price"), "단가"),
        "currency": currency,
        "memo": _manual_text(payload.get("memo"), "메모", 200, required=False),
    }


def _normalize_manual_ledger(payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping) or set(payload) != {"schema_version", "entries"}:
        raise AccountInputError("수동 매매일지 원장 형식이 올바르지 않습니다.")
    if payload.get("schema_version") != _MANUAL_SCHEMA_VERSION or not isinstance(payload.get("entries"), list):
        raise AccountInputError("지원하지 않는 수동 매매일지 형식입니다.")
    entries = [_manual_entry(entry, allow_missing_id=False) for entry in payload["entries"]]
    ids = [str(entry["id"]) for entry in entries]
    if len(ids) != len(set(ids)):
        raise AccountInputError("수동 매매일지 ID가 중복되었습니다.")
    entries.sort(key=lambda entry: (str(entry["date"]), str(entry["id"])))
    return {"schema_version": _MANUAL_SCHEMA_VERSION, "entries": entries}


def load_manual_entries(project_root: Path) -> dict[str, object]:
    path = Path(project_root) / MANUAL_PATH
    if not path.is_file():
        return {"schema_version": _MANUAL_SCHEMA_VERSION, "entries": []}
    try:
        return _normalize_manual_ledger(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, AccountInputError) as error:
        raise AccountInputError("수동 매매일지를 읽을 수 없습니다.") from error


def save_manual_entry(project_root: Path, payload: object) -> dict[str, object]:
    entry = _manual_entry(payload, allow_missing_id=True)
    ledger = load_manual_entries(project_root)
    entries = list(ledger["entries"])
    for index, current in enumerate(entries):
        if current["id"] == entry["id"]:
            entries[index] = entry
            break
    else:
        entries.append(entry)
    normalized = _normalize_manual_ledger({"schema_version": 1, "entries": entries})
    _atomic_json_replace(Path(project_root) / MANUAL_PATH, normalized)
    return normalized


def delete_manual_entry(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping) or set(payload) != {"id"}:
        raise AccountInputError("삭제할 수동 매매일지 형식이 올바르지 않습니다.")
    entry_id = payload.get("id")
    if not isinstance(entry_id, str) or _MANUAL_ID.fullmatch(entry_id) is None:
        raise AccountInputError("삭제할 수동 매매일지 ID가 올바르지 않습니다.")
    ledger = load_manual_entries(project_root)
    entries = [entry for entry in ledger["entries"] if entry["id"] != entry_id]
    if len(entries) == len(ledger["entries"]):
        raise AccountInputError("삭제할 수동 매매일지를 찾을 수 없습니다.")
    normalized = {"schema_version": 1, "entries": entries}
    _atomic_json_replace(Path(project_root) / MANUAL_PATH, normalized)
    return normalized


def _manual_public_event(entry: Mapping[str, object]) -> dict[str, object]:
    quantity = float(entry["quantity"])
    price = float(entry["price"])
    return {
        **entry,
        "source": "manual", "amount": quantity * price,
        "realized_pnl_est": None, "price_basis": "manual",
        "basis": "API 미연결 계좌의 사용자 수동 입력",
        "snapshot_dates": [], "recurring_like": False,
        "estimated": False, "origin": "manual",
    }


def _currency_totals(events: Iterable[Mapping[str, object]], field: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for event in events:
        value = event.get(field)
        if value is not None:
            totals[str(event.get("currency") or "KRW")] += float(value)
    return {currency: value for currency, value in sorted(totals.items())}


def build_trade_journal(project_root: Path, *, days: int = 60) -> dict[str, object]:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 36_500:
        raise AccountInputError("조회 기간은 1~36500일이어야 합니다.")
    root = Path(project_root)
    derivation = _load_or_build_derivation(root)
    trades = _tag_recurring(
        root, [dict(event) for event in derivation.get("events", [])],
        derivation.get("snapshot_days", {}),
    )
    dividends = _derive_dividends(root, derivation)
    manual = [_manual_public_event(entry) for entry in load_manual_entries(root)["entries"]]
    cutoff = (datetime.now(_KST).date() - timedelta(days=days - 1)).isoformat()
    events = [event for event in trades + dividends + manual if str(event["date"]) >= cutoff]
    events.sort(key=lambda event: (str(event["date"]), str(event["id"])), reverse=True)
    sells = [event for event in events if event.get("side") == "SELL"]
    dividend_events = [event for event in events if event.get("side") in {"DIVIDEND", "DIVIDEND?"}]
    gaps = [
        gap for gap in derivation.get("gaps", [])
        if str(gap.get("to_date") or gap.get("from_date") or cutoff) >= cutoff
    ]
    return {
        "events": events,
        "summary": {
            "buys": sum(event.get("side") == "BUY" for event in events),
            "sells": len(sells),
            "realized_pnl_est": _currency_totals(sells, "realized_pnl_est"),
            "dividends_est": _currency_totals(dividend_events, "amount"),
        },
        "gaps": gaps,
        "note": "일별 스냅샷 차이 기반 추정입니다. 체결 내역·수수료·세금·기업행동은 확정하지 않습니다.",
    }
