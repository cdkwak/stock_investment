from __future__ import annotations

from datetime import date
import hashlib
import json
import os

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.manual.audit import audit_a007_short_selling as cli
from stock_data.audit.a007_short_selling import (
    DatasetAuditPlan,
    audit_a007,
    render_markdown,
)
from stock_data.pipelines.short_selling_backfill import (
    ConservativeThrottle,
    MINIMUM_SOURCE_DATES,
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


class _GeneratedClient:
    def __init__(self, ledger, body_factory):
        self.ledger = ledger
        self.body_factory = body_factory
        self.raw_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def fetch(self, scope):
        self.raw_count += 1
        body = self.body_factory(scope)
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


def _balance_body(scope) -> bytes:
    symbol = "005930" if scope.market == "KOSPI" else "035720"
    return json.dumps({"OutBlock_1": [{
        "ISU_CD": symbol, "ISU_ABBRV": "name", "BAL_QTY": "3",
        "LIST_SHRS": "30", "BAL_AMT": "300", "MKTCAP": "3000", "BAL_RTO": "10",
    }]}, separators=(",", ":")).encode()


def _investor_body_factory(days):
    def body(scope):
        start = date.fromisoformat(
            f"{scope.start_date[:4]}-{scope.start_date[4:6]}-{scope.start_date[6:]}"
        )
        end = date.fromisoformat(
            f"{scope.end_date[:4]}-{scope.end_date[4:6]}-{scope.end_date[6:]}"
        )
        rows = [{
            "TRD_DD": day.strftime("%Y/%m/%d"), "STR_CONST_VAL1": "1",
            "STR_CONST_VAL2": "2", "STR_CONST_VAL3": "3", "STR_CONST_VAL4": "4",
            "STR_CONST_VAL5": "10",
        } for day in days if start <= day <= end]
        return json.dumps({"OutBlock_1": rows}, separators=(",", ":")).encode()
    return body


def _collect_generated(tmp_path, dataset, days, body_factory):
    scopes = plan_scopes(dataset, tuple(days))
    throttle = ConservativeThrottle(
        min_interval_seconds=6, max_jitter_seconds=1,
        sleep_fn=lambda _: None, monotonic_fn=lambda: 0.0,
        jitter_fn=lambda *_: 0.1,
    )
    run_short_selling_batch(
        dataset=dataset, trading_dates=tuple(days), max_business_calls=len(scopes),
        project_root=tmp_path,
        client_factory=lambda ledger: _GeneratedClient(ledger, body_factory),
        throttle=throttle,
    )


def _canonical_default_fixture(tmp_path, dataset):
    start = MINIMUM_SOURCE_DATES[dataset]
    days = []
    for year in range(start.year, 2027):
        day = start if year == start.year else date(year, 1, 2)
        if year == 2026:
            day = DAY
        days.append(day)
        for market in ("KOSPI", "KOSDAQ"):
            path = (
                tmp_path / "data/normalized/kr_equity_universe_daily" /
                f"market={market}/year={year}/data.parquet"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"date": [day]}).to_parquet(path, index=False)
    return tuple(days)


def _plan(*days: date, statuses=("BATCH_COMPLETE",)) -> DatasetAuditPlan:
    return _dataset_plan("trading", *days, statuses=statuses)


def _dataset_plan(dataset, *days: date, statuses=("BATCH_COMPLETE",)) -> DatasetAuditPlan:
    scopes = plan_scopes(dataset, tuple(days))
    return DatasetAuditPlan(
        dataset=dataset, start=min(days), end=max(days), trading_dates=tuple(days),
        expected_scope_ids=tuple(scope.scope_id for scope in scopes),
        acceptable_terminal_statuses=statuses,
    )


def _audit(tmp_path, plan=None, *, process_pids=()):
    return audit_a007(
        tmp_path, ("trading",), plans={"trading": plan or _plan(DAY)},
        collector_process_probe=lambda: (tuple(process_pids), "TEST_COMMAND_LINE"),
    )


def _ledger_path(tmp_path):
    return next((tmp_path / "data/landing/pykrx/short_selling/runs").glob("*/call_ledger.jsonl"))


