"""Provider-free local symbol resolution shared by account write forms."""
from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Mapping

import pandas as pd


class SymbolResolutionError(ValueError):
    """Sanitized identity-resolution error suitable for a form response."""


_SYMBOL_INDEX_CACHE: dict[str, tuple[str, dict[str, dict[str, str]]]] = {}
_SYMBOL_INDEX_LOCK = threading.Lock()


def _global_equity_registry() -> Mapping[str, Mapping[str, object]]:
    """Return the optional Data-owned global-equity identity registry."""
    try:
        from stock_data.contracts.global_equity import GLOBAL_EQUITY_REGISTRY
    except ImportError:
        GLOBAL_EQUITY_REGISTRY = {}
    return GLOBAL_EQUITY_REGISTRY if isinstance(GLOBAL_EQUITY_REGISTRY, Mapping) else {}


def global_equity_identities() -> tuple[dict[str, object], ...]:
    """Project registered global equities into the web identity vocabulary."""
    identities: list[dict[str, object]] = []
    for raw_symbol, raw_spec in _global_equity_registry().items():
        if not isinstance(raw_spec, Mapping):
            continue
        symbol = _text(raw_symbol).upper()
        name = _text(raw_spec.get("korean_name"))
        exchange = _text(raw_spec.get("official_exchange"))
        currency = _text(raw_spec.get("expected_currency"))
        underlying = _text(raw_spec.get("underlying_kr_symbol"))
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol)
            or not name or not exchange or currency != "USD"
        ):
            continue
        raw_type = _text(raw_spec.get("security_type"))
        security_type = "ADR" if raw_type == "DEPOSITARY_RECEIPT" else raw_type
        if not security_type:
            continue
        base_name = re.sub(r"\s*\(ADR\)\s*$", "", name, flags=re.IGNORECASE)
        aliases = [name, f"{base_name} ADR"]
        if base_name.upper().startswith("SK") and len(base_name) > 2:
            aliases.append(f"{base_name[2:]} ADR")
        identities.append({
            "market": "US 주식",
            "symbol": symbol,
            "name": name,
            "currency": currency,
            "security_type": security_type,
            "source": "global_equity_registry",
            "exchange": exchange,
            "underlying_kr_symbol": underlying or None,
            "aliases": tuple(dict.fromkeys(alias for alias in aliases if alias)),
        })
    return tuple(identities)


def global_equity_identity(symbol: object) -> dict[str, object] | None:
    clean_symbol = _text(symbol).upper()
    return next(
        (identity for identity in global_equity_identities() if identity["symbol"] == clean_symbol),
        None,
    )


def _dataset_signature(project_root: Path) -> str:
    """Return the identity datasets' cheap cache signature."""
    root = Path(project_root).resolve()
    parts: list[str] = []
    for relative in (
        "data/normalized/kr_equity_master",
        "data/normalized/kr_etf_universe_daily",
        "data/normalized/kr_etf_master",
    ):
        dataset = root / relative
        try:
            paths = sorted(dataset.rglob("*.parquet"))
        except OSError:
            paths = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            parts.append(
                f"{path.relative_to(root).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}"
            )
    return "|".join(parts) or "MISSING"


def _parquet_frames(dataset: Path, columns: list[str]) -> list[pd.DataFrame]:
    """Read physical files without interpreting directory names as partitions."""
    return [
        pd.read_parquet(path, columns=columns, partitioning=None)
        for path in sorted(dataset.rglob("*.parquet"))
    ]


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _identity(
    *, market: str, symbol: object, name: object, currency: str,
    security_type: object, source: str,
) -> dict[str, str] | None:
    clean_symbol = _text(symbol).upper()
    clean_name = _text(name)
    clean_type = _text(security_type)
    if not clean_symbol or not clean_name or not clean_type:
        return None
    return {
        "market": market,
        "symbol": clean_symbol,
        "name": clean_name,
        "currency": currency,
        "security_type": clean_type,
        "source": source,
    }


