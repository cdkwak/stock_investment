from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from stock_data.gui import net_worth_service as subject


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self) -> None:
        self.value += timedelta(seconds=1)


def _asset(
    record_id: str,
    claim_id: str,
    asset_class: str,
    gross: int,
    economic: int,
    *,
    holder: str = "SELF",
    owner: str = "SELF",
    uncertainty: str = "EXACT",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "economic_claim_id": claim_id,
        "asset_class": asset_class,
        "gross_value_krw": gross,
        "economic_value_krw": economic,
        "registered_holder_role": holder,
        "economic_owner_role": owner,
        "valuation_date": "2026-08-20",
        "valuation_method": "USER_DECLARED",
        "valuation_source": "USER_LOCAL",
        "valuation_status": "CURRENT",
        "uncertainty": uncertainty,
    }


def _liability(
    record_id: str,
    claim_id: str,
    liability_class: str,
    gross: int,
    economic: int,
    *,
    unused: int = 0,
    holder: str = "SELF",
    owner: str = "SELF",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "economic_claim_id": claim_id,
        "liability_class": liability_class,
        "gross_principal_krw": gross,
        "economic_principal_krw": economic,
        "unused_limit_krw": unused,
        "registered_holder_role": holder,
        "economic_owner_role": owner,
        "valuation_date": "2026-08-20",
        "valuation_method": "STATEMENT_VALUE",
        "valuation_source": "OFFICIAL_STATEMENT",
        "valuation_status": "CURRENT",
        "uncertainty": "EXACT",
    }


def _snapshot(
    *,
    snapshot_id: str = "snapshot-20260820-a",
    as_of: str = "2026-08-20",
) -> dict[str, object]:
    assets = [
        _asset("cash-local", "claim-cash", "CASH", 120_000, 120_000),
        _asset(
            "investment-local",
            "claim-investment",
            "INVESTMENT",
            300_000,
            180_000,
            holder="FAMILY",
            owner="SELF",
            uncertainty="LOW",
        ),
        _asset(
            "real-estate-local",
            "claim-real-estate",
            "REAL_ESTATE",
            800_000,
            400_000,
            holder="JOINT",
            owner="SELF",
        ),
        _asset(
            "jeonse-deposit-local",
            "claim-jeonse-deposit",
            "JEONSE_DEPOSIT",
            500_000,
            500_000,
        ),
        _asset(
            "receivable-local",
            "claim-receivable",
            "OTHER_RECEIVABLE",
            50_000,
            50_000,
        ),
    ]
    liabilities = [
        _liability(
            "mortgage-local",
            "claim-mortgage",
            "MORTGAGE",
            250_000,
            125_000,
            holder="JOINT",
            owner="SELF",
        ),
        _liability(
            "jeonse-loan-local",
            "claim-jeonse-loan",
            "JEONSE_LOAN",
            200_000,
            200_000,
        ),
        _liability(
            "overdraft-local",
            "claim-overdraft",
            "DRAWN_OVERDRAFT",
            30_000,
            30_000,
            unused=70_000,
        ),
        _liability(
            "other-debt-local",
            "claim-other-debt",
            "OTHER_DEBT",
            20_000,
            20_000,
        ),
    ]
    for entry in (*assets, *liabilities):
        entry["valuation_date"] = as_of
    return {
        "schema_version": subject.SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "as_of_date": as_of,
        "recorded_at_utc": f"{as_of}T01:00:00+00:00",
        "base_currency": "KRW",
        "assets": assets,
        "liabilities": liabilities,
    }


def _missing(entry: dict[str, object]) -> None:
    value_keys = (
        ("gross_value_krw", "economic_value_krw")
        if "gross_value_krw" in entry
        else ("gross_principal_krw", "economic_principal_krw")
    )
    for key in value_keys:
        entry[key] = None
    entry["valuation_date"] = None
    entry["valuation_method"] = "NOT_AVAILABLE"
    entry["valuation_source"] = "NOT_AVAILABLE"
    entry["valuation_status"] = "MISSING"
    entry["uncertainty"] = "UNKNOWN"


