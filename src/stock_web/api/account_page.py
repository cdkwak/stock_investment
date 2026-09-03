"""Provider-free account, cash-flow, and net-worth services for the local UI.

Market and retained account datasets are read-only. Explicit local-user inputs
are validated and atomically replaced under ``artifacts/local_user``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping
from uuid import uuid4

import pandas as pd

from stock_web.api.fmt import format_kst

from stock_data.gui.manual_account_store import (
    LocalManualAccountStore,
    ManualAccountPosition,
    ManualAccountRecord,
    ManualAccountRegistry,
    manual_account_registry_payload,
    parse_manual_account_registry,
)
from stock_data.gui.net_worth_service import (
    AssetClass,
    HolderRole,
    LiabilityClass,
    LocalNetWorthHistoryStore,
    NetWorthPersistenceError,
    NetWorthValidationError,
    ValuationMethod,
    ValuationSource,
    ValuationStatus,
    ValuationUncertainty,
    build_net_worth_timeline,
)

from stock_web.api import datasets


MANUAL_ACCOUNTS_PATH = Path("artifacts/local_user/manual_accounts.json")
MANUAL_WEB_PATH = Path("artifacts/local_user/manual_accounts_web.json")
NET_WORTH_ROOT = Path("data/local/net_worth_history")
NET_WORTH_LABELS_PATH = Path("artifacts/local_user/net_worth_labels.json")
CASH_FLOWS_PATH = Path("artifacts/local_user/cash_flows.json")

_MANUAL_SCHEMA_VERSION = 1
_MANUAL_CURRENCIES = frozenset({"KRW", "USD"})
_SOURCE_ID = re.compile(r"manual:[a-z0-9][a-z0-9_-]{0,47}\Z")
_TICKER = re.compile(r"[A-Za-z0-9.^_-]{1,20}\Z")
_ACCOUNT_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){9,13}\d(?!\d)")
_CASH_FLOW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

ASSET_LABELS = {
    AssetClass.CASH.value: "예금·현금",
    AssetClass.INVESTMENT.value: "연금·기타 투자",
    AssetClass.REAL_ESTATE.value: "부동산",
    AssetClass.JEONSE_DEPOSIT.value: "전세 보증금",
    AssetClass.OTHER_RECEIVABLE.value: "기타 받을 돈",
}
LIABILITY_LABELS = {
    LiabilityClass.MORTGAGE.value: "주택담보대출",
    LiabilityClass.JEONSE_LOAN.value: "전세대출",
    LiabilityClass.DRAWN_OVERDRAFT.value: "사용 중인 한도대출",
    LiabilityClass.OTHER_DEBT.value: "기타 부채",
}
HOLDER_LABELS = {
    HolderRole.SELF.value: "본인",
    HolderRole.SPOUSE.value: "배우자",
    HolderRole.FAMILY.value: "가족",
    HolderRole.JOINT.value: "공동",
    HolderRole.OTHER_DECLARED.value: "기타 명의",
}
METHOD_LABELS = {
    ValuationMethod.USER_DECLARED.value: "시세 추정·수동 입력",
    ValuationMethod.STATEMENT_VALUE.value: "명세서 확정값",
    ValuationMethod.MARKET_VALUE.value: "시장가",
    ValuationMethod.APPRAISAL.value: "감정평가",
    ValuationMethod.NOT_AVAILABLE.value: "평가 불가",
}
SOURCE_LABELS = {
    ValuationSource.USER_LOCAL.value: "수동 입력",
    ValuationSource.BROKER_LOCAL_SNAPSHOT.value: "로컬 증권 스냅샷",
    ValuationSource.OFFICIAL_STATEMENT.value: "공식 명세서",
    ValuationSource.APPROVED_LOCAL_SOURCE.value: "승인된 로컬 자료",
    ValuationSource.NOT_AVAILABLE.value: "출처 없음",
}
UNCERTAINTY_LABELS = {
    ValuationUncertainty.EXACT.value: "확정",
    ValuationUncertainty.LOW.value: "낮음",
    ValuationUncertainty.MEDIUM.value: "보통",
    ValuationUncertainty.HIGH.value: "높음",
    ValuationUncertainty.UNKNOWN.value: "알 수 없음",
}
STATUS_LABELS = {
    ValuationStatus.CURRENT.value: "현재값",
    ValuationStatus.STALE.value: "오래된 값",
    ValuationStatus.MISSING.value: "평가 불가",
}


class AccountInputError(ValueError):
    """Sanitized validation error suitable for an HTTP 400 response."""


def _atomic_json_replace(path: Path, payload: Mapping[str, object]) -> None:
    body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccountInputError(f"{field} 값이 숫자가 아닙니다.")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise AccountInputError(f"{field} 값의 범위가 올바르지 않습니다.")
    return result


def _optional_number(value: object, field: str, *, positive: bool = False) -> float | None:
    return None if value is None or value == "" else _number(value, field, positive=positive)


def _canonical_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise AccountInputError(f"{field} 날짜가 올바르지 않습니다.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise AccountInputError(f"{field} 날짜가 올바르지 않습니다.") from error
    if parsed.isoformat() != value:
        raise AccountInputError(f"{field} 날짜가 올바르지 않습니다.")
    return value


def _cash_flow_amount(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccountInputError("입출금 금액은 원 단위 숫자여야 합니다.")
    amount = float(value)
    if not math.isfinite(amount) or not amount.is_integer() or amount == 0:
        raise AccountInputError("입출금 금액은 0이 아닌 원 단위 정수여야 합니다.")
    if abs(amount) > 1_000_000_000_000_000:
        raise AccountInputError("입출금 금액의 범위가 올바르지 않습니다.")
    return int(amount)


def _cash_flow_text(
    value: object, field: str, *, maximum: int, required: bool,
) -> str:
    if not isinstance(value, str):
        raise AccountInputError(f"{field} 값이 올바르지 않습니다.")
    text = value.strip()
    if (required and not text) or len(text) > maximum or _ACCOUNT_NUMBER.search(text):
        raise AccountInputError(f"{field} 값이 비어 있거나 식별자처럼 보입니다.")
    return text


def _normalize_cash_flow_entry(
    payload: object, *, allow_missing_id: bool,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise AccountInputError("입출금 기록은 JSON 객체여야 합니다.")
    required = {"date", "amount_krw", "account", "memo"}
    allowed = required | {"id"}
    if not required.issubset(payload) or not set(payload).issubset(allowed):
        raise AccountInputError("입출금 기록 형식이 올바르지 않습니다.")
    raw_id = payload.get("id")
    if (raw_id is None or raw_id == "") and allow_missing_id:
        flow_id = f"flow_{uuid4().hex}"
    elif isinstance(raw_id, str) and _CASH_FLOW_ID.fullmatch(raw_id):
        flow_id = raw_id
    else:
        raise AccountInputError("입출금 기록 ID가 올바르지 않습니다.")
    return {
        "id": flow_id,
        "date": _canonical_date(payload["date"], "입출금"),
        "amount_krw": _cash_flow_amount(payload["amount_krw"]),
        "account": _cash_flow_text(
            payload["account"], "계좌", maximum=60, required=True,
        ),
        "memo": _cash_flow_text(
            payload["memo"], "메모", maximum=200, required=False,
        ),
    }


def _normalize_cash_flow_ledger(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "entries"}:
        raise AccountInputError("입출금 원장 형식이 올바르지 않습니다.")
    if payload["schema_version"] != 1 or not isinstance(payload["entries"], list):
        raise AccountInputError("지원하지 않는 입출금 원장 형식입니다.")
    entries = [
        _normalize_cash_flow_entry(entry, allow_missing_id=False)
        for entry in payload["entries"]
    ]
    ids = [str(entry["id"]) for entry in entries]
    if len(ids) != len(set(ids)):
        raise AccountInputError("입출금 기록 ID가 중복되었습니다.")
    entries.sort(key=lambda entry: (str(entry["date"]), str(entry["id"])))
    return {"schema_version": 1, "entries": entries}


def load_cash_flows(project_root: Path) -> dict[str, object]:
    path = project_root / CASH_FLOWS_PATH
    if not path.is_file():
        return {"schema_version": 1, "entries": []}
    try:
        return _normalize_cash_flow_ledger(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, AccountInputError) as error:
        raise AccountInputError("입출금 원장을 읽을 수 없습니다.") from error


def build_cash_flow_data(project_root: Path) -> dict[str, object]:
    ledger = load_cash_flows(project_root)
    entries = list(reversed(ledger["entries"]))
    monthly: dict[str, int] = defaultdict(int)
    for entry in entries:
        monthly[str(entry["date"])[:7]] += int(entry["amount_krw"])
    return {
        "schema_version": 1,
        "entries": entries,
        "monthly_subtotals": [
            {"month": month, "amount_krw": monthly[month]}
            for month in sorted(monthly, reverse=True)
        ],
    }


def save_cash_flow(project_root: Path, payload: object) -> dict[str, object]:
    entry = _normalize_cash_flow_entry(payload, allow_missing_id=True)
    ledger = load_cash_flows(project_root)
    entries = list(ledger["entries"])
    for index, current in enumerate(entries):
        if current["id"] == entry["id"]:
            entries[index] = entry
            break
    else:
        entries.append(entry)
    normalized = _normalize_cash_flow_ledger({"schema_version": 1, "entries": entries})
    _atomic_json_replace(project_root / CASH_FLOWS_PATH, normalized)
    return build_cash_flow_data(project_root)


def delete_cash_flow(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"id"}:
        raise AccountInputError("삭제할 입출금 기록 형식이 올바르지 않습니다.")
    flow_id = payload["id"]
    if not isinstance(flow_id, str) or _CASH_FLOW_ID.fullmatch(flow_id) is None:
        raise AccountInputError("삭제할 입출금 기록 ID가 올바르지 않습니다.")
    ledger = load_cash_flows(project_root)
    entries = [entry for entry in ledger["entries"] if entry["id"] != flow_id]
    if len(entries) == len(ledger["entries"]):
        raise AccountInputError("삭제할 입출금 기록을 찾을 수 없습니다.")
    _atomic_json_replace(
        project_root / CASH_FLOWS_PATH,
        {"schema_version": 1, "entries": entries},
    )
    return build_cash_flow_data(project_root)


def _generated_source_id(label: str, index: int) -> str:
    digest = hashlib.sha256(f"{index}:{label}".encode("utf-8")).hexdigest()[:10]
    return f"manual:account_{digest}"


def _validate_label_with_existing_parser(source_id: str, label: str, names: list[str]) -> None:
    """Reuse the existing privacy/label parser even for web-extension rows."""

    rows = names or ["현금"]
    for index, name in enumerate(rows):
        ticker = f"{index:06d}"[-6:]
        parse_manual_account_registry({
            "schema_version": 1,
            "accounts": [{
                "source_id": source_id,
                "label": label,
                "account_kind": "GENERAL",
                "snapshot_date": "2000-01-01",
                "currency": "KRW",
                "positions": [{
                    "name": name,
                    "ticker": ticker,
                    "quantity": 1.0,
                    "average_cost": 0.0,
                    "purchase_total": 0.0,
                }],
            }],
        })


def _normalize_manual_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise AccountInputError("수동 계좌 요청은 JSON 객체여야 합니다.")
    allowed_top = {"schema_version", "accounts"}
    if not set(payload).issubset(allowed_top) or "accounts" not in payload:
        raise AccountInputError("수동 계좌 요청 형식이 올바르지 않습니다.")
    if payload.get("schema_version", _MANUAL_SCHEMA_VERSION) != _MANUAL_SCHEMA_VERSION:
        raise AccountInputError("지원하지 않는 수동 계좌 형식입니다.")
    raw_accounts = payload["accounts"]
    if not isinstance(raw_accounts, list):
        raise AccountInputError("계좌 목록이 올바르지 않습니다.")
    accounts: list[dict[str, object]] = []
    seen_sources: set[str] = set()
    for index, raw in enumerate(raw_accounts):
        if not isinstance(raw, dict):
            raise AccountInputError("계좌 항목이 올바르지 않습니다.")
        allowed = {
            "source_id", "label", "account_kind", "snapshot_date", "currency",
            "cash", "positions",
        }
        if not set(raw).issubset(allowed):
            raise AccountInputError("계좌 항목에 지원하지 않는 필드가 있습니다.")
        label = raw.get("label")
        if (
            not isinstance(label, str) or not label.strip() or label != label.strip()
            or len(label) > 60 or _ACCOUNT_NUMBER.search(label)
        ):
            raise AccountInputError("계좌 이름이 올바르지 않거나 식별자처럼 보입니다.")
        source_id = raw.get("source_id") or _generated_source_id(label, index)
        if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
            raise AccountInputError("수동 계좌 내부 ID가 올바르지 않습니다.")
        if source_id in seen_sources:
            raise AccountInputError("수동 계좌가 중복되었습니다.")
        currency = raw.get("currency", "KRW")
        if currency not in _MANUAL_CURRENCIES:
            raise AccountInputError("수동 계좌 통화는 KRW 또는 USD여야 합니다.")
        account_kind = raw.get("account_kind", "GENERAL")
        if account_kind not in {"PENSION", "ISA", "GENERAL"}:
            raise AccountInputError("계좌 종류가 올바르지 않습니다.")
        snapshot_date = _canonical_date(
            raw.get("snapshot_date", date.today().isoformat()), "계좌 기준일",
        )
        cash = _number(raw.get("cash", 0), "현금")
        raw_positions = raw.get("positions", [])
        if not isinstance(raw_positions, list):
            raise AccountInputError("보유 종목 목록이 올바르지 않습니다.")
        positions: list[dict[str, object]] = []
        seen_tickers: set[str] = set()
        for position in raw_positions:
            if not isinstance(position, dict):
                raise AccountInputError("보유 종목 항목이 올바르지 않습니다.")
            allowed_position = {
                "ticker", "name", "quantity", "average_cost", "manual_price",
            }
            if not set(position).issubset(allowed_position):
                raise AccountInputError("보유 종목에 지원하지 않는 필드가 있습니다.")
            ticker = position.get("ticker")
            name = position.get("name")
            if not isinstance(ticker, str) or _TICKER.fullmatch(ticker) is None:
                raise AccountInputError("종목코드 또는 티커가 올바르지 않습니다.")
            ticker = ticker.upper()
            if ticker in seen_tickers:
                raise AccountInputError("한 계좌 안에 같은 종목이 중복되었습니다.")
            if (
                not isinstance(name, str) or not name.strip() or name != name.strip()
                or len(name) > 80 or _ACCOUNT_NUMBER.search(name)
            ):
                raise AccountInputError("종목명이 올바르지 않거나 식별자처럼 보입니다.")
            positions.append({
                "ticker": ticker,
                "name": name,
                "quantity": _number(position.get("quantity"), "수량", positive=True),
                "average_cost": _optional_number(position.get("average_cost"), "평균단가"),
                "manual_price": _optional_number(
                    position.get("manual_price"), "수동 현재가", positive=True,
                ),
            })
            seen_tickers.add(ticker)
        try:
            _validate_label_with_existing_parser(
                source_id, label, [str(position["name"]) for position in positions],
            )
        except (TypeError, ValueError) as error:
            raise AccountInputError("계좌 또는 종목 이름이 안전한 표시 규칙을 통과하지 못했습니다.") from error
        accounts.append({
            "source_id": source_id,
            "label": label,
            "account_kind": account_kind,
            "snapshot_date": snapshot_date,
            "currency": currency,
            "cash": cash,
            "positions": positions,
        })
        seen_sources.add(source_id)
    return {"schema_version": _MANUAL_SCHEMA_VERSION, "accounts": accounts}


def _legacy_manual_payload(registry: ManualAccountRegistry) -> dict[str, object]:
    return {
        "schema_version": _MANUAL_SCHEMA_VERSION,
        "accounts": [{
            "source_id": account.source_id,
            "label": account.label,
            "account_kind": account.account_kind,
            "snapshot_date": account.snapshot_date,
            "currency": account.currency,
            "cash": 0.0,
            "positions": [{
                "ticker": position.ticker,
                "name": position.name,
                "quantity": position.quantity,
                "average_cost": position.average_cost,
                "manual_price": None,
            } for position in account.positions],
        } for account in registry.accounts],
    }


def load_manual_accounts(project_root: Path) -> dict[str, object]:
    web_path = project_root / MANUAL_WEB_PATH
    if web_path.is_file():
        try:
            return _normalize_manual_payload(json.loads(web_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, AccountInputError) as error:
            raise AccountInputError("수동 계좌 확장 파일을 읽을 수 없습니다.") from error
    try:
        registry = LocalManualAccountStore(project_root / MANUAL_ACCOUNTS_PATH).load()
        return _legacy_manual_payload(registry)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise AccountInputError("수동 계좌 파일을 읽을 수 없습니다.") from error


def save_manual_accounts(project_root: Path, payload: object) -> dict[str, object]:
    normalized = _normalize_manual_payload(payload)
    compatible: list[ManualAccountRecord] = []
    for account in normalized["accounts"]:
        positions = account["positions"]
        if (
            account["currency"] != "KRW" or not positions
            or any(re.fullmatch(r"\d{6}", str(row["ticker"])) is None for row in positions)
        ):
            continue
        compatible_positions = tuple(ManualAccountPosition(
            name=str(row["name"]),
            ticker=str(row["ticker"]),
            quantity=float(row["quantity"]),
            average_cost=(
                None if row["average_cost"] is None else float(row["average_cost"])
            ),
            purchase_total=(
                None if row["average_cost"] is None
                else float(row["quantity"]) * float(row["average_cost"])
            ),
        ) for row in positions)
        compatible.append(ManualAccountRecord(
            source_id=str(account["source_id"]),
            label=str(account["label"]),
            account_kind=str(account["account_kind"]),
            snapshot_date=str(account["snapshot_date"]),
            currency="KRW",
            positions=compatible_positions,
        ))
    registry = ManualAccountRegistry(tuple(compatible))
    # Round-trip the existing parser before the existing atomic store writes.
    registry = parse_manual_account_registry(manual_account_registry_payload(registry))
    LocalManualAccountStore(project_root / MANUAL_ACCOUNTS_PATH).save(registry)
    _atomic_json_replace(project_root / MANUAL_WEB_PATH, normalized)
    return normalized


def _latest_fx(project_root: Path) -> tuple[float | None, str | None, str | None]:
    candidates: list[tuple[pd.Timestamp, float, int, str]] = []
    sources = (
        (
            "data/normalized/bok_ecos_usd_krw_daily",
            "rate_krw_per_usd",
            1,
            "BOK 매매기준율",
        ),
        ("data/normalized/fred_usd_fx_daily", "dexkous", 0, "FRED"),
    )
    for path, value_column, priority, label in sources:
        frame = datasets.load(project_root, path, columns=["date", value_column])
        if frame is None or frame.empty:
            continue
        work = frame.copy()
        work["date"] = pd.to_datetime(work["date"], errors="coerce")
        work[value_column] = pd.to_numeric(work[value_column], errors="coerce")
        work = work.dropna(subset=["date", value_column]).sort_values("date")
        work = work[work[value_column] > 0]
        if work.empty:
            continue
        row = work.iloc[-1]
        candidates.append((row["date"], float(row[value_column]), priority, label))
    if not candidates:
        return None, None, None
    observed, value, _priority, label = max(
        candidates, key=lambda item: (item[0], item[2]),
    )
    observed_date = observed.date().isoformat()
    return value, observed_date, f"{label} {observed:%m-%d}"


def _latest_kr_prices(
    project_root: Path, tickers: set[str],
) -> dict[str, tuple[float, str]]:
    if not tickers:
        return {}
    frame = datasets.load(
        project_root, "data/normalized/kr_equity_price_daily",
        columns=["date", "symbol", "close"],
        filter_expr=datasets.field("symbol").isin(sorted(tickers)),
    )
    if frame is None or frame.empty:
        return {}
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["symbol"] = work["symbol"].astype(str).str.zfill(6)
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["date", "symbol", "close"])
    work = work[work["close"] > 0].sort_values(["symbol", "date"])
    latest = work.groupby("symbol", as_index=False).tail(1)
    return {
        str(row.symbol): (float(row.close), row.date.date().isoformat())
        for row in latest.itertuples(index=False)
    }


def build_manual_account_data(project_root: Path) -> dict[str, object]:
    try:
        payload = load_manual_accounts(project_root)
    except AccountInputError as error:
        return {
            "schema_version": _MANUAL_SCHEMA_VERSION,
            "accounts": [], "rows": [], "total_krw": 0.0,
            "cash_krw": 0.0, "reason": str(error), "unpriced_count": 0,
        }
    accounts = payload["accounts"]
    kr_tickers = {
        str(position["ticker"])
        for account in accounts if account["currency"] == "KRW"
        for position in account["positions"]
        if re.fullmatch(r"\d{6}", str(position["ticker"]))
    }
    prices = _latest_kr_prices(project_root, kr_tickers)
    fx, fx_as_of, fx_source = _latest_fx(project_root)
    total_krw = 0.0
    cash_krw = 0.0
    unpriced_count = 0
    rows: list[dict[str, object]] = []
    valued_accounts: list[dict[str, object]] = []
    for account in accounts:
        currency = str(account["currency"])
        conversion = 1.0 if currency == "KRW" else fx
        cash_native = float(account["cash"])
        converted_cash = None if conversion is None else cash_native * conversion
        account_total = float(converted_cash or 0.0)
        account_pnl = 0.0
        pnl_rows = 0
        valued_positions: list[dict[str, object]] = []
        as_of_values: list[str] = [str(account["snapshot_date"])]
        if currency == "USD" and fx_as_of:
            as_of_values.append(fx_as_of)
        notes: list[str] = []
        for position in account["positions"]:
            ticker = str(position["ticker"])
            price_native: float | None = None
            price_source: str | None = None
            price_as_of: str | None = None
            if currency == "KRW" and ticker in prices:
                price_native, price_as_of = prices[ticker]
                price_source = "로컬 종가"
            elif position["manual_price"] is not None:
                price_native = float(position["manual_price"])
                price_as_of = str(account["snapshot_date"])
                price_source = "수동 현재가"
            market_native = (
                None if price_native is None
                else float(position["quantity"]) * price_native
            )
            market_krw = (
                None if market_native is None or conversion is None
                else market_native * conversion
            )
            if market_krw is None:
                unpriced_count += 1
                note = (
                    "환율 없음으로 평가 불가" if currency == "USD" and fx is None
                    else "로컬 가격과 수동 현재가가 없어 평가 불가"
                )
                notes.append(f"{position['name']}: {note}")
            else:
                account_total += market_krw
                if price_as_of:
                    as_of_values.append(price_as_of)
                if position["average_cost"] is not None:
                    account_pnl += (
                        price_native - float(position["average_cost"])
                    ) * float(position["quantity"]) * conversion
                    pnl_rows += 1
            valued_positions.append({
                **position,
                "price": price_native,
                "price_source": price_source,
                "price_as_of": price_as_of,
                "market_value_krw": market_krw,
                "included": market_krw is not None,
                "note": None if market_krw is not None else note,
            })
        included = converted_cash is not None and (
            bool(account["positions"]) is False
            or converted_cash > 0 or any(row["included"] for row in valued_positions)
        )
        total_krw += account_total
        cash_krw += float(converted_cash or 0.0)
        row_note = " · ".join(notes) if notes else "로컬 보존값만 사용"
        rows.append({
            "kind": "manual",
            "source_id": account["source_id"],
            "name": account["label"],
            "value_krw": account_total,
            "cash_krw": converted_cash,
            "pnl_krw": account_pnl if pnl_rows else None,
            "as_of": max(as_of_values),
            "included": included,
            "partial": bool(notes),
            "note": row_note,
        })
        valued_accounts.append({
            **account,
            "value_krw": account_total,
            "cash_krw": converted_cash,
            "pnl_krw": account_pnl if pnl_rows else None,
            "valuation_note": row_note,
            "valued_positions": valued_positions,
        })
    return {
        "schema_version": _MANUAL_SCHEMA_VERSION,
        "accounts": valued_accounts,
        "rows": rows,
        "total_krw": total_krw,
        "cash_krw": cash_krw,
        "unpriced_count": unpriced_count,
        "fx_krw_per_usd": fx,
        "fx_as_of": fx_as_of,
        "fx_source": fx_source,
        "reason": None,
    }


def _convert(value: float | None, currency: str, fx: float | None) -> float | None:
    if value is None:
        return None
    if currency == "KRW":
        return float(value)
    if currency == "USD" and fx is not None:
        return float(value) * fx
    return None


def build_api_account_data(project_root: Path) -> dict[str, object]:
    from stock_data.gui.account_snapshot_service import LocalAccountSnapshotService

    fx, fx_as_of, fx_source = _latest_fx(project_root)
    candidates = (
        ("toss_self", "Toss", project_root / "data/normalized/toss_account_snapshot/latest.json"),
        ("kb_self", "KB", project_root / "data/local/account_snapshots/kb_self.json"),
    )
    rows: list[dict[str, object]] = []
    total_krw = 0.0
    cash_krw = 0.0
    broker_reported_pnl_krw = 0.0
    broker_reported_seen = False
    for source_id, name, path in candidates:
        snapshot = LocalAccountSnapshotService(path).load()
        if not snapshot.displays_values:
            rows.append({
                "kind": "api", "source_id": source_id, "name": name,
                "value_krw": None, "cash_krw": None, "pnl_krw": None,
                "broker_reported_pnl_krw": None,
                "as_of": None, "included": False,
                "note": "읽을 수 있는 로컬 스냅샷 없음",
            })
            continue
        value = 0.0
        cash_value = 0.0
        pnl_value = 0.0
        reported_pnl_value = 0.0
        value_seen = cash_seen = pnl_seen = False
        reported_pnl_seen = False
        conversion_missing = False
        if snapshot.currency:
            currency = snapshot.currency
            cash = snapshot.cash_balance
            component_cash = cash
            if source_id == "toss_self":
                # Toss exposes order buying power, not a settled cash balance.
                # Keep it in the explicitly documented account-value component
                # while leaving the generic cash column unavailable.
                component_cash = snapshot.available_cash
            elif cash is None:
                cash = snapshot.available_cash
                component_cash = cash
            total = snapshot.total_assets
            if total is None and snapshot.securities_value is not None:
                total = (
                    float(snapshot.securities_value) + float(component_cash or 0.0)
                )
            converted_total = _convert(total, currency, fx)
            converted_cash = _convert(cash, currency, fx)
            converted_pnl = _convert(snapshot.unrealized_pnl, currency, fx)
            converted_realized = _convert(snapshot.realized_pnl, currency, fx)
            conversion_missing = total is not None and converted_total is None
            if converted_total is not None:
                value += converted_total
                value_seen = True
            if converted_cash is not None:
                cash_value += converted_cash
                cash_seen = True
            if converted_pnl is not None:
                pnl_value += converted_pnl
                pnl_seen = True
                reported_pnl_value += converted_pnl
                reported_pnl_seen = True
            if converted_realized is not None:
                reported_pnl_value += converted_realized
                reported_pnl_seen = True
        else:
            for summary in snapshot.currency_summaries:
                component_cash = summary.cash_buying_power
                cash = None if source_id == "toss_self" else component_cash
                securities = summary.securities_value
                total = (
                    None if securities is None
                    else float(securities) + float(component_cash or 0.0)
                )
                converted_total = _convert(total, summary.currency, fx)
                converted_cash = _convert(cash, summary.currency, fx)
                converted_pnl = _convert(summary.unrealized_pnl, summary.currency, fx)
                conversion_missing |= total is not None and converted_total is None
                if converted_total is not None:
                    value += converted_total
                    value_seen = True
                if converted_cash is not None:
                    cash_value += converted_cash
                    cash_seen = True
                if converted_pnl is not None:
                    pnl_value += converted_pnl
                    pnl_seen = True
                    reported_pnl_value += converted_pnl
                    reported_pnl_seen = True
        included = value_seen and not conversion_missing
        if included:
            total_krw += value
            cash_krw += cash_value
            if reported_pnl_seen:
                broker_reported_pnl_krw += reported_pnl_value
                broker_reported_seen = True
        rows.append({
            "kind": "api", "source_id": source_id, "name": name,
            "value_krw": value if included else None,
            "cash_krw": cash_value if cash_seen and included else None,
            "pnl_krw": pnl_value if pnl_seen and included else None,
            "broker_reported_pnl_krw": (
                reported_pnl_value if reported_pnl_seen and included else None
            ),
            "as_of": snapshot.as_of,
            "included": included,
            "note": (
                "USD 환율 없음으로 제외" if conversion_missing
                else "식별자 없는 로컬 스냅샷"
            ),
        })
    return {
        "rows": rows, "total_krw": total_krw, "cash_krw": cash_krw,
        "fx_krw_per_usd": fx, "fx_as_of": fx_as_of, "fx_source": fx_source,
        "broker_reported_pnl_krw": (
            broker_reported_pnl_krw if broker_reported_seen else None
        ),
    }


def _enum_options(kind: type, labels: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"value": item.value, "label": labels[item.value]} for item in kind]


def _load_net_worth_labels(project_root: Path) -> dict[str, dict[str, str]]:
    path = project_root / NET_WORTH_LABELS_PATH
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return {}
        snapshots = payload.get("snapshots")
        if not isinstance(snapshots, dict):
            return {}
        return {
            str(snapshot_id): {
                str(record_id): str(label) for record_id, label in labels.items()
                if isinstance(record_id, str) and isinstance(label, str)
            }
            for snapshot_id, labels in snapshots.items() if isinstance(labels, dict)
        }
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _save_net_worth_labels(
    project_root: Path, snapshot_id: str, labels: Mapping[str, str],
) -> None:
    snapshots = _load_net_worth_labels(project_root)
    snapshots[snapshot_id] = dict(labels)
    _atomic_json_replace(project_root / NET_WORTH_LABELS_PATH, {
        "schema_version": 1, "snapshots": snapshots,
    })


def _net_worth_options() -> dict[str, object]:
    return {
        "asset_classes": _enum_options(AssetClass, ASSET_LABELS),
        "liability_classes": _enum_options(LiabilityClass, LIABILITY_LABELS),
        "holder_roles": _enum_options(HolderRole, HOLDER_LABELS),
        "valuation_methods": _enum_options(ValuationMethod, METHOD_LABELS),
        "valuation_sources": _enum_options(ValuationSource, SOURCE_LABELS),
        "valuation_statuses": _enum_options(ValuationStatus, STATUS_LABELS),
        "uncertainties": _enum_options(ValuationUncertainty, UNCERTAINTY_LABELS),
    }


def build_net_worth_data(project_root: Path) -> dict[str, object]:
    store = LocalNetWorthHistoryStore(project_root / NET_WORTH_ROOT)
    try:
        records = store.load_history()
    except (NetWorthPersistenceError, NetWorthValidationError):
        return {
            "exists": False, "reason": "순자산 이력 검증에 실패했습니다.",
            "timeline": [], "breakdown": [], "rows": [], "latest": None,
            "options": _net_worth_options(),
        }
    if not records:
        return {
            "exists": False, "reason": "저장된 순자산 스냅샷이 없습니다.",
            "timeline": [], "breakdown": [], "rows": [], "latest": None,
            "options": _net_worth_options(),
        }
    latest_record = records[-1]
    view = latest_record.view
    snapshot = view.snapshot
    totals = view.totals
    labels = _load_net_worth_labels(project_root).get(snapshot.snapshot_id, {})
    groups: dict[tuple[str, str], dict[str, object]] = {}
    latest_assets: list[dict[str, object]] = []
    latest_liabilities: list[dict[str, object]] = []
    for entry in snapshot.assets:
        label = labels.get(entry.record_id, ASSET_LABELS[entry.asset_class.value])
        latest_assets.append({
            "name": label, "asset_class": entry.asset_class.value,
            "amount_krw": entry.economic_value_krw,
            "valuation_date": entry.valuation_date.isoformat() if entry.valuation_date else None,
            "valuation_method": entry.valuation_method.value,
            "valuation_source": entry.valuation_source.value,
            "valuation_status": entry.valuation_status.value,
            "uncertainty": entry.uncertainty.value,
            "holder_role": entry.economic_owner_role.value,
        })
        key = ("asset", entry.asset_class.value)
        group = groups.setdefault(key, {
            "kind": "asset", "class": entry.asset_class.value,
            "name": ASSET_LABELS[entry.asset_class.value], "value_krw": 0,
            "dates": [], "complete": True,
        })
        if entry.economic_value_krw is not None:
            group["value_krw"] += entry.economic_value_krw
        if entry.valuation_date:
            group["dates"].append(entry.valuation_date.isoformat())
        group["complete"] = group["complete"] and entry.valuation_status is ValuationStatus.CURRENT
    for entry in snapshot.liabilities:
        label = labels.get(entry.record_id, LIABILITY_LABELS[entry.liability_class.value])
        latest_liabilities.append({
            "name": label, "liability_class": entry.liability_class.value,
            "amount_krw": entry.economic_principal_krw,
            "unused_limit_krw": entry.unused_limit_krw,
            "valuation_date": entry.valuation_date.isoformat() if entry.valuation_date else None,
            "valuation_method": entry.valuation_method.value,
            "valuation_source": entry.valuation_source.value,
            "valuation_status": entry.valuation_status.value,
            "uncertainty": entry.uncertainty.value,
            "holder_role": entry.economic_owner_role.value,
        })
        key = ("liability", entry.liability_class.value)
        group = groups.setdefault(key, {
            "kind": "liability", "class": entry.liability_class.value,
            "name": LIABILITY_LABELS[entry.liability_class.value], "value_krw": 0,
            "dates": [], "complete": True,
        })
        if entry.economic_principal_krw is not None:
            group["value_krw"] += entry.economic_principal_krw
        if entry.valuation_date:
            group["dates"].append(entry.valuation_date.isoformat())
        group["complete"] = group["complete"] and entry.valuation_status is ValuationStatus.CURRENT
    rows = [{
        "kind": group["kind"],
        "source_id": f"net-worth:{group['kind']}:{group['class'].lower()}",
        "name": group["name"],
        "value_krw": (
            -float(group["value_krw"])
            if group["kind"] == "liability" else float(group["value_krw"])
        ),
        "cash_krw": None, "pnl_krw": None,
        "as_of": max(group["dates"]) if group["dates"] else snapshot.as_of_date.isoformat(),
        "included": bool(group["complete"]),
        "note": "순자산에서 차감" if group["kind"] == "liability" else "순자산에 포함",
    } for group in groups.values()]
    timeline_view = build_net_worth_timeline(records)
    timeline = [{
        "t": point.as_of_date.isoformat(),
        "v": point.net_worth_krw,
        "state": point.display_state.value,
        "reason": point.display_reason,
        "delta_krw": point.delta_from_previous_complete_krw,
    } for point in timeline_view.points]
    breakdown = [{
        "kind": group["kind"], "class": group["class"], "name": group["name"],
        "value_krw": float(group["value_krw"]), "complete": bool(group["complete"]),
    } for group in groups.values()]
    return {
        "exists": True,
        "reason": None,
        "as_of": snapshot.as_of_date.isoformat(),
        "stored_net_worth_krw": totals.net_worth_krw,
        "total_assets_krw": totals.total_assets_krw,
        "total_liabilities_krw": totals.total_liabilities_krw,
        "complete": totals.complete,
        "timeline": timeline,
        "breakdown": breakdown,
        "rows": rows,
        "latest": {
            "snapshot_id": snapshot.snapshot_id,
            "as_of_date": snapshot.as_of_date.isoformat(),
            "assets": latest_assets,
            "liabilities": latest_liabilities,
        },
        "options": _net_worth_options(),
        "timeline_note": "저장된 자산·부채 스냅샷의 순자산 기록이며 투자 계좌 과거값을 덧붙이지 않습니다.",
    }


def _safe_label(value: object, field: str) -> str:
    if (
        not isinstance(value, str) or not value.strip() or value != value.strip()
        or len(value) > 80 or _ACCOUNT_NUMBER.search(value)
    ):
        raise AccountInputError(f"{field} 이름이 올바르지 않거나 식별자처럼 보입니다.")
    return value


def _friendly_net_worth_payload(payload: Mapping[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    allowed = {"as_of_date", "assets", "liabilities"}
    if set(payload) != allowed:
        raise AccountInputError("순자산 요청 형식이 올바르지 않습니다.")
    as_of = _canonical_date(payload["as_of_date"], "순자산 기준일")
    raw_assets = payload["assets"]
    raw_liabilities = payload["liabilities"]
    if not isinstance(raw_assets, list) or not isinstance(raw_liabilities, list):
        raise AccountInputError("자산·부채 목록이 올바르지 않습니다.")
    now = datetime.now(timezone.utc)
    seed = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    snapshot_id = f"snapshot_{as_of.replace('-', '')}_{seed}"
    labels: dict[str, str] = {}

    def common(raw: object, index: int, prefix: str) -> tuple[dict[str, object], str]:
        if not isinstance(raw, dict):
            raise AccountInputError("자산·부채 항목이 올바르지 않습니다.")
        allowed_fields = {
            "name", "asset_class", "liability_class", "amount_krw",
            "unused_limit_krw", "valuation_date", "valuation_method",
            "valuation_source", "valuation_status", "uncertainty", "holder_role",
        }
        if not set(raw).issubset(allowed_fields):
            raise AccountInputError("자산·부채 항목에 지원하지 않는 필드가 있습니다.")
        name = _safe_label(raw.get("name"), "자산·부채")
        record_hash = hashlib.sha256(f"{prefix}:{index}:{name}".encode("utf-8")).hexdigest()[:10]
        record_id = f"{prefix}_{index}_{record_hash}"
        status = str(raw.get("valuation_status", ValuationStatus.CURRENT.value))
        if status not in {item.value for item in ValuationStatus}:
            raise AccountInputError("평가 상태가 올바르지 않습니다.")
        amount = raw.get("amount_krw")
        if status == ValuationStatus.MISSING.value:
            if amount not in {None, ""}:
                raise AccountInputError("평가 불가 항목은 금액을 비워야 합니다.")
            amount_int = None
            valuation_date = None
            method = ValuationMethod.NOT_AVAILABLE.value
            source = ValuationSource.NOT_AVAILABLE.value
            uncertainty = ValuationUncertainty.UNKNOWN.value
        else:
            amount_number = _number(amount, "금액")
            if not amount_number.is_integer():
                raise AccountInputError("원화 금액은 정수여야 합니다.")
            amount_int = int(amount_number)
            valuation_date = _canonical_date(
                raw.get("valuation_date", as_of), "평가일",
            )
            method = str(raw.get("valuation_method", ValuationMethod.USER_DECLARED.value))
            source = str(raw.get("valuation_source", ValuationSource.USER_LOCAL.value))
            uncertainty = str(raw.get("uncertainty", ValuationUncertainty.EXACT.value))
        holder = str(raw.get("holder_role", HolderRole.SELF.value))
        if method not in {item.value for item in ValuationMethod}:
            raise AccountInputError("평가 방법이 올바르지 않습니다.")
        if source not in {item.value for item in ValuationSource}:
            raise AccountInputError("평가 출처가 올바르지 않습니다.")
        if uncertainty not in {item.value for item in ValuationUncertainty}:
            raise AccountInputError("불확실성 값이 올바르지 않습니다.")
        if holder not in {item.value for item in HolderRole}:
            raise AccountInputError("명의 값이 올바르지 않습니다.")
        labels[record_id] = name
        return ({
            "record_id": record_id,
            "economic_claim_id": f"claim_{prefix}_{index}_{record_hash}",
            "amount": amount_int,
            "registered_holder_role": holder,
            "economic_owner_role": holder,
            "valuation_date": valuation_date,
            "valuation_method": method,
            "valuation_source": source,
            "valuation_status": status,
            "uncertainty": uncertainty,
        }, name)

    assets: list[dict[str, object]] = []
    for index, raw in enumerate(raw_assets):
        base, _name = common(raw, index, "asset")
        assert isinstance(raw, dict)
        asset_class = str(raw.get("asset_class"))
        if asset_class not in {item.value for item in AssetClass}:
            raise AccountInputError("자산 분류가 올바르지 않습니다.")
        amount = base.pop("amount")
        assets.append({
            **base, "asset_class": asset_class,
            "gross_value_krw": amount, "economic_value_krw": amount,
        })
    liabilities: list[dict[str, object]] = []
    for index, raw in enumerate(raw_liabilities):
        base, _name = common(raw, index, "liability")
        assert isinstance(raw, dict)
        liability_class = str(raw.get("liability_class"))
        if liability_class not in {item.value for item in LiabilityClass}:
            raise AccountInputError("부채 분류가 올바르지 않습니다.")
        unused = _number(raw.get("unused_limit_krw", 0), "미사용 한도")
        if not unused.is_integer():
            raise AccountInputError("미사용 한도는 원 단위 정수여야 합니다.")
        amount = base.pop("amount")
        liabilities.append({
            **base, "liability_class": liability_class,
            "gross_principal_krw": amount, "economic_principal_krw": amount,
            "unused_limit_krw": int(unused),
        })
    return ({
        "schema_version": "local-net-worth-snapshot/v1",
        "snapshot_id": snapshot_id,
        "as_of_date": as_of,
        "recorded_at_utc": now.isoformat(),
        "base_currency": "KRW",
        "assets": assets,
        "liabilities": liabilities,
    }, labels)


def save_net_worth(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise AccountInputError("순자산 요청은 JSON 객체여야 합니다.")
    labels: dict[str, str] = {}
    native = payload.get("schema_version") == "local-net-worth-snapshot/v1"
    if native:
        snapshot_payload = dict(payload)
    else:
        snapshot_payload, labels = _friendly_net_worth_payload(payload)
    try:
        record = LocalNetWorthHistoryStore(project_root / NET_WORTH_ROOT).save_snapshot(
            snapshot_payload
        )
    except (NetWorthValidationError, NetWorthPersistenceError) as error:
        raise AccountInputError(str(error)) from error
    if labels:
        _save_net_worth_labels(project_root, record.view.snapshot.snapshot_id, labels)
    return build_net_worth_data(project_root)


def _portfolio_day(value: object) -> date:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Seoul").tz_localize(None)
    return timestamp.date()


def _snapshot_currency_values(snapshot: object) -> dict[str, float]:
    currency = getattr(snapshot, "currency", None)
    if currency:
        cash = getattr(snapshot, "cash_balance", None)
        if cash is None:
            cash = getattr(snapshot, "available_cash", None)
        total = getattr(snapshot, "total_assets", None)
        securities = getattr(snapshot, "securities_value", None)
        if total is None and securities is not None:
            total = float(securities) + float(cash or 0.0)
        return {} if total is None else {str(currency): float(total)}
    result: dict[str, float] = {}
    for summary in getattr(snapshot, "currency_summaries", ()):
        if summary.securities_value is None:
            continue
        result[str(summary.currency)] = (
            float(summary.securities_value) + float(summary.cash_buying_power or 0.0)
        )
    return result


def _total_asset_components(
    project_root: Path, manual: Mapping[str, object],
) -> list[dict[str, object]]:
    from stock_data.gui.account_snapshot_service import (
        LocalAccountPortfolioService,
        LocalAccountSnapshotService,
        LocalAccountSourceSpec,
    )

    candidates = (
        ("toss_self", "Toss", project_root / "data/normalized/toss_account_snapshot/latest.json"),
        ("kb_self", "KB", project_root / "data/local/account_snapshots/kb_self.json"),
    )
    source_specs: list[object] = []
    for source_id, title, path in candidates:
        if path.is_file() and LocalAccountSnapshotService(path).load().displays_values:
            source_specs.append(LocalAccountSourceSpec(source_id, title, path))
    portfolio = LocalAccountPortfolioService(
        tuple(source_specs),
        history_root=project_root / "data/local/account_value_history",
    ).load()
    entries = {
        entry.source_id: entry
        for entry in portfolio.entries if entry.snapshot.displays_values
    }
    cutoffs: dict[str, datetime] = {}
    invalid_sources: set[str] = set()
    for source_id, entry in entries.items():
        try:
            parsed = datetime.fromisoformat(entry.snapshot.as_of or "")
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
            cutoffs[source_id] = parsed.astimezone(timezone.utc)
        except ValueError:
            invalid_sources.add(source_id)
    for series in portfolio.value_histories:
        cutoff = cutoffs.get(series.source_id)
        if cutoff is None:
            invalid_sources.add(series.source_id)
            continue
        try:
            if any(
                datetime.fromisoformat(point.observed_at).astimezone(timezone.utc) > cutoff
                for point in series.points
            ):
                invalid_sources.add(series.source_id)
        except ValueError:
            invalid_sources.add(series.source_id)

    # A source/currency can cross a schema generation (for example, Toss
    # securities-only to observable component sum).  Use only the metric with
    # the newest observation so two incompatible series are never added.
    selected: dict[tuple[str, str], object] = {}
    for series in portfolio.value_histories:
        if series.source_id not in entries or series.source_id in invalid_sources or not series.points:
            continue
        key = (series.source_id, series.currency)
        current = selected.get(key)
        if current is None or series.points[-1].observed_at > current.points[-1].observed_at:
            selected[key] = series

    points_by_component: dict[tuple[str, str], dict[date, float]] = {}
    for key, series in selected.items():
        points_by_component[key] = {
            _portfolio_day(point.observed_at): float(point.value)
            for point in series.points
        }
    for source_id, entry in entries.items():
        if not entry.snapshot.as_of:
            continue
        observed = _portfolio_day(entry.snapshot.as_of)
        for currency, value in _snapshot_currency_values(entry.snapshot).items():
            points_by_component.setdefault((source_id, currency), {})[observed] = value

    components = [{
        "source_id": f"{source_id}:{currency}",
        "currency": currency,
        "points": [{"date": day.isoformat(), "value": value} for day, value in sorted(points.items())],
    } for (source_id, currency), points in sorted(points_by_component.items()) if points]
    for row in manual.get("rows", []):
        if not row.get("included") or row.get("value_krw") is None or not row.get("as_of"):
            continue
        components.append({
            "source_id": str(row["source_id"]),
            "currency": "KRW",
            "points": [{"date": _portfolio_day(row["as_of"]).isoformat(), "value": float(row["value_krw"])}],
        })
    return components


def _fx_history(project_root: Path) -> list[dict[str, object]]:
    frame = datasets.load(
        project_root, "data/normalized/fred_usd_fx_daily",
        columns=["date", "dexkous"],
    )
    if frame is None or frame.empty:
        return []
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["dexkous"] = pd.to_numeric(work["dexkous"], errors="coerce")
    work = work.dropna(subset=["date", "dexkous"]).sort_values("date")
    return [
        {"date": row.date.date().isoformat(), "value": float(row.dexkous)}
        for row in work.itertuples(index=False) if float(row.dexkous) > 0
    ]


def _combine_total_asset_series(
    components: list[dict[str, object]], fx_history: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Forward-fill each source on a daily calendar and flag stale composites."""

    prepared: list[tuple[str, list[tuple[date, float]]]] = []
    for component in components:
        currency = str(component["currency"])
        points = sorted({
            date.fromisoformat(str(point["date"])): float(point["value"])
            for point in component["points"]
        }.items())
        if points:
            prepared.append((currency, points))
    if not prepared:
        return []
    fx_points = sorted({
        date.fromisoformat(str(point["date"])): float(point["value"])
        for point in fx_history
    }.items())
    first = min(points[0][0] for _currency, points in prepared)
    last = max(points[-1][0] for _currency, points in prepared)
    result: list[dict[str, object]] = []
    current = first
    while current <= last:
        total = 0.0
        included = 0
        partial = False
        observed_today = False
        for currency, points in prepared:
            prior = [point for point in points if point[0] <= current]
            if not prior:
                partial = True
                continue
            observed, value = prior[-1]
            observed_today |= observed == current
            partial |= (current - observed).days > 3
            if currency == "KRW":
                converted = value
            elif currency == "USD":
                prior_fx = [point for point in fx_points if point[0] <= current]
                if not prior_fx:
                    partial = True
                    continue
                fx_date, rate = prior_fx[-1]
                observed_today |= fx_date == current
                partial |= (current - fx_date).days > 3
                converted = value * rate
            else:
                partial = True
                continue
            total += converted
            included += 1
        if included:
            result.append({
                "t": current.isoformat(), "v": total,
                "total_invest_krw": total, "partial": partial or included < len(prepared),
                "observed": observed_today,
            })
        current += timedelta(days=1)
    return result