def _kr_stock_identities(project_root: Path) -> tuple[dict[str, str], ...]:
    dataset = Path(project_root) / "data/normalized/kr_equity_master"
    try:
        frames = _parquet_frames(dataset, [
            "symbol", "name", "market", "delisting_date", "security_type_name",
        ])
        if not frames:
            return ()
        frame = pd.concat(frames, ignore_index=True)
        active = frame.loc[
            frame["delisting_date"].isna()
            & frame["security_type_name"].astype(str).eq("보통주")
            & frame["market"].astype(str).isin({"KOSPI", "KOSDAQ"})
            & frame["symbol"].astype(str).str.fullmatch(r"\d{6}")
        ]
        if active.duplicated(["market", "symbol"]).any():
            return ()
        rows = (
            _identity(
                market=_text(row.market), symbol=row.symbol, name=row.name,
                currency="KRW", security_type=row.security_type_name,
                source="kr_equity_master",
            )
            for row in active.sort_values(["market", "symbol"], kind="stable").itertuples(index=False)
        )
        return tuple(item for item in rows if item is not None)
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return ()


def _kr_etf_universe_identities(
    project_root: Path,
) -> tuple[dict[str, str], ...] | None:
    dataset = Path(project_root) / "data/normalized/kr_etf_universe_daily"
    try:
        frames = _parquet_frames(dataset, [
            "source_date", "symbol", "name", "market", "security_type", "listing_status",
        ])
        if not frames:
            return None
        frame = pd.concat(frames, ignore_index=True)
        frame["_source_date"] = pd.to_datetime(frame["source_date"], errors="coerce")
        if frame.empty or frame["_source_date"].isna().any():
            return None
        latest = frame.loc[frame["_source_date"].eq(frame["_source_date"].max())].copy()
        if (
            latest.empty
            or latest["symbol"].astype(str).duplicated().any()
            or not latest["symbol"].astype(str).str.fullmatch(r"[0-9A-Z]{6}").all()
            or latest[["symbol", "name", "market", "security_type", "listing_status"]].isna().any().any()
            or not latest["market"].astype(str).eq("KRX").all()
            or not latest["security_type"].astype(str).eq("ETF").all()
            or not latest["listing_status"].astype(str).eq("LISTED_AT_SOURCE_DATE").all()
        ):
            return None
        rows = (
            _identity(
                market="KRX", symbol=row.symbol, name=row.name, currency="KRW",
                security_type="ETF", source="kr_etf_universe_daily",
            )
            for row in latest.sort_values("symbol", kind="stable").itertuples(index=False)
        )
        resolved = tuple(item for item in rows if item is not None)
        return resolved or None
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return None


def _kr_etf_master_identities(project_root: Path) -> tuple[dict[str, str], ...]:
    dataset = Path(project_root) / "data/normalized/kr_etf_master"
    try:
        frames = _parquet_frames(
            dataset, ["symbol", "name", "market", "security_type", "listing_status"],
        )
        if not frames:
            return ()
        frame = pd.concat(frames, ignore_index=True)
        active = frame.loc[
            frame["market"].astype(str).eq("KRX")
            & frame["security_type"].astype(str).eq("ETF")
            & frame["listing_status"].astype(str).eq("LISTED_AT_SOURCE_DATE")
        ]
        if active["symbol"].astype(str).duplicated().any():
            return ()
        rows = (
            _identity(
                market="KRX", symbol=row.symbol, name=row.name, currency="KRW",
                security_type="ETF", source="kr_etf_master",
            )
            for row in active.sort_values("symbol", kind="stable").itertuples(index=False)
        )
        return tuple(item for item in rows if item is not None)
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return ()


