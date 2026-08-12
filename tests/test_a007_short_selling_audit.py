from __future__ import annotations

from datetime import date
import hashlib
import json
import os

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.manual import audit_a007_short_selling as cli
from stock_data.audit.a007_short_selling import (
    DatasetAuditPlan,
    audit_a007,
    render_markdown,
)
from stock_data.pipelines.short_selling_backfill import (
    ConservativeThrottle,
    RawResponse,
    plan_scopes,
    run_short_selling_batch,
)


DAY = date(2026, 8, 10)
NEXT_DAY = date(2026, 8, 11)


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
    for market in ("KOSPI", "KOSDAQ"):
        canonical = (
            tmp_path / "data/normalized/kr_equity_universe_daily" /
            f"market={market}/year=2026/data.parquet"
        )
        canonical.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": [DAY]}).to_parquet(canonical, index=False)
    throttle = ConservativeThrottle(
        min_interval_seconds=6, max_jitter_seconds=1,
        sleep_fn=lambda _: None, monotonic_fn=lambda: 0.0,
        jitter_fn=lambda *_: 0.1,
    )
    run_short_selling_batch(
        dataset="trading", trading_dates=(DAY,), max_business_calls=2,
        project_root=tmp_path, client_factory=_Client, throttle=throttle,
    )


def _plan(*days: date, statuses=("BATCH_COMPLETE",)) -> DatasetAuditPlan:
    scopes = plan_scopes("trading", tuple(days))
    return DatasetAuditPlan(
        dataset="trading", start=min(days), end=max(days), trading_dates=tuple(days),
        expected_scope_ids=tuple(scope.scope_id for scope in scopes),
        acceptable_terminal_statuses=statuses,
    )


def _audit(tmp_path, plan=None):
    return audit_a007(tmp_path, ("trading",), plans={"trading": plan or _plan(DAY)})


def _ledger_path(tmp_path):
    return next((tmp_path / "data/landing/pykrx/short_selling/runs").glob("*/call_ledger.jsonl"))


