from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

import pytest

import stock_data.orchestration.workflow_control.listener_gateway as listener_module

from stock_data.orchestration.workflow_control.listener_gateway import (
    ListenerGateway,
    ListenerDigestCollision,
    ListenerIntent,
    MailboxEnvelope,
    ListenerRoute,
    ListenerSinks,
    ListenerStateConflict,
    ListenerValidationError,
    PMMailboxIdentity,
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
        self.envelopes: list[MailboxEnvelope] = []

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

    def deliver_mailbox_envelope(self, envelope: MailboxEnvelope) -> str:
        self.envelopes.append(envelope)
        return self._accept("pm", envelope.message_id)

    def accept_new_candidate(
        self,
        *,
        receipt_key: str,
        intent_key: str,
        summary: str,
        source_route: str,
    ) -> str:
        return self._accept(f"new:{source_route}", receipt_key)


class MutablePMIdentityResolver:
    def __init__(self, identity: PMMailboxIdentity) -> None:
        self.identity = identity

    def resolve_pm_mailbox_identity(self) -> PMMailboxIdentity:
        return self.identity


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
        expected_pm_identity=PMMailboxIdentity(
            "project_manager", "pm-session-7", 7
        ),
        allow_legacy_direct_pm=True,
    )


def _intent(
    *,
    cursor: str = "turn-12",
    received_at: str = "2026-08-31T00:00:00Z",
    previous: str | None = None,
    listener_id: str = "root-listener",
    conversation_id: str = "conversation-7",
) -> ListenerIntent:
    return ListenerIntent(
        listener_id=listener_id,
        conversation_id=conversation_id,
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
        [
            {
                "kind": "direct_pm",
                "payload": {"message": "x", "recipient": "Project Manager", "generation": 1},
            }
        ],
        [
            {
                "kind": "direct_pm",
                "payload": {"message": "x", "recipient": "project_manager"},
            }
        ],
        [
            {
                "kind": "direct_pm",
                "payload": {"message": "x", "recipient": "worker", "generation": 1},
            }
        ],
        [
            {
                "kind": "direct_pm",
                "payload": {
                    "generation": 1,
                    "message": "x",
                    "message_type": [],
                    "recipient": "project_manager",
                },
            }
        ],
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


def test_generation_bound_mailbox_envelope_survives_lost_ack_and_restart() -> None:
    with _workspace("listener-envelope-restart") as root:
        fake = FakeControlPlane(fail_after_accept={"pm"})
        route = ListenerRoute(
            RouteKind.DIRECT_PM,
            {
                "generation": 7,
                "message": "현재 PM 세션을 깨워 주세요",
                "message_type": "operational_wake",
                "queue_id": "RQ-20260831T010203-A1B2",
                "recipient": "project_manager",
                "session_id": "pm-session-7",
            },
        )
        with _gateway(root, fake) as gateway:
            with pytest.raises(RuntimeError, match="lost pm acknowledgement"):
                gateway.intake(_intent(), [route])
        assert len(fake.accepted) == 1

        with _gateway(root, fake) as resumed:
            receipts = resumed.resume_pending()
            assert len(receipts) == 1
            assert receipts[0].deliveries[0].status == "accepted"
            assert resumed.resume_pending() == ()

        assert [kind for kind, _key in fake.events] == ["pm"]
        assert len(fake.envelopes) == 2
        first, retry = fake.envelopes
        assert retry == first
        assert first.recipient == "project_manager"
        assert first.session_id == "pm-session-7"
        assert first.generation == 7
        assert first.queue_id == "RQ-20260831T010203-A1B2"
        assert first.message_type == "operational_wake"
        assert first.delivery_status == "pending"
        assert first.body_digest not in first.body
        assert first.message_id == receipts[0].deliveries[0].receipt_key


def test_mailbox_rejects_decreasing_generation_and_legacy_default() -> None:
    with _workspace("listener-stale-generation") as root:
        fake = FakeControlPlane()
        current = ListenerRoute(
            RouteKind.DIRECT_PM,
            {
                "generation": 7,
                "message": "current",
                "recipient": "project_manager",
                "session_id": "pm-session-7",
            },
        )
        stale = ListenerRoute(
            RouteKind.DIRECT_PM,
            {
                "generation": 6,
                "message": "stale",
                "recipient": "project_manager",
                "session_id": "pm-session-7",
            },
        )
        intent = _intent()
        with _gateway(root, fake) as gateway:
            gateway.intake(intent, [current])
            with pytest.raises(ListenerValidationError, match="generation"):
                gateway.intake(
                    _intent(
                        cursor="turn-13",
                        received_at="2026-08-31T00:10:00Z",
                        previous=intent.intent_key,
                    ),
                    [stale],
                )

    with _workspace("listener-legacy-default") as root:
        fake = FakeControlPlane()
        database, journal = _paths(root)
        with ListenerGateway(
            database,
            journal,
            sinks=ListenerSinks(fake, fake, fake),
        ) as gateway:
            with pytest.raises(ListenerValidationError, match="legacy"):
                gateway.intake(
                    _intent(),
                    [ListenerRoute(RouteKind.DIRECT_PM, {"message": "legacy"})],
                )


def test_mailbox_resolver_fences_restart_to_the_current_stored_session() -> None:
    with _workspace("listener-current-session") as root:
        database, journal = _paths(root)
        fake = FakeControlPlane(fail_after_accept={"pm"})
        resolver = MutablePMIdentityResolver(
            PMMailboxIdentity("project_manager", "pm-session-7", 7)
        )
        route = ListenerRoute(
            RouteKind.DIRECT_PM,
            {
                "generation": 7,
                "message": "current session only",
                "recipient": "project_manager",
                "session_id": "pm-session-7",
            },
        )
        with ListenerGateway(
            database,
            journal,
            sinks=ListenerSinks(fake, fake, fake),
            pm_identity_resolver=resolver,
        ) as gateway:
            with pytest.raises(RuntimeError, match="lost pm acknowledgement"):
                gateway.intake(_intent(), [route])

        resolver.identity = PMMailboxIdentity(
            "project_manager", "pm-session-8", 8
        )
        with ListenerGateway(
            database,
            journal,
            sinks=ListenerSinks(fake, fake, fake),
            pm_identity_resolver=resolver,
        ) as restarted:
            with pytest.raises(ListenerValidationError, match="stale"):
                restarted.resume_pending()
        assert len(fake.events) == 1


def test_pending_legacy_delivery_requires_explicit_restart_opt_in() -> None:
    with _workspace("listener-legacy-restart") as root:
        database, journal = _paths(root)
        fake = FakeControlPlane(fail_after_accept={"pm"})
        with ListenerGateway(
            database,
            journal,
            sinks=ListenerSinks(fake, fake, fake),
            allow_legacy_direct_pm=True,
        ) as compatibility_gateway:
            with pytest.raises(RuntimeError, match="lost pm acknowledgement"):
                compatibility_gateway.intake(
                    _intent(),
                    [ListenerRoute(RouteKind.DIRECT_PM, {"message": "legacy"})],
                )
        with ListenerGateway(
            database,
            journal,
            sinks=ListenerSinks(fake, fake, fake),
        ) as production_gateway:
            with pytest.raises(ListenerValidationError, match="legacy"):
                production_gateway.resume_pending()
        assert len(fake.events) == 1


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("payload_json", '{"message":"tampered"}'),
        ("route_kind", "bounded_new"),
        ("intent_key", "0" * 64),
    ],
)
def test_lost_ack_restart_rejects_noncanonical_action_columns(
    column: str,
    replacement: str,
) -> None:
    with _workspace("listener-row-tamper") as root:
        fake = FakeControlPlane(fail_after_accept={"pm"})
        with _gateway(root, fake) as gateway:
            with pytest.raises(RuntimeError, match="lost pm acknowledgement"):
                gateway.intake(_intent(), [_all_routes()[1]])
        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                f"UPDATE listener_actions SET {column} = ?",  # noqa: S608 - fixed test columns
                (replacement,),
            )
            connection.commit()

        delivered_before = list(fake.events)
        with pytest.raises((ListenerDigestCollision, ListenerStateConflict)):
            _gateway(root, fake)
        assert fake.events == delivered_before


