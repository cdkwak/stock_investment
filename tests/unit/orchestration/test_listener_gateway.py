from __future__ import annotations

from contextlib import closing, contextmanager
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

import pytest

from stock_data.orchestration.workflow_control.listener_gateway import (
    ListenerGateway,
    ListenerDigestCollision,
    ListenerIntent,
    ListenerRoute,
    ListenerSinks,
    ListenerStateConflict,
    ListenerValidationError,
    PMAuthorityRequired,
    PMMutationAuthority,
    RouteKind,
)


class FakeControlPlane:
    def __init__(self, *, fail_after_accept: set[str] | None = None) -> None:
        self.events: list[tuple[str, str]] = []
        self.accepted: dict[str, str] = {}
        self.fail_after_accept = fail_after_accept or set()
        self.failed: set[str] = set()

    def _accept(self, kind: str, receipt_key: str) -> str:
        acceptance = self.accepted.get(receipt_key)
        if acceptance is None:
            acceptance = f"{kind}:{receipt_key[:16]}"
            self.accepted[receipt_key] = acceptance
            self.events.append((kind, receipt_key))
        if kind in self.fail_after_accept and kind not in self.failed:
            self.failed.add(kind)
            raise RuntimeError(f"lost {kind} acknowledgement")
        return acceptance

    def accept_project_goal_receipt(
        self, *, receipt_key: str, intent_key: str, goal_text: str
    ) -> str:
        return self._accept("goal", receipt_key)

    def deliver_pm_message(
        self, *, receipt_key: str, intent_key: str, message: str
    ) -> str:
        return self._accept("pm", receipt_key)

    def accept_new_candidate(
        self,
        *,
        receipt_key: str,
        intent_key: str,
        summary: str,
        source_route: str,
    ) -> str:
        return self._accept(f"new:{source_route}", receipt_key)


def _paths(root: str) -> tuple[Path, Path]:
    return Path(root) / "listener.sqlite3", Path(root) / "listener.jsonl"


@contextmanager
def _workspace(prefix: str):
    root = Path(os.environ["TEMP"]) / f"{prefix}-{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root)


def _gateway(
    root: str,
    fake: FakeControlPlane,
    *,
    authorized: bool = True,
) -> ListenerGateway:
    database, journal = _paths(root)
    return ListenerGateway(
        database,
        journal,
        sinks=ListenerSinks(fake, fake, fake),
        pm_authority=(
            PMMutationAuthority("python_pm", True) if authorized else None
        ),
    )


def _intent(
    *,
    cursor: str = "turn-12",
    received_at: str = "2026-08-31T00:00:00Z",
    previous: str | None = None,
) -> ListenerIntent:
    return ListenerIntent(
        listener_id="root-listener",
        conversation_id="conversation-7",
        checkpoint_cursor=cursor,
        user_text="목표 변경과 상태 전달 및 새 작업 관찰",
        received_at=received_at,
        previous_intent_key=previous,
    )


def _all_routes() -> list[ListenerRoute]:
    return [
        ListenerRoute(RouteKind.GOAL_CHANGE, {"goal_text": "검증된 목표"}),
        ListenerRoute(RouteKind.DIRECT_PM, {"message": "현재 상태를 확인"}),
        ListenerRoute(RouteKind.BOUNDED_NEW, {"summary": "작은 새 작업"}),
    ]


def test_listener_schema_and_identical_records_are_idempotent() -> None:
    with _workspace("listener-schema") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake) as gateway:
            first = gateway.intake(_intent(), [_all_routes()[1]])
            replay = gateway.intake(
                _intent(received_at="2026-08-31T00:09:00Z"),
                [_all_routes()[1]],
            )
            assert replay == first
            assert [kind for kind, _key in fake.events] == ["pm"]

        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert {
                "listener_intents",
                "listener_checkpoints",
                "listener_actions",
                "listener_delivery_receipts",
            } <= tables
            assert connection.execute(
                "SELECT COUNT(*) FROM listener_intents"
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM listener_actions"
            ).fetchone() == (1,)