def calculate_return_metrics(
    history: list[dict[str, object]],
    cash_flows: list[dict[str, object]],
    *,
    broker_reported_pnl_krw: float | None,
) -> dict[str, dict[str, object]]:
    """Calculate flow-adjusted portfolio returns for the supported windows.

    Modified Dietz is ``(V_end - V_start - ΣCF) / (V_start + Σw_i·CF_i)``,
    where ``w_i`` is the fraction of the window remaining after flow ``i``.
    TWR chain-links genuine valuation boundaries. A dated flow creates an
    after-flow capital base ``V_previous + CF``; calendar values that were only
    forward-filled do not create a return factor. This splits the path at flow
    dates without treating an unobserved deposit day as an investment loss.
    """

    points = sorted(
        ({**point, "day": date.fromisoformat(str(point["t"])), "v": float(point["v"])} for point in history),
        key=lambda point: point["day"],
    )
    if not points:
        return {key: {"window": key, "reason": "투자 자산 관측이 없습니다."} for key in ("1M", "3M", "YTD", "ALL")}
    flow_rows = [{
        **flow,
        "day": date.fromisoformat(str(flow["date"])),
        "amount": float(flow["amount_krw"]),
    } for flow in cash_flows]
    end = points[-1]["day"]
    first = points[0]["day"]
    targets = {
        "1M": (pd.Timestamp(end) - pd.DateOffset(months=1)).date(),
        "3M": (pd.Timestamp(end) - pd.DateOffset(months=3)).date(),
        "YTD": date(end.year, 1, 1),
        "ALL": first,
    }
    metrics: dict[str, dict[str, object]] = {}
    for key, target in targets.items():
        eligible = [index for index, point in enumerate(points) if point["day"] <= target]
        if key != "ALL" and not eligible:
            metrics[key] = {
                "window": key,
                "reason": f"관측 시작 {first.strftime('%m-%d')} 이후만 계산 가능",
            }
            continue
        start_index = eligible[-1] if eligible else 0
        window_points = points[start_index:]
        if len(window_points) < 2:
            metrics[key] = {
                "window": key,
                "reason": "수익률 계산에는 관측이 2개 이상 필요합니다.",
            }
            continue
        start = window_points[0]["day"]
        start_value = float(window_points[0]["v"])
        end_value = float(window_points[-1]["v"])
        included_flows = [flow for flow in flow_rows if start < flow["day"] <= end]
        net_flows = sum(float(flow["amount"]) for flow in included_flows)
        true_pnl = end_value - start_value - net_flows
        duration = max((end - start).days, 1)
        weighted_flows = sum(
            ((end - flow["day"]).days / duration) * float(flow["amount"])
            for flow in included_flows
        )
        dietz_denominator = start_value + weighted_flows
        dietz = true_pnl / dietz_denominator * 100.0 if dietz_denominator else None
        twr_factor = 1.0
        twr_valid = True
        last_valuation = start_value
        pending_flows = 0.0
        for previous, current in zip(window_points, window_points[1:]):
            pending_flows += sum(
                float(flow["amount"])
                for flow in flow_rows
                if previous["day"] < flow["day"] <= current["day"]
            )
            if not bool(current.get("observed", True)):
                continue
            capital_base = last_valuation + pending_flows
            if capital_base <= 0 or float(current["v"]) < 0:
                twr_valid = False
                break
            twr_factor *= float(current["v"]) / capital_base
            last_valuation = float(current["v"])
            pending_flows = 0.0
        metrics[key] = {
            "window": key,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "start_value_krw": start_value,
            "end_value_krw": end_value,
            "net_flows_krw": net_flows,
            "true_pnl_krw": true_pnl,
            "return_pct_modified_dietz": dietz,
            "return_pct_twr": (twr_factor - 1.0) * 100.0 if twr_valid else None,
            "broker_reported_pnl_krw": broker_reported_pnl_krw,
            "partial": any(bool(point.get("partial")) for point in window_points),
            "reason": None,
        }
    return metrics


