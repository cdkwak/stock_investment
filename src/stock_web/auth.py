"""Optional PIN authentication for remote web-dashboard readers."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PIN_RELATIVE_PATH = Path("data/local/web_pin.json")
SESSION_SECRET_RELATIVE_PATH = Path("data/local/web_session_secret")
PIN_SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000
MAX_ITERATIONS = 10_000_000
SESSION_COOKIE_NAME = "stock_web_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 10 * 60


def validate_pin(pin: str) -> None:
    """Reject values outside the user-facing 4–12 character contract."""
    if not 4 <= len(pin) <= 12 or "\n" in pin or "\r" in pin:
        raise ValueError("PIN은 4~12자여야 합니다.")


def pin_path(project_root: Path) -> Path:
    return project_root / PIN_RELATIVE_PATH


def pin_is_configured(project_root: Path) -> bool:
    return pin_path(project_root).is_file()


def set_pin(
    project_root: Path,
    pin: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
) -> None:
    """Hash and atomically store a dashboard PIN."""
    validate_pin(pin)
    if not 1 <= iterations <= MAX_ITERATIONS:
        raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    payload = {
        "scheme": PIN_SCHEME,
        "iterations": iterations,
        "salt": salt.hex(),
        "hash": digest.hex(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination = pin_path(project_root)
    _atomic_private_write(
        destination,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def clear_pin(project_root: Path) -> bool:
    """Remove the PIN configuration while retaining the installation secret."""
    try:
        pin_path(project_root).unlink()
    except FileNotFoundError:
        return False
    return True


def verify_pin(project_root: Path, candidate: str) -> bool:
    """Verify a candidate without exposing stored or submitted PIN material."""
    try:
        payload = json.loads(pin_path(project_root).read_text(encoding="utf-8"))
        if payload.get("scheme") != PIN_SCHEME:
            return False
        iterations = int(payload["iterations"])
        if not 1 <= iterations <= MAX_ITERATIONS:
            return False
        salt = bytes.fromhex(payload["salt"])
        expected = bytes.fromhex(payload["hash"])
        if len(salt) < 16 or len(expected) != hashlib.sha256().digest_size:
            return False
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256", candidate.encode("utf-8"), salt, iterations,
    )
    return hmac.compare_digest(actual, expected)


def create_session_cookie(project_root: Path, *, now: int | None = None) -> str:
    expiry = int(time.time() if now is None else now) + SESSION_MAX_AGE_SECONDS
    expiry_text = str(expiry)
    signature = hmac.new(
        _get_or_create_session_secret(project_root),
        expiry_text.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{expiry_text}.{signature}"


def verify_session_cookie(
    project_root: Path,
    value: str | None,
    *,
    now: int | None = None,
) -> bool:
    if not value:
        return False
    try:
        expiry_text, supplied_signature = value.split(".", 1)
        if not expiry_text.isascii() or not expiry_text.isdigit():
            return False
        expiry = int(expiry_text)
        secret = _read_session_secret(project_root)
        if secret is None:
            return False
    except (TypeError, ValueError):
        return False
    expected_signature = hmac.new(
        secret, expiry_text.encode("ascii"), hashlib.sha256,
    ).hexdigest()
    signature_valid = hmac.compare_digest(expected_signature, supplied_signature)
    return signature_valid and expiry > int(time.time() if now is None else now)


@dataclass
class PinFailureLimiter:
    """Small per-process, per-client lockout tracker for failed PIN attempts."""

    max_failures: int = MAX_FAILURES
    lockout_seconds: int = LOCKOUT_SECONDS
    _failures: dict[str, tuple[int, float]] = field(default_factory=dict)

    def is_locked(self, client_key: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        count, locked_until = self._failures.get(client_key, (0, 0.0))
        if locked_until > current:
            return True
        if locked_until:
            self._failures.pop(client_key, None)
        return False

    def record_failure(self, client_key: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        count, locked_until = self._failures.get(client_key, (0, 0.0))
        if locked_until > current:
            return True
        count = count + 1 if not locked_until else 1
        if count >= self.max_failures:
            self._failures[client_key] = (count, current + self.lockout_seconds)
            return True
        self._failures[client_key] = (count, 0.0)
        return False

    def reset(self, client_key: str) -> None:
        self._failures.pop(client_key, None)


def _get_or_create_session_secret(project_root: Path) -> bytes:
    existing = _read_session_secret(project_root)
    if existing is not None:
        return existing
    path = project_root / SESSION_SECRET_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _read_session_secret(project_root)
        if existing is None:
            raise ValueError("Invalid web session secret") from None
        return existing
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(secret.hex().encode("ascii") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _restrict_permissions(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return secret


def _read_session_secret(project_root: Path) -> bytes | None:
    path = project_root / SESSION_SECRET_RELATIVE_PATH
    try:
        raw = path.read_text(encoding="ascii").strip()
        secret = bytes.fromhex(raw)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return secret if len(secret) == 32 else None


def _atomic_private_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _restrict_permissions(temporary)
        os.replace(temporary, path)
        _restrict_permissions(path)
    finally:
        temporary.unlink(missing_ok=True)


def _restrict_permissions(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
