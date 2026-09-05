from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any

from stock_data.contracts.kbsec_transactions import (
    KBSecTransactionCategory,
    KBSecTransactionDirection,
)
from stock_data.providers.kbsec.client import (
    KBSecBusinessError,
    KBSecClient,
    KBSecResponse,
    KBSecResponseError,
)


OPERATION = "SWQA2301"
TRANSACTIONS_PATH = "/api/v1/swqa2301"
MAX_ROWS_PER_PAGE = 6

_REQUEST_DEFAULTS = {
    "strt_no": "",
    "crdt_crd_isnc_info": "",
    "is_no": "",
    "dl_md_ccd": "",
    "srt_clsf": "",
    "md_isnc_tno": "",
    "inq_clsf4": "0",
    "dl_clsf": "",
    "inq_clsf3": "0",
    "inq_clsf6": "0",
    "inq_clsf5": "0",
    "isng_bl_at_trsns_xcl_f": "",
    "inq_clsf2": "0",
    "inq_clsf1": "0",
    "onl_prt_ccd": "",
}
_LANDING_ROW_FIELDS = (
    "dl_dt", "dl_typ_cd", "smry_typ_cd", "smry_nm", "dl_amt",
    "incm_tx", "rsdnt_tx",
)
_ACCOUNT_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){9,13}\d(?!\d)")
_INTEGER = re.compile(r"^[+-]?\d+$")


class KBSecTransactionContractError(ValueError):
    pass


def transaction_request_body(
    start_date: date, end_date: date, *, next_key: str = "",
) -> dict[str, str]:
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise TypeError("transaction dates must be date values")
    if start_date > end_date:
        raise ValueError("transaction start date follows end date")
    if not isinstance(next_key, str):
        raise TypeError("transaction next_key must be text")
    return {
        "end_dt": end_date.strftime("%Y%m%d"),
        **_REQUEST_DEFAULTS,
        "nxt_key": next_key,
        "inq_clsf": "1",
        "strt_dt": start_date.strftime("%Y%m%d"),
    }


class KBSecTransactionsClient(KBSecClient):
    """Read-only SWQA2301 client reusing the established in-memory OAuth path."""

    def transaction_history_page(
        self, start_date: date, end_date: date, *, next_key: str = "",
    ) -> KBSecResponse:
        payload, http_status = self._post(
            TRANSACTIONS_PATH,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=transaction_request_body(start_date, end_date, next_key=next_key),
        )
        header = payload["dataHeader"]
        result_code = self._safe(header.get("resultCode")) or ""
        process_code = self._safe(header.get("processCode")) or ""
        result_message = self._safe(header.get("resultMessage"))
        process_message = self._safe(header.get("processMessage"))
        # 0011 = complete; 0015 = "조회가 계속됩니다 · 다음키" (more pages follow, nxt_key set).
        # Verified live 2026-09-05: the first SWQA2301 page of a multi-page window answers 0015.
        if result_code != "200" or process_code not in {"0011", "0015"}:
            raise KBSecBusinessError(
                "KB transaction history rejected",
                http_status=http_status,
                result_code=result_code,
                result_message=result_message,
                process_code=process_code,
                process_message=process_message,
            )
        return KBSecResponse(
            result_code,
            process_code,
            dict(payload["dataBody"]),
            payload,
            http_status,
            result_message=result_message,
            process_message=process_message,
        )


def raw_row_sha256(row: Any) -> str:
    try:
        body = json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise KBSecTransactionContractError("KB transaction row is not canonical JSON") from None
    return hashlib.sha256(body).hexdigest()


def project_transaction_page_for_landing(
    response: KBSecResponse, *, retrieved_at: datetime, page_number: int,
) -> dict[str, Any]:
    """Produce a Landing-safe page with required source bytes but no identifiers."""

    if not isinstance(response, KBSecResponse):
        raise KBSecTransactionContractError("KB transaction response type differs")
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise KBSecTransactionContractError("retrieved_at must be timezone-aware")
    if type(page_number) is not int or page_number < 1:
        raise KBSecTransactionContractError("page_number must be positive")
    body = response.data_body
    source_rows = body.get("Record1")
    rows = source_rows if isinstance(source_rows, list) else []
    next_key = body.get("nxt_key")

    projected_rows: list[dict[str, Any]] = []
    for row in rows:
        projected = {
            field: row.get(field) if isinstance(row, dict) else None
            for field in _LANDING_ROW_FIELDS
        }
        projected["raw_row_sha256"] = raw_row_sha256(row)
        projected_rows.append(projected)
    return {
        "schema_version": 1,
        "provider": "KB_SECURITIES",
        "source_operation": OPERATION,
        "retrieved_at": retrieved_at.isoformat(),
        "page_number": page_number,
        "record1_is_list": isinstance(source_rows, list),
        "declared_row_count": body.get("grid_cnt1"),
        "row_count": len(projected_rows),
        "next_key_is_text": isinstance(next_key, str),
        "has_next_page": bool(next_key.strip()) if isinstance(next_key, str) else False,
        "rows": projected_rows,
    }


def validate_landing_transaction_page(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "provider", "source_operation", "retrieved_at",
        "page_number", "record1_is_list", "declared_row_count", "row_count",
        "next_key_is_text", "has_next_page", "rows",
    }:
        raise KBSecTransactionContractError("KB Landing page schema differs")
    rows = payload["rows"]
    if (
        payload["schema_version"] != 1
        or payload["provider"] != "KB_SECURITIES"
        or payload["source_operation"] != OPERATION
        or payload["record1_is_list"] is not True
        or payload["next_key_is_text"] is not True
        or not isinstance(rows, list)
        or len(rows) > MAX_ROWS_PER_PAGE
        or payload["row_count"] != len(rows)
    ):
        raise KBSecTransactionContractError("KB Landing page identity differs")
    try:
        declared = int(str(payload["declared_row_count"]).strip())
    except ValueError:
        raise KBSecTransactionContractError("KB transaction page count is invalid") from None
    if declared != len(rows):
        raise KBSecTransactionContractError("KB transaction page count does not reconcile")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            *_LANDING_ROW_FIELDS, "raw_row_sha256",
        }:
            raise KBSecTransactionContractError("KB Landing transaction row schema differs")