def _kospi_benchmark(
    project_root: Path, history: list[dict[str, object]],
) -> list[dict[str, object]]:
    valid_history = [
        point for point in history
        if point.get("v") is not None and math.isfinite(float(point["v"]))
    ]
    if not valid_history:
        return []
    try:
        frame = datasets.load(
            project_root, "data/normalized/kr_index_daily",
            columns=["date", "market", "symbol", "close"],
        )
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return []
    if frame is None or frame.empty:
        return []
    work = frame.copy()
    if "market" in work:
        work = work[work["market"].astype(str) == "KOSPI"]
    if "symbol" in work:
        work = work[work["symbol"].astype(str) == "KOSPI"]
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["date", "close"]).sort_values("date")
    if work.empty:
        return []
    account = pd.DataFrame({
        "date": pd.to_datetime([point["t"] for point in valid_history]),
        "value": [float(point["v"]) for point in valid_history],
    })
    joined = pd.merge_asof(account, work[["date", "close"]], on="date").dropna(subset=["close"])
    if joined.empty or not float(joined["close"].iloc[0]):
        return []
    scale = float(joined["value"].iloc[0]) / float(joined["close"].iloc[0])
    return [
        {"t": row.date.date().isoformat(), "v": float(row.close) * scale}
        for row in joined.itertuples(index=False)
    ]


