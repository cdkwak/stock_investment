from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.contracts.toss_account_snapshot import (
    TOSS_ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
    TOSS_ACCOUNT_SOURCE,
    TOSS_ACCOUNT_SOURCE_SPEC_VERSION,
)
from stock_data.contracts.kbsec_account_snapshot import (
    KB_ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
    KB_ACCOUNT_SOURCE,
    KB_ACCOUNT_SOURCE_EVIDENCE_VERSION,
    KB_ACCOUNT_SOURCE_MODE,
    KB_ACCOUNT_SOURCE_OPERATION,
    KB_ACCOUNT_UNSUPPORTED_FIELDS,
)
from stock_data.orchestration.account_privacy import redact_account_text
from stock_data.gui.manual_account_snapshot import (
    MANUAL_ACCOUNT_SECTIONS,
    ManualAccountSnapshot,
    validate_manual_account_snapshot,
)
from stock_data.gui.manual_account_store import (
    LocalManualAccountStore,
    ManualAccountRegistry,
)
from stock_data.gui.account_value_history import (
    AccountValueHistorySeries,
    load_account_value_history,
)


def _require_identifier_free_position_text(symbol: str, name: str) -> None:
    for value in (symbol, name):
        if redact_account_text(value, limit=max(1, len(value))) != value:
            raise ValueError(
                "account position display text contains sensitive data"
            )


class AccountSnapshotState(str, Enum):
    NOT_AVAILABLE = "NOT_AVAILABLE"
    LOCAL_MOCK = "LOCAL_MOCK"
    TOSS_READ_ONLY = "TOSS_READ_ONLY"
    KB_READ_ONLY = "KB_READ_ONLY"
    FAMILY_LOCAL_MANUAL = "FAMILY_LOCAL_MANUAL"
    MANUAL_HOLDINGS_BASIS = "MANUAL_HOLDINGS_BASIS"


@dataclass(frozen=True)
class AccountPositionView:
    symbol: str
    name: str
    quantity: float
    market_value: float | None
    realized_pnl: float | None
    unrealized_pnl: float | None
    purchase_amount: float | None = None
    average_purchase_price: float | None = None
    current_price: float | None = None
    orderable_quantity: float | None = None
    currency: str | None = None
    weight_pct: float | None = None
    return_pct: float | None = None
    price_provider: str | None = None
    price_provider_symbol: str | None = None
    price_unit: str | None = None
    price_as_of: str | None = None
    price_finality: str | None = None
    unrealized_pnl_after_cost: float | None = None
    daily_pnl: float | None = None
    return_pct_after_cost: float | None = None
    daily_return_pct: float | None = None
    commission: float | None = None
    tax: float | None = None


@dataclass(frozen=True)
class AccountAssetPoint:
    date: str
    total_assets: float


@dataclass(frozen=True)
class AccountCurrencySummaryView:
    currency: str
    purchase_amount: float | None
    securities_value: float | None
    securities_value_after_cost: float | None
    unrealized_pnl: float | None
    unrealized_pnl_after_cost: float | None
    daily_pnl: float | None
    cash_buying_power: float | None = None


@dataclass(frozen=True)
class AccountSnapshotView:
    """Read-only local account-shaped data; never a live brokerage claim."""

    state: AccountSnapshotState
    provider: str | None = None
    source_mode: str | None = None
    registered_holder_scope: str = "UNSPECIFIED"
    economic_attribution_scope: str = "UNSPECIFIED"
    include_in_user_fund_total: bool = False
    legal_ownership_claimed: bool = False
    as_of: str | None = None
    last_reconciled_at: str | None = None
    currency: str | None = None
    total_assets: float | None = None
    securities_value: float | None = None
    cash_balance: float | None = None
    available_cash: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    positions: tuple[AccountPositionView, ...] = ()
    asset_history: tuple[AccountAssetPoint, ...] = ()
    currency_summaries: tuple[AccountCurrencySummaryView, ...] = ()
    reason: str | None = None
    freshness: str = "LOCAL_VALIDATED"

    @property
    def available(self) -> bool:
        return self.state in {
            AccountSnapshotState.LOCAL_MOCK,
            AccountSnapshotState.TOSS_READ_ONLY,
            AccountSnapshotState.KB_READ_ONLY,
            AccountSnapshotState.FAMILY_LOCAL_MANUAL,
            AccountSnapshotState.MANUAL_HOLDINGS_BASIS,
        }

    @property
    def displays_values(self) -> bool:
        return self.available and self.freshness in {
            "CURRENT", "LOCAL_VALIDATED", "AS_RETRIEVED", "DATED_MANUAL_BASIS",
        }


@dataclass(frozen=True)
class LocalAccountSourceSpec:
    source_id: str
    title: str
    snapshot_path: Path
    enabled: bool = True
    unavailable_reason: str = "RUNTIME_CONFIG_REQUIRED"


@dataclass(frozen=True)
class AccountPortfolioEntryView:
    source_id: str
    title: str
    snapshot: AccountSnapshotView


@dataclass(frozen=True)
class AccountCurrencyTotalView:
    currency: str
    total_assets: float | None
    included_accounts: int
    complete: bool


@dataclass(frozen=True)
class AccountPortfolioView:
    entries: tuple[AccountPortfolioEntryView, ...]
    user_fund_totals: tuple[AccountCurrencyTotalView, ...]
    value_histories: tuple[AccountValueHistorySeries, ...] = ()
    history_reason: str | None = None


@dataclass(frozen=True)
class AccountPortfolioCurrencyView:
    currency: str
    account_count: int
    total_assets: float | None
    securities_value: float | None
    cash_balance: float | None
    available_cash: float | None
    unrealized_pnl: float | None
    complete: bool
    reason: str | None
    unrealized_pnl_after_cost: float | None = None
    daily_pnl: float | None = None


@dataclass(frozen=True)
class AccountHoldingView:
    account_title: str
    provider_scope: str
    ownership_scope: str
    symbol: str
    name: str
    currency: str | None
    quantity: float
    purchase_amount: float | None
    market_value: float | None
    unrealized_pnl: float | None
    weight_pct: float | None
    as_of: str | None
    freshness: str
    average_purchase_price: float | None = None
    current_price: float | None = None
    return_pct: float | None = None
    price_provider: str | None = None
    price_provider_symbol: str | None = None
    price_unit: str | None = None
    price_as_of: str | None = None
    price_finality: str | None = None
    orderable_quantity: float | None = None
    unrealized_pnl_after_cost: float | None = None
    daily_pnl: float | None = None
    return_pct_after_cost: float | None = None
    daily_return_pct: float | None = None
    commission: float | None = None
    tax: float | None = None


@dataclass(frozen=True)
class AccountAllocationView:
    currency: str
    label: str
    market_value: float
    weight_pct: float
    is_other: bool
    exact_breakdown: tuple[str, ...]


