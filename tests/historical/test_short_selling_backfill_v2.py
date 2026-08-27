from datetime import date
import hashlib
import json
from pathlib import Path
from datetime import timedelta

import pytest

from scripts.manual.backfill import backfill_pykrx_short_selling as cli
from stock_data.pipelines.short_selling_backfill import (
    AppendOnlyRedactedLedger,
    ConservativeThrottle,
    RawResponse,
    AuthenticatedPykrxRawClient,
    ShortSellingCollectionStopped,
    ShortSellingResumeError,
    _write_landing_provenance_new,
    _scope_sha256,
    plan_scopes,
    run_short_selling_batch,
)


def encoded(rows):
    return json.dumps({"OutBlock_1": rows}, separators=(",", ":")).encode()


def trading_response(symbol="005930"):
    return encoded([{
        "ISU_CD": symbol, "ISU_ABBRV": "name", "SECUGRP_NM": "stock",
        "CVSRTSELL_TRDVOL": "3", "UPTICKRULE_APPL_TRDVOL": "2",
        "UPTICKRULE_EXCPT_TRDVOL": "1", "ACC_TRDVOL": "30", "TRDVOL_WT": "10",
        "CVSRTSELL_TRDVAL": "300", "UPTICKRULE_APPL_TRDVAL": "200",
        "UPTICKRULE_EXCPT_TRDVAL": "100", "ACC_TRDVAL": "3000", "TRDVAL_WT": "10",
    }])


class FakeClient:
    def __init__(self, responses, seen, ledger=None, *, enter_error=False):
        self.responses = iter(responses)
        self.seen = seen
        self.raw_count = 0
        self.enter_error = enter_error
        self.ledger = ledger

    def __enter__(self):
        if self.enter_error:
            raise AssertionError("client must not be entered")
        return self

    def __exit__(self, *args):
        return None

    def fetch(self, scope):
        self.seen.append(scope.scope_id)
        self.raw_count += 1
        response = next(self.responses)
        response = RawResponse(
            response.status_code, response.content, response.content_type, self.raw_count
        )
        if self.ledger is not None:
            self.ledger.append(
                "HTTP_RESPONSE", raw_sequence=self.raw_count, method="POST",
                url="https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
                status_code=response.status_code, elapsed_ms=1,
                response_bytes=len(response.content), authentication=False,
                response_sha256=hashlib.sha256(response.content).hexdigest(),
            )
        return response


def no_sleep_throttle():
    ticks = iter([0.0, 0.0, 20.0, 20.0, 40.0, 40.0])
    return ConservativeThrottle(
        min_interval_seconds=8, max_jitter_seconds=1,
        sleep_fn=lambda _: None, monotonic_fn=lambda: next(ticks), jitter_fn=lambda *_: 0.5,
    )


def test_adaptive_throttle_uses_default_four_to_five_second_window_and_endpoint_override():
    throttle = ConservativeThrottle(
        min_interval_seconds=4.5,
        max_jitter_seconds=0.5,
        endpoint_policies={"restricted": (8.0, 0.0)},
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
        jitter_fn=lambda *_: 0.5,
    )
    assert throttle.min_interval_seconds == 4.5
    assert throttle.max_jitter_seconds == 0.5
    with pytest.raises(ValueError, match="base>=4"):
        ConservativeThrottle(min_interval_seconds=3.99, max_jitter_seconds=1.0)

    ticks = iter([0.0, 0.0, 3.5, 4.0, 10.0, 10.0])
    observed = []
    policy = ConservativeThrottle(
        min_interval_seconds=4.5, max_jitter_seconds=0.5,
        endpoint_policies={"restricted": (8.0, 0.0)},
        sleep_fn=observed.append, monotonic_fn=lambda: next(ticks), jitter_fn=lambda low, high: low,
    )
    policy.wait("ordinary")
    policy.wait("ordinary")
    policy.wait("restricted")
    assert observed == [0.5, 2.0]


def test_cli_exposes_explicit_adaptive_throttle_parameters():
    args = cli.build_parser().parse_args([
        "--project-root", ".",
        "--min-interval-seconds", "4.5",
        "--max-jitter-seconds", "1.0",
    ])
    assert args.min_interval_seconds == 4.5
    assert args.max_jitter_seconds == 1.0