def test_all_routes_have_distinct_receipts_and_replay_exactly_once() -> None:
    with _workspace("listener-unit") as root:
        fake = FakeControlPlane()
        intent = _intent()
        with _gateway(root, fake) as gateway:
            receipts = gateway.intake(intent, _all_routes())

            assert [receipt.route_kind for receipt in receipts] == list(RouteKind)
            assert len({receipt.action_key for receipt in receipts}) == 3
            delivery_keys = [
                delivery.receipt_key
                for receipt in receipts
                for delivery in receipt.deliveries
            ]
            assert len(delivery_keys) == len(set(delivery_keys)) == 4
            assert all(
                delivery.status == "accepted"
                for receipt in receipts
                for delivery in receipt.deliveries
            )
            assert [kind for kind, _key in fake.events] == [
                "goal",
                "new:goal_change",
                "pm",
                "new:bounded_new",
            ]

            retry = _intent(received_at="2026-08-31T00:09:00Z")
            replay = gateway.intake(retry, _all_routes())
            assert [item.action_key for item in replay] == [
                item.action_key for item in receipts
            ]
            assert len(fake.events) == 4

            advanced = _intent(
                cursor="turn-13",
                received_at="2026-08-31T00:10:00Z",
                previous=intent.intent_key,
            )
            advanced_receipt = gateway.intake(
                advanced,
                [ListenerRoute(RouteKind.DIRECT_PM, {"message": "다음 상태"})],
            )
            assert advanced_receipt[0].action_key not in {
                item.action_key for item in receipts
            }
            assert [kind for kind, _key in fake.events].count("pm") == 2

        journal_text = _paths(root)[1].read_text(encoding="utf-8")
        assert "검증된 목표" not in journal_text
        assert "현재 상태를 확인" not in journal_text
        assert "작은 새 작업" not in journal_text


def test_goal_and_new_require_explicit_pm_authority_before_sink_calls() -> None:
    with _workspace("listener-authority") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake, authorized=False) as gateway:
            with pytest.raises(PMAuthorityRequired):
                gateway.intake(_intent(), [_all_routes()[0]])
            with pytest.raises(PMAuthorityRequired):
                gateway.intake(_intent(), [_all_routes()[2]])
            assert fake.events == []

            direct = gateway.intake(_intent(), [_all_routes()[1]])
            assert direct[0].route_kind is RouteKind.DIRECT_PM
            assert [kind for kind, _key in fake.events] == ["pm"]


@pytest.mark.parametrize(
    "declarations",
    [
        [{"kind": "queue", "payload": {"message": "x"}}],
        [
            {"kind": "direct_pm", "payload": {"message": "x"}},
            {"kind": "direct_pm", "payload": {"message": "y"}},
        ],
        [{"kind": "direct_pm", "payload": {"message": "x"}, "extra": True}],
        [{"kind": "direct_pm", "payload": {"message": " "}}],
        [{"kind": "bounded_new", "payload": {"summary": "x", "message": "y"}}],
        [{"kind": "goal_change", "payload": ["not", "an", "object"]}],
        [],
    ],
)
def test_invalid_or_ambiguous_routes_fail_before_sink_calls(declarations: object) -> None:
    with _workspace("listener-invalid") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake) as gateway:
            with pytest.raises(ListenerValidationError):
                gateway.intake(_intent(), declarations)  # type: ignore[arg-type]
            assert fake.events == []


def test_digest_and_journal_state_conflicts_fail_closed() -> None:
    with _workspace("listener-collision") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake) as gateway:
            gateway.intake(_intent(), [_all_routes()[1]])
        database, journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE listener_intents SET canonical_json = ?",
                ('{"tampered":true}',),
            )
            connection.commit()
        fresh_fake = FakeControlPlane()
        with pytest.raises(ListenerDigestCollision):
            _gateway(root, fresh_fake)
        assert fresh_fake.events == []

        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT listener_id, conversation_id, checkpoint_cursor, user_text, "
                "canonical_json FROM listener_intents"
            ).fetchone()
            assert row is not None
            repaired = _intent().canonical_json
            connection.execute(
                "UPDATE listener_intents SET canonical_json = ?", (repaired,)
            )
            connection.execute(
                "UPDATE listener_delivery_receipts SET status = 'pending'"
            )
            connection.commit()
        with pytest.raises(ListenerStateConflict):
            _gateway(root, fresh_fake)
        assert fresh_fake.events == []

        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE listener_delivery_receipts SET status = 'accepted'"
            )
            connection.execute(
                "UPDATE listener_checkpoints SET checkpoint_cursor = 'tampered'"
            )
            connection.commit()
        with pytest.raises(ListenerStateConflict):
            _gateway(root, fresh_fake)
        assert fresh_fake.events == []

        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE listener_checkpoints SET checkpoint_cursor = 'turn-12'"
            )
            connection.commit()
        journal.write_text(journal.read_text(encoding="utf-8") + "{malformed", encoding="utf-8")
        with pytest.raises(ListenerStateConflict):
            _gateway(root, fresh_fake)
        assert fresh_fake.events == []
