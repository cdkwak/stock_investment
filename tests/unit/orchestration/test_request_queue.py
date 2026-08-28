from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
from threading import Barrier, Thread, local

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/request_queue.py"
SPEC = importlib.util.spec_from_file_location("request_queue_cli", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)
_RAW_QUEUE_MAIN = queue.main
_TEST_CLAIM_TOKENS: dict[tuple[str, str], str] = {}
_REAL_TOKEN_HEX = queue.secrets.token_hex
_TOKEN_CONTEXT = local()
_ACTIVE_TOKEN_COMMANDS = {
    "checkpoint", "submit", "block", "route", "release", "wait",
    "orca-bind", "orca-reconcile",
}


def _observed_token_hex(size: int) -> str:
    token = getattr(_TOKEN_CONTEXT, "token", None)
    if token is None:
        return _REAL_TOKEN_HEX(size)
    del _TOKEN_CONTEXT.token
    return token


queue.secrets.token_hex = _observed_token_hex


def _token_aware_main(argv: list[str]) -> int:
    args = list(argv)
    command_index = next(
        index for index, value in enumerate(args)
        if value in {"claim", *_ACTIVE_TOKEN_COMMANDS}
    ) if any(
        value in {"claim", *_ACTIVE_TOKEN_COMMANDS} for value in args
    ) else None
    if command_index is not None:
        root = Path(args[args.index("--root") + 1]).resolve()
        command = args[command_index]
        task_id = args[command_index + 1]
        key = (str(root), task_id)
        if command == "claim":
            token = _REAL_TOKEN_HEX(32)
            _TOKEN_CONTEXT.token = token
            _TEST_CLAIM_TOKENS[key] = token
        elif command in _ACTIVE_TOKEN_COMMANDS and "--claim-token" not in args:
            try:
                state, task, meta = queue.find_task(root, task_id)
            except queue.QueueError:
                state, task, meta = "", root, {}
            if state == "active" and meta.get("assigned_role") == "lead":
                if "--expected-generation" not in args:
                    args.extend(("--expected-generation", queue._queue_generation(task)))
            else:
                token = _TEST_CLAIM_TOKENS.get(key)
                if token is not None:
                    args.extend(("--claim-token", token))
    return _RAW_QUEUE_MAIN(args)


queue.main = _token_aware_main


def _discover(root: Path, fingerprint: str = "component:boundary:defect") -> str:
    assert queue.main([
        "--root", str(root), "discover", "--title", "Bounded defect",
        "--discovered-by", "luna", "--source-task", "RQ-parent",
        "--priority-hint", "P1", "--fingerprint", fingerprint,
        "--symptom", "A bounded behavior differs.", "--evidence", "offline test",
        "--impact", "false status", "--suspected-scope", "src/component.py",
        "--reproduce", "pytest exact-node",
    ]) == 0
    task = next((root / "inbox/new").iterdir())
    return json.loads((task / "META.json").read_text(encoding="utf-8"))["id"]


def _triage(
    root: Path, task_id: str, *, review: bool = False, reviewer: str = "sol",
    write_scope: str = "src/component.py", writer_lane: str | None = None,
    resource_locks: tuple[str, ...] = (),
) -> None:
    args = [
        "--root", str(root), "triage", task_id, "--priority", "P1",
        "--risk", "medium", "--write-scope", write_scope,
        "--problem", "A bounded behavior differs.",
        "--evidence", "offline test reproduces the mismatch",
        "--allow", write_scope, "--deny", "external operations",
        "--done-when", "The bounded behavior matches its contract.",
        "--verify", "pytest exact-node",
    ]
    if writer_lane:
        args.extend(("--writer-lane", writer_lane))
    for resource_lock in resource_locks:
        args.extend(("--resource-lock", resource_lock))
    if review:
        args.extend(("--review-required", "--reviewer", reviewer))
    assert queue.main(args) == 0


def _complete(root: Path, fingerprint: str) -> str:
    task_id = _discover(root, fingerprint)
    _triage(root, task_id)
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "prerequisite",
        "--next", "complete prerequisite",
    ]) == 0
    assert queue.main([
        "--root", str(root), "submit", task_id, "--owner", "prerequisite",
        "--result", "completed", "--changed", "src/component.py",
        "--verified", "exact prerequisite passed",
    ]) == 0
    return task_id


def _review_generation(root: Path) -> str:
    review = next((root / "review").iterdir())
    fields = queue._read_fields(review / "REVIEW.md", ("review_generation",))
    return fields["review_generation"]


def _review_fields(root: Path) -> dict[str, str]:
    review = next((root / "review").iterdir())
    return queue._read_fields(
        review / "REVIEW.md", ("review_generation", "handoff_sha256"),
    )


def _mock_clean_tracked_done(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    def fake_git(start: Path, *args: str) -> str:
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return str(Path(start).resolve())
        relative = args[-1]
        task = Path(start) / relative
        if args[0] == "ls-files":
            if "--error-unmatch" in args:
                return relative
            return "\n".join(
                path.resolve().relative_to(Path(start).resolve()).as_posix()
                for path in sorted(task.rglob("*")) if path.is_file()
            )
        if args[0] == "status":
            return ""
        if args[:2] == ("cat-file", "-e"):
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(queue, "_git", fake_git)


def _commit_done(root: Path) -> None:
    repository = root.parent.parent
    commands = (
        ("init",),
        ("config", "user.email", "queue-test@example.invalid"),
        ("config", "user.name", "Queue Test"),
        ("add", "artifacts/request_queue/done"),
        ("commit", "-m", "retain completed queue receipt"),
    )
    for command in commands:
        subprocess.run(
            ("git", "-c", f"safe.directory={repository.resolve().as_posix()}",
             "-C", str(repository), *command),
            check=True, capture_output=True, text=True,
        )


def _task_bytes(task: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(task.iterdir()) if path.is_file()
    }


def _expire_active_receipt(task: Path) -> dict[str, object]:
    meta = json.loads((task / "META.json").read_text(encoding="utf-8"))
    meta.update({
        "created_at": "1999-12-31T23:58:00+00:00",
        "updated_at": "1999-12-31T23:59:00+00:00",
        "heartbeat": "1999-12-31T23:59:00+00:00",
        "lease_until": "2000-01-01T00:00:00+00:00",
    })
    queue._atomic_json(task / "META.json", meta)
    handoff = queue._read_handoff(task)
    handoff["updated_at"] = str(meta["updated_at"])
    queue._atomic_text(task / "HANDOFF.md", queue._handoff_text(handoff))
    return meta


def test_normal_flow_is_discover_triage_atomic_claim_checkpoint_and_done(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    assert queue.main(["--root", str(root), "init"]) == 0
    task_id = _discover(root)
    _triage(root, task_id)
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement exact fix",
    ]) == 0
    active = next((root / "active").iterdir())
    meta = json.loads((active / "META.json").read_text(encoding="utf-8"))
    assert meta["owner"] == "terra" and meta["heartbeat"] and meta["lease_until"]
    assert queue.main([
        "--root", str(root), "checkpoint", task_id, "--owner", "terra",
        "--phase", "testing", "--summary", "fix implemented",
        "--completed", "targeted edit", "--next", "run regression",
        "--files-touched", "src/component.py", "--tests", "exact node passed",
        "--risks", "none", "--new-discoveries", "none",
    ]) == 0
    assert queue.main([
        "--root", str(root), "submit", task_id, "--owner", "luna",
        "--result", "fixed", "--changed", "src/component.py",
        "--verified", "targeted and regression passed",
    ]) == 2
    assert queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "fixed",
        "--changed", "src/component.py", "--verified", "targeted and regression passed",
    ]) == 0
    done = next((root / "done").iterdir())
    assert (done / "RESULT.md").read_text(encoding="utf-8").startswith("result: fixed")
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_waiting_and_lead_release_do_not_abuse_blocked(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:v2:waiting-release")
    _triage(root, task_id)
    assert queue.main([
        "--root", str(root), "route", task_id, "--domain", "data",
        "--lead-owner", "data_lead", "--next", "wait for a dependency",
    ]) == 0
    assert queue.main([
        "--root", str(root), "wait", task_id, "--reason", "dependency cooldown",
        "--resume-condition", "dependency receipt is Done",
        "--next-check-at", "2030-01-01T09:00:00+09:00",
    ]) == 0
    waiting = next((root / "waiting").iterdir())
    assert (waiting / "WAITING.md").is_file()
    assert list((root / "blocked").iterdir()) == []
    assert queue.main([
        "--root", str(root), "resume-waiting", task_id,
        "--decision-basis", "dependency receipt is now Done", "--next", "claim by Lead",
    ]) == 0
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "data_lead",
        "--role", "lead", "--domain", "data", "--next", "dispatch a worker",
    ]) == 0
    assert queue.main([
        "--root", str(root), "release", task_id, "--owner", "data_lead",
        "--reason", "higher priority work arrived", "--next", "reclaim after P0",
    ]) == 0
    ready = next((root / "inbox/ready").iterdir())
    meta = json.loads((ready / "META.json").read_text(encoding="utf-8"))
    assert meta["owner"] is None and meta["assigned_role"] is None
    assert meta["domain"] == "data" and meta["lead_owner"] == "data_lead"
    assert "claim_token_sha256" not in meta
    assert queue.doctor(root) == []


def test_routed_lead_reads_own_worklist_and_resumes_without_session_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:v2:routed-lead-worklist")
    _triage(root, task_id)
    assert queue.main([
        "--root", str(root), "route", task_id, "--domain", "data",
        "--lead-owner", "data_lead", "--next", "Lead reads and decomposes the task",
    ]) == 0

    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "claim", task_id, "--owner", "unrelated_worker",
        "--role", "worker", "--domain", "data", "--next", "must not claim",
    ]) == 2
    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "claim", task_id, "--owner", "data_lead",
        "--role", "lead", "--domain", "gui", "--next", "wrong domain",
    ]) == 2
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "data_lead",
        "--role", "lead", "--domain", "data", "--next", "dispatch bounded workers",
    ]) == 0

    capsys.readouterr()
    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "status", "--lead-owner", "data_lead",
    ]) == 0
    worklist = capsys.readouterr().out
    assert f"state=active task=" in worklist
    assert task_id in worklist
    assert "domain=data" in worklist
    assert "next=dispatch bounded workers" in worklist
    generation = worklist.split("generation=", 1)[1].split()[0]
    claim_token = _TEST_CLAIM_TOKENS[(str(root.resolve()), task_id)]

    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "checkpoint", task_id, "--owner", "data_lead",
        "--claim-token", claim_token,
        "--phase", "stale-token", "--next", "must not bypass Lead generation",
    ]) == 2


    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "checkpoint", task_id, "--owner", "data_lead",
        "--expected-generation", generation,
        "--phase", "implementing", "--next", "collect worker results",
    ]) == 0
    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "checkpoint", task_id, "--owner", "data_lead",
        "--expected-generation", generation,
        "--phase", "stale", "--next", "must not overwrite the new generation",
    ]) == 2
    active = next((root / "active").iterdir())
    meta_path = active / "META.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["assigned_role"] = "worker"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    queue.write_board(root)
    assert any("Active Lead routing differs from assignment" in issue for issue in queue.doctor(root))