def run(tmp_path, responses, seen, *, max_calls, enter_error=False):
    return run_short_selling_batch(
        dataset="trading", trading_dates=(date(2026, 8, 10),),
        max_business_calls=max_calls, project_root=tmp_path,
        client_factory=lambda ledger: FakeClient(
            responses, seen, ledger, enter_error=enter_error
        ),
        throttle=no_sleep_throttle(),
    )


def test_plan_is_full_market_date_order_and_investor_chunks_are_bounded():
    scopes = plan_scopes("trading", (date(2026, 8, 6), date(2026, 8, 7)))
    assert [scope.scope_id for scope in scopes] == [
        "20260806_KOSPI", "20260806_KOSDAQ", "20260807_KOSPI", "20260807_KOSDAQ"
    ]
    investor = plan_scopes("investor", (date(2024, 1, 2), date(2026, 1, 1), date(2026, 1, 2)))
    assert all(
        (date.fromisoformat(scope.end_date[:4] + "-" + scope.end_date[4:6] + "-" + scope.end_date[6:])
         - date.fromisoformat(scope.start_date[:4] + "-" + scope.start_date[4:6] + "-" + scope.start_date[6:])).days <= 730
        for scope in investor
    )
    sixty_dates = tuple(date(2026, 1, 2) + timedelta(days=index) for index in range(61))
    sixty = plan_scopes(
        "investor", sixty_dates, investor_max_trading_dates=60,
    )
    assert len(sixty) == 8  # two chunks × KOSPI/KOSDAQ × volume/value
    assert {scope.start_date for scope in sixty[:4]} == {"20260102"}
    assert {scope.end_date for scope in sixty[:4]} == {"20260302"}
    with pytest.raises(ValueError, match="positive"):
        plan_scopes("investor", sixty_dates, investor_max_trading_dates=0)


def test_call_cap_order_landing_first_checkpoint_and_exact_resume(tmp_path):
    seen = []
    first = run(tmp_path, [RawResponse(200, trading_response())], seen, max_calls=1)
    assert first.requested_business_calls == 1
    assert seen == ["20260810_KOSPI"]
    checkpoint = json.loads(first.checkpoint_path.read_text())
    assert checkpoint["status"] == "BATCH_LIMIT_REACHED"
    landing = tmp_path / "data/landing/pykrx/short_selling/trading/20260810_KOSPI.json"
    assert landing.read_bytes() == trading_response()

    second = run(tmp_path, [RawResponse(200, trading_response("035720"))], seen, max_calls=1)
    assert second.requested_business_calls == 1
    assert seen == ["20260810_KOSPI", "20260810_KOSDAQ"]
    assert json.loads(second.checkpoint_path.read_text())["status"] == "BATCH_COMPLETE"
    third = run(tmp_path, [], seen, max_calls=1, enter_error=True)
    assert third.requested_business_calls == 0
    assert seen == ["20260810_KOSPI", "20260810_KOSDAQ"]


def test_disjoint_ranges_share_checkpoint_but_complete_independently(tmp_path):
    def collect(days, responses, seen, *, enter_error=False):
        return run_short_selling_batch(
            dataset="trading", trading_dates=days, max_business_calls=2,
            project_root=tmp_path,
            client_factory=lambda ledger: FakeClient(
                responses, seen, ledger, enter_error=enter_error
            ),
            throttle=no_sleep_throttle(),
        )

    earlier = (date(2026, 8, 7),)
    later = (date(2026, 8, 10),)
    seen = []
    first = collect(
        earlier,
        [RawResponse(200, trading_response()), RawResponse(200, trading_response("035720"))],
        seen,
    )
    assert json.loads(first.checkpoint_path.read_text())["status"] == "BATCH_COMPLETE"
    second = collect(
        later,
        [RawResponse(200, trading_response()), RawResponse(200, trading_response("035720"))],
        seen,
    )
    checkpoint = json.loads(second.checkpoint_path.read_text())
    assert checkpoint["status"] == "BATCH_COMPLETE"
    assert set(checkpoint["completed"]) == {
        "20260807_KOSPI", "20260807_KOSDAQ", "20260810_KOSPI", "20260810_KOSDAQ"
    }
    reconciled = collect(earlier, [], seen, enter_error=True)
    assert reconciled.previously_completed_scopes == 2
    assert reconciled.requested_business_calls == 0