def test_schema_keeps_asset_liability_and_attribution_classes_separate() -> None:
    view = subject.NetWorthView(
        snapshot=subject.parse_snapshot(_snapshot()),
        totals=subject.calculate_net_worth(subject.parse_snapshot(_snapshot())),
    )
    assert {entry.asset_class for entry in view.snapshot.assets} == set(
        subject.AssetClass
    )
    assert {entry.liability_class for entry in view.snapshot.liabilities} == set(
        subject.LiabilityClass
    )
    investment = view.snapshot.assets[1]
    assert investment.registered_holder_role is subject.HolderRole.FAMILY
    assert investment.economic_owner_role is subject.HolderRole.SELF
    assert investment.gross_value_krw == 300_000
    assert investment.economic_value_krw == 180_000


def test_exact_calculation_counts_each_claim_once_and_excludes_unused_limit() -> None:
    snapshot = subject.parse_snapshot(_snapshot())
    totals = subject.calculate_net_worth(snapshot)
    assert totals.liquid_financial_assets_krw == 300_000
    assert totals.total_assets_krw == 1_250_000
    assert totals.total_liabilities_krw == 375_000
    assert totals.net_worth_krw == 875_000
    assert totals.unused_credit_limit_krw == 70_000
    assert totals.uncertain_claim_ids == ("claim-investment",)
    assert totals.complete is True


def test_jeonse_deposit_and_loan_are_never_invisibly_netted() -> None:
    snapshot = subject.parse_snapshot(_snapshot())
    deposit = next(
        item
        for item in snapshot.assets
        if item.asset_class is subject.AssetClass.JEONSE_DEPOSIT
    )
    loan = next(
        item
        for item in snapshot.liabilities
        if item.liability_class is subject.LiabilityClass.JEONSE_LOAN
    )
    assert deposit.economic_value_krw == 500_000
    assert loan.economic_principal_krw == 200_000
    assert deposit.economic_claim_id != loan.economic_claim_id


def test_duplicate_economic_claim_is_rejected_instead_of_double_counted() -> None:
    payload = _snapshot()
    payload["assets"][1]["economic_claim_id"] = "claim-cash"
    with pytest.raises(
        subject.NetWorthValidationError, match="NET_WORTH_DOUBLE_COUNT_REJECTED"
    ):
        subject.parse_snapshot(payload)


def test_stale_and_missing_values_fail_closed_without_hiding_valid_subtotals() -> None:
    payload = _snapshot()
    payload["assets"][2]["valuation_status"] = "STALE"
    snapshot = subject.parse_snapshot(payload)
    totals = subject.calculate_net_worth(snapshot)
    assert totals.liquid_financial_assets_krw == 300_000
    assert totals.total_assets_krw is None
    assert totals.total_liabilities_krw == 375_000
    assert totals.net_worth_krw is None
    assert totals.stale_claim_ids == ("claim-real-estate",)

    payload = _snapshot()
    _missing(payload["liabilities"][3])
    totals = subject.calculate_net_worth(subject.parse_snapshot(payload))
    assert totals.total_assets_krw == 1_250_000
    assert totals.total_liabilities_krw is None
    assert totals.net_worth_krw is None
    assert totals.missing_claim_ids == ("claim-other-debt",)


def test_invalid_missing_shape_attribution_and_credit_limit_fail_closed() -> None:
    payload = _snapshot()
    payload["assets"][0]["valuation_status"] = "MISSING"
    with pytest.raises(subject.NetWorthValidationError, match="MISSING_VALUE"):
        subject.parse_snapshot(payload)

    payload = _snapshot()
    payload["assets"][0]["economic_value_krw"] = 120_001
    with pytest.raises(subject.NetWorthValidationError, match="VALUATION_REJECTED"):
        subject.parse_snapshot(payload)

    payload = _snapshot()
    payload["liabilities"][0]["unused_limit_krw"] = 1
    with pytest.raises(subject.NetWorthValidationError, match="UNUSED_LIMIT_CLASS"):
        subject.parse_snapshot(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"address": "rejected"}),
        lambda payload: payload.update({"account_number": "rejected"}),
        lambda payload: payload["assets"][0].update({"holder_name": "rejected"}),
        lambda payload: payload.update({"snapshot_id": "account-12345678901"}),
    ],
)
def test_identifiers_addresses_and_account_numbers_are_strictly_rejected(mutate) -> None:
    payload = _snapshot()
    mutate(payload)
    with pytest.raises(subject.NetWorthValidationError) as error:
        subject.parse_snapshot(payload)
    assert "rejected" not in str(error.value)
    assert "12345678901" not in str(error.value)