def test_one_lead_can_supervise_three_disjoint_active_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    monkeypatch.setattr(queue, "WRITER_LIMIT", 4)
    task_ids = []
    for index in range(4):
        task_id = _discover(root, f"queue:v2:lead-wip:{index}")
        _triage(
            root, task_id,
            write_scope=f"src/stock_data/providers/lead_wip_{index}.py",
            writer_lane="data",
        )
        task_ids.append(task_id)
    for task_id in task_ids[:3]:
        assert queue.main([
            "--root", str(root), "claim", task_id, "--owner", "data_lead",
            "--role", "lead", "--domain", "data", "--next", "supervise worker",
        ]) == 0
    assert queue.main([
        "--root", str(root), "claim", task_ids[3], "--owner", "data_lead",
        "--role", "lead", "--domain", "data", "--next", "exceed WIP",
    ]) == 2
    assert len(list((root / "active").iterdir())) == queue.LEAD_WIP_LIMIT == 3
    assert queue.doctor(root) == []


def test_orca_link_does_not_duplicate_dispatch_or_review_authority(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:v2:orca-link-only")
    _triage(root, task_id, review=True, reviewer="domain_reviewer")
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "data_lead",
        "--role", "lead", "--domain", "data", "--next", "bind Orca execution",
    ]) == 0
    assert queue.main([
        "--root", str(root), "orca-bind", task_id, "--owner", "data_lead",
        "--run-id", "run_queue_v2", "--orca-task-id", "task_queue_v2",
        "--domain", "gui", "--next-action", "must preserve Queue routing",
    ]) == 2
    assert queue.main([
        "--root", str(root), "orca-bind", task_id, "--owner", "data_lead",
        "--run-id", "run_queue_v2", "--orca-task-id", "task_queue_v2",
        "--domain", "data", "--next-action", "dispatch bounded workers",
    ]) == 0
    active = next((root / "active").iterdir())
    projection = json.loads((active / queue.ORCA_STATE_NAME).read_text(encoding="utf-8"))
    assert projection["phase"] == "BOUND"
    assert queue.main([
        "--root", str(root), "submit", task_id, "--owner", "data_lead",
        "--result", "Queue v2 implemented", "--changed", "src/component.py",
        "--verified", "focused tests passed",
    ]) == 0
    review = next((root / "review").iterdir())
    receipt = queue._read_fields(
        review / "REVIEW.md",
        ("review_generation", "orca_dispatch_id", "candidate_commit", "diff_digest"),
    )
    assert receipt == {"review_generation": receipt["review_generation"]}
    projection = json.loads((review / queue.ORCA_STATE_NAME).read_text(encoding="utf-8"))
    assert projection["phase"] == "BOUND"
    assert queue.main([
        "--root", str(root), "review-pass", task_id,
        "--reviewer", "domain_reviewer",
        "--review-generation", receipt["review_generation"],
        "--decision-basis", "Queue result and focused tests match",
    ]) == 0
    done = next((root / "done").iterdir())
    assert not (done / queue.ORCA_STATE_NAME).exists()
    assert queue.doctor(root) == []


def test_queue_receipt_hashes_are_portable_across_crlf_checkouts(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:v2:portable-text-receipts")
    _triage(root, task_id, review=True, reviewer="domain_reviewer")
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "data_lead",
        "--role", "lead", "--domain", "data", "--next", "implement",
    ]) == 0
    assert queue.main([
        "--root", str(root), "submit", task_id, "--owner", "data_lead",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "focused tests passed",
    ]) == 0
    review = next((root / "review").iterdir())
    expected = (
        queue._digest(root),
        queue._handoff_snapshot_digest(review),
        queue._task_receipt_digest(review),
    )

    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".md"}:
            body = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(body.replace(b"\n", b"\r\n"))

    assert expected == (
        queue._digest(root),
        queue._handoff_snapshot_digest(review),
        queue._task_receipt_digest(review),
    )
    assert queue.doctor(root) == []


def test_orca_reconciliation_exact_replay_repairs_partial_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:v2:orca-replay-repair")
    _triage(root, task_id)
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "infra_lead",
        "--role", "lead", "--domain", "infra", "--next", "bind Orca execution",
    ]) == 0
    assert queue.main([
        "--root", str(root), "orca-bind", task_id, "--owner", "infra_lead",
        "--run-id", "run_replay", "--orca-task-id", "task_replay",
        "--domain", "infra", "--next-action", "reconcile failed attempt",
    ]) == 0
    active = next((root / "active").iterdir())
    real_atomic_json = queue._atomic_json
    injected = False

    def fail_first_meta_write(path: Path, value: object) -> None:
        nonlocal injected
        if path == active / "META.json" and not injected:
            injected = True
            raise OSError("injected META write failure")
        real_atomic_json(path, value)

    monkeypatch.setattr(queue, "_atomic_json", fail_first_meta_write)
    reconcile = [
        "--root", str(root), "orca-reconcile", task_id, "--owner", "infra_lead",
        "--dispatch-id", "ctx_failed", "--attempt", "1",
        "--observed-status", "failed", "--error", "agent_prompt_stalled",
        "--next-action", "dispatch a bounded retry",
    ]
    assert queue.main(reconcile) == 2
    projection = json.loads(
        (active / queue.ORCA_STATE_NAME).read_text(encoding="utf-8")
    )
    assert projection["phase"] == "RECOVERY_REQUIRED"

    monkeypatch.setattr(queue, "_atomic_json", real_atomic_json)
    assert queue.main(reconcile) == 0
    reconciled_at = projection["last_reconciled_at"]
    meta = json.loads((active / "META.json").read_text(encoding="utf-8"))
    handoff = queue._read_handoff(active)
    assert meta["updated_at"] == meta["heartbeat"] == reconciled_at
    assert handoff["updated_at"] == reconciled_at
    assert handoff["phase"] == "orca_recovery_required"
    assert f"updated_at: {reconciled_at}\n" in (root / "BOARD.md").read_text(
        encoding="utf-8"
    )
    assert queue.doctor(root) == []

    repaired = {
        "task": _task_bytes(active),
        "board": (root / "BOARD.md").read_bytes(),
    }
    assert queue.main(reconcile) == 0
    assert repaired == {
        "task": _task_bytes(active),
        "board": (root / "BOARD.md").read_bytes(),
    }


def test_claim_has_one_winner_and_never_overwrites_active_task(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root)
    _triage(root, task_id)
    barrier = Barrier(3)
    results: list[int] = []

    def claim(owner: str) -> None:
        barrier.wait()
        results.append(queue.main([
            "--root", str(root), "claim", task_id, "--owner", owner,
            "--next", "bounded work",
        ]))

    workers = [Thread(target=claim, args=(owner,)) for owner in ("terra", "luna")]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()
    assert sorted(results) == [0, 2]
    active = list((root / "active").iterdir())
    assert len(active) == 1
    assert json.loads((active[0] / "META.json").read_text(encoding="utf-8"))["owner"] in {"terra", "luna"}


def test_shared_owner_client_requires_exact_unstored_claim_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "queue"
    assert _RAW_QUEUE_MAIN(["--root", str(root), "init"]) == 0
    task_id = _discover(root, "queue:claim-token:shared-owner")
    _triage(root, task_id)
    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "claim", task_id, "--owner", "shared-owner",
        "--next", "bounded mutation",
    ]) == 0
    output = capsys.readouterr().out
    token = output.split("claim_token=", 1)[1].splitlines()[0]
    assert f"claim_token={token}" in output
    active = next((root / "active").iterdir())
    meta_text = (active / "META.json").read_text(encoding="utf-8")
    assert token not in meta_text
    assert hashlib.sha256(token.encode()).hexdigest() in meta_text

    base = [
        "--root", str(root), "checkpoint", task_id,
        "--owner", "shared-owner", "--next", "continue",
    ]
    assert _RAW_QUEUE_MAIN(base) == 2
    assert _RAW_QUEUE_MAIN([*base, "--claim-token", "2" * 64]) == 2
    assert _RAW_QUEUE_MAIN([*base, "--claim-token", token]) == 0


