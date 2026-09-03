from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from stock_web.auth import (
    PIN_SCHEME,
    SESSION_MAX_AGE_SECONDS,
    PinFailureLimiter,
    clear_pin,
    create_session_cookie,
    pin_is_configured,
    set_pin,
    verify_pin,
    verify_session_cookie,
)
from tests.unit.web import new_temp_root


def test_pin_file_round_trip_and_schema() -> None:
    root = new_temp_root()
    sample_pin = "2468"

    set_pin(root, sample_pin, iterations=1_000)

    path = root / "data/local/web_pin.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.keys() == {"scheme", "iterations", "salt", "hash", "updated_at"}
    assert payload["scheme"] == PIN_SCHEME
    assert payload["iterations"] == 1_000
    assert sample_pin not in path.read_text(encoding="utf-8")
    assert pin_is_configured(root)
    assert verify_pin(root, sample_pin)
    assert not verify_pin(root, "1357")
    assert clear_pin(root)
    assert not pin_is_configured(root)
    assert not clear_pin(root)


@pytest.mark.parametrize("value", ["123", "1234567890123", "ab\ncd"])
def test_pin_length_contract_is_enforced(value: str) -> None:
    with pytest.raises(ValueError, match="4~12"):
        set_pin(new_temp_root(), value, iterations=1_000)


def test_malformed_pin_file_fails_closed() -> None:
    root = new_temp_root()
    path = root / "data/local/web_pin.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"scheme":"unknown"}\n', encoding="utf-8")

    assert pin_is_configured(root)
    assert not verify_pin(root, "2468")


def test_session_cookie_round_trip_expiry_and_tamper() -> None:
    root = new_temp_root()
    cookie = create_session_cookie(root, now=1_000)

    assert verify_session_cookie(root, cookie, now=1_001)
    assert not verify_session_cookie(root, cookie + "0", now=1_001)
    assert not verify_session_cookie(root, cookie, now=1_000 + SESSION_MAX_AGE_SECONDS)
    assert cookie.count(".") == 1
    secret_path = root / "data/local/web_session_secret"
    assert secret_path.is_file()
    assert len(bytes.fromhex(secret_path.read_text(encoding="ascii").strip())) == 32
    if os.name != "nt":
        assert secret_path.stat().st_mode & 0o777 == 0o600


def test_failure_limiter_locks_on_fifth_failure_for_ten_minutes() -> None:
    limiter = PinFailureLimiter()
    for attempt in range(4):
        assert not limiter.record_failure("100.86.222.47", now=float(attempt))
    assert limiter.record_failure("100.86.222.47", now=4.0)
    assert limiter.is_locked("100.86.222.47", now=603.9)
    assert not limiter.is_locked("100.86.222.47", now=604.0)


def test_cli_set_and_clear_round_trip() -> None:
    root = new_temp_root()
    repository = Path(__file__).parents[3]
    script = repository / "scripts/manual/web_pin.py"
    sample_pin = "cli-8642"
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"

    set_result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(root),
            "set",
            "--pin-stdin",
        ],
        cwd=repository,
        env=environment,
        input=f"{sample_pin}\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert set_result.returncode == 0
    assert sample_pin not in set_result.stdout + set_result.stderr
    assert verify_pin(root, sample_pin)

    clear_result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(root), "clear"],
        cwd=repository,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert clear_result.returncode == 0
    assert not pin_is_configured(root)