def _krw_integer(value: Any, field: str, *, blank_is_zero: bool = False) -> int:
    if not isinstance(value, str):
        raise KBSecTransactionContractError(f"{field} must be provider text")
    raw = value.strip().replace(",", "")
    if not raw and blank_is_zero:
        return 0
    if _INTEGER.fullmatch(raw) is None:
        raise KBSecTransactionContractError(f"{field} must be integer KRW")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        raise KBSecTransactionContractError(f"{field} must be integer KRW") from None
    if not number.is_finite() or number != number.to_integral_value() or number < 0:
        raise KBSecTransactionContractError(f"{field} must be non-negative integer KRW")
    return int(number)


def _summary(value: Any) -> str:
    if not isinstance(value, str):
        raise KBSecTransactionContractError("smry_nm must be provider text")
    summary = " ".join(value.split())
    if not summary or len(summary) > 200 or _ACCOUNT_NUMBER.search(summary):
        raise KBSecTransactionContractError("smry_nm is empty or identifier-like")
    return summary


def _code(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise KBSecTransactionContractError(f"{field} must be provider text")
    code = value.strip()
    if not code or len(code) > 20:
        raise KBSecTransactionContractError(f"{field} is invalid")
    return code


def classify_transaction(
    summary_name: str, transaction_type_code: str, summary_type_code: str,
) -> tuple[KBSecTransactionDirection, KBSecTransactionCategory]:
    """Classify only documented summaries; unrecognized summaries stay OTHER."""

    compact = re.sub(r"\s+", "", summary_name)
    if any(token in compact for token in ("배당", "분배금")):
        return KBSecTransactionDirection.IN, KBSecTransactionCategory.DIVIDEND
    if any(token in compact for token in ("오픈뱅킹입금", "전자금융입금", "이체입금")):
        return KBSecTransactionDirection.IN, KBSecTransactionCategory.DEPOSIT
    if any(token in compact for token in ("송금출금", "이체출금")):
        return KBSecTransactionDirection.OUT, KBSecTransactionCategory.WITHDRAWAL
    if any(token in compact for token in ("세금", "세액", "원천징수")):
        return KBSecTransactionDirection.OUT, KBSecTransactionCategory.TAX
    if "수수료" in compact:
        return KBSecTransactionDirection.OUT, KBSecTransactionCategory.FEE

    if any(token in compact for token in ("출금", "출고", "매수", "납부", "상환")):
        direction = KBSecTransactionDirection.OUT
    elif any(token in compact for token in ("입금", "입고", "매도", "환급")):
        direction = KBSecTransactionDirection.IN
    elif transaction_type_code == "01":
        direction = KBSecTransactionDirection.IN
    elif transaction_type_code == "02":
        direction = KBSecTransactionDirection.OUT
    else:
        raise KBSecTransactionContractError(
            "unknown KB transaction direction cannot be inferred without guessing"
        )
    # The codes remain part of the retained contract even when no reviewed code
    # mapping exists; they are never used to invent a non-OTHER category.
    _ = summary_type_code
    return direction, KBSecTransactionCategory.OTHER


def normalize_landing_transaction_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != {
        *_LANDING_ROW_FIELDS, "raw_row_sha256",
    }:
        raise KBSecTransactionContractError("KB Landing transaction row schema differs")
    try:
        transaction_date = datetime.strptime(row["dl_dt"], "%Y%m%d").date()
    except (TypeError, ValueError):
        raise KBSecTransactionContractError("dl_dt is invalid") from None
    summary = _summary(row["smry_nm"])
    transaction_code = _code(row["dl_typ_cd"], "dl_typ_cd")
    summary_code = _code(row["smry_typ_cd"], "smry_typ_cd")
    direction, category = classify_transaction(
        summary, transaction_code, summary_code,
    )
    amount = _krw_integer(row["dl_amt"], "dl_amt", blank_is_zero=True)
    tax = _krw_integer(row["incm_tx"], "incm_tx", blank_is_zero=True) + _krw_integer(
        row["rsdnt_tx"], "rsdnt_tx", blank_is_zero=True,
    )
    digest = row["raw_row_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise KBSecTransactionContractError("raw_row_sha256 is invalid")
    if category is not KBSecTransactionCategory.OTHER and amount <= 0:
        raise KBSecTransactionContractError("cash-flow transaction amount must be positive")
    return {
        "date": transaction_date.isoformat(),
        "direction": direction.value,
        "category": category.value,
        "amount_krw": amount,
        "tax_krw": tax,
        "summary_name": summary,
        "transaction_type_code": transaction_code,
        "summary_type_code": summary_code,
        "raw_row_sha256": digest,
    }


def continuation_key(response: KBSecResponse) -> str:
    value = response.data_body.get("nxt_key")
    if not isinstance(value, str):
        raise KBSecResponseError("KB transaction continuation key is invalid")
    return value.strip()


__all__ = [
    "KBSecTransactionContractError",
    "KBSecTransactionsClient",
    "MAX_ROWS_PER_PAGE",
    "OPERATION",
    "TRANSACTIONS_PATH",
    "classify_transaction",
    "continuation_key",
    "normalize_landing_transaction_row",
    "project_transaction_page_for_landing",
    "raw_row_sha256",
    "transaction_request_body",
    "validate_landing_transaction_page",
]