@dataclass(frozen=True)
class AccountHistorySeriesView:
    account_title: str
    currency: str
    points: tuple[AccountAssetPoint, ...]
    as_of: str | None
    metric: str = "TOTAL_ASSETS"
    interpretation: str = "ACCOUNT_SCALE_NOT_PERFORMANCE_EXTERNAL_FLOWS_UNSEPARATED"


@dataclass(frozen=True)
class AccountSourceOptionView:
    source_id: str
    label: str
    displayable: bool
    provider_scope: str
    ownership_scope: str
    as_of: str | None
    freshness: str
    reason: str | None


@dataclass(frozen=True)
class AccountSourceActionView:
    """Identifier-free operational context for one local account source."""

    source_id: str
    last_accepted_at: str | None
    freshness: str
    reason: str | None
    refresh_capability: str
    last_outcome: str
    last_outcome_at: str | None
    next_eligibility: str


@dataclass(frozen=True)
class AccountPortfolioPresentationView:
    currencies: tuple[AccountPortfolioCurrencyView, ...]
    holdings: tuple[AccountHoldingView, ...]
    allocations: tuple[AccountAllocationView, ...]
    histories: tuple[AccountHistorySeriesView, ...]
    displayable_accounts: int
    unavailable_accounts: int
    as_of_values: tuple[str, ...]
    freshness_values: tuple[str, ...]
    source_options: tuple[AccountSourceOptionView, ...]
    selected_source_id: str | None
    scope_label: str
    scope_complete: bool
    scope_reason: str | None
    history_reason: str | None

    @property
    def available(self) -> bool:
        return self.displayable_accounts > 0