def test_atomic_history_is_hash_chained_idempotent_and_exact_date_only(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    store = subject.LocalNetWorthHistoryStore(tmp_path / "history", clock=clock)
    first_payload = _snapshot(snapshot_id="snapshot-20260818-a", as_of="2026-08-18")
    first = store.save_snapshot(first_payload)
    assert store.save_snapshot(first_payload).record_digest == first.record_digest
    assert len(list((tmp_path / "history").glob("record-*.json"))) == 1

    clock.advance()
    second = store.save_snapshot(_snapshot())
    history = store.load_history()
    assert [item.record_digest for item in history] == [
        first.record_digest,
        second.record_digest,
    ]
    assert second.previous_record_digest == first.record_digest
    assert store.load_exact(date(2026, 8, 19)) is None
    assert store.load_exact(date(2026, 8, 18)).snapshot.snapshot_id == (
        "snapshot-20260818-a"
    )
    assert store.load_exact(date(2026, 8, 20)).totals.net_worth_krw == 875_000


def test_same_date_revision_is_a_new_auditable_record_not_an_overwrite(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    store = subject.LocalNetWorthHistoryStore(tmp_path / "history", clock=clock)
    first = store.save_snapshot(_snapshot())
    first_files = {
        path.name: path.read_bytes()
        for path in (tmp_path / "history").glob("record-*.json")
    }
    clock.advance()
    revised_payload = _snapshot(snapshot_id="snapshot-20260820-b")
    revised_payload["assets"][0]["gross_value_krw"] = 121_000
    revised_payload["assets"][0]["economic_value_krw"] = 121_000
    revised = store.save_snapshot(revised_payload)
    assert revised.record_digest != first.record_digest
    assert len(store.load_history()) == 2
    assert store.load_exact(date(2026, 8, 20)).totals.total_assets_krw == 1_251_000
    for name, body in first_files.items():
        assert (tmp_path / "history" / name).read_bytes() == body


def test_failed_atomic_append_preserves_prior_history_and_cleans_pending_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock()
    root = tmp_path / "history"
    store = subject.LocalNetWorthHistoryStore(root, clock=clock)
    store.save_snapshot(_snapshot())
    before = {path.name: path.read_bytes() for path in root.glob("record-*.json")}
    original_rename = subject.os.rename

    def fail_rename(_source, _target):
        raise OSError("synthetic local write failure")

    monkeypatch.setattr(subject.os, "rename", fail_rename)
    clock.advance()
    with pytest.raises(
        subject.NetWorthPersistenceError, match="NET_WORTH_HISTORY_WRITE_FAILED"
    ):
        store.save_snapshot(_snapshot(snapshot_id="snapshot-20260820-b"))
    monkeypatch.setattr(subject.os, "rename", original_rename)
    assert {path.name: path.read_bytes() for path in root.glob("record-*.json")} == before
    assert not list(root.glob(".pending-*.tmp"))
    assert len(store.load_history()) == 1


def test_corrupt_latest_history_fails_closed_without_prior_value_fallback(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    root = tmp_path / "history"
    store = subject.LocalNetWorthHistoryStore(root, clock=clock)
    store.save_snapshot(_snapshot(snapshot_id="snapshot-20260818-a", as_of="2026-08-18"))
    clock.advance()
    store.save_snapshot(_snapshot())
    latest = sorted(root.glob("record-*.json"))[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["totals"]["net_worth_krw"] = 0
    latest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(subject.NetWorthPersistenceError, match="HISTORY_INVALID"):
        store.load_exact(date(2026, 8, 20))


def test_exact_date_removal_deletes_only_trailing_revisions(tmp_path: Path) -> None:
    clock = FixedClock()
    store = subject.LocalNetWorthHistoryStore(tmp_path / "history", clock=clock)
    store.save_snapshot(_snapshot(snapshot_id="snapshot-20260818-a", as_of="2026-08-18"))
    clock.advance()
    store.save_snapshot(_snapshot())
    clock.advance()
    revised = _snapshot(snapshot_id="snapshot-20260820-b")
    revised["assets"][0]["gross_value_krw"] = 121_000
    revised["assets"][0]["economic_value_krw"] = 121_000
    store.save_snapshot(revised)

    assert store.remove_exact_date(date(2026, 8, 20)) == 2
    assert store.load_exact(date(2026, 8, 20)) is None
    assert store.load_exact(date(2026, 8, 18)).totals.net_worth_krw == 875_000
    assert len(store.load_history()) == 1


def test_exact_date_removal_rejects_non_tail_without_changing_history(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    root = tmp_path / "history"
    store = subject.LocalNetWorthHistoryStore(root, clock=clock)
    store.save_snapshot(_snapshot(snapshot_id="snapshot-20260818-a", as_of="2026-08-18"))
    clock.advance()
    store.save_snapshot(_snapshot())
    before = {path.name: path.read_bytes() for path in root.glob("record-*.json")}

    with pytest.raises(
        subject.NetWorthPersistenceError, match="REMOVAL_NON_TAIL_REJECTED"
    ):
        store.remove_exact_date(date(2026, 8, 18))

    assert {path.name: path.read_bytes() for path in root.glob("record-*.json")} == before
    assert len(store.load_history()) == 2


def _timeline_record(
    payload: dict[str, object],
    *,
    saved_at: datetime,
    record_digest: str,
) -> subject.NetWorthHistoryRecord:
    snapshot = subject.parse_snapshot(payload)
    return subject.NetWorthHistoryRecord(
        saved_at_utc=saved_at,
        snapshot_digest="s" * 64,
        previous_record_digest=None,
        record_digest=record_digest,
        view=subject.NetWorthView(
            snapshot=snapshot,
            totals=subject.calculate_net_worth(snapshot),
        ),
    )


def test_timeline_selects_latest_revision_orders_dates_and_calculates_delta() -> None:
    first_payload = _snapshot(
        snapshot_id="snapshot-20260818-a", as_of="2026-08-18"
    )
    revised_payload = deepcopy(first_payload)
    revised_payload["snapshot_id"] = "snapshot-20260818-b"
    revised_payload["assets"][0]["gross_value_krw"] += 10_000
    revised_payload["assets"][0]["economic_value_krw"] += 10_000
    latest_payload = _snapshot(
        snapshot_id="snapshot-20260820-a", as_of="2026-08-20"
    )
    latest_payload["assets"][0]["gross_value_krw"] += 30_000
    latest_payload["assets"][0]["economic_value_krw"] += 30_000
    first = _timeline_record(
        first_payload,
        saved_at=datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    revised = _timeline_record(
        revised_payload,
        saved_at=datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
        record_digest="b" * 64,
    )
    latest = _timeline_record(
        latest_payload,
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="c" * 64,
    )

    timeline = subject.build_net_worth_timeline((latest, first, revised))

    assert [point.as_of_date for point in timeline.points] == [
        date(2026, 8, 18),
        date(2026, 8, 20),
    ]
    assert timeline.points[0].net_worth_krw == revised.view.totals.net_worth_krw
    assert timeline.points[0].delta_state is subject.NetWorthTimelineDeltaState.UNAVAILABLE
    assert (
        timeline.points[0].delta_reason == "NO_PREVIOUS_COMPLETE"
    )
    assert timeline.points[1].delta_state is subject.NetWorthTimelineDeltaState.AVAILABLE
    assert timeline.points[1].delta_from_previous_complete_krw == 20_000
    assert timeline.points[1].previous_complete_date == date(2026, 8, 18)


def test_timeline_keeps_partial_stale_and_invalid_snapshots_as_explicit_gaps() -> None:
    complete = _timeline_record(
        _snapshot(snapshot_id="snapshot-20260818-a", as_of="2026-08-18"),
        saved_at=datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    stale_payload = _snapshot(
        snapshot_id="snapshot-20260819-a", as_of="2026-08-19"
    )
    stale_payload["assets"][0]["valuation_status"] = "STALE"
    stale = _timeline_record(
        stale_payload,
        saved_at=datetime(2026, 8, 19, 1, tzinfo=timezone.utc),
        record_digest="b" * 64,
    )
    invalid = _timeline_record(
        _snapshot(snapshot_id="snapshot-20260820-a", as_of="2026-08-20"),
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="c" * 64,
    )
    invalid = replace(
        invalid,
        view=replace(
            invalid.view,
            totals=replace(
                invalid.view.totals,
                net_worth_krw=invalid.view.totals.net_worth_krw + 1,
            ),
        ),
    )
    later_payload = _snapshot(
        snapshot_id="snapshot-20260821-a", as_of="2026-08-21"
    )
    later_payload["assets"][0]["gross_value_krw"] += 50_000
    later_payload["assets"][0]["economic_value_krw"] += 50_000
    later = _timeline_record(
        later_payload,
        saved_at=datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
        record_digest="d" * 64,
    )

    points = subject.build_net_worth_timeline(
        (invalid, later, stale, complete)
    ).points

    assert points[1].display_state is subject.NetWorthTimelineDisplayState.GAP
    assert (
        points[1].display_reason == "SNAPSHOT_INCOMPLETE"
    )
    assert points[2].display_state is subject.NetWorthTimelineDisplayState.GAP
    assert (
        points[2].display_reason == "SNAPSHOT_INVALID"
    )
    for point in points[1:3]:
        assert point.net_worth_krw is None
        assert point.delta_state is subject.NetWorthTimelineDeltaState.UNAVAILABLE
        assert point.delta_from_previous_complete_krw is None
        assert point.previous_complete_date is None
    assert points[3].delta_from_previous_complete_krw == 50_000
    assert points[3].previous_complete_date == date(2026, 8, 18)


def test_timeline_currency_mismatch_is_a_gap_and_blocks_cross_currency_delta() -> None:
    first = _timeline_record(
        _snapshot(snapshot_id="snapshot-20260818-a", as_of="2026-08-18"),
        saved_at=datetime(2026, 8, 18, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    mismatch = _timeline_record(
        _snapshot(snapshot_id="snapshot-20260819-a", as_of="2026-08-19"),
        saved_at=datetime(2026, 8, 19, 1, tzinfo=timezone.utc),
        record_digest="b" * 64,
    )
    mismatch = replace(
        mismatch,
        view=replace(
            mismatch.view,
            snapshot=replace(mismatch.view.snapshot, base_currency="USD"),
        ),
    )
    latest = _timeline_record(
        _snapshot(snapshot_id="snapshot-20260820-a", as_of="2026-08-20"),
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="c" * 64,
    )

    points = subject.build_net_worth_timeline((latest, mismatch, first)).points

    assert points[1].display_state is subject.NetWorthTimelineDisplayState.GAP
    assert (
        points[1].display_reason == "CURRENCY_MISMATCH"
    )
    assert points[1].net_worth_krw is None
    assert (
        points[1].delta_reason == "CURRENCY_MISMATCH"
    )
    assert points[2].display_state is subject.NetWorthTimelineDisplayState.DISPLAYABLE
    assert points[2].delta_state is subject.NetWorthTimelineDeltaState.UNAVAILABLE
    assert (
        points[2].delta_reason == "CURRENCY_MISMATCH"
    )
    assert points[2].delta_from_previous_complete_krw is None
    assert points[2].previous_complete_date == date(2026, 8, 19)


def test_timeline_duplicate_tie_break_is_deterministic_and_inputs_are_unchanged() -> None:
    original = _timeline_record(
        _snapshot(snapshot_id="snapshot-20260820-a"),
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    revised_payload = _snapshot(snapshot_id="snapshot-20260820-b")
    revised_payload["assets"][0]["gross_value_krw"] += 7_000
    revised_payload["assets"][0]["economic_value_krw"] += 7_000
    revised = _timeline_record(
        revised_payload,
        saved_at=original.saved_at_utc,
        record_digest="b" * 64,
    )
    records = [original, revised, original]
    before = deepcopy(records)

    forward = subject.build_net_worth_timeline(records)
    reverse = subject.build_net_worth_timeline(reversed(records))

    assert forward == reverse
    assert forward.points[0].net_worth_krw == revised.view.totals.net_worth_krw
    assert records == before


def test_timeline_empty_history_is_an_immutable_empty_view() -> None:
    timeline = subject.build_net_worth_timeline(())

    assert timeline == subject.NetWorthTimelineView(points=())
    with pytest.raises(AttributeError):
        timeline.points = ()