def _append(path, record):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def _make_diagnostic_recovery(tmp_path, scope_id="20260810_KOSPI"):
    original = _ledger_path(tmp_path)
    records = [json.loads(line) for line in original.read_text().splitlines()]
    sidecar = (
        tmp_path / "data/landing/pykrx/short_selling/trading" /
        f"{scope_id}.json.provenance.json"
    )
    provenance = json.loads(sidecar.read_text())
    sequence = provenance["raw_sequence"]
    response = next(
        item for item in records
        if item["event"] == "HTTP_RESPONSE" and item.get("raw_sequence") == sequence
    )
    correlation = next(
        item for item in records
        if item["event"] == "SCOPE_HTTP_CORRELATED" and item.get("raw_sequence") == sequence
    )
    records = [
        item for item in records
        if not (
            (item["event"] in {"HTTP_RESPONSE", "SCOPE_HTTP_CORRELATED"}
             and item.get("raw_sequence") == sequence)
            or (item["event"] == "SCOPE_COMPLETED" and item.get("scope") == scope_id)
        )
    ]
    original.write_text("".join(json.dumps(item) + "\n" for item in records))

    diagnostic_run = "diagnostic_recovery_test"
    diagnostic = (
        tmp_path / "data/landing/diagnostics/a007_balance_recovery" /
        diagnostic_run / "call_ledger.jsonl"
    )
    diagnostic.parent.mkdir(parents=True)
    sentinel_response = {
        **response, "run_id": diagnostic_run,
        "recorded_at_utc": "2026-08-10T00:00:01+00:00",
    }
    sentinel_correlation = {
        **correlation, "run_id": diagnostic_run,
        "recorded_at_utc": "2026-08-10T00:00:02+00:00",
    }
    diagnostic.write_text(
        json.dumps(sentinel_response) + "\n" + json.dumps(sentinel_correlation) + "\n"
    )
    provenance.update({
        "run_id": diagnostic_run,
        "ledger_relative_path": diagnostic.relative_to(tmp_path).as_posix(),
    })
    sidecar.write_text(json.dumps(provenance))

    adoption_run = "production_adoption_test"
    adoption = (
        tmp_path / "data/landing/pykrx/short_selling/runs" /
        adoption_run / "call_ledger.jsonl"
    )
    adoption.parent.mkdir(parents=True)
    adoption_records = [
        {
            "event": "SCOPE_STARTED", "dataset": "trading", "scope": scope_id,
            "run_id": adoption_run, "recorded_at_utc": "2026-08-10T00:00:03+00:00",
        },
        {
            "event": "SCOPE_RECOVERED_WITHOUT_REQUEST", "scope": scope_id,
            "run_id": adoption_run, "recorded_at_utc": "2026-08-10T00:00:04+00:00",
        },
        {
            "event": "SCOPE_COMPLETED", "scope": scope_id, "run_id": adoption_run,
            "classification": "SUCCESS", "normalized_rows": 1,
            "recorded_at_utc": "2026-08-10T00:00:05+00:00",
        },
    ]
    adoption.write_text("".join(json.dumps(item) + "\n" for item in adoption_records))
    return sidecar, diagnostic, adoption


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


def test_diagnostic_sentinel_is_adopted_without_a_second_business_request(tmp_path):
    _fixture(tmp_path)
    _make_diagnostic_recovery(tmp_path)
    report = _audit(tmp_path)
    result = report["datasets"][0]
    assert report["status"] == "PASS"
    assert result["checkpoint"]["declared_normalized_rows"] == 2
    assert result["normalized"]["rows"] == 2
    assert result["normalized"]["exact_values"] == {
        "status": "PASS", "compared_rows": 2, "mismatched_partitions": 0,
        "missing_partitions": [], "orphan_partitions": [],
        "method": "EXACT_SORTED_STREAM_COMPARISON",
    }
    assert result["landing"]["diagnostic_sentinel_recoveries"] == 1