class LocalAccountSnapshotService:
    """Load only an explicitly supplied local JSON snapshot, fail closed."""

    SCHEMA_VERSION = 2
    _FIELDS = (
        "total_assets",
        "securities_value",
        "cash_balance",
        "available_cash",
        "realized_pnl",
        "unrealized_pnl",
    )
    _REQUIRED_KEYS = frozenset({
        "schema_version", "state", "as_of", "last_reconciled_at", "currency",
        "positions", "asset_history", *_FIELDS,
    })

    def __init__(self, snapshot_path: Path | None = None):
        self.snapshot_path = Path(snapshot_path) if snapshot_path is not None else None

    def load(self) -> AccountSnapshotView:
        if self.snapshot_path is None:
            return self._unavailable("ACCOUNT_SNAPSHOT_NOT_CONFIGURED")
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            return self._validate(payload)
        except FileNotFoundError:
            return self._unavailable("ACCOUNT_SNAPSHOT_MISSING")
        except PermissionError:
            return self._unavailable("ACCOUNT_SNAPSHOT_LOCKED")
        except (OSError, UnicodeError):
            return self._unavailable("ACCOUNT_SNAPSHOT_READ_FAILED")
        except (json.JSONDecodeError, TypeError, ValueError):
            return self._unavailable("ACCOUNT_SNAPSHOT_INVALID")

    def _validate(self, payload: Any) -> AccountSnapshotView:
        if not isinstance(payload, dict):
            raise TypeError("snapshot must be a JSON object")
        if payload.get("provider") == TOSS_ACCOUNT_SOURCE:
            return self._validate_toss(payload)
        if payload.get("provider") == KB_ACCOUNT_SOURCE:
            return self._validate_kb(payload)
        if payload.get("state") == AccountSnapshotState.FAMILY_LOCAL_MANUAL.value:
            return self._validate_family_local(payload)
        if set(payload) != self._REQUIRED_KEYS:
            raise ValueError("snapshot keys do not match schema v1")
        if payload["schema_version"] != self.SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        if payload["state"] != AccountSnapshotState.LOCAL_MOCK.value:
            raise ValueError("only LOCAL_MOCK snapshots are accepted")

        normalized_as_of = self._iso_value(payload["as_of"], "as_of")
        reconciled_at = self._iso_value(
            payload["last_reconciled_at"], "last_reconciled_at",
        )

        currency = payload["currency"]
        if not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
            raise ValueError("currency must be a three-letter uppercase code")

        values = {field: self._optional_number(payload[field], field) for field in self._FIELDS}
        positions = self._positions(payload["positions"])
        history = self._asset_history(payload["asset_history"], normalized_as_of)
        return AccountSnapshotView(
            state=AccountSnapshotState.LOCAL_MOCK,
            provider="LOCAL",
            source_mode="LOCAL_MANUAL",
            registered_holder_scope="SELF",
            economic_attribution_scope="SELF",
            include_in_user_fund_total=True,
            as_of=normalized_as_of,
            last_reconciled_at=reconciled_at,
            currency=currency,
            positions=positions,
            asset_history=history,
            **values,
        )

    def _validate_toss(self, payload: dict[str, Any]) -> AccountSnapshotView:
        required = {
            "schema_version", "provider", "source_operation", "source_spec_version",
            "collected_at", "registered_holder_scope", "economic_attribution_scope",
            "cash_balance", "buying_power", "unsupported_fields", "summaries",
            "overall_rates", "positions",
        }
        if set(payload) != required:
            raise ValueError("Toss snapshot keys do not match schema v1")
        schema_version = payload["schema_version"]
        if schema_version not in {1, TOSS_ACCOUNT_SNAPSHOT_SCHEMA_VERSION}:
            raise ValueError("unsupported Toss snapshot schema version")
        if payload["source_operation"] != "getHoldings":
            raise ValueError("unsupported Toss snapshot operation")
        if payload["source_spec_version"] != TOSS_ACCOUNT_SOURCE_SPEC_VERSION:
            raise ValueError("unsupported Toss source specification")
        if payload["registered_holder_scope"] != "SELF" or payload["economic_attribution_scope"] != "SELF":
            raise ValueError("unsupported Toss account attribution scope")
        if payload["cash_balance"] is not None:
            raise ValueError("Toss account routes do not provide a cash balance")
        buying_power_by_currency: dict[str, float] = {}
        if schema_version == 1:
            if payload["buying_power"] is not None:
                raise ValueError("legacy Toss holdings cannot provide buying power")
            expected_unsupported = {"cash_balance", "buying_power", "realized_pnl"}
        else:
            buying_power_payload = payload["buying_power"]
            if not isinstance(buying_power_payload, list) or len(buying_power_payload) != 2:
                raise ValueError("Toss buying-power pair is incomplete")
            for row in buying_power_payload:
                if not isinstance(row, dict) or set(row) != {
                    "currency", "cash_buying_power", "source_operation",
                }:
                    raise ValueError("Toss buying-power keys are invalid")
                currency = row["currency"]
                if (
                    currency not in {"KRW", "USD"}
                    or currency in buying_power_by_currency
                    or row["source_operation"] != "getBuyingPower"
                ):
                    raise ValueError("Toss buying-power identity is invalid")
                amount = self._decimal_string(
                    row["cash_buying_power"], f"buying_power.{currency}"
                )
                if amount < 0 or (currency == "KRW" and not amount.is_integer()):
                    raise ValueError("Toss buying-power amount is invalid")
                buying_power_by_currency[currency] = amount
            if set(buying_power_by_currency) != {"KRW", "USD"}:
                raise ValueError("Toss buying-power currencies are incomplete")
            expected_unsupported = {"cash_balance", "realized_pnl"}
        if set(payload["unsupported_fields"]) != expected_unsupported:
            raise ValueError("Toss unsupported-field declaration is invalid")
        collected_at = self._iso_value(payload["collected_at"], "collected_at")
        overall_rates = payload["overall_rates"]
        if not isinstance(overall_rates, dict) or set(overall_rates) != {
            "profit_loss_rate", "profit_loss_rate_after_cost", "daily_profit_loss_rate",
        }:
            raise ValueError("Toss overall-rate keys are invalid")
        for key, value in overall_rates.items():
            self._decimal_string(value, key)

        summaries_payload = payload["summaries"]
        if not isinstance(summaries_payload, list) or not summaries_payload:
            raise ValueError("Toss currency summaries must be a non-empty list")
        summary_keys = {
            "currency", "purchase_amount", "market_value", "market_value_after_cost",
            "profit_loss", "profit_loss_after_cost", "daily_profit_loss",
        }
        summaries: list[AccountCurrencySummaryView] = []
        summary_values: dict[str, dict[str, float]] = {}
        seen_currencies: set[str] = set()
        for row in summaries_payload:
            if not isinstance(row, dict) or set(row) != summary_keys:
                raise ValueError("Toss currency summary keys are invalid")
            currency = row["currency"]
            if currency not in {"KRW", "USD"} or currency in seen_currencies:
                raise ValueError("Toss currency summary is duplicated or unsupported")
            seen_currencies.add(currency)
            parsed_summary = {
                "purchase_amount": self._decimal_string(row["purchase_amount"], "purchase_amount"),
                "market_value": self._decimal_string(row["market_value"], "market_value"),
                "market_value_after_cost": self._decimal_string(
                    row["market_value_after_cost"], "market_value_after_cost"
                ),
                "profit_loss": self._decimal_string(row["profit_loss"], "profit_loss"),
                "profit_loss_after_cost": self._decimal_string(
                    row["profit_loss_after_cost"], "profit_loss_after_cost"
                ),
                "daily_profit_loss": self._decimal_string(
                    row["daily_profit_loss"], "daily_profit_loss"
                ),
            }
            summary_values[currency] = parsed_summary
            summaries.append(AccountCurrencySummaryView(
                currency=currency,
                purchase_amount=parsed_summary["purchase_amount"],
                securities_value=parsed_summary["market_value"],
                securities_value_after_cost=parsed_summary["market_value_after_cost"],
                unrealized_pnl=parsed_summary["profit_loss"],
                unrealized_pnl_after_cost=parsed_summary["profit_loss_after_cost"],
                daily_pnl=parsed_summary["daily_profit_loss"],
                cash_buying_power=buying_power_by_currency.get(currency),
            ))

        positions_payload = payload["positions"]
        if not isinstance(positions_payload, list):
            raise ValueError("Toss positions must be a list")
        position_keys = {
            "symbol", "name", "market_country", "currency", "quantity", "last_price",
            "average_purchase_price", "purchase_amount", "market_value",
            "market_value_after_cost", "profit_loss", "profit_loss_after_cost",
            "profit_loss_rate", "profit_loss_rate_after_cost", "daily_profit_loss",
            "daily_profit_loss_rate", "commission", "tax",
        }
        positions: list[AccountPositionView] = []
        position_sums = {
            currency: {key: 0.0 for key in (
                "purchase_amount", "market_value", "market_value_after_cost",
                "profit_loss", "profit_loss_after_cost", "daily_profit_loss",
            )}
            for currency in seen_currencies
        }
        seen_positions: set[tuple[str, str]] = set()
        for row in positions_payload:
            if not isinstance(row, dict) or set(row) != position_keys:
                raise ValueError("Toss position keys are invalid")
            if not all(isinstance(row[key], str) and row[key].strip() for key in ("symbol", "name")):
                raise ValueError("Toss position text is invalid")
            _require_identifier_free_position_text(row["symbol"], row["name"])
            identity = (row["market_country"], row["symbol"])
            if identity in seen_positions or row["currency"] not in seen_currencies:
                raise ValueError("Toss position identity/currency is invalid")
            seen_positions.add(identity)
            parsed_position = {
                key: self._decimal_string(row[key], f"position.{key}")
                for key in (
                    "quantity", "last_price", "average_purchase_price", "purchase_amount",
                    "market_value", "market_value_after_cost", "profit_loss",
                    "profit_loss_after_cost", "profit_loss_rate",
                    "profit_loss_rate_after_cost", "daily_profit_loss",
                    "daily_profit_loss_rate", "commission",
                )
            }
            # Toss OpenAPI publishes rates as decimal ratios
            # (for example, 0.1077 means 10.77%).  Every AccountPositionView
            # return field is expressed in percentage points, matching the KB
            # and manual-account projections and the GUI's percent formatter.
            for rate_key in (
                "profit_loss_rate",
                "profit_loss_rate_after_cost",
                "daily_profit_loss_rate",
            ):
                parsed_position[rate_key] *= 100.0
            tax = (
                None if row["tax"] is None
                else self._decimal_string(row["tax"], "position.tax")
            )
            positions.append(AccountPositionView(
                symbol=str(row["symbol"]),
                name=str(row["name"]),
                quantity=parsed_position["quantity"],
                market_value=parsed_position["market_value"],
                realized_pnl=None,
                unrealized_pnl=parsed_position["profit_loss"],
                purchase_amount=parsed_position["purchase_amount"],
                average_purchase_price=parsed_position["average_purchase_price"],
                current_price=parsed_position["last_price"],
                currency=str(row["currency"]),
                return_pct=parsed_position["profit_loss_rate"],
                unrealized_pnl_after_cost=parsed_position["profit_loss_after_cost"],
                daily_pnl=parsed_position["daily_profit_loss"],
                return_pct_after_cost=parsed_position["profit_loss_rate_after_cost"],
                daily_return_pct=parsed_position["daily_profit_loss_rate"],
                commission=parsed_position["commission"],
                tax=tax,
            ))
            for key in position_sums[row["currency"]]:
                position_sums[row["currency"]][key] += parsed_position[key]

        for currency, expected in summary_values.items():
            if any(
                not math.isclose(expected[key], position_sums[currency][key], rel_tol=1e-12, abs_tol=1e-9)
                for key in expected
            ):
                raise ValueError("Toss local snapshot summary does not reconcile")

        for currency in ("KRW", "USD"):
            if currency in buying_power_by_currency and currency not in seen_currencies:
                summaries.append(AccountCurrencySummaryView(
                    currency=currency,
                    purchase_amount=None,
                    securities_value=None,
                    securities_value_after_cost=None,
                    unrealized_pnl=None,
                    unrealized_pnl_after_cost=None,
                    daily_pnl=None,
                    cash_buying_power=buying_power_by_currency[currency],
                ))

        single = summaries[0] if len(summaries) == 1 else None
        return AccountSnapshotView(
            state=AccountSnapshotState.TOSS_READ_ONLY,
            provider="TOSS_SECURITIES",
            source_mode="SANITIZED_READ_ONLY",
            registered_holder_scope="SELF",
            economic_attribution_scope="SELF",
            include_in_user_fund_total=True,
            as_of=collected_at,
            last_reconciled_at=collected_at,
            currency=single.currency if single else None,
            total_assets=None,
            securities_value=single.securities_value if single else None,
            cash_balance=None,
            available_cash=(
                single.cash_buying_power if single is not None else None
            ),
            realized_pnl=None,
            unrealized_pnl=single.unrealized_pnl if single else None,
            positions=tuple(positions),
            currency_summaries=tuple(summaries),
        )

    def _validate_kb(self, payload: dict[str, Any]) -> AccountSnapshotView:
        required = {
            "schema_version", "provider", "source_operation",
            "source_evidence_version", "source_mode", "collected_at",
            "registered_holder_scope", "economic_attribution_scope", "currency",
            "total_assets", "securities_value", "purchase_amount",
            "unrealized_pnl", "cash_balance", "buying_power", "realized_pnl",
            "unsupported_fields", "positions",
        }
        if set(payload) != required:
            raise ValueError("KB account snapshot keys do not match schema v1")
        if payload["schema_version"] != KB_ACCOUNT_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported KB account snapshot schema version")
        if payload["source_operation"] != KB_ACCOUNT_SOURCE_OPERATION:
            raise ValueError("unsupported KB account operation")
        if payload["source_evidence_version"] != KB_ACCOUNT_SOURCE_EVIDENCE_VERSION:
            raise ValueError("unsupported KB account source evidence")
        if payload["source_mode"] != KB_ACCOUNT_SOURCE_MODE:
            raise ValueError("unsupported KB account source mode")
        if payload["registered_holder_scope"] != "SELF" or payload[
            "economic_attribution_scope"
        ] != "SELF":
            raise ValueError("unsupported KB account attribution scope")
        if payload["currency"] != "KRW":
            raise ValueError("KB SSQM2952 projection must remain KRW")
        if any(payload[key] is not None for key in (
            "cash_balance", "buying_power", "realized_pnl",
        )):
            raise ValueError("KB unsupported account values must remain null")
        if set(payload["unsupported_fields"]) != KB_ACCOUNT_UNSUPPORTED_FIELDS:
            raise ValueError("KB unsupported-field declaration is invalid")

        collected_at = self._iso_value(payload["collected_at"], "collected_at")
        total_assets = self._decimal_string(payload["total_assets"], "total_assets")
        securities_value = self._decimal_string(
            payload["securities_value"], "securities_value"
        )
        purchase_amount = self._decimal_string(
            payload["purchase_amount"], "purchase_amount"
        )
        unrealized_pnl = self._decimal_string(
            payload["unrealized_pnl"], "unrealized_pnl"
        )
        position_keys = {
            "position_key", "symbol", "name", "classification", "currency",
            "quantity", "orderable_quantity", "average_purchase_price",
            "current_price", "purchase_amount", "market_value", "unrealized_pnl",
        }
        positions_payload = payload["positions"]
        if not isinstance(positions_payload, list):
            raise ValueError("KB positions must be a list")
        positions: list[AccountPositionView] = []
        seen: set[str] = set()
        purchase_sum = market_sum = pnl_sum = 0.0
        for row in positions_payload:
            if not isinstance(row, dict) or set(row) != position_keys:
                raise ValueError("KB position keys are invalid")
            position_key = row["position_key"]
            if not isinstance(position_key, str) or not position_key or position_key in seen:
                raise ValueError("KB position identity is invalid")
            if row["currency"] != "KRW":
                raise ValueError("KB position currency is invalid")
            if not all(
                isinstance(row[key], str) and row[key].strip()
                for key in ("symbol", "name", "classification")
            ):
                raise ValueError("KB position text is invalid")
            _require_identifier_free_position_text(row["symbol"], row["name"])
            seen.add(position_key)
            quantity = self._decimal_string(row["quantity"], "position.quantity")
            orderable_quantity = self._decimal_string(
                row["orderable_quantity"], "position.orderable_quantity"
            )
            average_purchase_price = self._decimal_string(
                row["average_purchase_price"], "position.average_purchase_price"
            )
            current_price = self._decimal_string(
                row["current_price"], "position.current_price"
            )
            row_purchase = self._decimal_string(
                row["purchase_amount"], "position.purchase_amount"
            )
            row_market = self._decimal_string(row["market_value"], "position.market_value")
            row_pnl = self._decimal_string(row["unrealized_pnl"], "position.unrealized_pnl")
            if quantity < 0 or orderable_quantity < 0:
                raise ValueError("KB position quantity cannot be negative")
            purchase_sum += row_purchase
            market_sum += row_market
            pnl_sum += row_pnl
            positions.append(AccountPositionView(
                symbol=row["symbol"],
                name=row["name"],
                quantity=quantity,
                market_value=row_market,
                realized_pnl=None,
                unrealized_pnl=row_pnl,
                purchase_amount=row_purchase,
                average_purchase_price=average_purchase_price,
                current_price=current_price,
                orderable_quantity=orderable_quantity,
                currency="KRW",
                return_pct=(
                    row_pnl / row_purchase * 100.0 if row_purchase != 0 else None
                ),
            ))
        if not all((
            math.isclose(purchase_sum, purchase_amount, rel_tol=1e-12, abs_tol=1e-9),
            math.isclose(market_sum, securities_value, rel_tol=1e-12, abs_tol=1e-9),
            math.isclose(pnl_sum, unrealized_pnl, rel_tol=1e-12, abs_tol=1e-9),
        )):
            raise ValueError("KB local account snapshot does not reconcile")
        return AccountSnapshotView(
            state=AccountSnapshotState.KB_READ_ONLY,
            provider="KB_SECURITIES",
            source_mode=payload["source_mode"],
            registered_holder_scope="SELF",
            economic_attribution_scope="SELF",
            include_in_user_fund_total=True,
            as_of=collected_at,
            last_reconciled_at=collected_at,
            currency="KRW",
            total_assets=total_assets,
            securities_value=securities_value,
            cash_balance=None,
            available_cash=None,
            realized_pnl=None,
            unrealized_pnl=unrealized_pnl,
            positions=tuple(positions),
        )

    def _validate_family_local(self, payload: dict[str, Any]) -> AccountSnapshotView:
        required = {
            "schema_version", "state", "provider", "source_mode", "as_of",
            "last_reconciled_at", "registered_holder_scope",
            "economic_attribution_scope", "legal_ownership_claimed",
            "include_in_user_fund_total", "currency", "positions", "asset_history",
            *self._FIELDS,
        }
        if set(payload) != required or payload["schema_version"] != 3:
            raise ValueError("family local snapshot keys do not match schema v3")
        if payload["provider"] != "MIRAE_ASSET_LOCAL_MANUAL":
            raise ValueError("family local snapshot provider is invalid")
        if payload["source_mode"] != "LOCAL_MANUAL":
            raise ValueError("family account must remain local/manual")
        if payload["registered_holder_scope"] != "FAMILY_MEMBER":
            raise ValueError("family registered-holder scope is invalid")
        if payload["economic_attribution_scope"] != "USER_DECLARED_FUNDS":
            raise ValueError("family economic attribution is invalid")
        if payload["legal_ownership_claimed"] is not False:
            raise ValueError("family local snapshot cannot claim legal ownership")
        if not isinstance(payload["include_in_user_fund_total"], bool):
            raise ValueError("family inclusion flag must be boolean")
        currency = payload["currency"]
        if not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
            raise ValueError("currency must be a three-letter uppercase code")
        as_of = self._iso_value(payload["as_of"], "as_of")
        reconciled = self._iso_value(
            payload["last_reconciled_at"], "last_reconciled_at"
        )
        values = {
            field: self._optional_number(payload[field], field) for field in self._FIELDS
        }
        return AccountSnapshotView(
            state=AccountSnapshotState.FAMILY_LOCAL_MANUAL,
            provider="MIRAE_ASSET",
            source_mode="LOCAL_MANUAL",
            registered_holder_scope="FAMILY_MEMBER",
            economic_attribution_scope="USER_DECLARED_FUNDS",
            include_in_user_fund_total=payload["include_in_user_fund_total"],
            legal_ownership_claimed=False,
            as_of=as_of,
            last_reconciled_at=reconciled,
            currency=currency,
            positions=self._positions(payload["positions"]),
            asset_history=self._asset_history(payload["asset_history"], as_of),
            **values,
        )

    @staticmethod
    def _iso_value(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be an ISO date or datetime")
        try:
            return (
                date.fromisoformat(value).isoformat()
                if "T" not in value
                else datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
            )
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date or datetime") from exc

    @classmethod
    def _positions(cls, payload: Any) -> tuple[AccountPositionView, ...]:
        if not isinstance(payload, list):
            raise TypeError("positions must be a list")
        required = {
            "symbol", "name", "quantity", "market_value", "realized_pnl",
            "unrealized_pnl",
        }
        positions: list[AccountPositionView] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict) or set(item) != required:
                raise ValueError("position keys do not match schema v2")
            symbol, name = item["symbol"], item["name"]
            if not isinstance(symbol, str) or not symbol.strip() or symbol in seen:
                raise ValueError("position symbol must be unique and non-empty")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("position name must be non-empty")
            _require_identifier_free_position_text(symbol, name)
            quantity = cls._optional_number(item["quantity"], "position.quantity")
            if quantity is None:
                raise ValueError("position.quantity cannot be null")
            seen.add(symbol)
            positions.append(AccountPositionView(
                symbol=symbol,
                name=name,
                quantity=quantity,
                market_value=cls._optional_number(item["market_value"], "position.market_value"),
                realized_pnl=cls._optional_number(item["realized_pnl"], "position.realized_pnl"),
                unrealized_pnl=cls._optional_number(item["unrealized_pnl"], "position.unrealized_pnl"),
            ))
        return tuple(positions)

    @classmethod
    def _asset_history(
        cls, payload: Any, snapshot_as_of: str,
    ) -> tuple[AccountAssetPoint, ...]:
        if not isinstance(payload, list):
            raise TypeError("asset_history must be a list")
        points: list[AccountAssetPoint] = []
        previous: date | None = None
        snapshot_date = datetime.fromisoformat(snapshot_as_of).date() if "T" in snapshot_as_of else date.fromisoformat(snapshot_as_of)
        for item in payload:
            if not isinstance(item, dict) or set(item) != {"date", "total_assets"}:
                raise ValueError("asset history keys do not match schema v2")
            point_date = date.fromisoformat(item["date"])
            if previous is not None and point_date <= previous:
                raise ValueError("asset history dates must be unique and increasing")
            if point_date > snapshot_date:
                raise ValueError("asset history cannot extend beyond snapshot as_of")
            total = cls._optional_number(item["total_assets"], "asset_history.total_assets")
            if total is None:
                raise ValueError("asset_history.total_assets cannot be null")
            points.append(AccountAssetPoint(point_date.isoformat(), total))
            previous = point_date
        return tuple(points)

    @staticmethod
    def _optional_number(value: Any, field: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be numeric or null")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite")
        return number

    @staticmethod
    def _decimal_string(value: Any, field: str) -> float:
        if not isinstance(value, str) or not value:
            raise TypeError(f"{field} must be a decimal string")
        try:
            number = float(value)
        except (ValueError, OverflowError):
            raise ValueError(f"{field} must be a finite decimal string") from None
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite")
        return number

    @staticmethod
    def _unavailable(reason: str) -> AccountSnapshotView:
        return AccountSnapshotView(
            state=AccountSnapshotState.NOT_AVAILABLE,
            reason=reason,
            freshness="UNKNOWN",
        )