def test_coordinator_recovers_only_an_exact_expired_active_capability(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "queue"
    assert _RAW_QUEUE_MAIN(["--root", str(root), "init"]) == 0
    task_id = _discover(root, "queue:lost-active-capability")
    _triage(root, task_id, review=True, reviewer="verifier")
    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "claim", task_id, "--owner", "lost-client",
        "--next", "implement bounded work",
    ]) == 0
    claim_output = capsys.readouterr().out
    old_token = claim_output.split("claim_token=", 1)[1].splitlines()[0]
    active = next((root / "active").iterdir())
    before_meta = json.loads((active / "META.json").read_text(encoding="utf-8"))

    live_base = [
        "--root", str(root), "recover-expired-active", task_id,
        "--coordinator", "root",
        "--expected-owner", "lost-client",
        "--expected-updated-at", str(before_meta["updated_at"]),
        "--expected-lease-until", str(before_meta["lease_until"]),
        "--expected-claim-token-sha256", str(before_meta["claim_token_sha256"]),
        "--decision-basis", "The raw client capability is unavailable.",
        "--next", "revalidate and submit the completed scope",
    ]
    receipt_before = queue._task_receipt_digest(active)
    assert _RAW_QUEUE_MAIN(live_base) == 2
    assert queue._task_receipt_digest(active) == receipt_before

    expired_meta = _expire_active_receipt(active)
    base = list(live_base)
    updated_index = base.index("--expected-updated-at") + 1
    base[updated_index] = str(expired_meta["updated_at"])
    lease_index = base.index("--expected-lease-until") + 1
    base[lease_index] = str(expired_meta["lease_until"])
    expired_receipt = queue._task_receipt_digest(active)
    wrong_owner = list(base)
    owner_index = wrong_owner.index("--expected-owner") + 1
    wrong_owner[owner_index] = "other-client"
    assert _RAW_QUEUE_MAIN(wrong_owner) == 2
    assert queue._task_receipt_digest(active) == expired_receipt
    wrong_lease = list(base)
    wrong_lease[lease_index] = "1999-01-01T00:00:00+00:00"
    assert _RAW_QUEUE_MAIN(wrong_lease) == 2
    assert queue._task_receipt_digest(active) == expired_receipt
    wrong_digest = list(base)
    digest_index = wrong_digest.index("--expected-claim-token-sha256") + 1
    wrong_digest[digest_index] = "0" * 64
    assert _RAW_QUEUE_MAIN(wrong_digest) == 2
    assert queue._task_receipt_digest(active) == expired_receipt

    malformed_meta = dict(expired_meta)
    malformed_meta["lease_until"] = "not-an-aware-time"
    (active / "META.json").write_text(
        json.dumps(malformed_meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    malformed_base = list(base)
    malformed_base[lease_index] = "not-an-aware-time"
    malformed_receipt = queue._task_receipt_digest(active)
    assert _RAW_QUEUE_MAIN(malformed_base) == 2
    assert queue._task_receipt_digest(active) == malformed_receipt
    queue._atomic_json(active / "META.json", expired_meta)

    assert _RAW_QUEUE_MAIN(base) == 0
    ready = next((root / "inbox/ready").iterdir())
    recovered_meta = json.loads((ready / "META.json").read_text(encoding="utf-8"))
    assert recovered_meta["owner"] is None
    assert recovered_meta["assigned_agent"] is None
    assert recovered_meta["lease_until"] is None
    assert recovered_meta["heartbeat"] is None
    assert "claim_token_sha256" not in recovered_meta
    handoff = (ready / "HANDOFF.md").read_text(encoding="utf-8")
    assert "phase: coordinator_recovery" in handoff
    assert "The raw client capability is unavailable." in handoff
    assert queue.doctor(root) == []

    assert _RAW_QUEUE_MAIN([
        "--root", str(root), "claim", task_id, "--owner", "fresh-client",
        "--next", "revalidate recovered work",
    ]) == 0
    fresh_output = capsys.readouterr().out
    fresh_token = fresh_output.rsplit("claim_token=", 1)[1].splitlines()[0]
    reclaimed = next((root / "active").iterdir())
    reclaimed_text = (reclaimed / "META.json").read_text(encoding="utf-8")
    assert fresh_token != old_token and fresh_token not in reclaimed_text
    assert hashlib.sha256(fresh_token.encode()).hexdigest() in reclaimed_text


def test_expired_active_recovery_has_one_concurrent_winner(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:expired-recovery-race")
    _triage(root, task_id)
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "lost-client",
        "--next", "simulate expiry",
    ])
    active = next((root / "active").iterdir())
    meta = _expire_active_receipt(active)
    args = [
        "--root", str(root), "recover-expired-active", task_id,
        "--coordinator", "root", "--expected-owner", "lost-client",
        "--expected-updated-at", str(meta["updated_at"]),
        "--expected-lease-until", str(meta["lease_until"]),
        "--expected-claim-token-sha256", str(meta["claim_token_sha256"]),
        "--decision-basis", "The raw capability was lost after lease expiry.",
        "--next", "reclaim once",
    ]
    barrier = Barrier(3)
    results: list[int] = []

    def recover() -> None:
        barrier.wait()
        results.append(_RAW_QUEUE_MAIN(args))

    workers = [Thread(target=recover) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()
    assert sorted(results) == [0, 2]
    assert list((root / "active").iterdir()) == []
    assert len(list((root / "inbox/ready").iterdir())) == 1
    assert queue.doctor(root) == []


@pytest.mark.parametrize(
    "malformation",
    (
        "missing_assigned_agent", "null_assigned_agent", "different_assigned_agent",
        "null_assigned_role", "null_heartbeat", "handoff_generation",
        "handoff_noncanonical", "task_incomplete", "task_empty_sections",
        "task_extra_title", "task_presection_content",
        "task_indented_extra_title", "task_indented_duplicate_section",
    ),
)
def test_expired_active_recovery_rejects_malformed_receipt_byte_identically(
    tmp_path: Path, malformation: str,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, f"queue:expired-recovery-malformed:{malformation}")
    _triage(root, task_id)
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "lost-client",
        "--next", "simulate an expired capability",
    ])
    active = next((root / "active").iterdir())
    meta = _expire_active_receipt(active)

    if malformation == "missing_assigned_agent":
        meta.pop("assigned_agent")
        queue._atomic_json(active / "META.json", meta)
    elif malformation == "null_assigned_agent":
        meta["assigned_agent"] = None
        queue._atomic_json(active / "META.json", meta)
    elif malformation == "different_assigned_agent":
        meta["assigned_agent"] = "different-client"
        queue._atomic_json(active / "META.json", meta)
    elif malformation == "null_assigned_role":
        meta["assigned_role"] = None
        queue._atomic_json(active / "META.json", meta)
    elif malformation == "null_heartbeat":
        meta["heartbeat"] = None
        queue._atomic_json(active / "META.json", meta)
    elif malformation == "handoff_generation":
        handoff = queue._read_handoff(active)
        handoff["updated_at"] = "1999-12-31T23:58:30+00:00"
        queue._atomic_text(active / "HANDOFF.md", queue._handoff_text(handoff))
    elif malformation == "handoff_noncanonical":
        (active / "HANDOFF.md").write_text(
            (active / "HANDOFF.md").read_text(encoding="utf-8") + "unexpected: value\n",
            encoding="utf-8",
        )
    elif malformation == "task_incomplete":
        (active / "TASK.md").write_text("# Incomplete\n", encoding="utf-8")
    elif malformation == "task_empty_sections":
        title = str(meta["title"])
        (active / "TASK.md").write_text(
            f"# {title}\n\n## Problem\n\n## Evidence\n\n## Scope\n\n"
            "## Done When\n\n## Verify\n",
            encoding="utf-8",
        )
    else:
        task_text = (active / "TASK.md").read_text(encoding="utf-8")
        marker = "\n\n## Problem"
        injected = {
            "task_extra_title": "\n\n# Extra title",
            "task_presection_content": "\n\nunowned text",
            "task_indented_extra_title": "\n\n # Extra title",
            "task_indented_duplicate_section": "\n\n ## Evidence",
        }[malformation]
        (active / "TASK.md").write_text(
            task_text.replace(marker, injected + marker, 1), encoding="utf-8",
        )

    expected_meta = json.loads((active / "META.json").read_text(encoding="utf-8"))
    before = _task_bytes(active)
    args = [
        "--root", str(root), "recover-expired-active", task_id,
        "--coordinator", "root", "--expected-owner", "lost-client",
        "--expected-updated-at", str(expected_meta.get("updated_at")),
        "--expected-lease-until", str(expected_meta.get("lease_until")),
        "--expected-claim-token-sha256", str(expected_meta.get("claim_token_sha256")),
        "--decision-basis", "The exact expired capability cannot be recovered by its client.",
        "--next", "repair the malformed receipt before recovery",
    ]
    assert _RAW_QUEUE_MAIN(args) == 2
    assert active.is_dir()
    assert _task_bytes(active) == before


@pytest.mark.parametrize("spaces", (1, 2, 3))
@pytest.mark.parametrize("heading", ("# Extra title", "## Evidence"))
def test_expired_active_task_contract_rejects_commonmark_indented_h1_h2(
    spaces: int, heading: str,
) -> None:
    title = "Canonical task"
    task_text = (
        f"# {title}\n\n{' ' * spaces}{heading}\n\n"
        "## Problem\nproblem\n\n## Evidence\nevidence\n\n"
        "## Scope\nallow:\n- src/a.py\ndeny:\n- provider calls\n\n"
        "## Done When\ndone\n\n## Verify\nverify\n"
    )
    with pytest.raises(queue.QueueError):
        queue._validate_task_contract_text(task_text, expected_title=title)


def test_claim_rejects_caller_supplied_token_and_preserves_exact_ready_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:claim-token:invalid-is-atomic")
    _triage(root, task_id)
    ready = next((root / "inbox" / "ready").iterdir())
    before = queue._task_receipt_digest(ready)

    with pytest.raises(SystemExit, match="2"):
        _RAW_QUEUE_MAIN([
            "--root", str(root), "claim", task_id, "--owner", "shared-owner",
            "--claim-token", "short", "--next", "must not move",
        ])

    ready_after = next((root / "inbox" / "ready").iterdir())
    assert queue._task_receipt_digest(ready_after) == before
    assert list((root / "active").iterdir()) == []


def test_legacy_active_claim_adoption_has_one_token_winner(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:claim-token:legacy-adoption")
    _triage(root, task_id)
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "legacy-owner",
        "--next", "simulate legacy",
    ])
    token = _TEST_CLAIM_TOKENS[(str(root.resolve()), task_id)]
    active = next((root / "active").iterdir())
    meta = json.loads((active / "META.json").read_text(encoding="utf-8"))
    meta.pop("claim_token_sha256")
    (active / "META.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    base = [
        "--root", str(root), "checkpoint", task_id,
        "--owner", "legacy-owner", "--next", "continue",
    ]
    assert _RAW_QUEUE_MAIN([*base, "--claim-token", token]) == 2
    assert _RAW_QUEUE_MAIN([
        *base, "--claim-token", token, "--adopt-legacy-claim",
        "--lease-minutes", "0",
    ]) == 2
    unchanged = json.loads((active / "META.json").read_text(encoding="utf-8"))
    assert "claim_token_sha256" not in unchanged
    assert _RAW_QUEUE_MAIN([
        *base, "--claim-token", token, "--adopt-legacy-claim",
    ]) == 0
    assert _RAW_QUEUE_MAIN([
        *base, "--claim-token", "4" * 64, "--adopt-legacy-claim",
    ]) == 2