def test_orphan_landing_is_recovered_atomically_without_repeat(tmp_path):
    orphan = tmp_path / "data/landing/pykrx/short_selling/trading/20260810_KOSPI.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(trading_response())
    scope = plan_scopes("trading", (date(2026, 8, 10),))[0]
    run_id = "20260811T000000Z_11111111111111111111111111111111"
    ledger_path = (
        tmp_path / "data/landing/pykrx/short_selling/runs" / run_id / "call_ledger.jsonl"
    )
    ledger = AppendOnlyRedactedLedger(ledger_path, run_id=run_id)
    ledger.append(
        "HTTP_RESPONSE", raw_sequence=1, method="POST",
        url="https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        status_code=200, response_bytes=len(trading_response()),
        response_sha256=hashlib.sha256(trading_response()).hexdigest(),
        authentication=False,
    )
    ledger.append(
        "SCOPE_HTTP_CORRELATED", raw_sequence=1, scope=scope.scope_id,
        scope_sha256=_scope_sha256(scope),
    )
    _write_landing_provenance_new(
        orphan, scope, RawResponse(200, trading_response(), "application/json", 1),
        run_id=run_id, ledger_path=ledger_path, project_root=tmp_path,
    )
    seen = []
    result = run(
        tmp_path, [RawResponse(200, trading_response("035720"))], seen, max_calls=1
    )
    assert result.recovered_scopes == 1
    assert seen == ["20260810_KOSDAQ"]
    checkpoint = json.loads(result.checkpoint_path.read_text())
    assert len(checkpoint["completed"]) == 2


def test_completed_checkpoint_requires_matching_normalized_artifact(tmp_path):
    seen = []
    run(
        tmp_path,
        [RawResponse(200, trading_response()), RawResponse(200, trading_response("035720"))],
        seen, max_calls=2,
    )
    partition = (
        tmp_path / "data/normalized/kr_short_selling_trading_daily/"
        "market=KOSPI/year=2026/data.parquet"
    )
    partition.unlink()
    with pytest.raises(ShortSellingResumeError, match="normalized partition is missing"):
        run(tmp_path, [], seen, max_calls=1, enter_error=True)


def test_anomalous_empty_is_landed_then_stops_without_zero_fill(tmp_path):
    seen = []
    with pytest.raises(ShortSellingCollectionStopped, match="ANOMALOUS_VALID_EMPTY"):
        run(tmp_path, [RawResponse(200, encoded([]))], seen, max_calls=1)
    path = tmp_path / "data/landing/pykrx/short_selling/trading/20260810_KOSPI.json"
    assert path.read_bytes() == encoded([])
    assert not (tmp_path / "data/normalized/kr_short_selling_trading_daily").exists()


def test_403_stops_after_preserving_exact_body(tmp_path):
    with pytest.raises(ShortSellingCollectionStopped, match="403"):
        run(tmp_path, [RawResponse(403, b"restricted")], [], max_calls=1)
    assert (tmp_path / "data/landing/pykrx/short_selling/trading/20260810_KOSPI.json").read_bytes() == b"restricted"


def test_429_success_shaped_body_can_never_be_recovered_as_success(tmp_path):
    valid_shape = trading_response()
    with pytest.raises(ShortSellingCollectionStopped, match="429"):
        run(tmp_path, [RawResponse(429, valid_shape)], [], max_calls=1)
    normalized = tmp_path / "data/normalized/kr_short_selling_trading_daily"
    assert not normalized.exists()
    with pytest.raises(ShortSellingResumeError, match="status mismatch"):
        run(tmp_path, [RawResponse(200, valid_shape)], [], max_calls=1)
    assert not normalized.exists()


