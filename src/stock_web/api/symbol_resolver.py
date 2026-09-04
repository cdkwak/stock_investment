"""Provider-free local symbol resolution shared by account write forms."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping


class SymbolResolutionError(ValueError):
    """Sanitized identity-resolution error suitable for a form response."""


def _candidates(project_root: Path, query: str) -> list[dict[str, object]]:
    from stock_web.api.stocks_page import search_stocks

    result = search_stocks(Path(project_root), query)
    matches = result.get("matches") if isinstance(result, Mapping) else None
    return [dict(item) for item in matches or [] if isinstance(item, Mapping)]


def resolve_local_symbol(
    project_root: Path, *, symbol: object, name: object,
) -> dict[str, str | None]:
    """Resolve one local catalog identity without making a provider call.

    A supplied symbol remains authoritative. A blank symbol requires either one
    exact name match or one unique search match. Ambiguous errors deliberately
    contain only the first three local code/name candidates.
    """
    clean_symbol = str(symbol or "").strip().upper()
    clean_name = str(name or "").strip()
    if clean_symbol:
        if clean_name:
            return {"symbol": clean_symbol, "name": clean_name, "currency": None}
        exact = [
            item for item in _candidates(project_root, clean_symbol)
            if str(item.get("symbol") or "").strip().upper() == clean_symbol
        ]
        if len(exact) == 1:
            selected = exact[0]
            return {
                "symbol": clean_symbol,
                "name": str(selected.get("name") or clean_symbol).strip(),
                "currency": str(selected.get("currency") or "").strip() or None,
            }
        return {"symbol": clean_symbol, "name": clean_name, "currency": None}

    if not clean_name or len(clean_name) > 80:
        raise SymbolResolutionError("종목명이 올바르지 않습니다.")
    matches = _candidates(project_root, clean_name)
    folded = clean_name.casefold()
    exact = [
        item for item in matches
        if folded in {
            str(item.get("name") or "").strip().casefold(),
            str(item.get("full_name") or "").strip().casefold(),
        }
    ]
    candidates = exact if exact else matches
    if len(candidates) == 1:
        selected = candidates[0]
        resolved_symbol = str(selected.get("symbol") or "").strip().upper()
        resolved_name = str(selected.get("name") or clean_name).strip()
        if not resolved_symbol or not resolved_name:
            raise SymbolResolutionError("로컬 종목 식별정보가 올바르지 않습니다.")
        return {
            "symbol": resolved_symbol,
            "name": resolved_name,
            "currency": str(selected.get("currency") or "").strip() or None,
        }
    if candidates:
        choices = " · ".join(
            f"{str(item.get('symbol') or '').strip()} {str(item.get('name') or '').strip()}"
            for item in candidates[:3]
        )
        raise SymbolResolutionError(f"종목코드를 고르세요: {choices}")
    raise SymbolResolutionError(
        "종목명을 찾을 수 없습니다. 종목코드를 직접 입력하거나 검색 결과에서 고르세요."
    )


__all__ = ["SymbolResolutionError", "resolve_local_symbol"]