def test_shared_lane_tasks_cannot_race_past_exclusive_writer_lane(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    first_id = _discover(root, "component:first:defect")
    _triage(root, first_id)
    second_id = _discover(root, "component:second:defect")
    _triage(root, second_id)
    barrier = Barrier(3)
    results: list[int] = []

    def claim(task_id: str, owner: str) -> None:
        barrier.wait()
        results.append(queue.main([
            "--root", str(root), "claim", task_id, "--owner", owner,
            "--next", "bounded work",
        ]))

    workers = [
        Thread(target=claim, args=(first_id, "terra")),
        Thread(target=claim, args=(second_id, "luna")),
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()
    assert sorted(results) == [0, 2]
    assert len(list((root / "active").iterdir())) == 1
    assert len(list((root / "inbox/ready").iterdir())) == 1


def test_gui_data_and_backtest_writers_can_be_active_together(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    tasks = []
    for lane, scope in (
        ("gui", "src/stock_data/gui/window.py"),
        ("data", "src/stock_data/storage/table.py"),
        ("backtest", "src/stock_data/backtest/engine.py"),
    ):
        task_id = _discover(root, f"queue:parallel:{lane}")
        _triage(root, task_id, write_scope=scope)
        tasks.append((task_id, lane))

    for task_id, lane in tasks:
        assert queue.main([
            "--root", str(root), "claim", task_id, "--owner", lane,
            "--next", f"implement {lane}",
        ]) == 0

    active = [
        json.loads((path / "META.json").read_text(encoding="utf-8"))
        for path in (root / "active").iterdir()
    ]
    assert {queue._writer_lane(meta, meta["write_scope"]) for meta in active} == {
        "gui", "data", "backtest",
    }
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_concurrent_disjoint_data_claims_wait_for_global_mutation_lock(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    tasks = []
    for owner, scope in (
        ("data-prices", "src/stock_data/storage/prices.py"),
        ("data-fundamentals", "src/stock_data/storage/fundamentals.py"),
    ):
        task_id = _discover(root, f"queue:concurrent:{owner}")
        _triage(root, task_id, write_scope=scope)
        tasks.append((task_id, owner))
    barrier = Barrier(3)
    results: list[int] = []

    def claim(task_id: str, owner: str) -> None:
        barrier.wait()
        results.append(queue.main([
            "--root", str(root), "claim", task_id, "--owner", owner,
            "--next", "bounded work",
        ]))

    workers = [Thread(target=claim, args=item) for item in tasks]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()

    assert sorted(results) == [0, 0]
    third = _discover(root, "queue:concurrent:data-ratios")
    _triage(
        root, third, write_scope="src/stock_data/storage/ratios.py",
    )
    assert queue.main([
        "--root", str(root), "claim", third, "--owner", "data-ratios",
        "--next", "bounded work",
    ]) == 0

    fourth = _discover(root, "queue:concurrent:data-options")
    _triage(
        root, fourth, write_scope="src/stock_data/storage/options.py",
    )
    assert queue.main([
        "--root", str(root), "claim", fourth, "--owner", "data-options",
        "--next", "must wait for writer capacity",
    ]) == 2

    active = [
        json.loads((path / "META.json").read_text(encoding="utf-8"))
        for path in (root / "active").iterdir()
    ]
    assert len(active) == queue.WRITER_LIMIT == 3
    assert {
        queue._writer_lane(meta, meta["write_scope"]) for meta in active
    } == {"data"}
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_same_data_lane_scope_and_resource_conflicts_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    first = _discover(root, "queue:conflict:first")
    _triage(
        root, first, write_scope="src/stock_data/storage/prices.py",
        resource_locks=("live-data-root",),
    )
    assert queue.main([
        "--root", str(root), "claim", first, "--owner", "price-owner",
        "--next", "implement prices",
    ]) == 0

    same_lane = _discover(root, "queue:conflict:same-lane")
    _triage(
        root, same_lane,
        write_scope="src/stock_data/storage/fundamentals.py",
    )
    assert queue.main([
        "--root", str(root), "claim", same_lane,
        "--owner", "fundamental-owner", "--next", "implement fundamentals",
    ]) == 0

    same_resource = _discover(root, "queue:conflict:resource")
    _triage(
        root, same_resource, write_scope="src/stock_data/storage/options.py",
        resource_locks=("live-data-root",),
    )
    assert queue.main([
        "--root", str(root), "claim", same_resource, "--owner", "option-owner",
        "--next", "must wait",
    ]) == 2

    overlapping = _discover(root, "queue:conflict:scope")
    _triage(
        root, overlapping, write_scope="src/stock_data/storage/prices.py",
    )
    assert queue.main([
        "--root", str(root), "claim", overlapping, "--owner", "overlap-owner",
        "--next", "must wait",
    ]) == 2

    mismatched = _discover(root, "queue:conflict:mismatched-lane")
    with pytest.raises(AssertionError):
        _triage(
            root, mismatched, write_scope="src/stock_data/gui/other.py",
            writer_lane="data",
        )
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_legacy_lane_inference_is_backward_compatible_and_mixed_scope_is_shared(
    tmp_path: Path,
) -> None:
    assert queue._writer_lane({}, [
        "src/stock_data/storage/table.py",
        "scripts/refresh_table.py",
        "docs/project/PROJECT_STATUS.md",
    ]) == "shared"
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    gui_id = _discover(root, "queue:legacy:gui")
    _triage(root, gui_id, write_scope="src/stock_data/gui/window.py")
    gui = next((root / "inbox/ready").iterdir())
    gui_meta_path = gui / "META.json"
    gui_meta = json.loads(gui_meta_path.read_text(encoding="utf-8"))
    gui_meta.pop("writer_lane", None)
    gui_meta.pop("resource_locks", None)
    gui_meta_path.write_text(json.dumps(gui_meta), encoding="utf-8")
    assert queue.main([
        "--root", str(root), "claim", gui_id, "--owner", "gui-owner",
        "--next", "legacy-compatible claim",
    ]) == 0
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_linked_worktree_manager_is_rejected_when_canonical_manager_exists(
    tmp_path: Path, monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    root = repository / "artifacts" / "request_queue"
    canonical = repository / "scripts" / "request_queue.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("# canonical\n", encoding="utf-8")

    queue._ensure_canonical_manager(root, canonical)
    with pytest.raises(queue.QueueError, match="linked-worktree queue manager"):
        queue._ensure_canonical_manager(
            root, tmp_path / "linked" / "scripts" / "request_queue.py",
        )

    linked = tmp_path / "linked"
    linked_manager = linked / "scripts" / "request_queue.py"
    linked_manager.parent.mkdir(parents=True)
    linked_manager.write_text("# stale\n", encoding="utf-8")
    monkeypatch.setattr(
        queue, "_git", lambda *_args: (
            f"worktree {repository}\nHEAD current\n\n"
            f"worktree {linked}\nHEAD stale\n"
        ),
    )
    assert queue._linked_worktree_managers(root) == [linked_manager.resolve()]

    def fail_git(*_args):
        raise subprocess.CalledProcessError(128, "git worktree list")

    monkeypatch.setattr(queue, "_git", fail_git)
    with pytest.raises(queue.QueueError, match="cannot verify linked worktree"):
        queue._linked_worktree_managers(root)
    assert any(
        "cannot verify linked worktree" in issue for issue in queue.doctor(root)
    )


def test_concurrent_discovery_deduplicates_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    barrier = Barrier(3)
    results: list[int] = []

    def discover(agent: str) -> None:
        barrier.wait()
        results.append(queue.main([
            "--root", str(root), "discover", "--title", f"Defect from {agent}",
            "--discovered-by", agent, "--source-task", "RQ-parent",
            "--priority-hint", "P1", "--fingerprint", "same:fingerprint",
            "--symptom", "A bounded behavior differs.", "--evidence", "offline test",
            "--impact", "false status", "--suspected-scope", "src/component.py",
            "--reproduce", "pytest exact-node",
        ]))

    workers = [Thread(target=discover, args=(agent,)) for agent in ("terra", "luna")]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()
    assert sorted(results) == [0, 2]
    assert len(list((root / "inbox/new").iterdir())) == 1


def test_goal_discovery_pauses_at_live_backlog_but_user_intake_continues(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    for index in range(6):
        task_id = _discover(root, f"backlog:{index}")
        _triage(root, task_id, write_scope=f"src/component_{index}.py")

    goal_args = [
        "--root", str(root), "discover", "--title", "Unsolicited goal idea",
        "--discovered-by", "goal_inbox_review", "--source-task", "PROJECT_GOAL",
        "--priority-hint", "P2", "--fingerprint", "goal:paused:idea",
        "--symptom", "A possible future improvement exists.",
        "--evidence", "Goal comparison only", "--impact", "Optional expansion",
        "--suspected-scope", "src/future.py", "--reproduce", "Planning pass",
    ]
    assert queue.main(goal_args) == 2

    user_args = list(goal_args)
    user_args[user_args.index("PROJECT_GOAL")] = "telegram-user-request"
    user_args[user_args.index("goal_inbox_review")] = "telegram-intake-agent"
    user_args[user_args.index("goal:paused:idea")] = "user:accepted:request"
    assert queue.main(user_args) == 0


def test_review_required_task_rejects_self_review_assignment(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "component:self-review")
    _triage(root, task_id, review=True, reviewer="terra")
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "would self review",
    ]) == 2
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "luna",
        "--next", "independently reviewed work",
    ]) == 0


def test_self_review_is_rejected_at_checkpoint_submit_review_pass_and_doctor(
    tmp_path: Path,
) -> None:
    checkpoint_root = tmp_path / "checkpoint-queue"
    queue.main(["--root", str(checkpoint_root), "init"])
    checkpoint_id = _discover(checkpoint_root, "component:checkpoint-self-review")
    _triage(checkpoint_root, checkpoint_id)
    queue.main([
        "--root", str(checkpoint_root), "claim", checkpoint_id,
        "--owner", "terra", "--next", "implement",
    ])
    assert queue.main([
        "--root", str(checkpoint_root), "checkpoint", checkpoint_id,
        "--owner", "terra", "--require-review", "--reviewer", "terra",
    ]) == 2

    submit_root = tmp_path / "submit-queue"
    queue.main(["--root", str(submit_root), "init"])
    submit_id = _discover(submit_root, "component:submit-self-review")
    _triage(submit_root, submit_id)
    queue.main([
        "--root", str(submit_root), "claim", submit_id,
        "--owner", "terra", "--next", "implement",
    ])
    assert queue.main([
        "--root", str(submit_root), "submit", submit_id, "--owner", "terra",
        "--review", "--reviewer", "terra", "--result", "implemented",
        "--changed", "src/component.py", "--verified", "tests passed",
    ]) == 2

    review_root = tmp_path / "review-queue"
    queue.main(["--root", str(review_root), "init"])
    review_id = _discover(review_root, "component:review-pass-self-review")
    _triage(review_root, review_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(review_root), "claim", review_id,
        "--owner", "terra", "--next", "implement",
    ])
    queue.main([
        "--root", str(review_root), "submit", review_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "tests passed",
    ])
    review_task = next((review_root / "review").iterdir())
    meta_path = review_task / "META.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["reviewer"] = "terra"
    meta["review_required"] = False
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert queue.main([
        "--root", str(review_root), "review-pass", review_id,
        "--reviewer", "terra", "--review-generation",
        _review_generation(review_root), "--decision-basis", "self review",
    ]) == 2
    issues = queue.doctor(review_root)
    assert any("review state must require review" in issue for issue in issues)
    assert any("reviewer matches implementing agent" in issue for issue in issues)


def test_review_scope_reserves_overlap_but_not_disjoint_lane_until_pass(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    reviewed_id = _discover(root, "review:scope:reserved-pass")
    _triage(
        root, reviewed_id, review=True,
        write_scope="src/stock_data/gui/shared.py", writer_lane="gui",
    )
    queue.main([
        "--root", str(root), "claim", reviewed_id, "--owner", "gui-author",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(root), "submit", reviewed_id, "--owner", "gui-author",
        "--result", "implemented", "--changed", "exact generation",
        "--verified", "tests passed",
    ])

    overlap_id = _discover(root, "review:scope:overlap")
    _triage(
        root, overlap_id, write_scope="src/stock_data/gui/shared.py",
        writer_lane="gui",
    )
    assert queue.main([
        "--root", str(root), "claim", overlap_id, "--owner", "later-gui",
        "--next", "must wait",
    ]) == 2

    disjoint_id = _discover(root, "review:scope:disjoint")
    _triage(
        root, disjoint_id,
        write_scope="src/stock_data/orchestration/disjoint.py",
        writer_lane="data",
    )
    assert queue.main([
        "--root", str(root), "claim", disjoint_id, "--owner", "data-author",
        "--next", "disjoint work",
    ]) == 0
    assert queue.main([
        "--root", str(root), "submit", disjoint_id, "--owner", "data-author",
        "--result", "done", "--changed", "disjoint generation",
        "--verified", "tests passed",
    ]) == 0

    assert queue.main([
        "--root", str(root), "review-pass", reviewed_id, "--reviewer", "sol",
        "--review-generation", _review_generation(root),
        "--decision-basis", "exact generation accepted",
    ]) == 0
    assert queue.main([
        "--root", str(root), "claim", overlap_id, "--owner", "later-gui",
        "--next", "reservation released",
    ]) == 0
    assert queue.doctor(root) == []


def test_review_fail_releases_exact_scope_reservation(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    reviewed_id = _discover(root, "review:scope:reserved-fail")
    _triage(
        root, reviewed_id, review=True,
        write_scope="src/stock_data/gui/shared.py", writer_lane="gui",
    )
    queue.main([
        "--root", str(root), "claim", reviewed_id, "--owner", "gui-author",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(root), "submit", reviewed_id, "--owner", "gui-author",
        "--result", "implemented", "--changed", "exact generation",
        "--verified", "tests passed",
    ])
    overlap_id = _discover(root, "review:scope:overlap-after-fail")
    _triage(
        root, overlap_id, write_scope="src/stock_data/gui/shared.py",
        writer_lane="gui",
    )
    assert queue.main([
        "--root", str(root), "claim", overlap_id, "--owner", "later-gui",
        "--next", "must wait",
    ]) == 2
    assert queue.main([
        "--root", str(root), "review-fail", reviewed_id, "--reviewer", "sol",
        "--review-generation", _review_generation(root),
        "--decision-basis", "rework required", "--next", "repair",
    ]) == 0
    assert queue.main([
        "--root", str(root), "claim", overlap_id, "--owner", "later-gui",
        "--next", "reservation released",
    ]) == 0
    assert queue.doctor(root) == []


def test_review_rework_and_external_block_are_distinct(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    review_id = _discover(root, "ui:visual:review")
    _triage(root, review_id, review=True)
    queue.main(["--root", str(root), "claim", review_id, "--owner", "terra", "--next", "implement"])
    assert queue.main([
        "--root", str(root), "submit", review_id, "--owner", "terra",
        "--result", "implemented",
        "--changed", "src/component.py", "--verified", "automated tests passed",
    ]) == 0
    review = next((root / "review").iterdir())
    review_meta = json.loads((review / "META.json").read_text(encoding="utf-8"))
    assert (review / "REVIEW.md").is_file()
    assert not (review / "RESULT.md").exists()
    assert review_meta["completed_at"] is None
    assert review_meta["owner"] is None
    assert review_meta["heartbeat"] is None and review_meta["lease_until"] is None
    assert queue.main([
        "--root", str(root), "review-fail", review_id, "--reviewer", "sol",
        "--review-generation", _review_generation(root),
        "--decision-basis", "bounded defect remains",
        "--next", "repair review finding",
    ]) == 0
    assert next((root / "inbox/ready").iterdir()).is_dir()
    queue.main(["--root", str(root), "claim", review_id, "--owner", "terra", "--next", "repair"])
    assert queue.main([
        "--root", str(root), "submit", review_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "automated tests passed",
    ]) == 0
    assert queue.main([
        "--root", str(root), "review-pass", review_id, "--reviewer", "sol",
        "--review-generation", _review_generation(root),
        "--decision-basis", "exact behavior and regression pass",
    ]) == 0
    done = next((root / "done").iterdir())
    done_meta = json.loads((done / "META.json").read_text(encoding="utf-8"))
    assert (done / "RESULT.md").is_file()
    assert not (done / "REVIEW.md").exists()
    assert done_meta["completed_at"]

    block_id = _discover(root, "system:scheduler:mutation")
    _triage(root, block_id)
    queue.main(["--root", str(root), "claim", block_id, "--owner", "terra", "--next", "local work"])
    assert queue.main([
        "--root", str(root), "block", block_id, "--owner", "luna",
        "--reason", "Task Scheduler mutation",
        "--required-action", "user runs approved installer",
        "--resume-condition", "registered XML readback exists",
    ]) == 2
    assert queue.main([
        "--root", str(root), "block", block_id, "--owner", "terra",
        "--reason", "Task Scheduler mutation",
        "--required-action", "user runs approved installer",
        "--resume-condition", "registered XML readback exists",
    ]) == 0
    blocked = next((root / "blocked").iterdir())
    assert "resume_condition:" in (blocked / "BLOCKED.md").read_text(encoding="utf-8")


def test_stale_review_generation_cannot_complete_a_reworked_submission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "component:stale-review-generation")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement generation one",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "generation one tests passed",
    ])
    generation_one = _review_generation(root)
    queue.main([
        "--root", str(root), "review-fail", task_id, "--reviewer", "sol",
        "--review-generation", generation_one,
        "--decision-basis", "generation one still fails",
        "--next", "repair and resubmit",
    ])
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement generation two",
    ])
    queue.main([
        "--root", str(root), "checkpoint", task_id, "--owner", "terra",
        "--phase", "rework", "--summary", "generation two repair",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "generation two tests passed",
    ])
    generation_two = _review_generation(root)
    assert generation_two != generation_one
    review = next((root / "review").iterdir())
    before = {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    }
    board_before = (root / "BOARD.md").read_bytes()

    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation_one,
        "--decision-basis", "stale generation one verdict",
    ]) == 2
    assert review.is_dir() and not (review / "RESULT.md").exists()
    assert {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    } == before
    assert (root / "BOARD.md").read_bytes() == board_before
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation_two,
        "--decision-basis", "generation two exact contract passes",
    ]) == 0
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_compact_done_preserves_identity_fingerprint_and_later_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository/artifacts/request_queue"
    queue.main(["--root", str(root), "init"])
    completed = _complete(root, "queue:completed:indexed")
    _commit_done(root)

    assert queue.main(["--root", str(root), "compact-done", completed]) == 0
    assert not any((root / "done").iterdir())
    entries = queue._load_completed_index(root)
    assert entries[0]["id"] == completed
    assert entries[0]["fingerprint"] == "queue:completed:indexed"
    assert len(entries[0]["receipt_sha256"]) == 64
    original_new_id = queue._new_id
    monkeypatch.setattr(queue, "_new_id", lambda _now=None: completed)
    assert queue.main([
        "--root", str(root), "discover", "--title", "id collision",
        "--discovered-by", "test", "--source-task", "parent",
        "--fingerprint", "queue:new:fingerprint", "--symptom", "same id",
        "--evidence", "injected", "--impact", "collision",
        "--suspected-scope", "src/component.py", "--reproduce", "offline",
    ]) == 2
    monkeypatch.setattr(queue, "_new_id", original_new_id)
    assert queue.main([
        "--root", str(root), "discover", "--title", "duplicate",
        "--discovered-by", "test", "--source-task", "parent",
        "--fingerprint", "queue:completed:indexed", "--symptom", "same",
        "--evidence", "same", "--impact", "same",
        "--suspected-scope", "src/component.py", "--reproduce", "offline",
    ]) == 2

    dependent = _discover(root, "queue:dependent:after-compaction")
    assert queue.main([
        "--root", str(root), "triage", dependent, "--priority", "P1",
        "--risk", "medium", "--write-scope", "src/dependent.py",
        "--depends-on", completed, "--problem", "dependency",
        "--evidence", "indexed completion", "--allow", "src/dependent.py",
        "--deny", "external", "--done-when", "claimed", "--verify", "offline",
    ]) == 0
    assert queue.main([
        "--root", str(root), "claim", dependent, "--owner", "worker",
        "--next", "verify indexed dependency",
    ]) == 0
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_compact_done_rejects_live_reference_and_untracked_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository/artifacts/request_queue"
    queue.main(["--root", str(root), "init"])
    completed = _complete(root, "queue:completed:referenced")
    dependent = _discover(root, "queue:dependent:live-reference")
    assert queue.main([
        "--root", str(root), "triage", dependent, "--priority", "P1",
        "--risk", "medium", "--write-scope", "src/dependent.py",
        "--depends-on", completed, "--problem", "dependency",
        "--evidence", "done receipt", "--allow", "src/dependent.py",
        "--deny", "external", "--done-when", "ready", "--verify", "offline",
    ]) == 0
    _mock_clean_tracked_done(monkeypatch, root)
    assert queue.main(["--root", str(root), "compact-done", completed]) == 2
    assert any((root / "done").iterdir())
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()