def _us_etf_identities() -> tuple[dict[str, str], ...]:
    from stock_data.contracts.global_etf import GLOBAL_ETF_REGISTRY

    identities: dict[str, dict[str, str]] = {}
    for symbol, spec in GLOBAL_ETF_REGISTRY.items():
        item = _identity(
            market="US ETF", symbol=symbol, name=spec.get("official_fund_name"),
            currency="USD", security_type=spec.get("instrument_type"),
            source="global_etf_registry",
        )
        if item is not None:
            identities[item["symbol"]] = item

    # The accepted GUI catalog intentionally contains several display-only ETFs
    # in addition to the contract-registry daily lane.
    from stock_data.gui.services import US_ETF_CHART_IDENTITIES

    for identity in US_ETF_CHART_IDENTITIES:
        item = _identity(
            market="US ETF", symbol=identity.symbol, name=identity.name,
            currency="USD", security_type=identity.security_type,
            source="us_etf_catalog",
        )
        if item is not None:
            identities.setdefault(item["symbol"], item)
    return tuple(identities.values())


def _build_symbol_index(project_root: Path) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    universe = _kr_etf_universe_identities(project_root)
    ordered = (
        *_kr_stock_identities(project_root),
        *(universe if universe is not None else _kr_etf_master_identities(project_root)),
        *_us_etf_identities(),
        *(
            {
                key: str(value)
                for key, value in identity.items()
                if key in {"market", "symbol", "name", "currency", "security_type", "source"}
            }
            for identity in global_equity_identities()
        ),
    )
    for item in ordered:
        index.setdefault(item["symbol"], item)
    return index


def _symbol_index(project_root: Path) -> dict[str, dict[str, str]]:
    root = Path(project_root).resolve()
    key = str(root)
    signature = _dataset_signature(root)
    cached = _SYMBOL_INDEX_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    with _SYMBOL_INDEX_LOCK:
        cached = _SYMBOL_INDEX_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        index = _build_symbol_index(root)
        _SYMBOL_INDEX_CACHE[key] = (signature, index)
        return index


def resolve_symbol_code(project_root: Path, code: object) -> dict[str, object]:
    """Resolve one exact KRX code or U.S. ETF ticker from retained catalogs."""
    clean_code = str(code or "").strip().upper()
    if not (
        re.fullmatch(r"[0-9A-Z]{6}", clean_code)
        or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", clean_code)
    ):
        return {"found": False, "reason": "미등록 코드"}
    selected = _symbol_index(Path(project_root)).get(clean_code)
    if selected is None:
        return {"found": False, "reason": "미등록 코드"}
    return {"found": True, **selected}


def _candidates(project_root: Path, query: str) -> list[dict[str, object]]:
    from stock_web.api.stocks_page import search_stocks

    result = search_stocks(Path(project_root), query)
    matches = result.get("matches") if isinstance(result, Mapping) else None
    return [dict(item) for item in matches or [] if isinstance(item, Mapping)]


def resolve_local_symbol(
    project_root: Path, *, symbol: object, name: object,
) -> dict[str, str | None]:
    """Resolve one local catalog identity without making a provider call.

    A supplied symbol and name remain authoritative. A blank name is filled
    only for an exact registered symbol. A blank symbol requires either one
    exact name match or one unique search match. Ambiguous errors deliberately
    contain only the first three local code/name candidates.
    """
    clean_symbol = str(symbol or "").strip().upper()
    clean_name = str(name or "").strip()
    if clean_symbol:
        if clean_name:
            return {"symbol": clean_symbol, "name": clean_name, "currency": None}
        resolved = resolve_symbol_code(project_root, clean_symbol)
        if resolved.get("found") is True:
            return {
                "symbol": str(resolved["symbol"]),
                "name": str(resolved["name"]),
                "currency": str(resolved["currency"]),
            }
        raise SymbolResolutionError("미등록 코드입니다. 종목명으로 검색하세요.")

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


__all__ = [
    "SymbolResolutionError", "global_equity_identities", "global_equity_identity",
    "resolve_local_symbol", "resolve_symbol_code",
]
