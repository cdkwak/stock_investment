from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from stock_data.gui.account_snapshot_service import LocalAccountSnapshotService
from stock_data.orchestration.account_privacy import (
    AccountSnapshotRemovalError,
    mask_account_identifier,
    prune_account_landing,
    redact_account_text,
    remove_retained_account_snapshots,
)


def _make_directory_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        completed = subprocess.run(
            [
                os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink",
                "/J", str(link), str(target),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("Windows test volume does not permit a synthetic directory junction")
    else:
        link.symlink_to(target, target_is_directory=True)


def _make_file_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlink creation is unavailable")


def test_account_identifier_masking_and_diagnostics_never_echo_sensitive_values():
    assert mask_account_identifier("123-45-678901") == "••••-8901"
    assert mask_account_identifier("123") == "••••"

    safe = redact_account_text(
        "account_number=123-45-678901 balance=999999 positions=PRIVATE"
    )

    assert "123-45-678901" not in safe
    assert "999999" not in safe
    assert "PRIVATE" not in safe
    assert "[REDACTED_ACCOUNT]" in safe


@pytest.mark.parametrize("separator", ["=", ":", ":="])
@pytest.mark.parametrize("key_quote", ["", '"', "'"])
@pytest.mark.parametrize(
    "key",
    [
        "buying_power", "buying-power", "buying power", "buyingPower",
        "buying_power_amount", "buyingPowerAmount",
        "cash_buying_power", "cashBuyingPower", "cashBuyingPowerAmount",
        "order_available", "orderAvailable", "order_available_amount",
        "orderAvailableAmount", "현금 매수가능", "현금매수가능",
        "현금매수가능금액", "매수가능", "매수가능금액", "주문가능",
        "주문가능금액",
    ],
)
def test_account_buying_power_diagnostics_redact_complete_currency_amounts(
    key, key_quote, separator,
):
    rendered_key = f"{key_quote}{key}{key_quote}"
    amounts = (
        "345000", "-3,500.50", "+12.34", "1.2e3",
        "KRW 345,000", "USD -12.34", "$3,500.50", "₩ 345000",
        "345000 KRW", "12.34 USD", "345000원", "12.34 달러",
        '"3,500.50 USD"', "'USD -12.34'",
    )
    for amount in amounts:
        redacted = redact_account_text(
            f"before {rendered_key}{separator}{amount} after"
        )

        assert redacted == (
            f"before {rendered_key}=[REDACTED_ACCOUNT_VALUE] after"
        )
        assert not any(character.isdigit() for character in redacted)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '{"buyingPower":"USD -12.34","cashflow":42}',
            '{"buyingPower"=[REDACTED_ACCOUNT_VALUE],"cashflow":42}',
        ),
        (
            '{"orderAvailableAmount":345000,"cashflow":42}',
            '{"orderAvailableAmount"=[REDACTED_ACCOUNT_VALUE],"cashflow":42}',
        ),
        (
            "buying_power:=3,500.50, cashflow=42",
            "buying_power=[REDACTED_ACCOUNT_VALUE], cashflow=42",
        ),
    ],
)
def test_account_buying_power_redaction_handles_following_delimiters(text, expected):
    assert redact_account_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        '"buying_power\'=345000',
        "buying_power'=345000",
        'buying_power"=345000',
        'cashBuyingPower="3,500.50\'',
        'orderAvailableAmount="USD -12.34\'',
        '현금매수가능금액="345,000\'',
        '주문가능금액="345,000\'',
        'buying_power="3,500.50\'',
    ],
)
def test_account_buying_power_redaction_fails_safe_for_mismatched_quotes(text):
    redacted = redact_account_text(text)

    assert "[REDACTED_ACCOUNT_VALUE]" in redacted
    assert not any(character.isdigit() for character in redacted)


@pytest.mark.parametrize(
    "text",
    [
        "cashBuyingPower=345000. next=x",
        "cashBuyingPower=345000, next=x",
        "cashBuyingPower=345000}",
        "cashBuyingPower=345000]",
    ],
)
def test_account_buying_power_redaction_never_leaves_continued_amount_digits(text):
    redacted = redact_account_text(text)

    assert "[REDACTED_ACCOUNT_VALUE]" in redacted
    assert not any(character.isdigit() for character in redacted)


@pytest.mark.parametrize(
    "text",
    [
        "buying_power=3,50",
        "buying_power=3,500x",
        "buying_power=3,500.50.7",
        '{"buyingPower":"3,50","cashflow":42}',
    ],
)
def test_account_buying_power_redaction_never_leaves_partial_grouped_values(text):
    assert redact_account_text(text) == text