@pytest.mark.parametrize("fault", ["outside", "wrong_parent", "wrong_name", "missing"])
def test_diagnostic_declared_ledger_path_fails_closed(tmp_path, fault):
    _fixture(tmp_path)
    sidecar, diagnostic, _ = _make_diagnostic_recovery(tmp_path)
    provenance = json.loads(sidecar.read_text())
    if fault == "outside":
        provenance["ledger_relative_path"] = "../outside/call_ledger.jsonl"
    elif fault == "wrong_parent":
        provenance["run_id"] = "different_run"
    elif fault == "wrong_name":
        wrong = diagnostic.with_name("other.jsonl")
        diagnostic.replace(wrong)
        provenance["ledger_relative_path"] = wrong.relative_to(tmp_path).as_posix()
    else:
        diagnostic.unlink()
    sidecar.write_text(json.dumps(provenance))
    result = _audit(tmp_path)["datasets"][0]
    assert result["status"] == "FAIL"
    assert any(item["code"] == "ARTIFACT_CHAIN_INVALID"
               for item in result["artifact_integrity"]["findings"])


@pytest.mark.parametrize("fault", ["duplicate_response", "duplicate_correlation", "scope_hash"])
def test_diagnostic_ledger_chain_must_be_exact_unique(tmp_path, fault):
    _fixture(tmp_path)
    _, diagnostic, _ = _make_diagnostic_recovery(tmp_path)
    records = [json.loads(line) for line in diagnostic.read_text().splitlines()]
    if fault == "duplicate_response":
        records.append(dict(records[0]))
    elif fault == "duplicate_correlation":
        records.append(dict(records[1]))
    else:
        records[1]["scope_sha256"] = "0" * 64
    diagnostic.write_text("".join(json.dumps(item) + "\n" for item in records))
    result = _audit(tmp_path)["datasets"][0]
    assert result["status"] == "FAIL"
    assert any(item["code"] == "ARTIFACT_CHAIN_INVALID"
               for item in result["artifact_integrity"]["findings"])


@pytest.mark.parametrize("fault", ["missing_recovery", "duplicate_recovery", "adoption_request"])
def test_diagnostic_adoption_must_be_unique_and_request_free(tmp_path, fault):
    _fixture(tmp_path)
    _, diagnostic, adoption = _make_diagnostic_recovery(tmp_path)
    records = [json.loads(line) for line in adoption.read_text().splitlines()]
    recovery = next(item for item in records if item["event"] == "SCOPE_RECOVERED_WITHOUT_REQUEST")
    if fault == "missing_recovery":
        records.remove(recovery)
    elif fault == "duplicate_recovery":
        records.append(dict(recovery))
    else:
        sentinel = [json.loads(line) for line in diagnostic.read_text().splitlines()]
        response, correlation = sentinel
        records.extend([
            {**response, "run_id": records[0]["run_id"], "raw_sequence": 99},
            {**correlation, "run_id": records[0]["run_id"], "raw_sequence": 99},
        ])
    adoption.write_text("".join(json.dumps(item) + "\n" for item in records))
    result = _audit(tmp_path)["datasets"][0]
    assert result["status"] == "FAIL"
    assert any(item["code"] == "ARTIFACT_CHAIN_INVALID"
               for item in result["artifact_integrity"]["findings"])


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