def test_compact_done_rejects_untracked_receipt_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository/artifacts/request_queue"
    queue.main(["--root", str(root), "init"])
    completed = _complete(root, "queue:completed:untracked")
    before = next((root / "done").iterdir())
    monkeypatch.setattr(queue, "_git", lambda *_args: "")

    assert queue.main(["--root", str(root), "compact-done", completed]) == 2
    assert before.is_dir()
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()


def test_compact_done_rolls_back_index_when_exact_deletion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository/artifacts/request_queue"
    queue.main(["--root", str(root), "init"])
    completed = _complete(root, "queue:completed:delete-failure")
    _commit_done(root)
    monkeypatch.setattr(queue, "_delete_done_directory", lambda _path: (_ for _ in ()).throw(OSError("denied")))

    assert queue.main(["--root", str(root), "compact-done", completed]) == 2
    assert any((root / "done").iterdir())
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_compact_done_preserves_receipt_when_atomic_index_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository/artifacts/request_queue"
    queue.main(["--root", str(root), "init"])
    completed = _complete(root, "queue:completed:index-write-failure")
    _mock_clean_tracked_done(monkeypatch, root)
    done = next((root / "done").iterdir())
    before = {path.name: path.read_bytes() for path in done.iterdir() if path.is_file()}
    monkeypatch.setattr(queue, "_write_completed_index", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))

    assert queue.main(["--root", str(root), "compact-done", completed]) == 2
    assert {path.name: path.read_bytes() for path in done.iterdir() if path.is_file()} == before
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_compact_done_rejects_malformed_index_without_touching_done(tmp_path: Path) -> None:
    root = tmp_path / "repository/artifacts/request_queue"
    queue.main(["--root", str(root), "init"])
    completed = _complete(root, "queue:completed:malformed-index")
    index = root / queue.COMPLETED_INDEX_NAME
    index.write_text('{"schema_version":1,"entries":[],"entries_sha256":"bad"}', encoding="utf-8")
    done_before = next((root / "done").iterdir())

    assert queue.main(["--root", str(root), "compact-done", completed]) == 2
    assert done_before.is_dir()
    assert index.read_text(encoding="utf-8").endswith('"bad"}')