@pytest.mark.parametrize("separator", [":=", ": ="])
@pytest.mark.parametrize("key_quote", ["", '"', "'"])
@pytest.mark.parametrize(
    "key",
    [
        "buying_power", "cashBuyingPower", "orderAvailableAmount",
        "현금매수가능금액", "주문가능금액",
    ],
)
@pytest.mark.parametrize(
    "value", ["3,50", '"3,500x"', "'3,500.50.7'"],
)
def test_malformed_grouped_values_never_backtrack_to_short_colon_separator(
    key, key_quote, separator, value,
):
    rendered_key = f"{key_quote}{key}{key_quote}"
    text = f"{rendered_key}{separator}{value}"

    assert redact_account_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "buying_power=PRIVATE",
        "orderAvailable=PRIVATE",
        "주문가능=PRIVATE",
    ],
)
def test_account_buying_power_fallback_still_protects_sensitive_tokens(text):
    assert redact_account_text(text).endswith("=[REDACTED_ACCOUNT_VALUE]")


@pytest.mark.parametrize(
    "text",
    [
        "cashBuyingPower=345000",
        '"orderAvailableAmount":"USD -12.34"',
        "현금매수가능금액=PRIVATE",
    ],
)
def test_account_buying_power_redaction_is_idempotent(text):
    once = redact_account_text(text)

    assert redact_account_text(once) == once


@pytest.mark.parametrize(
    "text",
    [
        "market_power=42",
        "cashflow=42",
        "purchasing_power=42",
        "power=42",
        "매수가능한 종목=42",
        "현금흐름=42",
        "buying power is 42",
    ],
)
def test_account_buying_power_redaction_preserves_unrelated_text(text):
    assert redact_account_text(text) == text


@pytest.mark.parametrize(
    ("text", "forbidden", "identifier_count", "value_count"),
    [
        (
            '{"balance":123456,"accountSeq":42,"cash":999,"holdings":[1]}',
            ("123456", "42", "999", "[1]"),
            1,
            3,
        ),
        (
            '{"balance":"PRIVATE","cash":{"available":999},'
            '"holdings":["SECRET",2]}',
            ("PRIVATE", "available", "999", "SECRET", "2"),
            0,
            3,
        ),
    ],
)
def test_account_redaction_covers_quoted_json_scalars_arrays_and_objects(
    text, forbidden, identifier_count, value_count,
):
    redacted = redact_account_text(text)

    assert all(value not in redacted for value in forbidden)
    assert redacted.count("[REDACTED_ACCOUNT]") == identifier_count
    assert redacted.count("[REDACTED_ACCOUNT_VALUE]") == value_count
    assert redact_account_text(redacted) == redacted


def test_account_redaction_preserves_unrelated_quoted_json_fields():
    text = '{"symbol":"005930","cashflow":42,"portfolioName":"SAFE"}'

    assert redact_account_text(text) == text


def test_account_landing_retention_is_limited_to_exact_dataset(tmp_path):
    landing = tmp_path / "data/landing/tossinvest/account_snapshot"
    landing.mkdir(parents=True)
    for name in ("001.json", "002.json", "003.json"):
        (landing / name).write_text("{}", encoding="utf-8")
    market = tmp_path / "data/landing/yahoo/market.json"
    market.parent.mkdir(parents=True)
    market.write_text("market", encoding="utf-8")

    assert prune_account_landing(tmp_path, keep=1) == 2
    assert [path.name for path in landing.glob("*.json")] == ["003.json"]
    assert market.read_text(encoding="utf-8") == "market"


def test_exact_account_snapshot_removal_never_touches_credentials_or_market_data(tmp_path):
    account_files = (
        tmp_path / "data/normalized/toss_account_snapshot/latest.json",
        tmp_path / "data/state/toss_account_snapshot.json",
        tmp_path / "data/local/account_snapshots/kb_self.json",
        tmp_path / "data/local/account_snapshots/family_mirae_etf.json",
        tmp_path / "data/landing/tossinvest/account_snapshot/one.json",
        tmp_path / "data/state/transactions/toss_account_snapshot/one.json",
        tmp_path / "data/staging/toss_account_snapshot/orphan/candidate/normalized.json",
        tmp_path / "data/staging/toss_account_snapshot/orphan/backup/state.json",
        tmp_path / "data/local/account_value_history/toss_self/one.json",
        tmp_path / "data/local/account_value_history/kb_self/one.json",
    )
    for path in account_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"status": "SUCCEEDED"}) if "transactions" in path.parts else "private",
            encoding="utf-8",
        )
    unrelated = tmp_path / "data/normalized/kr_index_daily/data.parquet"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"market")
    credential = tmp_path / "runtime_credentials.json"
    credential.write_text("secret", encoding="utf-8")

    result = remove_retained_account_snapshots(tmp_path)

    assert result.status == "REMOVED" and result.removed_files == len(account_files)
    assert all(not path.exists() for path in account_files)
    assert unrelated.read_bytes() == b"market"
    assert credential.read_text(encoding="utf-8") == "secret"

    restarted = LocalAccountSnapshotService(account_files[0]).load()
    assert not restarted.available and restarted.reason == "ACCOUNT_SNAPSHOT_MISSING"
    assert remove_retained_account_snapshots(tmp_path).removed_files == 0