@pytest.mark.parametrize("mode", ["missing", "forged"])
def test_missing_or_forged_ledger_correlation_fails_closed(tmp_path, mode):
    run(tmp_path, [RawResponse(200, trading_response())], [], max_calls=1)
    landing = tmp_path / "data/landing/pykrx/short_selling/trading/20260810_KOSPI.json"
    evidence = json.loads(
        landing.with_name(f"{landing.name}.provenance.json").read_text()
    )
    ledger = tmp_path / Path(evidence["ledger_relative_path"])
    if mode == "missing":
        ledger.unlink()
        message = "ledger is missing"
    else:
        records = [json.loads(line) for line in ledger.read_text().splitlines()]
        for record in records:
            if record.get("event") == "HTTP_RESPONSE":
                record["response_sha256"] = "0" * 64
        ledger.write_text("".join(json.dumps(record) + "\n" for record in records))
        message = "HTTP ledger correlation mismatch"
    with pytest.raises(ShortSellingResumeError, match=message):
        run(tmp_path, [], [], max_calls=1, enter_error=True)


def test_arbitrary_valid_shaped_landing_without_provenance_is_not_adopted(tmp_path):
    path = tmp_path / "data/landing/pykrx/short_selling/trading/20260810_KOSPI.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(trading_response())
    with pytest.raises(ShortSellingResumeError, match="no durable HTTP provenance"):
        run(tmp_path, [RawResponse(200, trading_response())], [], max_calls=1)
    assert not (tmp_path / "data/normalized/kr_short_selling_trading_daily").exists()


def test_current_run_cannot_normalize_without_client_http_ledger_record(tmp_path):
    with pytest.raises(ShortSellingResumeError, match="no unique ledger"):
        run_short_selling_batch(
            dataset="trading", trading_dates=(date(2026, 8, 10),),
            max_business_calls=1, project_root=tmp_path,
            client_factory=lambda _: FakeClient([RawResponse(200, trading_response())], []),
            throttle=no_sleep_throttle(),
        )
    assert not (tmp_path / "data/normalized/kr_short_selling_trading_daily").exists()


def test_investor_missing_canonical_date_stops_instead_of_zero_fill(tmp_path):
    row = {"TRD_DD": "2026/08/06", **{f"STR_CONST_VAL{i}": "0" for i in range(1, 6)}}
    with pytest.raises(ShortSellingCollectionStopped, match="DATE_COVERAGE"):
        run_short_selling_batch(
            dataset="investor", trading_dates=(date(2026, 8, 6), date(2026, 8, 7)),
            max_business_calls=1, project_root=tmp_path,
            client_factory=lambda ledger: FakeClient(
                [RawResponse(200, encoded([row]))], [], ledger
            ),
            throttle=no_sleep_throttle(),
        )
    landing = tmp_path / (
        "data/landing/pykrx/short_selling/investor/"
        "20260806_20260807_KOSPI_volume.json"
    )
    assert landing.read_bytes() == encoded([row])


def test_ledger_redacts_keys_and_literal_credentials(tmp_path):
    ledger = AppendOnlyRedactedLedger(tmp_path / "ledger.jsonl", secrets=("literal-secret",))
    ledger.append("ERROR", password="literal-secret", error="KRX_ID=literal-secret")
    value = (tmp_path / "ledger.jsonl").read_text()
    assert "literal-secret" not in value
    assert value.endswith("\n")


def test_cli_has_no_implicit_live_mode(tmp_path):
    with pytest.raises(SystemExit, match="disabled"):
        cli.main(["--project-root", str(tmp_path), "--dataset", "trading"])


def test_raw_client_fetch_uses_only_direct_endpoint_once_without_public_fallback(tmp_path):
    ledger = AppendOnlyRedactedLedger(tmp_path / "ledger.jsonl")
    client = AuthenticatedPykrxRawClient(
        project_root=tmp_path, ledger=ledger, max_raw_calls=5
    )
    calls = []

    class Session:
        is_authenticated = True
        def is_valid(self):
            return True
        def post(self, url, data, timeout):
            calls.append((url, data.copy(), timeout))
            return client._request(self, "POST", url, data=data, timeout=timeout)

    def original(session, method, url, **kwargs):
        return type("Response", (), {
                "status_code": 200, "content": trading_response(),
                "headers": {"Content-Type": "application/json"},
            })()

    client._original = original
    client._session_getter = lambda: Session()
    scope = plan_scopes("trading", (date(2026, 8, 10),))[0]
    response = client.fetch(scope)
    assert response.status_code == 200
    assert response.raw_sequence == 1
    assert len(calls) == 1
    assert calls[0][1]["bld"].endswith("MDCSTAT30101")