def test_review_handoff_mutation_requires_recovery_and_fresh_submission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:review-handoff-binding")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement generation one",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "generation one passed",
    ])
    review = next((root / "review").iterdir())
    receipt = queue._read_fields(
        review / "REVIEW.md", ("review_generation", "handoff_sha256"),
    )
    generation_one = receipt["review_generation"]
    assert receipt["handoff_sha256"] == queue._handoff_snapshot_digest(review)

    handoff = queue._read_handoff(review)
    handoff["summary"] = "changed after the reviewer read this snapshot"
    queue._atomic_text(review / "HANDOFF.md", queue._handoff_text(handoff))
    before = {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    }
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation_one,
        "--decision-basis", "must reject changed handoff",
    ]) == 2
    assert queue.main([
        "--root", str(root), "review-fail", task_id, "--reviewer", "sol",
        "--review-generation", generation_one,
        "--decision-basis", "must reject changed handoff",
        "--next", "must not transition",
    ]) == 2
    assert {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    } == before
    assert any("review HANDOFF digest mismatch" in issue for issue in queue.doctor(root))

    assert queue.main([
        "--root", str(root), "review-recover", task_id,
        "--reviewer", "sol", "--review-generation", generation_one,
        "--decision-basis", "submitted HANDOFF snapshot is no longer verifiable",
        "--next", "reclaim and submit a fresh generation",
    ]) == 0
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "resubmit current files",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "generation two passed",
    ])
    generation_two = _review_generation(root)
    assert generation_two != generation_one
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation_two,
        "--decision-basis", "fresh digest-bound snapshot passes",
    ]) == 0


def test_legacy_review_without_handoff_digest_requires_explicit_recovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:legacy-review-handoff-binding")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "tests passed",
    ])
    review = next((root / "review").iterdir())
    generation = _review_generation(root)
    receipt = (review / "REVIEW.md").read_text(encoding="utf-8")
    (review / "REVIEW.md").write_text(
        "\n".join(
            line for line in receipt.splitlines()
            if not line.startswith("handoff_sha256:")
        ) + "\n",
        encoding="utf-8",
    )
    before = {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    }
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation, "--decision-basis", "legacy pass",
    ]) == 2
    assert queue.main([
        "--root", str(root), "review-fail", task_id, "--reviewer", "sol",
        "--review-generation", generation, "--decision-basis", "legacy fail",
        "--next", "must not transition",
    ]) == 2
    assert {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    } == before
    assert queue.main([
        "--root", str(root), "review-recover", task_id,
        "--reviewer", "sol", "--review-generation", generation,
        "--decision-basis", "legacy receipt predates HANDOFF binding",
        "--next", "reclaim and resubmit",
    ]) == 0
    assert next((root / "inbox/ready").iterdir()).is_dir()
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_concurrent_review_verdicts_have_one_lock_serialized_winner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:concurrent-review-verdict")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "tests passed",
    ])
    generation = _review_generation(root)
    barrier = Barrier(3)
    results: list[int] = []

    def decide(command: str) -> None:
        args = [
            "--root", str(root), command, task_id, "--reviewer", "sol",
            "--review-generation", generation,
            "--decision-basis", f"concurrent {command}",
        ]
        if command == "review-fail":
            args.extend(("--next", "repair"))
        barrier.wait()
        results.append(queue.main(args))

    threads = [
        Thread(target=decide, args=("review-pass",)),
        Thread(target=decide, args=("review-fail",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(results) == [0, 2]
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_review_verdict_is_bound_to_exact_handoff_snapshot_and_can_recover(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:review:handoff-snapshot")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement generation one",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "generation one tests passed",
    ])
    review = next((root / "review").iterdir())
    fields_one = _review_fields(root)
    assert fields_one["handoff_sha256"] == queue._handoff_snapshot_digest(review)

    handoff = queue._read_handoff(review)
    handoff["summary"] = "changed after the reviewer opened the submission"
    queue._atomic_text(review / "HANDOFF.md", queue._handoff_text(handoff))
    before = {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    }
    board_before = (root / "BOARD.md").read_bytes()

    for command in ("review-pass", "review-fail"):
        args = [
            "--root", str(root), command, task_id, "--reviewer", "sol",
            "--review-generation", fields_one["review_generation"],
            "--decision-basis", "verdict for a different snapshot",
        ]
        if command == "review-fail":
            args.extend(("--next", "repair rejected snapshot"))
        assert queue.main(args) == 2
        assert {
            name: (review / name).read_bytes()
            for name in ("META.json", "HANDOFF.md", "REVIEW.md")
        } == before
        assert (root / "BOARD.md").read_bytes() == board_before
    assert any("review HANDOFF digest mismatch" in issue for issue in queue.doctor(root))

    assert queue.main([
        "--root", str(root), "review-recover", task_id, "--reviewer", "sol",
        "--review-generation", fields_one["review_generation"],
        "--decision-basis", "submitted HANDOFF changed before verdict",
        "--next", "claim and submit a fresh review generation",
    ]) == 0
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "submit generation two",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "generation two tests passed",
    ])
    fields_two = _review_fields(root)
    assert fields_two["review_generation"] != fields_one["review_generation"]
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", fields_two["review_generation"],
        "--decision-basis", "exact generation two snapshot passes",
    ]) == 0
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_legacy_review_without_handoff_digest_fails_closed_then_recovers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:review:legacy-receipt")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "tests passed",
    ])
    review = next((root / "review").iterdir())
    fields = queue._read_fields(
        review / "REVIEW.md",
        ("result", "changed", "verified", "review_generation", "submitted_at"),
    )
    queue._atomic_text(review / "REVIEW.md", (
        f"result: {fields['result']}\nchanged: {fields['changed']}\n"
        f"verified: {fields['verified']}\n"
        f"review_generation: {fields['review_generation']}\n"
        f"submitted_at: {fields['submitted_at']}\n"
    ))

    for command in ("review-pass", "review-fail"):
        args = [
            "--root", str(root), command, task_id, "--reviewer", "sol",
            "--review-generation", fields["review_generation"],
            "--decision-basis", "legacy receipt cannot identify a snapshot",
        ]
        if command == "review-fail":
            args.extend(("--next", "recover legacy review"))
        assert queue.main(args) == 2
    assert any("review field handoff_sha256 missing" in issue for issue in queue.doctor(root))
    assert queue.main([
        "--root", str(root), "review-recover", task_id, "--reviewer", "sol",
        "--review-generation", fields["review_generation"],
        "--decision-basis", "legacy review has no HANDOFF digest",
        "--next", "claim and resubmit under the current protocol",
    ]) == 0
    assert next((root / "inbox/ready").iterdir()).is_dir()
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_review_verdict_rechecks_handoff_while_queue_mutation_lock_is_held(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:review:digest-under-lock")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "tests passed",
    ])
    generation = _review_generation(root)
    original_digest = queue._handoff_snapshot_digest
    observed_lock: list[bool] = []

    def digest_with_lock_assertion(task: Path) -> str:
        observed_lock.append((root / ".queue-mutation.lock").is_file())
        return original_digest(task)

    monkeypatch.setattr(queue, "_handoff_snapshot_digest", digest_with_lock_assertion)
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation,
        "--decision-basis", "exact submitted snapshot passes",
    ]) == 0
    assert observed_lock == [True]


def test_review_verdict_is_bound_to_exact_handoff_snapshot_and_can_recover(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "component:review-handoff-snapshot")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement exact snapshot",
    ])
    queue.main([
        "--root", str(root), "checkpoint", task_id, "--owner", "terra",
        "--phase", "implemented", "--summary", "exact reviewed evidence",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "snapshot tests passed",
    ])
    review = next((root / "review").iterdir())
    generation = _review_generation(root)
    receipt = queue._read_fields(
        review / "REVIEW.md", ("handoff_sha256",),
    )
    assert receipt["handoff_sha256"] == hashlib.sha256(
        (review / "HANDOFF.md").read_bytes()
    ).hexdigest()

    handoff_path = review / "HANDOFF.md"
    queue._atomic_text(
        handoff_path,
        handoff_path.read_text(encoding="utf-8").replace(
            "summary: exact reviewed evidence",
            "summary: changed after reviewer read",
        ),
    )
    before = {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    }
    board_before = (root / "BOARD.md").read_bytes()
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation,
        "--decision-basis", "stale snapshot passed",
    ]) == 2
    assert queue.main([
        "--root", str(root), "review-fail", task_id, "--reviewer", "sol",
        "--review-generation", generation,
        "--decision-basis", "stale snapshot failed",
        "--next", "repair",
    ]) == 2
    assert {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    } == before
    assert (root / "BOARD.md").read_bytes() == board_before
    assert any("review HANDOFF digest mismatch" in issue for issue in queue.doctor(root))

    assert queue.main([
        "--root", str(root), "review-recover", task_id,
        "--reviewer", "sol", "--review-generation", generation,
        "--decision-basis", "HANDOFF changed after submission",
        "--next", "reclaim and submit a fresh snapshot",
    ]) == 0
    assert next((root / "inbox/ready").iterdir()).is_dir()
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "resubmit exact snapshot",
    ]) == 0
    assert queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "fresh snapshot tests passed",
    ]) == 0
    fresh_generation = _review_generation(root)
    assert fresh_generation != generation
    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", fresh_generation,
        "--decision-basis", "fresh exact snapshot passed",
    ]) == 0


def test_legacy_review_without_handoff_digest_requires_recovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "component:legacy-review-recovery")
    _triage(root, task_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "tests passed",
    ])
    review = next((root / "review").iterdir())
    generation = _review_generation(root)
    receipt_path = review / "REVIEW.md"
    receipt_path.write_text(
        "\n".join(
            line for line in receipt_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("handoff_sha256:")
        ) + "\n",
        encoding="utf-8",
    )

    assert queue.main([
        "--root", str(root), "review-pass", task_id, "--reviewer", "sol",
        "--review-generation", generation,
        "--decision-basis", "legacy receipt passed",
    ]) == 2
    assert queue.main([
        "--root", str(root), "review-fail", task_id, "--reviewer", "sol",
        "--review-generation", generation,
        "--decision-basis", "legacy receipt failed", "--next", "repair",
    ]) == 2
    assert review.is_dir()
    assert any("review field handoff_sha256 missing" in issue for issue in queue.doctor(root))
    assert queue.main([
        "--root", str(root), "review-recover", task_id,
        "--reviewer", "sol", "--review-generation", generation,
        "--decision-basis", "legacy receipt has no HANDOFF digest",
        "--next", "resubmit with a bound snapshot",
    ]) == 0
    assert next((root / "inbox/ready").iterdir()).is_dir()


