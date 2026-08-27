from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from stock_data.contracts.manual_account_market_values import (
    ManualAccountMarketValueCache,
    manual_account_basis_sha256,
    manual_account_market_value_cache_payload,
    parse_manual_account_market_value_cache,
)
from stock_data.gui.manual_account_snapshot import (
    MANUAL_ACCOUNT_SECTIONS,
    ManualAccountSnapshot,
    validate_manual_account_snapshot,
)


def manual_account_snapshot_to_valued_portfolio(
    snapshot: ManualAccountSnapshot,
    cache: ManualAccountMarketValueCache,
):
    """Join a validated cache without changing any acquisition-basis value."""

    # Late importing keeps the source-neutral account view module usable without
    # making its dataclasses depend on this optional valuation adapter.
    from stock_data.gui.account_snapshot_service import (
        AccountPortfolioEntryView,
        AccountPortfolioView,
        AccountPositionView,
        AccountSnapshotState,
        AccountSnapshotView,
        _require_identifier_free_position_text,
    )

    snapshot = validate_manual_account_snapshot(snapshot)
    cache = parse_manual_account_market_value_cache(
        manual_account_market_value_cache_payload(cache)
    )
    if (
        cache.source_sheet != snapshot.source_sheet
        or cache.snapshot_date != snapshot.snapshot_date
        or cache.basis_sha256 != manual_account_basis_sha256(snapshot)
    ):
        raise ValueError("manual market-value cache does not bind the acquisition basis")
    cache_rows = {(row.section, row.ticker): row for row in cache.rows}
    basis_keys = [(row.section, row.ticker) for row in snapshot.holdings]
    if list(cache_rows) != basis_keys:
        raise ValueError("manual market-value cache row identity/order differs")

    entries: list[AccountPortfolioEntryView] = []
    for section in MANUAL_ACCOUNT_SECTIONS:
        holdings = tuple(row for row in snapshot.holdings if row.section == section)
        if not holdings:
            continue
        valued = [cache_rows[(row.section, row.ticker)] for row in holdings]
        if any(row.currency != snapshot.currency for row in valued):
            raise ValueError("manual market-value cache currency differs from basis")
        summary = next((
            item for item in cache.section_summaries
            if item.section == section and item.currency == snapshot.currency
        ), None)
        if summary is None or summary.total_rows != len(holdings):
            raise ValueError("manual market-value section summary differs")
        available = [row for row in valued if row.status == "AVAILABLE"]
        if summary.available_rows != len(available) or summary.market_value != sum(
            (row.market_value or Decimal(0) for row in available), Decimal(0),
        ):
            raise ValueError("manual market-value section totals differ")
        denominator = sum(
            (row.market_value or Decimal(0) for row in available), Decimal(0),
        )
        for basis, market in zip(holdings, valued, strict=True):
            if market.status != "AVAILABLE":
                continue
            expected_market_value = Decimal(str(basis.quantity)) * market.price
            purchase_total = (
                None if basis.purchase_total is None
                else Decimal(str(basis.purchase_total))
            )
            expected_pnl = (
                None if purchase_total is None
                else expected_market_value - purchase_total
            )
            expected_return = (
                None if purchase_total in {None, Decimal(0)}
                else expected_pnl / purchase_total * Decimal(100)
            )
            expected_weight = expected_market_value / denominator * Decimal(100)
            if (
                market.market_value != expected_market_value
                or market.unrealized_pnl != expected_pnl
                or market.return_pct != expected_return
                or market.weight_pct != expected_weight
            ):
                raise ValueError("manual market-value derived arithmetic differs")
        providers = {row.provider for row in available}
        provider = (
            next(iter(providers)) if len(providers) == 1
            else "MULTIPLE_LABELLED_PROVIDERS" if providers
            else "LOCAL_MANUAL"
        )
        as_of = (
            max(
                (datetime.fromisoformat(row.as_of.replace("Z", "+00:00")), row.as_of)
                for row in available if row.as_of is not None
            )[1]
            if available else snapshot.snapshot_date
        )
        positions = []
        for basis, market in zip(holdings, valued, strict=True):
            _require_identifier_free_position_text(basis.ticker, basis.name)
            positions.append(AccountPositionView(
                symbol=basis.ticker, name=basis.name, quantity=basis.quantity,
                market_value=(
                    None if market.market_value is None else float(market.market_value)
                ),
                realized_pnl=None,
                unrealized_pnl=(
                    None if market.unrealized_pnl is None
                    else float(market.unrealized_pnl)
                ),
                purchase_amount=basis.purchase_total,
                average_purchase_price=basis.average_cost,
                current_price=None if market.price is None else float(market.price),
                orderable_quantity=None, currency=snapshot.currency,
                weight_pct=(
                    None if market.weight_pct is None else float(market.weight_pct)
                ),
                return_pct=(
                    None if market.return_pct is None else float(market.return_pct)
                ),
                price_provider=market.provider,
                price_provider_symbol=market.provider_symbol,
                price_unit=market.unit,
                price_as_of=market.as_of,
                price_finality=market.finality,
            ))
        pnl_values = [row.unrealized_pnl for row in available]
        unrealized = (
            float(sum((value for value in pnl_values if value is not None), Decimal(0)))
            if summary.complete and available
            and all(value is not None for value in pnl_values)
            else None
        )
        reason = (
            "LABELLED_CURRENT_PRICE_CACHE"
            if summary.complete else "PARTIAL_LABELLED_CURRENT_PRICE_CACHE"
        )
        view = AccountSnapshotView(
            state=AccountSnapshotState.MANUAL_HOLDINGS_BASIS,
            provider=provider, source_mode="SANITIZED_CURRENT_PRICE_CACHE",
            registered_holder_scope="UNSPECIFIED",
            economic_attribution_scope="USER_AUTHORIZED_LOCAL_BASIS",
            include_in_user_fund_total=False, legal_ownership_claimed=False,
            as_of=as_of, last_reconciled_at=cache.generated_at,
            currency=snapshot.currency,
            total_assets=(float(summary.market_value) if summary.complete else None),
            securities_value=(float(summary.market_value) if summary.complete else None),
            cash_balance=None,
            available_cash=None, realized_pnl=None, unrealized_pnl=unrealized,
            positions=tuple(positions), reason=reason, freshness="AS_RETRIEVED",
        )
        entries.append(AccountPortfolioEntryView(
            source_id=f"manual:{section}", title=f"수기 원가 기준 · {section}",
            snapshot=view,
        ))
    return AccountPortfolioView(entries=tuple(entries), user_fund_totals=())


__all__ = ["manual_account_snapshot_to_valued_portfolio"]