def test_restart_rejects_receipt_acceptance_reference_tamper() -> None:
    with _workspace("listener-receipt-tamper") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake) as gateway:
            gateway.intake(_intent(), [_all_routes()[1]])
        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE listener_delivery_receipts SET acceptance_ref = 'tampered'"
            )
            connection.commit()
        delivered_before = list(fake.events)
        with pytest.raises(ListenerStateConflict, match="journal event"):
            _gateway(root, fake)
        assert fake.events == delivered_before


def test_restart_rejects_checkpoint_updated_at_tamper() -> None:
    with _workspace("listener-checkpoint-time-tamper") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake) as gateway:
            gateway.intake(_intent(), [_all_routes()[1]])
        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE listener_checkpoints SET updated_at = ?",
                ("2099-01-01T00:00:00Z",),
            )
            connection.commit()
        with pytest.raises(ListenerStateConflict, match="checkpoint"):
            _gateway(root, FakeControlPlane())


def test_restart_rejects_deleted_checkpoint_pointer() -> None:
    with _workspace("listener-checkpoint-delete") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake) as gateway:
            gateway.intake(_intent(), [_all_routes()[1]])
        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DELETE FROM listener_checkpoints")
            connection.commit()
        with pytest.raises(ListenerStateConflict, match="checkpoint|pointer"):
            _gateway(root, FakeControlPlane())


def test_restart_rejects_rewound_checkpoint_pointer() -> None:
    with _workspace("listener-checkpoint-rewind") as root:
        fake = FakeControlPlane()
        first = _intent()
        second = _intent(
            cursor="turn-13",
            received_at="2026-08-31T00:10:00Z",
            previous=first.intent_key,
        )
        with _gateway(root, fake) as gateway:
            gateway.intake(first, [_all_routes()[1]])
            gateway.intake(second, [_all_routes()[1]])
        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE listener_checkpoints SET conversation_id = ?, "
                "checkpoint_cursor = ?, last_intent_key = ?, updated_at = ?",
                (
                    first.conversation_id,
                    first.checkpoint_cursor,
                    first.intent_key,
                    first.received_at,
                ),
            )
            connection.commit()
        with pytest.raises(ListenerStateConflict, match="head|chain|checkpoint"):
            _gateway(root, FakeControlPlane())