def test_invalid_done_receipt_can_be_reopened_only_from_done(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "component:invalid-done-reopen")
    _triage(root, task_id)
    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "implement bounded fix",
    ])
    queue.main([
        "--root", str(root), "submit", task_id, "--owner", "terra",
        "--result", "incorrectly accepted", "--changed", "src/component.py",
        "--verified", "stale evidence",
    ])

    assert queue.main([
        "--root", str(root), "reopen", task_id,
        "--reason", "newer exact evidence disproves completion",
        "--next", "repair the failed boundary",
    ]) == 0
    ready = next((root / "inbox/ready").iterdir())
    meta = json.loads((ready / "META.json").read_text(encoding="utf-8"))
    handoff = queue._read_handoff(ready)
    assert not (ready / "RESULT.md").exists()
    assert meta["state"] == "ready"
    assert meta["completed_at"] is None
    assert meta["owner"] is None and meta["assigned_agent"] is None
    assert handoff["phase"] == "rework"
    assert "newer exact evidence" in handoff["summary"]
    assert handoff["next"] == "repair the failed boundary"
    assert queue.main(["--root", str(root), "doctor"]) == 0

    task_before = {
        name: (ready / name).read_bytes()
        for name in queue.REQUIRED_TASK_FILES
    }
    board_before = (root / "BOARD.md").read_bytes()
    assert queue.main([
        "--root", str(root), "reopen", task_id,
        "--reason", "duplicate reopen", "--next", "must not run",
    ]) == 2
    assert {
        name: (ready / name).read_bytes()
        for name in queue.REQUIRED_TASK_FILES
    } == task_before
    assert (root / "BOARD.md").read_bytes() == board_before