def test_account_snapshot_removal_cleans_crashed_history_bootstrap_stage(tmp_path):
    staged = (
        tmp_path / "data/staging/account_value_history/orphan/observation.json"
    )
    staged.parent.mkdir(parents=True)
    staged.write_text('{"value":"sensitive"}', encoding="utf-8")

    result = remove_retained_account_snapshots(tmp_path)

    assert result.status == "REMOVED"
    assert result.removed_files == 1
    assert not staged.exists()
    assert not staged.parent.exists()


def test_account_snapshot_removal_fails_closed_while_commit_is_incomplete(tmp_path):
    journal = tmp_path / "data/state/transactions/toss_account_snapshot/live.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"status": "PROMOTING"}), encoding="utf-8")
    retained = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    retained.parent.mkdir(parents=True)
    retained.write_text("private", encoding="utf-8")

    with pytest.raises(AccountSnapshotRemovalError, match="OPERATION_BUSY"):
        remove_retained_account_snapshots(tmp_path)

    assert retained.exists()


@pytest.mark.parametrize(
    ("linked_root", "external_file", "body"),
    [
        ("data/landing/tossinvest/account_snapshot", "one.json", "private"),
        ("data/local/account_snapshots", "kb_self.json", "private"),
        (
            "data/state/transactions/toss_account_snapshot",
            "one.json",
            json.dumps({"status": "SUCCEEDED"}),
        ),
        (
            "data/staging/toss_account_snapshot",
            "orphan/candidate/normalized.json",
            "private",
        ),
        ("data/normalized/toss_account_snapshot", "latest.json", "private"),
    ],
)
def test_account_snapshot_removal_rejects_owned_root_junction_escape_before_delete(
    tmp_path, linked_root, external_file, body,
):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    sentinel = outside / external_file
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(body, encoding="utf-8")
    _make_directory_link(project / linked_root, outside)
    assert (project / linked_root).resolve() == outside.resolve()
    retained = project / "data/state/toss_account_snapshot.json"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text("in-project-private", encoding="utf-8")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert sentinel.read_text(encoding="utf-8") == body
    assert retained.read_text(encoding="utf-8") == "in-project-private"


@pytest.mark.parametrize("staged_kind", ["candidate", "backup"])
def test_account_snapshot_removal_rejects_nested_staging_junction_escape(
    tmp_path, staged_kind,
):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    sentinel = outside / "state.json"
    sentinel.write_text("outside-private", encoding="utf-8")
    linked = project / f"data/staging/toss_account_snapshot/run/{staged_kind}"
    _make_directory_link(linked, outside)
    assert linked.resolve() == outside.resolve()
    retained = project / "data/state/toss_account_snapshot.json"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text("in-project-private", encoding="utf-8")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert sentinel.read_text(encoding="utf-8") == "outside-private"
    assert retained.read_text(encoding="utf-8") == "in-project-private"


@pytest.mark.parametrize("staged_kind", ["candidate", "backup"])
@pytest.mark.parametrize("target_kind", ["missing-directory", "external-file"])
def test_account_snapshot_removal_rejects_malformed_staging_junction_before_delete(
    tmp_path, staged_kind, target_kind,
):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    target = outside / "missing"
    target.mkdir()
    linked = project / f"data/staging/toss_account_snapshot/run/{staged_kind}"
    _make_directory_link(linked, target)
    target.rmdir()
    if target_kind == "external-file":
        target.write_text("outside-private", encoding="utf-8")
    retained = project / "data/state/toss_account_snapshot.json"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text("in-project-private", encoding="utf-8")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert retained.read_text(encoding="utf-8") == "in-project-private"
    if target_kind == "external-file":
        assert target.read_text(encoding="utf-8") == "outside-private"