def test_restart_rejects_branched_predecessor_chain() -> None:
    with _workspace("listener-checkpoint-branch") as root:
        fake = FakeControlPlane()
        first = _intent()
        second = _intent(
            cursor="turn-13",
            received_at="2026-08-31T00:10:00Z",
            previous=first.intent_key,
        )
        branch = _intent(
            cursor="turn-14",
            received_at="2026-08-31T00:20:00Z",
            previous=first.intent_key,
        )
        with _gateway(root, fake) as gateway:
            gateway.intake(first, [_all_routes()[1]])
            gateway.intake(second, [_all_routes()[1]])
        database, _journal = _paths(root)
        event_id, event_json = listener_module._event_line(
            listener_module._checkpoint_event(branch)
        )
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "INSERT INTO listener_intents "
                "(intent_key, listener_id, conversation_id, checkpoint_cursor, "
                "user_text, received_at, canonical_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    branch.intent_key,
                    branch.listener_id,
                    branch.conversation_id,
                    branch.checkpoint_cursor,
                    branch.user_text,
                    branch.received_at,
                    branch.canonical_json,
                ),
            )
            connection.execute(
                "INSERT INTO listener_journal_events (event_id, event_json) VALUES (?, ?)",
                (event_id, event_json),
            )
            connection.commit()
        with pytest.raises(ListenerStateConflict, match="branch|chain|head"):
            _gateway(root, FakeControlPlane())


def test_restart_rejects_orphan_and_duplicate_cross_listener_pointer() -> None:
    with _workspace("listener-checkpoint-cross-listener") as root:
        fake = FakeControlPlane()
        first = _intent(listener_id="listener-a", conversation_id="conversation-a")
        other = _intent(listener_id="listener-b", conversation_id="conversation-b")
        with _gateway(root, fake) as gateway:
            gateway.intake(first, [_all_routes()[1]])
            gateway.intake(other, [_all_routes()[1]])
        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE listener_checkpoints SET last_intent_key = ? "
                "WHERE listener_id = ?",
                (first.intent_key, other.listener_id),
            )
            connection.commit()
        with pytest.raises(ListenerStateConflict, match="checkpoint|pointer|listener"):
            _gateway(root, FakeControlPlane())


def test_restart_checkpoint_validation_holds_immediate_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _workspace("listener-checkpoint-race") as root:
        fake = FakeControlPlane()
        with _gateway(root, fake) as gateway:
            gateway.intake(_intent(), [_all_routes()[1]])
        database, _journal = _paths(root)
        original = ListenerGateway._validate_canonical_rows
        writer_was_fenced = False

        def race_delete(self: ListenerGateway, *args: object) -> None:
            nonlocal writer_was_fenced
            with closing(sqlite3.connect(database, timeout=0.0)) as connection:
                try:
                    connection.execute("DELETE FROM listener_checkpoints")
                    connection.commit()
                except sqlite3.OperationalError as error:
                    writer_was_fenced = "locked" in str(error).lower()
                    connection.rollback()
            original(self, *args)  # type: ignore[arg-type]

        monkeypatch.setattr(ListenerGateway, "_validate_canonical_rows", race_delete)
        with _gateway(root, FakeControlPlane()):
            pass
        assert writer_was_fenced


def test_restart_rejects_duplicate_checkpoint_event_for_one_intent() -> None:
    with _workspace("listener-checkpoint-event-duplicate") as root:
        fake = FakeControlPlane()
        intent = _intent()
        with _gateway(root, fake) as gateway:
            gateway.intake(intent, [_all_routes()[1]])
        database, _journal = _paths(root)
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute(
                "SELECT event_id, event_json, exported FROM listener_journal_events"
            ).fetchall()
            exact = next(
                row for row in rows
                if json.loads(row[1]).get("event_type") == "checkpoint"
            )
            tampered_body = json.loads(exact[1])
            tampered_body.pop("event_id")
            tampered_body["received_at"] = "2099-01-01T00:00:00Z"
            tampered_id, tampered_json = listener_module._event_line(tampered_body)
            connection.execute(
                "DELETE FROM listener_journal_events WHERE event_id = ?",
                (exact[0],),
            )
            connection.execute(
                "INSERT INTO listener_journal_events (event_id, event_json, exported) "
                "VALUES (?, ?, 0)",
                (tampered_id, tampered_json),
            )
            connection.execute(
                "INSERT INTO listener_journal_events (event_id, event_json, exported) "
                "VALUES (?, ?, ?)",
                exact,
            )
            connection.commit()
        with pytest.raises(ListenerStateConflict, match="checkpoint.*event|exactly one"):
            _gateway(root, FakeControlPlane())
