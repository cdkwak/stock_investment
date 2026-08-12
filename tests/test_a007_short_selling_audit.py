from __future__ import annotations

from datetime import date
import hashlib
import json

import pandas as pd

from scripts.manual import audit_a007_short_selling as cli
from stock_data.audit.a007_short_selling import audit_a007, render_markdown
from stock_data.pipelines.short_selling_backfill import (
    ConservativeThrottle,
    RawResponse,
    run_short_selling_batch,
)


def _body(symbol: str) -> bytes:
    return json.dumps({"OutBlock_1": [{
        "ISU_CD": symbol, "ISU_ABBRV": "name", "SECUGRP_NM": "stock",
        "CVSRTSELL_TRDVOL": "3", "UPTICKRULE_APPL_TRDVOL": "2",
        "UPTICKRULE_EXCPT_TRDVOL": "1", "ACC_TRDVOL": "30", "TRDVOL_WT": "10",
        "CVSRTSELL_TRDVAL": "300", "UPTICKRULE_APPL_TRDVAL": "200",
        "UPTICKRULE_EXCPT_TRDVAL": "100", "ACC_TRDVAL": "3000", "TRDVAL_WT": "10",
    }]}, separators=(",", ":")).encode()


class _Client:
    def __init__(self, ledger):
        self.ledger = ledger
        self.raw_count = 0
        self.responses = iter((_body("005930"), _body("035720")))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def fetch(self, scope):
        self.raw_count += 1
        body = next(self.responses)
        self.ledger.append(
            "HTTP_RESPONSE", raw_sequence=self.raw_count, method="POST",
            url="https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            status_code=200, elapsed_ms=1, response_bytes=len(body),
            authentication=False, response_sha256=hashlib.sha256(body).hexdigest(),
        )
        return RawResponse(200, body, "application/json", self.raw_count)


def _fixture(tmp_path):
    throttle = ConservativeThrottle(
        min_interval_seconds=6, max_jitter_seconds=1,
        sleep_fn=lambda _: None, monotonic_fn=lambda: 0.0,
        jitter_fn=lambda *_: 0.1,
    )
    run_short_selling_batch(
        dataset="trading", trading_dates=(date(2026, 8, 10),),
        max_business_calls=2, project_root=tmp_path,
        client_factory=_Client, throttle=throttle,
    )


def test_exact_audit_is_read_only_deterministic_and_reports_unavailable_followons(tmp_path):
    _fixture(tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    first = audit_a007(tmp_path)
    second = audit_a007(tmp_path)
    after = {
        path.relative_to(tmp_path).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }

    assert first == second
    assert before == after
    assert first["status"] == "PASS"
    trading, balance, investor = first["datasets"]
    assert trading["status"] == "PASS"
    assert trading["checkpoint"]["completed_scopes"] == 2
    assert trading["landing"]["valid_checkpoint_artifacts"] == 2
    assert trading["ledger"]["unique_business_responses"] == 2
    assert trading["ledger"]["http_status_counts"] == {"200": 2}
    assert trading["ledger"]["all_runs_context"]["raw_http_responses"] == 2
    assert trading["normalized"]["rows"] == 2
    assert trading["normalized"]["primary_key"]["status"] == "PASS"
    assert trading["normalized"]["coverage"] == {
        "first": "2026-08-10", "last": "2026-08-10"
    }
    assert balance["status"] == investor["status"] == "NOT_AVAILABLE"
    assert first["runtime"] == {
        "network_calls": 0,
        "lock": {
            "path": "data/state/d_owned_krx_short_selling.lock",
            "exists": False, "status": "RELEASED",
        },
    }
    assert "| trading | PASS | 2 | 2 |" in render_markdown(first)


def test_audit_fails_closed_on_landing_hash_and_streamed_pk_duplicate(tmp_path):
    _fixture(tmp_path)
    body = tmp_path / "data/landing/pykrx/short_selling/trading/20260810_KOSPI.json"
    body.write_bytes(body.read_bytes() + b" ")
    partition = (
        tmp_path / "data/normalized/kr_short_selling_trading_daily/"
        "market=KOSPI/year=2026/data.parquet"
    )
    frame = pd.read_parquet(partition)
    pd.concat([frame, frame], ignore_index=True).to_parquet(partition, index=False)

    report = audit_a007(tmp_path, ("trading",))
    result = report["datasets"][0]
    codes = {finding["code"] for finding in result["findings"]}
    assert report["status"] == result["status"] == "FAIL"
    assert "LANDING_PROVENANCE_INVALID" in codes
    assert "PRIMARY_KEY_DUPLICATE" in codes
    assert "CHECKPOINT_PARQUET_ROW_MISMATCH" in codes
    assert result["normalized"]["primary_key"]["duplicates_after_first"] == 1


def test_cli_writes_only_when_output_is_explicit(tmp_path, capsys):
    _fixture(tmp_path)
    assert cli.main([
        "--project-root", str(tmp_path), "--dataset", "trading", "--format", "markdown"
    ]) == 0
    assert "# A007 Short-Selling Audit" in capsys.readouterr().out
    assert not (tmp_path / "audit.md").exists()

    output = tmp_path / "audit.md"
    assert cli.main([
        "--project-root", str(tmp_path), "--dataset", "trading",
        "--format", "markdown", "--output", str(output),
    ]) == 0
    assert output.read_text(encoding="utf-8").startswith("# A007 Short-Selling Audit")