def _attach_kospi_returns(
    metrics: dict[str, dict[str, object]], benchmark: list[dict[str, object]],
) -> None:
    values = {str(point["t"]): float(point["v"]) for point in benchmark}
    for metric in metrics.values():
        start = values.get(str(metric.get("start_date")))
        end = values.get(str(metric.get("end_date")))
        metric["kospi_return_pct"] = (
            (end / start - 1.0) * 100.0 if start is not None and end is not None and start else None
        )


def _month_true_pnl(
    history: list[dict[str, object]], cash_flows: list[dict[str, object]],
) -> float | None:
    if len(history) < 2:
        return None
    end = date.fromisoformat(str(history[-1]["t"]))
    target = date(end.year, end.month, 1)
    starts = [point for point in history if date.fromisoformat(str(point["t"])) <= target]
    if not starts:
        return None
    start = starts[-1]
    if bool(start.get("partial")) or bool(history[-1].get("partial")):
        return None
    start_day = date.fromisoformat(str(start["t"]))
    net_flows = sum(
        float(flow["amount_krw"])
        for flow in cash_flows
        if start_day < date.fromisoformat(str(flow["date"])) <= end
    )
    return float(history[-1]["v"]) - float(start["v"]) - net_flows


def _daily_true_change(
    history: list[dict[str, object]], cash_flows: list[dict[str, object]],
) -> float | None:
    if len(history) < 2:
        return None
    previous, current = history[-2], history[-1]
    if (
        bool(previous.get("partial")) or bool(current.get("partial"))
        or not bool(previous.get("observed", True))
        or not bool(current.get("observed", True))
    ):
        return None
    previous_day = date.fromisoformat(str(previous["t"]))
    current_day = date.fromisoformat(str(current["t"]))
    net_flows = sum(
        float(flow["amount_krw"])
        for flow in cash_flows
        if previous_day < date.fromisoformat(str(flow["date"])) <= current_day
    )
    return float(current["v"]) - float(previous["v"]) - net_flows


