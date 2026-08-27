from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import traceback
import hashlib
from uuid import uuid4


SCHEMA = "runtime-diagnostic/v1"
_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
_ID = re.compile(r"^[a-f0-9-]{16,64}$")
_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ARTIFACT = re.compile(r"^([^@]+)@sha256:([a-f0-9]{64})$")


@dataclass(frozen=True)
class RuntimeDiagnosticEvent:
    schema: str
    event_id: str
    occurred_at: str
    domain: str
    kind: str
    session_id: str
    run_id: str | None
    code: str
    stage: str
    exception_classes: tuple[str, ...]
    frames: tuple[str, ...]
    artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError("runtime diagnostic schema differs")
        if not _ID.fullmatch(self.event_id) or not _ID.fullmatch(self.session_id):
            raise ValueError("runtime diagnostic identity is invalid")
        if self.run_id is not None and not _ID.fullmatch(self.run_id):
            raise ValueError("runtime diagnostic run identity is invalid")
        for value in (self.domain, self.kind, self.code, self.stage):
            if not _TOKEN.fullmatch(value):
                raise ValueError("runtime diagnostic token is invalid")
        parsed = datetime.fromisoformat(self.occurred_at)
        if parsed.tzinfo is None:
            raise ValueError("runtime diagnostic timestamp must be aware")
        minimum_classes = 0 if self.kind == "LIFECYCLE" else 1
        if not minimum_classes <= len(self.exception_classes) <= 8:
            raise ValueError("runtime diagnostic exception chain is invalid")
        if any(not _CLASS.fullmatch(value) for value in self.exception_classes):
            raise ValueError("runtime diagnostic exception class is invalid")
        if len(self.frames) > 24:
            raise ValueError("runtime diagnostic frame list is too large")
        for frame in self.frames:
            path, separator, line = frame.partition(":")
            pure = Path(path)
            if (
                not separator or not line.isdigit() or pure.is_absolute()
                or ".." in pure.parts or pure.suffix != ".py"
            ):
                raise ValueError("runtime diagnostic frame is invalid")
        if len(self.artifacts) > 8:
            raise ValueError("runtime diagnostic artifact list is too large")
        for artifact in self.artifacts:
            matched = _ARTIFACT.fullmatch(artifact)
            if matched is None:
                raise ValueError("runtime diagnostic artifact is invalid")
            path = Path(matched.group(1))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("runtime diagnostic artifact path is unsafe")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "RuntimeDiagnosticEvent":
        if type(value) is not dict or set(value) != {
            "schema", "event_id", "occurred_at", "domain", "kind",
            "session_id", "run_id", "code", "stage", "exception_classes",
            "frames", "artifacts",
        }:
            raise ValueError("runtime diagnostic fields differ")
        classes = value["exception_classes"]
        frames = value["frames"]
        artifacts = value["artifacts"]
        if type(classes) is not list or type(frames) is not list or type(artifacts) is not list:
            raise ValueError("runtime diagnostic lists differ")
        scalar_keys = (
            "schema", "event_id", "occurred_at", "domain", "kind",
            "session_id", "code", "stage",
        )
        if any(type(value[key]) is not str for key in scalar_keys):
            raise ValueError("runtime diagnostic scalar fields differ")
        if value["run_id"] is not None and type(value["run_id"]) is not str:
            raise ValueError("runtime diagnostic run identity differs")
        if any(type(item) is not str for item in (*classes, *frames, *artifacts)):
            raise ValueError("runtime diagnostic list values differ")
        return cls(
            **{key: value[key] for key in scalar_keys},
            run_id=value["run_id"], exception_classes=tuple(classes),
            frames=tuple(frames), artifacts=tuple(artifacts),
        )


def new_session_id() -> str:
    return uuid4().hex


def artifact_identity(project_root: Path, path: Path) -> str | None:
    root = project_root.resolve()
    try:
        target = path.resolve(strict=True)
        relative = target.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return f"{relative}@sha256:{digest.hexdigest()}"


def lifecycle_event(*, domain: str, session_id: str, code: str, stage: str) -> RuntimeDiagnosticEvent:
    return RuntimeDiagnosticEvent(
        schema=SCHEMA, event_id=uuid4().hex,
        occurred_at=datetime.now(timezone.utc).isoformat(), domain=domain,
        kind="LIFECYCLE", session_id=session_id, run_id=None, code=code,
        stage=stage, exception_classes=(), frames=(),
        artifacts=(),
    )


def failure_event(
    *,
    project_root: Path,
    domain: str,
    kind: str,
    session_id: str,
    run_id: str | None,
    code: str,
    stage: str,
    error: BaseException,
    artifacts: tuple[str, ...] = (),
    now: datetime | None = None,
) -> RuntimeDiagnosticEvent:
    root = project_root.resolve()
    classes: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(classes) < 8:
        seen.add(id(current))
        classes.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    frames: list[str] = []
    for item in traceback.extract_tb(error.__traceback__):
        try:
            relative = Path(item.filename).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if relative.endswith(".py"):
            frames.append(f"{relative}:{item.lineno}")
    timestamp = now or datetime.now(timezone.utc)
    return RuntimeDiagnosticEvent(
        schema=SCHEMA,
        event_id=uuid4().hex,
        occurred_at=timestamp.astimezone(timezone.utc).isoformat(),
        domain=domain,
        kind=kind,
        session_id=session_id,
        run_id=run_id,
        code=code,
        stage=stage,
        exception_classes=tuple(classes),
        frames=tuple(frames[-24:]),
        artifacts=artifacts,
    )