@pytest.mark.parametrize(
    "owned_root",
    [
        "data/state/transactions/toss_account_snapshot",
        "data/landing/tossinvest/account_snapshot",
        "data/local/account_snapshots",
        "data/staging/toss_account_snapshot",
    ],
)
def test_account_snapshot_removal_rejects_owned_root_regular_file_before_delete(
    tmp_path, owned_root,
):
    project = tmp_path / "project"
    malformed = project / owned_root
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text("not-a-directory", encoding="utf-8")
    retained = project / "data/normalized/toss_account_snapshot/latest.json"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text("in-project-private", encoding="utf-8")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert retained.read_text(encoding="utf-8") == "in-project-private"


@pytest.mark.parametrize(
    "fixed_target",
    [
        "data/normalized/toss_account_snapshot/latest.json",
        "data/state/toss_account_snapshot.json",
        "data/local/account_snapshots/kb_self.json",
        "data/local/account_snapshots/family_mirae_etf.json",
    ],
)
def test_account_snapshot_removal_rejects_fixed_target_directory_before_delete(
    tmp_path, fixed_target,
):
    project = tmp_path / "project"
    malformed = project / fixed_target
    malformed.mkdir(parents=True)
    retained = project / "data/landing/tossinvest/account_snapshot/one.json"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text("in-project-private", encoding="utf-8")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert retained.read_text(encoding="utf-8") == "in-project-private"


def test_account_snapshot_removal_rejects_fixed_file_symlink_escape(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sentinel = tmp_path / "outside" / "latest.json"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"outside-private")
    linked = project / "data/normalized/toss_account_snapshot/latest.json"
    _make_file_symlink(linked, sentinel)
    retained = project / "data/state/toss_account_snapshot.json"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"in-project-private")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert sentinel.read_bytes() == b"outside-private"
    assert retained.read_bytes() == b"in-project-private"


def test_account_snapshot_removal_rejects_journal_child_symlink_before_read(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sentinel = tmp_path / "outside" / "journal.json"
    sentinel.parent.mkdir()
    sentinel.write_text(json.dumps({"status": "SUCCEEDED"}), encoding="utf-8")
    linked = project / "data/state/transactions/toss_account_snapshot/escape.json"
    _make_file_symlink(linked, sentinel)
    retained = project / "data/normalized/toss_account_snapshot/latest.json"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"in-project-private")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert sentinel.read_text(encoding="utf-8") == json.dumps({"status": "SUCCEEDED"})
    assert retained.read_bytes() == b"in-project-private"


def test_account_snapshot_removal_rejects_non_directory_project_root(tmp_path):
    project = tmp_path / "project"
    project.write_text("not a directory", encoding="utf-8")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)


@pytest.mark.parametrize(
    "owned_root",
    (
        "data/landing/tossinvest/account_snapshot",
        "data/local/account_snapshots",
        "data/state/transactions/toss_account_snapshot",
        "data/staging/toss_account_snapshot",
    ),
)
def test_account_snapshot_removal_rejects_owned_root_with_file_type(tmp_path, owned_root):
    project = tmp_path / "project"
    project.mkdir()
    malformed = project / owned_root
    malformed.parent.mkdir(parents=True)
    malformed.write_bytes(b"not-an-owned-directory")

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert malformed.read_bytes() == b"not-an-owned-directory"


@pytest.mark.parametrize(
    "fixed_target",
    (
        "data/normalized/toss_account_snapshot/latest.json",
        "data/state/toss_account_snapshot.json",
        "data/local/account_snapshots/kb_self.json",
        "data/local/account_snapshots/family_mirae_etf.json",
    ),
)
def test_account_snapshot_removal_rejects_fixed_target_with_directory_type(
    tmp_path, fixed_target,
):
    project = tmp_path / "project"
    project.mkdir()
    malformed = project / fixed_target
    malformed.mkdir(parents=True)

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert malformed.is_dir()


def test_account_snapshot_removal_revalidates_each_file_immediately_before_unlink(
    tmp_path, monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "data/normalized/toss_account_snapshot/latest.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"in-project-private")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside-private")
    escaped = outside.resolve()
    original_resolve = Path.resolve
    target_resolutions = 0

    def resolve_with_late_escape(path, *args, **kwargs):
        nonlocal target_resolutions
        if path == target:
            target_resolutions += 1
            if target_resolutions >= 3:
                return escaped
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_late_escape)

    with pytest.raises(
        AccountSnapshotRemovalError,
        match="ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED",
    ):
        remove_retained_account_snapshots(project)

    assert target_resolutions == 3
    assert target.read_bytes() == b"in-project-private"
    assert outside.read_bytes() == b"outside-private"