def build_account_page_data(project_root: Path) -> dict[str, object]:
    api = build_api_account_data(project_root)
    manual = build_manual_account_data(project_root)
    net_worth = build_net_worth_data(project_root)
    cash_flow_error: str | None = None
    try:
        cash_flows = build_cash_flow_data(project_root)
    except AccountInputError as error:
        cash_flow_error = str(error)
        cash_flows = {
            "schema_version": 1, "entries": [], "monthly_subtotals": [],
            "reason": cash_flow_error,
        }
    history = _combine_total_asset_series(
        _total_asset_components(project_root, manual), _fx_history(project_root),
    )
    benchmark = _kospi_benchmark(project_root, history)
    if cash_flow_error is None:
        return_metrics = calculate_return_metrics(
            history, cash_flows["entries"],
            broker_reported_pnl_krw=api.get("broker_reported_pnl_krw"),
        )
        _attach_kospi_returns(return_metrics, benchmark)
        daily_true_change = _daily_true_change(history, cash_flows["entries"])
        month_true_pnl = _month_true_pnl(history, cash_flows["entries"])
    else:
        return_metrics = {
            key: {"window": key, "reason": "입출금 원장을 읽을 수 없어 계산할 수 없습니다."}
            for key in ("1M", "3M", "YTD", "ALL")
        }
        daily_true_change = month_true_pnl = None
    invest_total = float(api["total_krw"]) + float(manual["total_krw"])
    other_net = net_worth.get("stored_net_worth_krw") if net_worth.get("exists") else None
    combined_net_worth = (
        invest_total + float(other_net)
        if other_net is not None and net_worth.get("complete") else None
    )
    rows = [
        {**row, "as_of_label": format_kst(row.get("as_of"))}
        for row in [*api["rows"], *manual["rows"], *net_worth["rows"]]
    ]
    sources = [{
        "name": row["name"], "as_of": row["as_of"],
        "as_of_label": row["as_of_label"],
        "included": bool(row["included"]), "note": row["note"],
    } for row in rows]
    net_worth = {
        **net_worth,
        "as_of_label": format_kst(net_worth.get("as_of")),
        "benchmark": _kospi_benchmark(project_root, net_worth.get("timeline", [])),
    }
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "invest_total_krw": invest_total,
            "net_worth_krw": combined_net_worth,
            "net_worth_as_of": net_worth.get("as_of"),
            "net_worth_as_of_label": format_kst(net_worth.get("as_of")),
            "fx_krw_per_usd": api.get("fx_krw_per_usd") or manual.get("fx_krw_per_usd"),
            "fx_as_of": api.get("fx_as_of") or manual.get("fx_as_of"),
            "fx_as_of_label": format_kst(api.get("fx_as_of") or manual.get("fx_as_of")),
            "fx_source": api.get("fx_source") or manual.get("fx_source"),
            "broker_reported_pnl_krw": api.get("broker_reported_pnl_krw"),
            "sources": sources,
        },
        "rows": rows,
        "manual_accounts": manual,
        "net_worth": net_worth,
        "cash_flows": cash_flows,
        "total_asset_history": history,
        "benchmark": benchmark,
        "return_metrics": return_metrics,
        "daily_true_change_krw": daily_true_change,
        "month_true_pnl_krw": month_true_pnl,
        "safety_note": "이 페이지는 로컬 보존 파일만 읽으며 외부 접속에서는 저장할 수 없습니다.",
    }


__all__ = [
    "AccountInputError", "build_account_page_data", "build_api_account_data",
    "build_cash_flow_data", "build_manual_account_data", "build_net_worth_data",
    "calculate_return_metrics", "delete_cash_flow", "load_cash_flows",
    "load_manual_accounts", "save_cash_flow", "save_manual_accounts", "save_net_worth",
]