class LocalAccountPortfolioService:
    """Load independent sanitized account files and reconcile selected totals."""

    def __init__(
        self,
        sources: tuple[LocalAccountSourceSpec, ...],
        manual_store: LocalManualAccountStore | None = None,
        history_root: Path | None = None,
    ):
        source_ids = [source.source_id for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("account source ids must be unique")
        self.sources = sources
        self.manual_store = manual_store
        self.history_root = Path(history_root) if history_root is not None else None

    def load(self) -> AccountPortfolioView:
        entries = list(
            AccountPortfolioEntryView(
                source_id=source.source_id,
                title=source.title,
                snapshot=(
                    LocalAccountSnapshotService(source.snapshot_path).load()
                    if source.enabled
                    else AccountSnapshotView(
                        state=AccountSnapshotState.NOT_AVAILABLE,
                        reason=source.unavailable_reason,
                        freshness="UNKNOWN",
                    )
                ),
            )
            for source in self.sources
        )
        if self.manual_store is not None:
            try:
                manual = manual_account_registry_to_portfolio(
                    self.manual_store.load()
                )
            except (OSError, TypeError, ValueError):
                entries.append(AccountPortfolioEntryView(
                    source_id="manual_registry_invalid",
                    title="수동 계좌 저장소",
                    snapshot=AccountSnapshotView(
                        state=AccountSnapshotState.NOT_AVAILABLE,
                        reason="MANUAL_ACCOUNT_REGISTRY_INVALID",
                        freshness="READ_FAILURE",
                    ),
                ))
            else:
                entries.extend(manual.entries)
        entries = tuple(entries)
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        incomplete: set[str] = set()
        for entry in entries:
            view = entry.snapshot
            if not view.available or not view.include_in_user_fund_total:
                continue
            if view.currency is not None:
                counts[view.currency] = counts.get(view.currency, 0) + 1
                if view.total_assets is None:
                    incomplete.add(view.currency)
                else:
                    totals[view.currency] = totals.get(view.currency, 0.0) + view.total_assets
            else:
                for summary in view.currency_summaries:
                    counts[summary.currency] = counts.get(summary.currency, 0) + 1
                    incomplete.add(summary.currency)
        try:
            histories = (
                load_account_value_history(self.history_root)
                if self.history_root is not None else ()
            )
            history_reason = None
        except ValueError:
            histories = ()
            history_reason = "ACCOUNT_VALUE_HISTORY_INVALID"
        return AccountPortfolioView(
            entries=entries,
            user_fund_totals=tuple(
                AccountCurrencyTotalView(
                    currency=currency,
                    total_assets=(
                        totals.get(currency) if currency not in incomplete else None
                    ),
                    included_accounts=counts[currency],
                    complete=currency not in incomplete,
                )
                for currency in sorted(counts)
            ),
            value_histories=histories,
            history_reason=history_reason,
        )


def manual_account_registry_to_portfolio(
    registry: ManualAccountRegistry,
) -> AccountPortfolioView:
    """Project validated API-less accounts without inventing current values."""

    if not isinstance(registry, ManualAccountRegistry):
        raise TypeError("manual account registry is required")
    entries: list[AccountPortfolioEntryView] = []
    for account in registry.accounts:
        positions: list[AccountPositionView] = []
        for row in account.positions:
            _require_identifier_free_position_text(row.ticker, row.name)
            positions.append(AccountPositionView(
                symbol=row.ticker,
                name=row.name,
                quantity=row.quantity,
                market_value=None,
                realized_pnl=None,
                unrealized_pnl=None,
                purchase_amount=row.purchase_total,
                average_purchase_price=row.average_cost,
                current_price=None,
                orderable_quantity=None,
                currency=account.currency,
            ))
        entries.append(AccountPortfolioEntryView(
            source_id=account.source_id,
            title=account.label,
            snapshot=AccountSnapshotView(
                state=AccountSnapshotState.MANUAL_HOLDINGS_BASIS,
                provider="LOCAL_MANUAL",
                source_mode="USER_ENTERED_OR_EXPORTED_SHEET",
                registered_holder_scope="UNSPECIFIED",
                economic_attribution_scope="USER_AUTHORIZED_LOCAL_BASIS",
                include_in_user_fund_total=False,
                legal_ownership_claimed=False,
                as_of=account.snapshot_date,
                last_reconciled_at=account.snapshot_date,
                currency=account.currency,
                positions=tuple(positions),
                reason="DATED_PURCHASE_BASIS_ONLY_CURRENT_VALUATION_UNAVAILABLE",
                freshness="DATED_MANUAL_BASIS",
            ),
        ))
    return AccountPortfolioView(entries=tuple(entries), user_fund_totals=())


def manual_account_snapshot_to_portfolio(
    snapshot: ManualAccountSnapshot,
    market_values: object | None = None,
) -> AccountPortfolioView:
    """Map dated basis rows, optionally joining one strictly bound local cache."""

    snapshot = validate_manual_account_snapshot(snapshot)
    if market_values is not None:
        from stock_data.gui.manual_account_market_values import (
            manual_account_snapshot_to_valued_portfolio,
        )
        return manual_account_snapshot_to_valued_portfolio(snapshot, market_values)
    entries: list[AccountPortfolioEntryView] = []
    for section in MANUAL_ACCOUNT_SECTIONS:
        rows = tuple(row for row in snapshot.holdings if row.section == section)
        if not rows:
            continue
        positions: list[AccountPositionView] = []
        for row in rows:
            _require_identifier_free_position_text(row.ticker, row.name)
            positions.append(AccountPositionView(
                symbol=row.ticker,
                name=row.name,
                quantity=row.quantity,
                market_value=None,
                realized_pnl=None,
                unrealized_pnl=None,
                purchase_amount=row.purchase_total,
                average_purchase_price=row.average_cost,
                current_price=None,
                orderable_quantity=None,
                currency=snapshot.currency,
            ))
        view = AccountSnapshotView(
            state=AccountSnapshotState.MANUAL_HOLDINGS_BASIS,
            provider="LOCAL_MANUAL",
            source_mode="DATED_HOLDINGS_BASIS",
            registered_holder_scope="UNSPECIFIED",
            economic_attribution_scope="USER_AUTHORIZED_LOCAL_BASIS",
            include_in_user_fund_total=False,
            legal_ownership_claimed=False,
            as_of=snapshot.snapshot_date,
            last_reconciled_at=snapshot.snapshot_date,
            currency=snapshot.currency,
            total_assets=None,
            securities_value=None,
            cash_balance=None,
            available_cash=None,
            realized_pnl=None,
            unrealized_pnl=None,
            positions=tuple(positions),
            reason="DATED_PURCHASE_BASIS_ONLY_CURRENT_VALUATION_UNAVAILABLE",
            freshness="DATED_MANUAL_BASIS",
        )
        entries.append(AccountPortfolioEntryView(
            source_id=f"manual:{section}",
            title=f"수기 원가 기준 · {section}",
            snapshot=view,
        ))
    return AccountPortfolioView(entries=tuple(entries), user_fund_totals=())


_ACCOUNT_STATUS_TOKEN = re.compile(r"[A-Z][A-Z0-9_]{0,95}")
_ACCOUNT_KST = ZoneInfo("Asia/Seoul")


def _account_operation_record(
    path: Path,
    *,
    status_fields: tuple[str, ...],
    time_fields: tuple[str, ...],
) -> tuple[datetime, str] | None:
    """Read only allowlisted status/time scalars from a sanitized local record."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    status = next(
        (
            payload.get(field)
            for field in status_fields
            if isinstance(payload.get(field), str)
            and _ACCOUNT_STATUS_TOKEN.fullmatch(payload[field]) is not None
        ),
        None,
    )
    timestamp = next(
        (
            payload.get(field)
            for field in time_fields
            if isinstance(payload.get(field), str)
        ),
        None,
    )
    if status is None or timestamp is None:
        return None
    try:
        observed_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return None
    return observed_at.astimezone(timezone.utc), status


def build_account_source_action_views(
    portfolio: AccountPortfolioView,
    project_root: Path,
    *,
    toss_runtime_enabled: bool,
    kb_runtime_enabled: bool,
    now: datetime | None = None,
) -> tuple[AccountSourceActionView, ...]:
    """Build source-level freshness/actions without reading account values.

    Only allowlisted timestamps and typed outcome tokens are read from local
    operation records.  Account identifiers, positions, balances and payload
    text are never inspected or projected by this status path.
    """

    root = Path(project_root).resolve()
    clock = now or datetime.now(_ACCOUNT_KST)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("account action clock must be timezone-aware")
    local_clock = clock.astimezone(_ACCOUNT_KST)
    today_slot = local_clock.replace(hour=7, minute=0, second=0, microsecond=0)
    next_toss_slot = (
        today_slot if local_clock < today_slot else today_slot + timedelta(days=1)
    )

    toss_records = tuple(
        record
        for record in (
            _account_operation_record(
                root / "data/state/toss_account_snapshot.json",
                status_fields=("status",),
                time_fields=("collected_at",),
            ),
            _account_operation_record(
                root / "artifacts/scheduler_logs/STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json",
                status_fields=("outcome", "status"),
                time_fields=("finished_at_utc",),
            ),
        )
        if record is not None
    )
    toss_last = max(toss_records, default=None, key=lambda item: item[0])
    kb_last = _account_operation_record(
        root / "data/state/kbsec_account_snapshot.json",
        status_fields=("status",),
        time_fields=("collected_at",),
    )

    rows: list[AccountSourceActionView] = []
    for entry in portfolio.entries:
        snapshot = entry.snapshot
        if entry.source_id == "toss_self":
            capability = (
                "수동 읽기 전용 + 예약 정책 매일 07:00"
                if toss_runtime_enabled
                else "로컬 읽기 + 예약 정책 매일 07:00 · GUI 수동 설정 필요"
            )
            last = toss_last
            next_eligibility = (
                f"다음 예약 자격 {next_toss_slot.strftime('%m-%d %H:%M')} KST"
            )
        elif entry.source_id == "kb_self":
            capability = (
                "수동 읽기 전용 · 예약 미등록"
                if kb_runtime_enabled
                else "로컬 읽기 전용 · 런타임 설정 필요"
            )
            last = kb_last
            next_eligibility = (
                "예약 미등록 · 지금 수동 갱신 가능"
                if kb_runtime_enabled
                else "예약 미등록 · 로컬 스냅샷만 읽기 가능"
            )
        else:
            capability = "로컬 수동 · 자동 갱신 없음"
            last = None
            next_eligibility = "자동 갱신 없음 · 사용자가 저장할 때만 변경"
        rows.append(AccountSourceActionView(
            source_id=entry.source_id,
            last_accepted_at=snapshot.as_of if snapshot.displays_values else None,
            freshness=snapshot.freshness,
            reason=(snapshot.reason if not snapshot.displays_values else None),
            refresh_capability=capability,
            last_outcome=(last[1] if last is not None else "기록 없음"),
            last_outcome_at=(last[0].isoformat() if last is not None else None),
            next_eligibility=next_eligibility,
        ))
    return tuple(rows)


def _account_ownership_scope(view: AccountSnapshotView) -> str:
    if view.registered_holder_scope == "FAMILY_MEMBER":
        return "가족 명의 계좌 · 사용자 신고 자금 · 법적 소유 주장 아님"
    if view.registered_holder_scope == "SELF":
        return "본인 명의 · 본인 자금"
    return "사용자 승인 수기 원가 기준 · 명의 주장 없음"


def build_account_portfolio_presentation(
    portfolio: AccountPortfolioView,
    *,
    selected_source_id: str | None = None,
) -> AccountPortfolioPresentationView:
    """Project validated snapshots into a currency-safe read-only GUI model."""
    source_options = tuple(
        AccountSourceOptionView(
            source_id=entry.source_id,
            label=entry.title,
            displayable=entry.snapshot.displays_values,
            provider_scope=(
                f"{entry.snapshot.provider or 'LOCAL'} · "
                f"{entry.snapshot.source_mode or 'LOCAL'}"
            ),
            ownership_scope=_account_ownership_scope(entry.snapshot),
            as_of=entry.snapshot.as_of,
            freshness=entry.snapshot.freshness,
            reason=(
                None if entry.snapshot.displays_values
                else entry.snapshot.reason or entry.snapshot.freshness or "UNKNOWN"
            ),
        )
        for entry in portfolio.entries
    )
    if selected_source_id is None:
        scoped_entries = portfolio.entries
        scope_label = "전체 계좌 (통합)"
        selection_reason = None
    else:
        scoped_entries = tuple(
            entry for entry in portfolio.entries
            if entry.source_id == selected_source_id
        )
        scope_label = scoped_entries[0].title if scoped_entries else "선택한 계좌"
        selection_reason = (
            None if scoped_entries
            else "선택한 계좌 source가 현재 구성에 없습니다."
        )
    displayable = tuple(
        entry for entry in scoped_entries if entry.snapshot.displays_values
    )
    unavailable_count = len(scoped_entries) - len(displayable)
    scope_complete = bool(scoped_entries) and unavailable_count == 0
    scope_reason = selection_reason
    if scope_reason is None and not scope_complete:
        if selected_source_id is None:
            scope_reason = "일부 계좌가 unavailable/stale 상태라 통합 표시를 차단합니다."
        elif scoped_entries:
            snapshot = scoped_entries[0].snapshot
            scope_reason = snapshot.reason or snapshot.freshness or "UNKNOWN"
    records: dict[str, list[dict[str, float | None]]] = {}
    holdings: list[AccountHoldingView] = []
    histories: list[AccountHistorySeriesView] = []

    for entry in displayable:
        view = entry.snapshot
        summary_by_currency = {
            summary.currency: summary for summary in view.currency_summaries
        }
        currencies = (
            (view.currency,) if view.currency is not None
            else tuple(summary_by_currency)
        )
        for currency in currencies:
            summary = summary_by_currency.get(currency)
            records.setdefault(currency, []).append({
                "total_assets": view.total_assets if view.currency == currency else None,
                "securities_value": (
                    summary.securities_value if summary is not None
                    else view.securities_value if view.currency == currency else None
                ),
                "cash_balance": view.cash_balance if view.currency == currency else None,
                "available_cash": (
                    summary.cash_buying_power if summary is not None
                    else view.available_cash if view.currency == currency else None
                ),
                "unrealized_pnl": (
                    summary.unrealized_pnl if summary is not None
                    else view.unrealized_pnl if view.currency == currency else None
                ),
                "unrealized_pnl_after_cost": (
                    summary.unrealized_pnl_after_cost
                    if summary is not None else None
                ),
                "daily_pnl": summary.daily_pnl if summary is not None else None,
            })

        ownership = _account_ownership_scope(view)
        for position in view.positions if scope_complete else ():
            currency = position.currency or view.currency
            denominator = None
            if currency is not None:
                summary = summary_by_currency.get(currency)
                denominator = (
                    summary.securities_value if summary is not None
                    else view.securities_value if view.currency == currency else None
                )
            weight = position.weight_pct
            if weight is None:
                weight = (
                    position.market_value / denominator * 100.0
                    if position.market_value is not None
                    and denominator is not None and denominator > 0
                    else None
                )
            holdings.append(AccountHoldingView(
                account_title=entry.title,
                provider_scope=f"{view.provider or 'LOCAL'} · {view.source_mode or 'LOCAL'}",
                ownership_scope=ownership,
                symbol=position.symbol, name=position.name, currency=currency,
                quantity=position.quantity,
                purchase_amount=position.purchase_amount,
                market_value=position.market_value,
                unrealized_pnl=position.unrealized_pnl,
                weight_pct=weight, as_of=view.as_of, freshness=view.freshness,
                average_purchase_price=position.average_purchase_price,
                current_price=position.current_price,
                return_pct=position.return_pct,
                price_provider=position.price_provider,
                price_provider_symbol=position.price_provider_symbol,
                price_unit=position.price_unit,
                price_as_of=position.price_as_of,
                price_finality=position.price_finality,
                orderable_quantity=position.orderable_quantity,
                unrealized_pnl_after_cost=position.unrealized_pnl_after_cost,
                daily_pnl=position.daily_pnl,
                return_pct_after_cost=position.return_pct_after_cost,
                daily_return_pct=position.daily_return_pct,
                commission=position.commission,
                tax=position.tax,
            ))
        if scope_complete and view.currency is not None and len(view.asset_history) >= 2:
            histories.append(AccountHistorySeriesView(
                account_title=entry.title, currency=view.currency,
                points=view.asset_history, as_of=view.as_of,
            ))

    scoped_source_ids = {entry.source_id for entry in displayable}
    entry_titles = {entry.source_id: entry.title for entry in displayable}
    history_reason = portfolio.history_reason
    snapshot_cutoffs: dict[str, datetime] = {}
    for entry in displayable:
        try:
            cutoff = datetime.fromisoformat(entry.snapshot.as_of or "")
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError
        except ValueError:
            history_reason = history_reason or "ACCOUNT_VALUE_HISTORY_SNAPSHOT_TIME_INVALID"
            continue
        snapshot_cutoffs[entry.source_id] = cutoff.astimezone(timezone.utc)
    invalid_history_sources: set[str] = set()
    for series in portfolio.value_histories:
        cutoff = snapshot_cutoffs.get(series.source_id)
        if series.source_id not in scoped_source_ids:
            continue
        if cutoff is None:
            invalid_history_sources.add(series.source_id)
            continue
        try:
            for point in series.points:
                observed = datetime.fromisoformat(point.observed_at)
                if observed.tzinfo is None or observed.utcoffset() is None:
                    raise ValueError
                if observed.astimezone(timezone.utc) > cutoff:
                    invalid_history_sources.add(series.source_id)
                    break
        except ValueError:
            invalid_history_sources.add(series.source_id)
    if invalid_history_sources:
        history_reason = "ACCOUNT_VALUE_HISTORY_AFTER_CURRENT_SNAPSHOT"
    if scope_complete:
        for series in portfolio.value_histories:
            if (
                series.source_id not in scoped_source_ids
                or series.source_id in invalid_history_sources
                or len(series.points) < 2
            ):
                continue
            histories.append(AccountHistorySeriesView(
                account_title=entry_titles[series.source_id],
                currency=series.currency,
                points=tuple(
                    AccountAssetPoint(point.observed_at, point.value)
                    for point in series.points
                ),
                as_of=series.points[-1].observed_at,
                metric=series.metric,
            ))

    currency_views: list[AccountPortfolioCurrencyView] = []
    for currency in sorted(records):
        rows = records[currency]

        def total(field: str) -> float | None:
            values = [row[field] for row in rows]
            if not scope_complete or any(value is None for value in values):
                return None
            return float(sum(float(value) for value in values if value is not None))

        reason = None
        if not scope_complete:
            reason = scope_reason
        currency_views.append(AccountPortfolioCurrencyView(
            currency=currency, account_count=len(rows),
            total_assets=total("total_assets"),
            securities_value=total("securities_value"),
            cash_balance=total("cash_balance"),
            available_cash=total("available_cash"),
            unrealized_pnl=total("unrealized_pnl"),
            complete=scope_complete, reason=reason,
            unrealized_pnl_after_cost=total("unrealized_pnl_after_cost"),
            daily_pnl=total("daily_pnl"),
        ))

    allocations: list[AccountAllocationView] = []
    for currency_view in currency_views:
        denominator = currency_view.securities_value
        eligible = [
            item for item in holdings
            if item.currency == currency_view.currency and item.market_value is not None
        ]
        if denominator is None or denominator <= 0 or not eligible:
            continue
        if not math.isclose(
            sum(float(item.market_value) for item in eligible), denominator,
            rel_tol=1e-9, abs_tol=1e-6,
        ):
            continue
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in eligible:
            key = (item.symbol, item.name)
            bucket = grouped.setdefault(key, {"value": 0.0, "breakdown": []})
            bucket["value"] += float(item.market_value)
            bucket["breakdown"].append(
                f"{item.account_title} · {item.name} ({item.symbol})"
            )
        ranked = sorted(grouped.items(), key=lambda pair: pair[1]["value"], reverse=True)
        shown: list[tuple[tuple[str, str], dict[str, Any]]] = []
        other: list[tuple[tuple[str, str], dict[str, Any]]] = []
        for index, item in enumerate(ranked):
            weight = item[1]["value"] / denominator * 100.0
            (shown if index < 5 and weight >= 3.0 else other).append(item)
        for (symbol, name), value in shown:
            allocations.append(AccountAllocationView(
                currency=currency_view.currency,
                label=f"{name} ({symbol})", market_value=float(value["value"]),
                weight_pct=float(value["value"] / denominator * 100.0),
                is_other=False, exact_breakdown=tuple(value["breakdown"]),
            ))
        if other:
            other_value = sum(float(value["value"]) for _key, value in other)
            allocations.append(AccountAllocationView(
                currency=currency_view.currency, label="기타",
                market_value=other_value,
                weight_pct=float(other_value / denominator * 100.0),
                is_other=True,
                exact_breakdown=tuple(
                    detail
                    for _key, value in other
                    for detail in value["breakdown"]
                ),
            ))

    return AccountPortfolioPresentationView(
        currencies=tuple(currency_views), holdings=tuple(holdings),
        allocations=tuple(allocations), histories=tuple(histories),
        displayable_accounts=len(displayable),
        unavailable_accounts=unavailable_count,
        as_of_values=tuple(sorted({
            entry.snapshot.as_of for entry in displayable if entry.snapshot.as_of
        })),
        freshness_values=tuple(sorted({
            entry.snapshot.freshness for entry in displayable
        })),
        source_options=source_options,
        selected_source_id=selected_source_id,
        scope_label=scope_label,
        scope_complete=scope_complete,
        scope_reason=scope_reason,
        history_reason=history_reason,
    )