def test_malformed_or_unknown_http_response_classification_is_rejected(tmp_path):
    _fixture(tmp_path)
    path = _ledger_path(tmp_path)
    base = next(
        json.loads(line) for line in path.read_text().splitlines()
        if json.loads(line)["event"] == "HTTP_RESPONSE"
    )
    missing_auth = {key: value for key, value in base.items() if key != "authentication"}
    _append(path, {**missing_auth, "raw_sequence": 91})
    _append(path, {**base, "raw_sequence": 92, "url": "https://example.invalid/unknown"})
    _append(path, {**base, "raw_sequence": 93, "authentication": True})
    report = _audit(tmp_path)
    assert report["status"] == "FAIL"
    assert report["artifact_integrity"]["ledger"]["status"] == "FAIL"
    assert any(
        item["code"] == "LEDGER_INVALID_RECORD" and item["detail"] == "3"
        for item in report["artifact_integrity"]["findings"]
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
    report = _audit(tmp_path, process_pids=(os.getpid(),))
    assert report["status"] == "NOT_READY"
    assert report["runtime_readiness"]["status"] == "FAIL"
    assert report["runtime_readiness"]["pid_status"] == "RUNNING"
    assert report["runtime_readiness"]["owner_pid_matches_collector"] is True


def test_runtime_readiness_is_unknown_when_command_lines_cannot_be_verified(tmp_path):
    _fixture(tmp_path)
    report = audit_a007(
        tmp_path, ("trading",), plans={"trading": _plan(DAY)},
        collector_process_probe=lambda: (None, "CIM_COMMAND_LINE_FAILED"),
    )
    assert report["status"] == "UNKNOWN"
    assert report["runtime_readiness"]["status"] == "UNKNOWN"


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


def test_cli_plan_is_explicit_and_output_write_requires_path(tmp_path, capsys, monkeypatch):
    _fixture(tmp_path)
    real_audit = cli.audit_a007
    monkeypatch.setattr(
        cli, "audit_a007",
        lambda root, selected, plans: real_audit(
            root, selected, plans=plans,
            collector_process_probe=lambda: ((), "TEST_COMMAND_LINE"),
        ),
    )
    arguments = [
        "--project-root", str(tmp_path), "--dataset", "trading",
        "--plan", "trading:2026-08-10:2026-08-10", "--format", "markdown",
    ]
    assert cli.main(arguments) == 0
    assert "# A007 Short-Selling Audit" in capsys.readouterr().out
    output = tmp_path / "reports/audit.md"
    assert cli.main([*arguments, "--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8").startswith("# A007 Short-Selling Audit")


def test_balance_terminal_exact_values_and_default_plan_incomplete(tmp_path):
    boundary = date(2016, 6, 30)
    _collect_generated(tmp_path, "balance", (boundary,), _balance_body)
    exact = audit_a007(
        tmp_path, ("balance",), plans={"balance": _dataset_plan("balance", boundary)},
        collector_process_probe=lambda: ((), "TEST_COMMAND_LINE"),
    )
    item = exact["datasets"][0]
    assert exact["status"] == item["status"] == "PASS"
    assert item["checkpoint"]["completed_scopes"] == 2
    assert item["normalized"]["rows"] == 2
    assert item["normalized"]["exact_values"]["status"] == "PASS"

    default_root = tmp_path / "default"
    planned = _canonical_default_fixture(default_root, "balance")
    _collect_generated(default_root, "balance", (planned[0],), _balance_body)
    incomplete = audit_a007(
        default_root, ("balance",),
        collector_process_probe=lambda: ((), "TEST_COMMAND_LINE"),
    )
    result = incomplete["datasets"][0]
    assert incomplete["status"] == result["status"] == "INCOMPLETE"
    assert result["artifact_integrity"]["status"] == "PASS"
    assert result["completeness"]["missing_scopes"]["count"] > 0


def test_investor_chunk_ranges_exact_and_default_plan_incomplete(tmp_path):
    days = (date(2020, 1, 2), date(2022, 1, 4))
    assert len(plan_scopes("investor", days)) == 8  # two bounded chunks x 2 markets x 2 metrics
    _collect_generated(tmp_path, "investor", days, _investor_body_factory(days))
    exact = audit_a007(
        tmp_path, ("investor",), plans={"investor": _dataset_plan("investor", *days)},
        collector_process_probe=lambda: ((), "TEST_COMMAND_LINE"),
    )
    item = exact["datasets"][0]
    assert exact["status"] == item["status"] == "PASS"
    assert item["checkpoint"]["completed_scopes"] == 8
    assert item["normalized"]["rows"] == 40
    assert item["normalized"]["exact_values"] == {
        "status": "PASS", "compared_rows": 40, "mismatched_partitions": 0,
        "missing_partitions": [], "orphan_partitions": [],
        "method": "EXACT_SORTED_STREAM_COMPARISON",
    }

    default_root = tmp_path / "default"
    planned = _canonical_default_fixture(default_root, "investor")
    _collect_generated(
        default_root, "investor", (planned[0],), _investor_body_factory((planned[0],))
    )
    incomplete = audit_a007(
        default_root, ("investor",),
        collector_process_probe=lambda: ((), "TEST_COMMAND_LINE"),
    )
    result = incomplete["datasets"][0]
    assert incomplete["status"] == result["status"] == "INCOMPLETE"
    assert result["artifact_integrity"]["status"] == "PASS"
    assert result["completeness"]["missing_scopes"]["count"] > 0
    assert result["completeness"]["missing_dates"]["count"] > 0
