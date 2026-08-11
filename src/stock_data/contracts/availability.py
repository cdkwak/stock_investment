from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvailabilitySemantics:
    dataset: str
    source_snapshot_field: str
    event_effective_fields: tuple[str, ...]
    announcement_field: str | None
    predictive_available_from: str
    total_return_use: str


# These rules describe knowledge timing without changing or shifting source dates.
SNAPSHOT_EVENT_AVAILABILITY = {
    "kr_equity_dividend": AvailabilitySemantics(
        dataset="kr_equity_dividend",
        source_snapshot_field="date",
        event_effective_fields=(
            "dividend_record_date", "cash_payment_date", "stock_delivery_date"
        ),
        announcement_field=None,
        predictive_available_from=(
            "source snapshot date; announcement date is unavailable, so event dates "
            "must not be treated as knowledge dates"
        ),
        total_return_use=(
            "retrospective accounting only after the applicable event date and amount "
            "have been validated"
        ),
    ),
    "kr_equity_rights_schedule": AvailabilitySemantics(
        dataset="kr_equity_rights_schedule",
        source_snapshot_field="source_snapshot_date",
        event_effective_fields=(
            "exercise_start_date", "exercise_end_date",
            "registry_close_start_date", "registry_close_end_date",
        ),
        announcement_field=None,
        predictive_available_from=(
            "source snapshot date; no documented announcement field is available"
        ),
        total_return_use=(
            "not permitted until the economic action and adjustment rule are validated"
        ),
    ),
    "kr_equity_master": AvailabilitySemantics(
        dataset="kr_equity_master",
        source_snapshot_field="source_date",
        event_effective_fields=(
            "listing_date", "delisting_date", "deposit_registration_date",
            "deposit_cancellation_date",
        ),
        announcement_field=None,
        predictive_available_from=(
            "source_date when populated; otherwise identity/lifecycle is not eligible "
            "as a historical predictive feature"
        ),
        total_return_use="metadata only; never creates point-in-time universe membership",
    ),
}
