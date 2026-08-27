from __future__ import annotations

from datetime import date

import pandas as pd

from .labels import _validated_source_identity
from .signals import (
    _validate_decision_label_clock,
    _validate_label_evaluation_artifact,
    _validate_signal_evaluation_artifact,
    _validated_aware_iso_clock,
)


CRISIS_WINDOWS = {
    "dot_com": ("2000-01-01", "2003-03-31"),
    "global_financial_crisis": ("2007-07-01", "2009-06-30"),
    "covid_2020": ("2020-01-01", "2020-06-30"),
    "bear_market_2022": ("2022-01-01", "2022-12-31"),
    "april_2025": ("2025-04-01", "2025-04-30"),
    "recent_2026": ("2026-01-01", "2026-08-14"),
}


def _validated_holdout_start(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("holdout_start must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("holdout_start must be an ISO date") from error
    if parsed.isoformat() != value:
        raise ValueError("holdout_start must be an ISO date")
    return value


def replay_crisis_windows(signals: pd.DataFrame, labels: pd.DataFrame,
                          windows: dict[str, tuple[str, str]] = CRISIS_WINDOWS,
                          *, holdout_start: str) -> list[dict]:
    boundary = _validated_holdout_start(holdout_start)
    development_signals = signals.loc[
        signals["observation_date"].lt(boundary)
    ]
    development_labels = labels.loc[
        labels["observation_date"].lt(boundary)
    ]
    if development_labels.empty:
        eligible_labels = development_labels
    else:
        if development_labels.columns.tolist().count("label_available_at") != 1:
            raise ValueError("label availability schema/content is invalid")
        available_at = _validated_aware_iso_clock(
            development_labels, "label_available_at", artifact="label",
        )
        holdout_boundary = pd.Timestamp(boundary, tz="Asia/Seoul")
        eligible_labels = development_labels.loc[
            available_at.lt(holdout_boundary)
        ]
    eligible_dates = set(eligible_labels["observation_date"])
    eligible_signals = development_signals.loc[
        development_signals["observation_date"].isin(eligible_dates)
    ]
    signal_identity = (
        _validated_source_identity(eligible_signals, artifact="signal")
        if not eligible_signals.empty else None
    )
    label_identity = (
        _validated_source_identity(eligible_labels, artifact="label")
        if not eligible_labels.empty else None
    )
    if (
        signal_identity is not None
        and label_identity is not None
        and signal_identity != label_identity
    ):
        raise ValueError("signal/label source identity differs")
    if not eligible_signals.empty:
        _validated_aware_iso_clock(
            eligible_signals, "usable_from", artifact="signal",
        )
        _validate_signal_evaluation_artifact(eligible_signals)
    if not eligible_labels.empty:
        _validate_label_evaluation_artifact(eligible_labels)
    joined = eligible_signals.merge(
        eligible_labels,
        on=["observation_date", "ticker", "date_semantics"],
        how="inner", validate="one_to_one",
    )
    if not joined.empty:
        _validate_decision_label_clock(
            joined, joined, decision_artifact="signal",
        )
    results = []
    for name, (start, end) in windows.items():
        if start >= boundary:
            results.append({
                "event": name, "start": start, "end": end,
                "status": "UNTOUCHED_HOLDOUT",
                "holdout_observations_excluded": "NOT_INSPECTED",
            })
            continue
        selected = joined[joined["observation_date"].between(start, end)]
        overlaps_holdout = end >= boundary
        if selected.empty:
            results.append({
                "event": name, "start": start, "end": end, "status": "NO_COVERAGE",
                "holdout_observations_excluded": "NOT_INSPECTED" if overlaps_holdout else 0,
            })
            continue
        active = selected[selected["risk_off_signal"]]
        adverse = selected["forward_max_drawdown_20d"].le(-0.10)
        tp = int((selected["risk_off_signal"] & adverse).sum())
        results.append({
            "event": name, "start": start, "end": end,
            "status": "DIAGNOSTIC_ONLY_HOLDOUT_EXCLUDED" if overlaps_holdout else "DIAGNOSTIC_ONLY",
            "observations": len(selected), "risk_off_observations": len(active),
            "adverse_observations": int(adverse.sum()),
            "event_precision": tp / len(active) if len(active) else 0.0,
            "event_recall": tp / int(adverse.sum()) if adverse.any() else 0.0,
            "first_risk_off_date": None if active.empty else active.iloc[0]["observation_date"],
            "worst_forward_20d_drawdown": float(selected["forward_max_drawdown_20d"].min()),
            "mean_forward_20d_return": float(selected["forward_return_20d"].mean()),
            "holdout_observations_excluded": "NOT_INSPECTED" if overlaps_holdout else 0,
        })
    return results
