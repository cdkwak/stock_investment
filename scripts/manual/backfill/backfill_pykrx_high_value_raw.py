"""Checkpointed Landing-only acquisition for three approved pykrx candidates."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import random
import sys
import time
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pyarrow.parquet as pq
import requests
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.pilot.pykrx_fundamentals_pilot_support import (  # noqa: E402
    AUTH_ENDPOINT_PATHS, BUSINESS_ENDPOINT_PATH, AppendOnlyLedger, PilotStopped,
    assert_no_credentials, redact, shared_d_owned_krx_lock, write_bytes_atomic_new,
    write_json_atomic,
)

PYKRX_VERSION = "1.2.8"
LOCK_PATH = ROOT / "data/state/d_owned_krx_short_selling.lock"
STATE_ROOT = ROOT / "data/state/pykrx_high_value_raw"
LANDING_ROOT = ROOT / "data/landing/pykrx/high_value_raw"
CALENDAR_ROOT = ROOT / "data/derived/kr_market_breadth_daily"
MIN_INTERVAL_SECONDS = 2.75
JITTER_SECONDS = 0.25
HTTP_TIMEOUT_SECONDS = 30

CONFIG = {
    "kr_equity_foreign_ownership_daily": {
        "start": "20000105", "end": "20260812", "core": "market",
        "bld": "dbms/MDC/STAT/standard/MDCSTAT03701",
        "fields": ("ISU_SRT_CD", "LIST_SHRS", "FORN_HD_QTY", "FORN_SHR_RT", "FORN_ORD_LMT_QTY", "FORN_LMT_EXHST_RT"),
        "adopt": {
            "20000105": ("data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85", "response_03_foreign_all_20000105.json"),
            "20200102": ("data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85", "response_02_foreign_all_20200102.json"),
            "20260812": ("data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85", "response_01_foreign_all_recent.json"),
        },
    },
    "kr_equity_fundamental_daily": {
        "start": "20080103", "end": "20260812", "core": "market",
        "bld": "dbms/MDC/STAT/standard/MDCSTAT03501",
        "fields": ("ISU_SRT_CD", "ISU_ABBRV", "TDD_CLSPRC", "EPS", "PER", "BPS", "PBR", "DPS", "DVD_YLD"),
        "adopt": {
            "20080103": ("data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85", "response_06_equity_fundamental_all_20080103.json"),
            "20200102": ("data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85", "response_05_equity_fundamental_all_20200102.json"),
            "20260812": ("data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85", "response_04_equity_fundamental_all_recent.json"),
        },
    },
    "kr_etf_universe_daily": {
        "start": "20080102", "end": "20260812", "core": "etx",
        "bld": "dbms/MDC/STAT/standard/MDCSTAT04301",
        "fields": ("ISU_SRT_CD", "ISU_CD", "SECUGRP_ID", "ISU_ABBRV", "TDD_CLSPRC", "NAV", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "INVSTASST_NETASST_TOTAMT", "LIST_SHRS"),
        "adopt": {
            "20080102": ("data/landing/diagnostics/pykrx_etf_pilot/20260815T012527Z_616ef29c69954c9787b12f12c7d1fb1c", "response_02_market_source_coverage_20080102.json"),
            "20260810": ("data/landing/diagnostics/pykrx_etf_pilot/20260815T012527Z_616ef29c69954c9787b12f12c7d1fb1c", "response_01_market_recent_20260810.json"),
        },
    },
}

ETF_OHLCV_DATASET = "kr_etf_ohlcv_daily"
ETF_OHLCV_REQUIRED_FIELDS = (
    "ISU_SRT_CD", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC",
    "ACC_TRDVOL", "ACC_TRDVAL",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_credentials(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                if key.strip() in {"KRX_ID", "KRX_PW"}:
                    values[key.strip()] = value.strip().strip("\"'")
    # pykrx reads these names itself while creating its authenticated singleton.
    # Keep them process-only; never persist them in any artifact.
    for key in ("KRX_ID", "KRX_PW"):
        if not os.getenv(key) and values.get(key):
            os.environ[key] = values[key]
    return os.getenv("KRX_ID", ""), os.getenv("KRX_PW", "")


def trading_dates(start: str, end: str) -> list[str]:
    root = CALENDAR_ROOT.resolve(strict=True)
    if root.is_symlink() or ROOT.resolve() not in root.parents:
        raise PilotStopped("CALENDAR_PATH_UNSAFE")
    dates: set[str] = set()
    for path in sorted(root.rglob("*.parquet")):
        if path.is_symlink() or not path.is_file():
            raise PilotStopped("CALENDAR_FILE_UNSAFE")
        for value in pq.read_table(path, columns=["date"]).column("date").to_pylist():
            token = value.strftime("%Y%m%d")
            if start <= token <= end:
                dates.add(token)
    result = sorted(dates)
    if not result or result[0] != start or result[-1] != end:
        raise PilotStopped(f"CALENDAR_COVERAGE_MISMATCH:{result[:1]}:{result[-1:]}")
    return result


def plan_sha(dataset: str, dates: list[str]) -> str:
    cfg = CONFIG[dataset]
    body = json.dumps({"dataset": dataset, "bld": cfg["bld"], "dates": dates}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _analyze_body(dataset: str, date: str, body: bytes) -> dict[str, object]:
    if body.lstrip().startswith(b"<"):
        raise PilotStopped(f"HTML_OR_RESTRICTION:{date}")
    try:
        payload = json.loads(body)
    except Exception as error:
        raise PilotStopped(f"NON_JSON:{date}") from error
    if not isinstance(payload, dict) or payload.get("_error_code") or payload.get("error") or payload.get("errors"):
        raise PilotStopped(f"SOURCE_ERROR:{date}")
    rows = payload.get("output")
    if not isinstance(rows, list) or not rows:
        raise PilotStopped(f"ANOMALOUS_EMPTY:{date}")
    required = set(CONFIG[dataset]["fields"])
    positions: dict[str, list[int]] = {}
    for ordinal, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not required.issubset(row):
            raise PilotStopped(f"SCHEMA_CHANGE:{date}")
        key = str(row.get("ISU_SRT_CD", "")).strip()
        if not key:
            raise PilotStopped(f"DUPLICATE_OR_EMPTY_KEY:{date}:{key}")
        positions.setdefault(key, []).append(ordinal)
    duplicate_groups: list[dict[str, object]] = []
    for key, ordinals in positions.items():
        if len(ordinals) == 1:
            continue
        if dataset != "kr_equity_fundamental_daily":
            raise PilotStopped(f"DUPLICATE_OR_EMPTY_KEY:{date}:{key}")
        group = [rows[index - 1] for index in ordinals]
        all_fields = set().union(*(row.keys() for row in group))
        differing = sorted(
            field for field in all_fields
            if len({json.dumps(row.get(field), ensure_ascii=False, sort_keys=True) for row in group}) > 1
        )
        duplicate_groups.append({
            "entity_key": key,
            "source_row_ordinals": ordinals,
            "classification": "CONFLICTING_PROVIDER_DUPLICATE" if differing else "EXACT_PROVIDER_DUPLICATE",
            "differing_fields": differing,
        })
    return {
        "rows": len(rows),
        "body_sha256": sha256_bytes(body),
        "row_identity": "source_row_ordinal_1_based",
        "provider_duplicate_groups": duplicate_groups,
    }


def _validate_body(dataset: str, date: str, body: bytes) -> tuple[int, str]:
    analysis = _analyze_body(dataset, date, body)
    return int(analysis["rows"]), str(analysis["body_sha256"])


def _analyze_etf_ohlcv_body(date: str, body: bytes) -> dict[str, object]:
    """Validate the OHLCV projection without changing the shared source bytes."""
    analysis = _analyze_body("kr_etf_universe_daily", date, body)
    payload = json.loads(body)
    rows = payload["output"]
    for row in rows:
        if not set(ETF_OHLCV_REQUIRED_FIELDS).issubset(row):
            raise PilotStopped(f"ETF_OHLCV_SCHEMA_CHANGE:{date}")
    return analysis


def _atomic_provenance(path: Path, payload: Mapping[str, object]) -> None:
    write_json_atomic(path, payload)


def _verify_completed(dataset: str, checkpoint: dict[str, object]) -> None:
    completed = checkpoint.get("completed")
    if not isinstance(completed, dict):
        raise PilotStopped("CHECKPOINT_COMPLETED_INVALID")
    for date, record in completed.items():
        if not isinstance(record, dict):
            raise PilotStopped(f"CHECKPOINT_RECORD_INVALID:{date}")
        path = (ROOT / str(record.get("body_path", ""))).resolve(strict=True)
        if ROOT.resolve() not in path.parents or path.is_symlink() or sha256_file(path) != record.get("body_sha256"):
            raise PilotStopped(f"LANDING_CHECKPOINT_MISMATCH:{date}")
        analysis = _analyze_body(dataset, str(date), path.read_bytes())
        rows, digest = int(analysis["rows"]), str(analysis["body_sha256"])
        if rows != record.get("rows") or digest != record.get("body_sha256"):
            raise PilotStopped(f"LANDING_REPARSE_MISMATCH:{date}")
        if record.get("provider_duplicate_groups", []) != analysis["provider_duplicate_groups"]:
            raise PilotStopped(f"LANDING_DUPLICATE_METADATA_MISMATCH:{date}")
        provenance = record.get("provenance_path")
        if provenance:
            prov = (ROOT / str(provenance)).resolve(strict=True)
            if ROOT.resolve() not in prov.parents or prov.is_symlink():
                raise PilotStopped(f"PROVENANCE_PATH_UNSAFE:{date}")
            data = json.loads(prov.read_text(encoding="utf-8"))
            if data.get("market_date") != date or data.get("response_sha256") != digest:
                raise PilotStopped(f"PROVENANCE_MISMATCH:{date}")
            if data.get("provider_duplicate_groups", []) != analysis["provider_duplicate_groups"]:
                raise PilotStopped(f"PROVENANCE_DUPLICATE_METADATA_MISMATCH:{date}")


def _adopt_pilots(dataset: str, checkpoint: dict[str, object], dates: set[str]) -> None:
    completed = checkpoint["completed"]
    for date, (parent, name) in CONFIG[dataset]["adopt"].items():
        if date not in dates or date in completed:
            continue
        path = (ROOT / parent / name).resolve(strict=True)
        analysis = _analyze_body(dataset, date, path.read_bytes())
        completed[date] = {"classification": "ADOPTED_RETAINED_PILOT", "rows": analysis["rows"], "body_path": path.relative_to(ROOT).as_posix(), "body_sha256": analysis["body_sha256"], "provenance_path": None, "row_identity": analysis["row_identity"], "provider_duplicate_groups": analysis["provider_duplicate_groups"]}


def _recover_orphans(dataset: str, checkpoint: dict[str, object], run_dir: Path) -> None:
    """Adopt only a fully bound success orphan; never repeat its provider call."""
    completed = checkpoint["completed"]
    ledger_path = run_dir / "call_ledger.jsonl"
    ledger = []
    if ledger_path.exists():
        ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
    for day_dir in sorted(run_dir.glob("date=*")):
        date = day_dir.name.removeprefix("date=")
        if date in completed:
            continue
        body_path, provenance_path = day_dir / "response.json", day_dir / "provenance.json"
        if not body_path.is_file() or body_path.is_symlink():
            raise PilotStopped(f"ORPHAN_TOPOLOGY_INVALID:{date}")
        body = body_path.read_bytes()
        analysis = _analyze_body(dataset, date, body)
        rows, digest = int(analysis["rows"]), str(analysis["body_sha256"])
        matches = [item for item in ledger if item.get("event") == "HTTP_RESPONSE" and item.get("market_date") == date and item.get("response_sha256") == digest and item.get("status_code") == 200]
        if len(matches) != 1:
            raise PilotStopped(f"ORPHAN_LEDGER_MISMATCH:{date}")
        if provenance_path.exists():
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            if provenance.get("market_date") != date or provenance.get("response_sha256") != digest or provenance.get("rows") != rows:
                raise PilotStopped(f"ORPHAN_PROVENANCE_MISMATCH:{date}")
        else:
            _atomic_provenance(provenance_path, {"schema": "pykrx_high_value_raw.provenance.v1", "dataset": dataset, "source": "KRX_via_pykrx", "pykrx_version": PYKRX_VERSION, "bld": CONFIG[dataset]["bld"], "market_date": date, "captured_at_utc": matches[0].get("recorded_at_utc"), "request_payload": expected_payload(dataset, date), "response_path": body_path.relative_to(ROOT).as_posix(), "response_bytes": len(body), "response_sha256": digest, "rows": rows, "retry_count": 0, "normalized_writes": False, "recovered_from_ledger": True, "row_identity": analysis["row_identity"], "provider_duplicate_groups": analysis["provider_duplicate_groups"]})
        classification = "RECOVERED_SUCCESS_ORPHAN"
        if analysis["provider_duplicate_groups"]:
            classification = "RECOVERED_PROVIDER_DUPLICATE_OBSERVATION"
        completed[date] = {"classification": classification, "rows": rows, "body_path": body_path.relative_to(ROOT).as_posix(), "body_sha256": digest, "provenance_path": provenance_path.relative_to(ROOT).as_posix(), "row_identity": analysis["row_identity"], "provider_duplicate_groups": analysis["provider_duplicate_groups"]}
        checkpoint["business_calls"] = max(int(checkpoint.get("business_calls", 0)), int(matches[0].get("business_sequence", 0)))


def etf_ohlcv_plan_sha(dates: list[str], source_plan_sha256: str) -> str:
    body = json.dumps({
        "dataset": ETF_OHLCV_DATASET,
        "source_dataset": "kr_etf_universe_daily",
        "source_plan_sha256": source_plan_sha256,
        "bld": CONFIG["kr_etf_universe_daily"]["bld"],
        "dates": dates,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def reuse_etf_universe_raw_for_ohlcv() -> dict[str, object]:
    """Create only verifiable OHLCV references to completed ETF universe Raw.

    MDCSTAT04301 is a single full-market/date response containing both dated ETF
    identity and daily OHLCV fields. Copying or requesting it again would create
    duplicate provider captures, so this state retains exact upstream paths.
    """
    source_dataset = "kr_etf_universe_daily"
    source_path = STATE_ROOT / f"{source_dataset}.json"
    if not source_path.is_file():
        raise PilotStopped("ETF_UNIVERSE_STATE_MISSING")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if source.get("dataset") != source_dataset or source.get("status") != "RAW_BACKFILL_COMPLETE":
        raise PilotStopped("ETF_UNIVERSE_RAW_NOT_COMPLETE")
    completed = source.get("completed")
    expected = source.get("expected_dates")
    source_plan = source.get("plan_sha256")
    if not isinstance(completed, dict) or not isinstance(expected, int) or len(completed) != expected or not isinstance(source_plan, str):
        raise PilotStopped("ETF_UNIVERSE_CHECKPOINT_INVALID")
    dates = sorted(completed)
    target_plan = etf_ohlcv_plan_sha(dates, source_plan)
    target_path = STATE_ROOT / f"{ETF_OHLCV_DATASET}.json"
    if target_path.exists():
        target = json.loads(target_path.read_text(encoding="utf-8"))
        if target.get("dataset") != ETF_OHLCV_DATASET or target.get("plan_sha256") != target_plan:
            raise PilotStopped("ETF_OHLCV_CHECKPOINT_IDENTITY_MISMATCH")
        existing = target.get("completed")
        if not isinstance(existing, dict):
            raise PilotStopped("ETF_OHLCV_CHECKPOINT_INVALID")
    else:
        target = {
            "schema": "pykrx_high_value_raw.shared_source_reference.v1",
            "dataset": ETF_OHLCV_DATASET,
            "status": "CREATED",
            "plan_sha256": target_plan,
            "source_dataset": source_dataset,
            "source_plan_sha256": source_plan,
            "source_operation": CONFIG[source_dataset]["bld"],
            "expected_dates": expected,
            "completed": {},
            "business_calls": 0,
            "source_business_calls": source.get("business_calls"),
            "retry_count": 0,
            "normalized_writes": False,
            "raw_bytes_copied": False,
        }
        existing = target["completed"]
    for date in dates:
        record = completed[date]
        if not isinstance(record, dict):
            raise PilotStopped(f"ETF_UNIVERSE_RECORD_INVALID:{date}")
        body_path = (ROOT / str(record.get("body_path", ""))).resolve(strict=True)
        if ROOT.resolve() not in body_path.parents or body_path.is_symlink():
            raise PilotStopped(f"ETF_UNIVERSE_RAW_PATH_UNSAFE:{date}")
        body = body_path.read_bytes()
        analysis = _analyze_etf_ohlcv_body(date, body)
        digest = str(analysis["body_sha256"])
        if digest != record.get("body_sha256") or int(analysis["rows"]) != record.get("rows"):
            raise PilotStopped(f"ETF_UNIVERSE_RAW_REPARSE_MISMATCH:{date}")
        provenance_path = record.get("provenance_path")
        if provenance_path:
            provenance = (ROOT / str(provenance_path)).resolve(strict=True)
            if ROOT.resolve() not in provenance.parents or provenance.is_symlink():
                raise PilotStopped(f"ETF_UNIVERSE_PROVENANCE_PATH_UNSAFE:{date}")
            provenance_data = json.loads(provenance.read_text(encoding="utf-8"))
            if provenance_data.get("market_date") != date or provenance_data.get("response_sha256") != digest:
                raise PilotStopped(f"ETF_UNIVERSE_PROVENANCE_MISMATCH:{date}")
        existing[date] = {
            "classification": "ADOPTED_SHARED_SOURCE_RAW",
            "rows": int(analysis["rows"]),
            "body_path": body_path.relative_to(ROOT).as_posix(),
            "body_sha256": digest,
            "provenance_path": provenance_path,
            "source_dataset": source_dataset,
            "source_operation": CONFIG[source_dataset]["bld"],
            "row_identity": analysis["row_identity"],
            "provider_duplicate_groups": analysis["provider_duplicate_groups"],
        }
    target["status"] = "RAW_BACKFILL_COMPLETE"
    target["updated_at_utc"] = utc_now()
    write_json_atomic(target_path, target)
    return summary(target, LANDING_ROOT / source_dataset / f"plan={source_plan}")


class Capture:
    def __init__(self, *, dataset: str, run_dir: Path, ledger: AppendOnlyLedger, secrets: tuple[str, ...], expected_calls: int):
        self.dataset, self.run_dir, self.ledger, self.secrets = dataset, run_dir, ledger, secrets
        self.expected_calls, self.count, self.current_date = expected_calls, 0, "auth"
        self.auth_count = 0
        self.original = None
        self.pending = None

    def __enter__(self):
        self.original = requests.Session.request
        requests.Session.request = lambda session, method, url, **kw: self._request(session, method, url, **kw)
        return self

    def __exit__(self, *_):
        requests.Session.request = self.original

    def _request(self, session, method, url, **kwargs):
        path = urlsplit(str(url)).path
        auth = path in AUTH_ENDPOINT_PATHS
        if not auth and path != BUSINESS_ENDPOINT_PATH:
            raise PilotStopped(f"UNAPPROVED_ENDPOINT:{path}")
        if not auth:
            if self.pending is not None or self.count >= self.expected_calls:
                raise PilotStopped("BUSINESS_CALL_BOUNDARY")
            actual = kwargs.get("data")
            expected = expected_payload(self.dataset, self.current_date)
            if str(method).upper() != "POST" or kwargs.get("params") is not None or kwargs.get("json") is not None or not isinstance(actual, dict) or {str(k): str(v) for k, v in actual.items()} != expected:
                raise PilotStopped(f"REQUEST_SCOPE_MISMATCH:{self.current_date}")
            kwargs["allow_redirects"] = False
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        response = self.original(session, method, url, **kwargs)
        if auth:
            self.auth_count += 1
            self.ledger.append("HTTP_RESPONSE", authentication=True, raw_sequence=self.auth_count, method=str(method).upper(), url=urlunsplit((urlsplit(str(url)).scheme, urlsplit(str(url)).netloc, path, "", "")), status_code=response.status_code, response_bytes=len(response.content), retry_count=0)
        if not auth:
            self.count += 1
            body = response.content
            assert_no_credentials(body, self.secrets)
            day_dir = self.run_dir / f"date={self.current_date}"
            body_path = day_dir / "response.json"
            write_bytes_atomic_new(body_path, body)
            record = {"event": "HTTP_RESPONSE", "market_date": self.current_date, "business_sequence": self.count, "method": "POST", "url": urlunsplit((urlsplit(str(url)).scheme, urlsplit(str(url)).netloc, path, "", "")), "status_code": response.status_code, "response_bytes": len(body), "response_sha256": sha256_bytes(body), "body_path": body_path.relative_to(ROOT).as_posix(), "retry_count": 0}
            self.ledger.append(**record)
            self.pending = (response, body_path)
        if response.status_code in {403, 429}:
            raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}:{self.current_date}")
        if response.status_code != 200:
            raise PilotStopped(f"HTTP_STATUS:{response.status_code}:{self.current_date}")
        return response

    def take(self):
        if self.pending is None:
            raise PilotStopped("BUSINESS_RESPONSE_MISSING")
        value, self.pending = self.pending, None
        return value


def expected_payload(dataset: str, date: str) -> dict[str, str]:
    bld = str(CONFIG[dataset]["bld"])
    if dataset == "kr_equity_foreign_ownership_daily":
        return {"searchType": "1", "mktId": "ALL", "trdDd": date, "isuLmtRto": "0", "bld": bld}
    if dataset == "kr_equity_fundamental_daily":
        return {"mktId": "ALL", "trdDd": date, "bld": bld}
    return {"trdDd": date, "bld": bld}


def _operation(core, bld: str):
    found = []
    for value in vars(core).values():
        if isinstance(value, type):
            try:
                item = value()
                if getattr(item, "bld", None) == bld:
                    found.append(item)
            except Exception:
                pass
    if len(found) != 1:
        raise PilotStopped(f"CORE_OPERATION_UNRESOLVED:{bld}:{len(found)}")
    return found[0]


def execute(dataset: str, operation, date: str) -> None:
    if dataset == "kr_equity_foreign_ownership_daily":
        operation.fetch(date, "ALL", 0)
    elif dataset == "kr_equity_fundamental_daily":
        operation.fetch(date, "ALL")
    else:
        operation.fetch(date)


def run(dataset: str, *, env_file: Path, max_calls: int | None = None, sleep_fn=time.sleep) -> dict[str, object]:
    if dataset == ETF_OHLCV_DATASET:
        if max_calls is not None:
            raise PilotStopped("ETF_OHLCV_SHARED_RAW_DOES_NOT_SUPPORT_MAX_CALLS")
        return reuse_etf_universe_raw_for_ohlcv()
    if dataset not in CONFIG:
        raise PilotStopped("DATASET_NOT_APPROVED")
    if importlib.metadata.version("pykrx") != PYKRX_VERSION:
        raise PilotStopped("PYKRX_VERSION_MISMATCH")
    user, password = _load_credentials(env_file)
    if not user or not password:
        raise PilotStopped("KRX_CREDENTIALS_MISSING")
    secrets = (user, password)
    dates = trading_dates(str(CONFIG[dataset]["start"]), str(CONFIG[dataset]["end"]))
    digest = plan_sha(dataset, dates)
    state_path = STATE_ROOT / f"{dataset}.json"
    run_dir = LANDING_ROOT / dataset / f"plan={digest}"
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger = AppendOnlyLedger(run_dir / "call_ledger.jsonl", secrets=secrets)
    if state_path.exists():
        checkpoint = json.loads(state_path.read_text(encoding="utf-8"))
        if checkpoint.get("dataset") != dataset or checkpoint.get("plan_sha256") != digest:
            raise PilotStopped("CHECKPOINT_IDENTITY_MISMATCH")
    else:
        checkpoint = {"schema": "pykrx_high_value_raw.v1", "dataset": dataset, "status": "CREATED", "plan_sha256": digest, "expected_dates": len(dates), "completed": {}, "business_calls": 0, "retry_count": 0, "normalized_writes": False, "updated_at_utc": utc_now()}
    _verify_completed(dataset, checkpoint)
    _adopt_pilots(dataset, checkpoint, set(dates))
    _recover_orphans(dataset, checkpoint, run_dir)
    checkpoint["updated_at_utc"] = utc_now()
    write_json_atomic(state_path, checkpoint)
    remaining = [date for date in dates if date not in checkpoint["completed"]]
    if max_calls is not None:
        remaining = remaining[:max_calls]
    if not remaining:
        checkpoint["status"] = "RAW_BACKFILL_COMPLETE" if len(checkpoint["completed"]) == len(dates) else "BATCH_LIMIT_REACHED"
        write_json_atomic(state_path, checkpoint)
        return summary(checkpoint, run_dir)
    run_id = f"{dataset}:{digest}:{uuid4().hex}"
    with shared_d_owned_krx_lock(LOCK_PATH, run_id=run_id):
        with Capture(dataset=dataset, run_dir=run_dir, ledger=ledger, secrets=secrets, expected_calls=len(remaining)) as capture:
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    from pykrx.website.comm import get_session
                    if CONFIG[dataset]["core"] == "etx":
                        from pykrx.website.krx.etx import core
                    else:
                        from pykrx.website.krx.market import core
                session = get_session()
                if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
                    raise PilotStopped("AUTHENTICATION_FAILED")
                retry = Retry(total=0, connect=0, read=0, redirect=0, status=0, other=0)
                for adapter in session.session.adapters.values():
                    adapter.max_retries = retry
                operation = _operation(core, str(CONFIG[dataset]["bld"]))
                last_started = None
                for date in remaining:
                    now = time.monotonic()
                    if last_started is not None:
                        sleep_fn(max(0, MIN_INTERVAL_SECONDS + random.uniform(-JITTER_SECONDS, JITTER_SECONDS) - (now - last_started)))
                    last_started = time.monotonic()
                    capture.current_date = date
                    ledger.append("SCOPE_STARTED", market_date=date, dataset=dataset, bld=CONFIG[dataset]["bld"])
                    execute(dataset, operation, date)
                    response, body_path = capture.take()
                    analysis = _analyze_body(dataset, date, response.content)
                    rows, body_hash = int(analysis["rows"]), str(analysis["body_sha256"])
                    provenance_path = body_path.with_name("provenance.json")
                    provenance = {"schema": "pykrx_high_value_raw.provenance.v1", "dataset": dataset, "source": "KRX_via_pykrx", "pykrx_version": PYKRX_VERSION, "bld": CONFIG[dataset]["bld"], "market_date": date, "captured_at_utc": utc_now(), "request_payload": expected_payload(dataset, date), "response_path": body_path.relative_to(ROOT).as_posix(), "response_bytes": len(response.content), "response_sha256": body_hash, "rows": rows, "retry_count": 0, "normalized_writes": False, "row_identity": analysis["row_identity"], "provider_duplicate_groups": analysis["provider_duplicate_groups"]}
                    _atomic_provenance(provenance_path, provenance)
                    checkpoint["completed"][date] = {"classification": "SUCCESS_WITH_PROVIDER_DUPLICATE" if analysis["provider_duplicate_groups"] else "SUCCESS", "rows": rows, "body_path": body_path.relative_to(ROOT).as_posix(), "body_sha256": body_hash, "provenance_path": provenance_path.relative_to(ROOT).as_posix(), "row_identity": analysis["row_identity"], "provider_duplicate_groups": analysis["provider_duplicate_groups"]}
                    checkpoint["business_calls"] = int(checkpoint.get("business_calls", 0)) + 1
                    checkpoint["status"] = "IN_PROGRESS"
                    checkpoint["updated_at_utc"] = utc_now()
                    write_json_atomic(state_path, checkpoint)
                    ledger.append("SCOPE_COMPLETED", market_date=date, rows=rows, response_sha256=body_hash)
            except Exception as error:
                checkpoint.update(status="STOPPED", stop_type=type(error).__name__, stop_reason=redact(str(error), secrets), updated_at_utc=utc_now())
                write_json_atomic(state_path, checkpoint)
                ledger.append("RUN_STOPPED", dataset=dataset, error_type=type(error).__name__, error=redact(str(error), secrets))
                raise
    checkpoint["status"] = "RAW_BACKFILL_COMPLETE" if len(checkpoint["completed"]) == len(dates) else "BATCH_LIMIT_REACHED"
    checkpoint["updated_at_utc"] = utc_now()
    write_json_atomic(state_path, checkpoint)
    for artifact in run_dir.rglob("*"):
        if artifact.is_file():
            assert_no_credentials(artifact.read_bytes(), secrets)
    return summary(checkpoint, run_dir)


def summary(checkpoint: Mapping[str, object], run_dir: Path) -> dict[str, object]:
    completed = checkpoint["completed"]
    return {"dataset": checkpoint["dataset"], "status": checkpoint["status"], "expected_dates": checkpoint["expected_dates"], "completed_dates": len(completed), "business_calls": checkpoint["business_calls"], "rows": sum(int(v["rows"]) for v in completed.values()), "coverage": [min(completed) if completed else None, max(completed) if completed else None], "landing": run_dir.relative_to(ROOT).as_posix()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, choices=tuple(CONFIG) + (ETF_OHLCV_DATASET,))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--confirm-live-raw-backfill", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_raw_backfill:
        parser.error("--confirm-live-raw-backfill is required")
    print(json.dumps(run(args.dataset, env_file=args.env_file, max_calls=args.max_calls), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
