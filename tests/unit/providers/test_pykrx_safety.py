import pytest

from stock_data.providers.pykrx.safety import PykrxJobLimitError, PykrxRequestPolicy


def test_request_interval_and_exponential_backoff() -> None:
    sleeps=[]; times=iter([0.0,0.0,0.25,1.0])
    policy=PykrxRequestPolicy(
        min_interval_seconds=1.0, initial_backoff_seconds=2.0,
        sleep_fn=sleeps.append, monotonic_fn=lambda:next(times),
    )
    policy.before_request(); policy.before_request()
    policy.record_failure(); policy.record_failure()
    assert sleeps==[1.0,2.0,4.0]


def test_request_limit_stops_job() -> None:
    policy=PykrxRequestPolicy(
        min_interval_seconds=0,max_consecutive_requests=2,
        sleep_fn=lambda _:None,monotonic_fn=lambda:0,
    )
    policy.before_request(); policy.before_request()
    with pytest.raises(PykrxJobLimitError,match="request limit"):
        policy.before_request()


def test_failure_limit_stops_job() -> None:
    policy=PykrxRequestPolicy(
        max_consecutive_failures=3,sleep_fn=lambda _:None,
    )
    policy.record_failure(); policy.record_failure()
    with pytest.raises(PykrxJobLimitError,match="consecutive failures"):
        policy.record_failure()