def test_doctor_is_read_only_and_reports_duplicate_fingerprint_and_stale_board(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    _discover(root)
    first = next((root / "inbox/new").iterdir())
    duplicate = first.with_name(first.name + "-duplicate")
    duplicate.mkdir()
    for name in queue.REQUIRED_TASK_FILES:
        (duplicate / name).write_bytes((first / name).read_bytes())
    queue.write_board(root)
    board = root / "BOARD.md"
    board.write_text(
        board.read_text(encoding="utf-8").replace("## Blocked", "## Blocked (tampered)"),
        encoding="utf-8",
    )
    board_before = (root / "BOARD.md").read_bytes()
    issues = queue.doctor(root)
    assert any("duplicate id" in issue for issue in issues)
    assert any("duplicate fingerprint" in issue for issue in issues)
    assert any("BOARD.md is stale" in issue for issue in issues)
    assert (root / "BOARD.md").read_bytes() == board_before
    assert duplicate.is_dir()


def test_queue_root_falls_back_to_central_repository_when_git_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    nested = repository / "nested" / "work"
    (repository / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    def fail_git(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(queue, "_git", fail_git)
    assert queue.resolve_queue_root() == repository / "artifacts/request_queue"


def test_doctor_reports_malformed_meta_and_state_inappropriate_entries(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    _discover(root)
    task = next((root / "inbox/new").iterdir())
    meta_path = task / "META.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["depends_on"] = None
    meta["write_scope"] = "src/component.py"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (task / "RESULT.md").write_text("result: orphan\n", encoding="utf-8")
    (task / "unexpected").mkdir()

    issues = queue.doctor(root)

    assert any("invalid META depends_on type/value" in issue for issue in issues)
    assert any("invalid META write_scope type/value" in issue for issue in issues)
    assert sum("unexpected task entry for new" in issue for issue in issues) == 2
    assert any("BOARD.md is stale" in issue for issue in issues)


def test_joined_metadata_is_rejected_and_retarget_repairs_ready_atomically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    first_dependency = _complete(root, "queue:dependency:first")
    second_dependency = _complete(root, "queue:dependency:second")
    task_id = _discover(root, "queue:scope:repair")

    assert queue.main([
        "--root", str(root), "triage", task_id, "--priority", "P1",
        "--risk", "medium", "--write-scope", "src/first.py,src/second.py",
        "--problem", "A bounded behavior differs.",
        "--evidence", "offline test reproduces the mismatch",
        "--allow", "src/first.py", "--deny", "external operations",
        "--done-when", "The bounded behavior matches its contract.",
        "--verify", "pytest exact-node",
    ]) == 2
    assert queue.main([
        "--root", str(root), "triage", task_id, "--priority", "P1",
        "--risk", "medium", "--write-scope", "src/first.py",
        "--depends-on", f"{first_dependency},{second_dependency}",
        "--problem", "A bounded behavior differs.",
        "--evidence", "offline test reproduces the mismatch",
        "--allow", "src/first.py", "--deny", "external operations",
        "--done-when", "The bounded behavior matches its contract.",
        "--verify", "pytest exact-node",
    ]) == 2

    _triage(root, task_id)
    assert queue.main([
        "--root", str(root), "retarget", task_id,
        "--write-scope", "tests/test_second.py",
        "--write-scope", "src/first.py",
        "--depends-on", first_dependency,
        "--depends-on", second_dependency,
    ]) == 0
    ready = next((root / "inbox/ready").iterdir())
    meta = json.loads((ready / "META.json").read_text(encoding="utf-8"))
    assert meta["write_scope"] == ["src/first.py", "tests/test_second.py"]
    assert meta["depends_on"] == [first_dependency, second_dependency]
    assert queue.main(["--root", str(root), "doctor"]) == 0

    before = (ready / "META.json").read_bytes()
    assert queue.main([
        "--root", str(root), "retarget", task_id,
        "--write-scope", "src/changed.py",
        "--depends-on", f"{first_dependency},{second_dependency}",
    ]) == 2
    assert (ready / "META.json").read_bytes() == before
    assert queue.main([
        "--root", str(root), "retarget", task_id,
    ]) == 2

    queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "bounded work",
    ])
    assert queue.main([
        "--root", str(root), "retarget", task_id,
        "--write-scope", "src/other.py",
    ]) == 2


def test_doctor_and_claim_reject_delimiter_joined_metadata(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _discover(root, "queue:scope:doctor")
    _triage(root, task_id)
    ready = next((root / "inbox/ready").iterdir())
    meta_path = ready / "META.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["write_scope"] = ["src/first.py,src/second.py"]
    meta["depends_on"] = [
        "RQ-20260824T010101-ABCD,RQ-20260824T010102-BCDE"
    ]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    issues = queue.doctor(root)

    assert any("write scope must be an exact repository-relative path" in issue for issue in issues)
    assert any("dependency must be one exact task id" in issue for issue in issues)
    assert queue.main([
        "--root", str(root), "claim", task_id, "--owner", "terra",
        "--next", "must fail closed",
    ]) == 2


def test_retarget_repairs_joined_done_metadata_without_reopening_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _complete(root, "queue:done:joined-metadata")
    done = next((root / "done").iterdir())
    meta_path = done / "META.json"
    result_before = (done / "RESULT.md").read_bytes()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["write_scope"] = ["src/first.py,,tests/test_second.py"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    before = {
        name: (done / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "RESULT.md")
    }
    board_before = (root / "BOARD.md").read_bytes()
    assert queue.main([
        "--root", str(root), "retarget", task_id, "--repair-joined",
    ]) == 2
    assert {
        name: (done / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "RESULT.md")
    } == before
    assert (root / "BOARD.md").read_bytes() == board_before

    meta["write_scope"] = ["src/first.py,tests/test_second.py"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert queue.main([
        "--root", str(root), "retarget", task_id, "--repair-joined",
    ]) == 0
    repaired = json.loads(meta_path.read_text(encoding="utf-8"))
    assert repaired["state"] == "done"
    assert repaired["write_scope"] == ["src/first.py", "tests/test_second.py"]
    assert (done / "RESULT.md").read_bytes() == result_before
    assert queue.main(["--root", str(root), "doctor"]) == 0
    assert queue.main([
        "--root", str(root), "retarget", task_id, "--repair-joined",
    ]) == 2


def test_joined_repair_rejects_review_active_and_blocked_without_mutation(
    tmp_path: Path,
) -> None:
    review_root = tmp_path / "review-queue"
    queue.main(["--root", str(review_root), "init"])
    review_id = _discover(review_root, "queue:repair:review-rejected")
    _triage(review_root, review_id, review=True, reviewer="sol")
    queue.main([
        "--root", str(review_root), "claim", review_id, "--owner", "terra",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(review_root), "submit", review_id, "--owner", "terra",
        "--result", "implemented", "--changed", "src/component.py",
        "--verified", "tests passed",
    ])
    review = next((review_root / "review").iterdir())
    review_meta_path = review / "META.json"
    review_meta = json.loads(review_meta_path.read_text(encoding="utf-8"))
    review_meta["write_scope"] = ["src/first.py,tests/second.py"]
    review_meta_path.write_text(json.dumps(review_meta), encoding="utf-8")
    review_before = {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    }
    review_board_before = (review_root / "BOARD.md").read_bytes()
    assert queue.main([
        "--root", str(review_root), "retarget", review_id, "--repair-joined",
    ]) == 2
    assert {
        name: (review / name).read_bytes()
        for name in ("META.json", "HANDOFF.md", "REVIEW.md")
    } == review_before
    assert (review_root / "BOARD.md").read_bytes() == review_board_before

    active_root = tmp_path / "active-queue"
    queue.main(["--root", str(active_root), "init"])
    active_id = _discover(active_root, "queue:repair:active-rejected")
    _triage(active_root, active_id)
    queue.main([
        "--root", str(active_root), "claim", active_id, "--owner", "terra",
        "--next", "implement",
    ])
    active = next((active_root / "active").iterdir())
    active_before = {
        name: (active / name).read_bytes()
        for name in queue.REQUIRED_TASK_FILES
    }
    assert queue.main([
        "--root", str(active_root), "retarget", active_id, "--repair-joined",
    ]) == 2
    assert {
        name: (active / name).read_bytes()
        for name in queue.REQUIRED_TASK_FILES
    } == active_before

    blocked_root = tmp_path / "blocked-queue"
    queue.main(["--root", str(blocked_root), "init"])
    blocked_id = _discover(blocked_root, "queue:repair:blocked-rejected")
    _triage(blocked_root, blocked_id)
    queue.main([
        "--root", str(blocked_root), "claim", blocked_id, "--owner", "terra",
        "--next", "implement",
    ])
    queue.main([
        "--root", str(blocked_root), "block", blocked_id, "--owner", "terra",
        "--reason", "external approval", "--required-action", "approve",
        "--resume-condition", "approval recorded",
    ])
    blocked = next((blocked_root / "blocked").iterdir())
    blocked_before = {
        name: (blocked / name).read_bytes()
        for name in (*queue.REQUIRED_TASK_FILES, "BLOCKED.md")
    }
    assert queue.main([
        "--root", str(blocked_root), "retarget", blocked_id, "--repair-joined",
    ]) == 2
    assert {
        name: (blocked / name).read_bytes()
        for name in (*queue.REQUIRED_TASK_FILES, "BLOCKED.md")
    } == blocked_before


@pytest.mark.parametrize(
    "joined_scope",
    [
        "src/first.py;tests/second.py",
        "src/first.py, tests/second.py",
        "src/first.py,tests/second.py ",
    ],
)
def test_joined_repair_does_not_guess_noncomma_or_padded_values(
    tmp_path: Path, joined_scope: str,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    task_id = _complete(root, f"queue:repair:strict:{joined_scope}")
    done = next((root / "done").iterdir())
    meta_path = done / "META.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["write_scope"] = [joined_scope]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    before = {
        name: (done / name).read_bytes()
        for name in (*queue.REQUIRED_TASK_FILES, "RESULT.md")
    }
    board_before = (root / "BOARD.md").read_bytes()

    assert queue.main([
        "--root", str(root), "retarget", task_id, "--repair-joined",
    ]) == 2
    assert {
        name: (done / name).read_bytes()
        for name in (*queue.REQUIRED_TASK_FILES, "RESULT.md")
    } == before
    assert (root / "BOARD.md").read_bytes() == board_before


def test_compact_done_dry_run_then_preserves_identity_and_future_dependencies(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    fingerprint = "queue:compact:durable-completion"
    completed_id = _complete(root, fingerprint)
    done = next((root / "done").iterdir())
    before = _task_bytes(done)
    board_before = (root / "BOARD.md").read_bytes()
    _mock_clean_tracked_done(monkeypatch, root)

    assert queue.main([
        "--root", str(root), "compact-done", completed_id, "--dry-run",
    ]) == 0
    assert done.is_dir() and _task_bytes(done) == before
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert (root / "BOARD.md").read_bytes() == board_before

    assert queue.main([
        "--root", str(root), "compact-done", completed_id,
    ]) == 0
    assert not done.exists()
    entries = queue._load_completed_index(root)
    assert len(entries) == 1
    assert entries[0]["id"] == completed_id
    assert entries[0]["fingerprint"] == fingerprint
    assert entries[0]["receipt_sha256"] == hashlib.sha256(b"").hexdigest() or len(
        str(entries[0]["receipt_sha256"])
    ) == 64
    assert queue.main(["--root", str(root), "doctor"]) == 0

    assert queue.main([
        "--root", str(root), "discover", "--title", "Duplicate compacted task",
        "--discovered-by", "luna", "--source-task", "RQ-parent",
        "--priority-hint", "P1", "--fingerprint", fingerprint,
        "--symptom", "duplicate", "--evidence", "index",
        "--impact", "collision", "--suspected-scope", "src/component.py",
        "--reproduce", "pytest exact-node",
    ]) == 2

    dependent_id = _discover(root, "queue:compact:future-dependent")
    _triage(root, dependent_id)
    assert queue.main([
        "--root", str(root), "retarget", dependent_id,
        "--depends-on", completed_id,
    ]) == 0
    assert queue.main([
        "--root", str(root), "claim", dependent_id, "--owner", "dependent",
        "--next", "use compacted prerequisite",
    ]) == 0


def test_compact_done_rejects_live_reference_without_mutation(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    completed_id = _complete(root, "queue:compact:live-reference")
    dependent_id = _discover(root, "queue:compact:existing-dependent")
    _triage(root, dependent_id)
    queue.main([
        "--root", str(root), "retarget", dependent_id,
        "--depends-on", completed_id,
    ])
    done = next((root / "done").iterdir())
    before = _task_bytes(done)
    board_before = (root / "BOARD.md").read_bytes()
    _mock_clean_tracked_done(monkeypatch, root)

    assert queue.main([
        "--root", str(root), "compact-done", completed_id,
    ]) == 2
    assert done.is_dir() and _task_bytes(done) == before
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert (root / "BOARD.md").read_bytes() == board_before


def test_compact_done_rejects_untracked_record_without_mutation(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    completed_id = _complete(root, "queue:compact:untracked")
    done = next((root / "done").iterdir())
    before = _task_bytes(done)
    board_before = (root / "BOARD.md").read_bytes()
    monkeypatch.setattr(queue, "_git", lambda *_args: "")

    assert queue.main([
        "--root", str(root), "compact-done", completed_id,
    ]) == 2
    assert done.is_dir() and _task_bytes(done) == before
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert (root / "BOARD.md").read_bytes() == board_before


def test_compact_done_partial_deletion_restores_exact_task_and_index(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    completed_id = _complete(root, "queue:compact:partial-delete")
    done = next((root / "done").iterdir())
    before = _task_bytes(done)
    board_before = (root / "BOARD.md").read_bytes()
    _mock_clean_tracked_done(monkeypatch, root)

    def partial_delete(task: Path) -> None:
        (task / "RESULT.md").unlink()
        raise OSError("injected partial deletion")

    monkeypatch.setattr(queue, "_delete_done_directory", partial_delete)
    assert queue.main([
        "--root", str(root), "compact-done", completed_id,
    ]) == 2
    assert done.is_dir() and _task_bytes(done) == before
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert (root / "BOARD.md").read_bytes() == board_before
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_compact_done_index_write_failure_preserves_original_bytes(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    completed_id = _complete(root, "queue:compact:index-write-failure")
    done = next((root / "done").iterdir())
    before = _task_bytes(done)
    board_before = (root / "BOARD.md").read_bytes()
    _mock_clean_tracked_done(monkeypatch, root)
    monkeypatch.setattr(
        queue, "_write_completed_index",
        lambda *_args: (_ for _ in ()).throw(OSError("injected index write")),
    )

    assert queue.main([
        "--root", str(root), "compact-done", completed_id,
    ]) == 2
    assert done.is_dir() and _task_bytes(done) == before
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert (root / "BOARD.md").read_bytes() == board_before


def test_prune_done_keeps_newest_and_live_referenced_receipts(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    for index in range(5):
        _complete(root, f"queue:prune:retention:{index}")
    ordered = sorted(
        (
            (queue._aware(queue._load_meta(task).get("completed_at")), task)
            for task in (root / "done").iterdir()
        ),
        key=lambda item: (item[0], queue._load_meta(item[1])["id"]),
        reverse=True,
    )
    referenced = ordered[-1][1]
    referenced_id = queue._load_meta(referenced)["id"]
    dependent_id = _discover(root, "queue:prune:live-dependent")
    _triage(root, dependent_id)
    assert queue.main([
        "--root", str(root), "retarget", dependent_id,
        "--depends-on", referenced_id,
    ]) == 0
    newest_ids = {
        queue._load_meta(task)["id"] for _completed_at, task in ordered[:2]
    }
    _mock_clean_tracked_done(monkeypatch, root)

    assert queue.main([
        "--root", str(root), "prune-done", "--keep", "2", "--dry-run",
    ]) == 0
    assert len(list((root / "done").iterdir())) == 5
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()

    assert queue.main([
        "--root", str(root), "prune-done", "--keep", "2",
    ]) == 0
    retained_ids = {
        queue._load_meta(task)["id"] for task in (root / "done").iterdir()
    }
    assert retained_ids == newest_ids | {referenced_id}
    assert len(queue._load_completed_index(root)) == 2
    assert queue.main(["--root", str(root), "doctor"]) == 0


def test_prune_done_rolls_back_all_receipts_when_a_deletion_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    for index in range(4):
        _complete(root, f"queue:prune:rollback:{index}")
    before = {
        task.name: _task_bytes(task) for task in (root / "done").iterdir()
    }
    board_before = (root / "BOARD.md").read_bytes()
    _mock_clean_tracked_done(monkeypatch, root)
    original_delete = queue._delete_done_directory
    calls = 0

    def fail_second_delete(task: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            (task / "RESULT.md").unlink()
            raise OSError("injected bulk deletion failure")
        original_delete(task)

    monkeypatch.setattr(queue, "_delete_done_directory", fail_second_delete)
    assert queue.main([
        "--root", str(root), "prune-done", "--keep", "1",
    ]) == 2
    assert {
        task.name: _task_bytes(task) for task in (root / "done").iterdir()
    } == before
    assert not (root / queue.COMPLETED_INDEX_NAME).exists()
    assert (root / "BOARD.md").read_bytes() == board_before


def test_prune_done_allows_only_wholly_untracked_receipts_when_approved(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    for index in range(3):
        _complete(root, f"queue:prune:untracked:{index}")

    def fake_git(start: Path, *args: str) -> str:
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return str(Path(start).resolve())
        if args[0] == "ls-files":
            return ""
        if args[0] == "status":
            return "?? artifacts/request_queue/done/"
        if args[0] == "cat-file":
            raise subprocess.CalledProcessError(1, args)
        raise AssertionError(args)

    monkeypatch.setattr(queue, "_git", fake_git)
    assert queue.main([
        "--root", str(root), "prune-done", "--keep", "1",
    ]) == 2
    assert len(list((root / "done").iterdir())) == 3

    assert queue.main([
        "--root", str(root), "prune-done", "--keep", "1",
        "--allow-untracked",
    ]) == 0
    assert len(list((root / "done").iterdir())) == 1
    assert len(queue._load_completed_index(root)) == 2


def test_doctor_rejects_malformed_and_duplicate_completed_index(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue.main(["--root", str(root), "init"])
    index = root / queue.COMPLETED_INDEX_NAME
    index.write_text("{not-json", encoding="utf-8")
    assert any("completed index is unreadable" in issue for issue in queue.doctor(root))

    entry = {
        "id": "RQ-20260825T010101-ABCD", "legacy_id": None,
        "fingerprint": "queue:compact:duplicate", "completed_at": "2026-08-25T01:01:01+09:00",
        "directory": "P1-RQ-20260825T010101-ABCD-compacted",
        "result_summary": "completed", "receipt_sha256": "a" * 64,
    }
    payload = {
        "schema_version": queue.COMPLETED_INDEX_SCHEMA,
        "entries": [entry, dict(entry)],
        "entries_sha256": queue._completed_entries_digest([entry, dict(entry)]),
    }
    queue._atomic_json(index, payload)
    assert any("duplicate completed index id" in issue for issue in queue.doctor(root))