def _append(path, record):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def test_terminal_audit_is_read_only_and_separates_three_gates(tmp_path):
    _fixture(tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    report = _audit(tmp_path)
    after = {
        path.relative_to(tmp_path).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    result = report["datasets"][0]
    assert before == after
    assert report["status"] == "PASS"
    assert report["artifact_integrity"]["status"] == "PASS"
    assert report["completeness"]["status"] == "PASS"
    assert report["runtime_readiness"]["status"] == "PASS"
    assert result["normalized"]["exact_values"]["status"] == "PASS"
    assert result["normalized"]["exact_values"]["compared_rows"] == 2
    assert result["landing"]["validated_artifact_chains"] == 2
    assert "Artifact integrity: PASS" in render_markdown(report)


def test_partial_checkpoint_and_nonterminal_status_cannot_pass(tmp_path):
    _fixture(tmp_path)
    checkpoint = tmp_path / "data/state/kr_short_selling_trading_daily_v2.json"
    value = json.loads(checkpoint.read_text())
    value["status"] = "BATCH_LIMIT_REACHED"
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    report = _audit(tmp_path, _plan(DAY, NEXT_DAY))
    result = report["datasets"][0]
    codes = {item["code"] for item in result["completeness"]["findings"]}
    assert report["status"] == result["status"] == "INCOMPLETE"
    assert result["artifact_integrity"]["status"] == "PASS"
    assert {"PLANNED_SCOPES_MISSING", "CHECKPOINT_NOT_TERMINAL", "PLANNED_DATES_MISSING"} <= codes


def test_bidirectional_ledger_accounting_finds_orphans_and_duplicates(tmp_path):
    _fixture(tmp_path)
    path = _ledger_path(tmp_path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    response = next(item for item in records if item["event"] == "HTTP_RESPONSE")
    correlation = next(item for item in records if item["event"] == "SCOPE_HTTP_CORRELATED")
    _append(path, response)
    _append(path, correlation)
    _append(path, {**response, "raw_sequence": 99})
    _append(path, {**correlation, "raw_sequence": 100})
    report = _audit(tmp_path)
    codes = {item["code"] for item in report["artifact_integrity"]["findings"]}
    assert report["status"] == "FAIL"
    assert {
        "DUPLICATE_BUSINESS_HTTP_RESPONSE", "DUPLICATE_SCOPE_HTTP_CORRELATION",
        "ORPHAN_BUSINESS_HTTP_RESPONSE", "ORPHAN_SCOPE_HTTP_CORRELATION",
        "DUPLICATE_SCOPE_BUSINESS_CALL",
    } <= codes


def test_correlated_business_scope_without_checkpoint_is_not_terminal_integrity(tmp_path):
    _fixture(tmp_path)
    path = _ledger_path(tmp_path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    response = next(item for item in records if item["event"] == "HTTP_RESPONSE")
    correlation = next(item for item in records if item["event"] == "SCOPE_HTTP_CORRELATED")
    future_scope = plan_scopes("trading", (NEXT_DAY,))[0]
    _append(path, {**response, "raw_sequence": 99})
    _append(path, {
        **correlation, "raw_sequence": 99, "scope": future_scope.scope_id,
        "scope_sha256": hashlib.sha256(json.dumps({
            "dataset": future_scope.dataset, "scope_id": future_scope.scope_id,
            "market": future_scope.market, "start_date": future_scope.start_date,
            "end_date": future_scope.end_date, "metric": future_scope.metric,
            "params": future_scope.params,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    })
    result = _audit(tmp_path)["datasets"][0]
    assert result["status"] == "FAIL"
    assert result["ledger"]["uncheckpointed_scopes"]["count"] == 1
    assert any(
        item["code"] == "UNCHECKPOINTED_BUSINESS_SCOPE"
        for item in result["artifact_integrity"]["findings"]
    )


def test_reparse_counts_and_exact_normalized_values_fail_closed(tmp_path):
    _fixture(tmp_path)
    checkpoint = tmp_path / "data/state/kr_short_selling_trading_daily_v2.json"
    value = json.loads(checkpoint.read_text())
    value["completed"]["20260810_KOSPI"]["source_rows"] = 999
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    partition = (
        tmp_path / "data/normalized/kr_short_selling_trading_daily/"
        "market=KOSDAQ/year=2026/data.parquet"
    )
    frame = pd.read_parquet(partition)
    frame.loc[0, "short_volume"] = 4
    frame.to_parquet(partition, index=False)
    result = _audit(tmp_path)["datasets"][0]
    codes = {item["code"] for item in result["artifact_integrity"]["findings"]}
    assert result["status"] == "FAIL"
    assert "ARTIFACT_CHAIN_INVALID" in codes
    assert "NORMALIZED_VALUE_MISMATCH" in codes


def test_runtime_readiness_fails_for_active_lock_owner(tmp_path):
    _fixture(tmp_path)
    lock = tmp_path / "data/state/d_owned_krx_short_selling.lock"
    lock.write_text(json.dumps({
        "owner": "D", "run_id": "active", "pid": os.getpid(), "token": "test"
    }), encoding="utf-8")
    report = _audit(tmp_path)
    assert report["status"] == "NOT_READY"
    assert report["runtime_readiness"]["status"] == "FAIL"
    assert report["runtime_readiness"]["pid_status"] == "RUNNING"
    assert report["runtime_readiness"]["active_owner_process_count"] == 1


def test_scope_completed_must_be_exact_unique_and_consistent(tmp_path):
    _fixture(tmp_path)
    path = _ledger_path(tmp_path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    completion = next(item for item in records if item["event"] == "SCOPE_COMPLETED")
    _append(path, completion)
    result = _audit(tmp_path)["datasets"][0]
    assert result["status"] == "FAIL"
    assert any(
        item["code"] == "ARTIFACT_CHAIN_INVALID" and "SCOPE_COMPLETED is not exact unique" in item["detail"]
        for item in result["artifact_integrity"]["findings"]
    )


def test_scope_completed_classification_must_match_reparsed_landing(tmp_path):
    _fixture(tmp_path)
    path = _ledger_path(tmp_path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    for record in records:
        if record["event"] == "SCOPE_COMPLETED" and record["scope"] == "20260810_KOSPI":
            record["classification"] = "VALID_EMPTY"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    result = _audit(tmp_path)["datasets"][0]
    assert result["status"] == "FAIL"
    assert any(
        item["code"] == "ARTIFACT_CHAIN_INVALID" and "classification differs" in item["detail"]
        for item in result["artifact_integrity"]["findings"]
    )


def test_terminal_status_gate_can_be_explicitly_configured(tmp_path):
    _fixture(tmp_path)
    checkpoint = tmp_path / "data/state/kr_short_selling_trading_daily_v2.json"
    value = json.loads(checkpoint.read_text())
    value["status"] = "AUDITED_COMPLETE"
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    report = _audit(tmp_path, _plan(DAY, statuses=("AUDITED_COMPLETE",)))
    assert report["status"] == "PASS"


def test_orphan_body_and_sidecar_are_enumerated(tmp_path):
    _fixture(tmp_path)
    root = tmp_path / "data/landing/pykrx/short_selling/trading"
    (root / "orphan.json").write_text("{}", encoding="utf-8")
    (root / "ghost.json.provenance.json").write_text("{}", encoding="utf-8")
    result = _audit(tmp_path)["datasets"][0]
    assert result["status"] == "FAIL"
    assert result["landing"]["orphan_bodies"] == ["orphan.json"]
    assert result["landing"]["orphan_sidecars"] == ["ghost.json.provenance.json"]


def test_nan_and_infinity_are_both_rejected(tmp_path):
    _fixture(tmp_path)
    partition = (
        tmp_path / "data/normalized/kr_short_selling_trading_daily/"
        "market=KOSPI/year=2026/data.parquet"
    )
    frame = pd.read_parquet(partition)
    arrays = []
    for column in frame.columns:
        values = frame[column].tolist()
        if column == "short_volume_ratio":
            arrays.append(pa.array([float("nan")], type=pa.float64(), from_pandas=False))
        elif column == "short_trading_value_ratio":
            arrays.append(pa.array([float("inf")], type=pa.float64(), from_pandas=False))
        else:
            arrays.append(pa.array(values))
    pq.write_table(pa.Table.from_arrays(arrays, names=list(frame.columns)), partition)
    result = _audit(tmp_path)["datasets"][0]
    codes = {item["code"] for item in result["artifact_integrity"]["findings"]}
    assert {"NAN", "INFINITY"} <= codes
    assert result["normalized"]["non_finite"] == {
        "status": "FAIL", "infinity_count": 1, "nan_count": 1
    }


def test_cli_plan_is_explicit_and_output_write_requires_path(tmp_path, capsys):
    _fixture(tmp_path)
    arguments = [
        "--project-root", str(tmp_path), "--dataset", "trading",
        "--plan", "trading:2026-08-10:2026-08-10", "--format", "markdown",
    ]
    assert cli.main(arguments) == 0
    assert "# A007 Short-Selling Audit" in capsys.readouterr().out
    output = tmp_path / "reports/audit.md"
    assert cli.main([*arguments, "--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8").startswith("# A007 Short-Selling Audit")
