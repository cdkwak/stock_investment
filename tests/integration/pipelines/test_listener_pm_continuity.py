from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from stock_data.orchestration.workflow_control.listener_gateway import (
    ListenerGateway,
    ListenerIntent,
    ListenerRoute,
    ListenerSinks,
    ListenerStateConflict,
    PMMutationAuthority,
    RouteKind,
)


class ReceiptDeduplicatingControlPlane:
    """Local sink fake that models idempotency at the PM-owned boundary."""

    def __init__(self) -> None:
        self.observed: list[tuple[str, str]] = []
        self.accepted: dict[str, str] = {}
        self.raise_after_accepting_pm = True

    def _accept(self, kind: str, receipt_key: str) -> str:
        acceptance = self.accepted.get(receipt_key)
        if acceptance is None:
            acceptance = f"accepted:{kind}:{receipt_key[:16]}"
            self.accepted[receipt_key] = acceptance
            self.observed.append((kind, receipt_key))
        if kind == "pm" and self.raise_after_accepting_pm:
            self.raise_after_accepting_pm = False
            raise RuntimeError("simulated crash after PM accepted the stable receipt")
        return acceptance

    def accept_project_goal_receipt(
        self, *, receipt_key: str, intent_key: str, goal_text: str
    ) -> str:
        return self._accept("goal_receipt", receipt_key)

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


@contextmanager
def _workspace():
    root = Path(os.environ["TEMP"]) / f"listener-continuity-{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _gateway(root: Path, fake: ReceiptDeduplicatingControlPlane) -> ListenerGateway:
    return ListenerGateway(
        root / "listener.sqlite3",
        root / "listener.jsonl",
        sinks=ListenerSinks(fake, fake, fake),
        pm_authority=PMMutationAuthority("python_pm", True),
    )


def _intent(received_at: str = "2026-08-31T01:00:00Z") -> ListenerIntent:
    return ListenerIntent(
        listener_id="root-listener",
        conversation_id="fresh-conversation",
        checkpoint_cursor="cursor-21",
        user_text="목표와 실행 상태 및 새 관찰을 전달",
        received_at=received_at,
    )


def _routes() -> list[ListenerRoute]:
    return [
        ListenerRoute(RouteKind.GOAL_CHANGE, {"goal_text": "지속 가능한 목표"}),
        ListenerRoute(RouteKind.DIRECT_PM, {"message": "실행 상태 전달"}),
        ListenerRoute(RouteKind.BOUNDED_NEW, {"summary": "경계가 있는 새 관찰"}),
    ]


def test_fresh_session_resumes_pending_and_delivers_each_receipt_once() -> None:
    with _workspace() as root:
        fake = ReceiptDeduplicatingControlPlane()
        first = _gateway(root, fake)
        try:
            with pytest.raises(RuntimeError, match="simulated crash"):
                first.intake(_intent(), _routes())
        finally:
            first.close()

        assert [kind for kind, _key in fake.observed] == [
            "goal_receipt",
            "new:goal_change",
            "pm",
        ]
        assert len({key for _kind, key in fake.observed}) == 3

        with _gateway(root, fake) as resumed:
            recovered = resumed.resume_pending()
            assert [item.route_kind for item in recovered] == [
                RouteKind.DIRECT_PM,
                RouteKind.BOUNDED_NEW,
            ]
            assert all(
                delivery.status == "accepted"
                for item in recovered
                for delivery in item.deliveries
            )

        assert [kind for kind, _key in fake.observed] == [
            "goal_receipt",
            "new:goal_change",
            "pm",
            "new:bounded_new",
        ]
        assert len({key for _kind, key in fake.observed}) == 4

        with _gateway(root, fake) as later_session:
            assert later_session.resume_pending() == ()
            replay = later_session.intake(
                _intent("2026-08-31T01:30:00Z"),
                _routes(),
            )
            assert len(replay) == 3
        assert len(fake.observed) == 4

        journal = root / "listener.jsonl"
        journal.write_text(
            journal.read_text(encoding="utf-8") + "{malformed",
            encoding="utf-8",
        )
        clean_fake = ReceiptDeduplicatingControlPlane()
        clean_fake.raise_after_accepting_pm = False
        with pytest.raises(ListenerStateConflict):
            _gateway(root, clean_fake)
        assert clean_fake.observed == []
