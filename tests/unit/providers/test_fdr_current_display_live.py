from __future__ import annotations

from datetime import date, datetime, timezone

from stock_data.orchestration.current_observation import CurrentObservationFileStore
from stock_data.providers.fdr_current_display_live import (
    CHECKPOINT_PATH, IDENTITY, PROJECTION_PATH, ROUTE, SOURCE_DATE,
    execute_fdr_current_display_operation,
)
from stock_data.providers.fdr_display_daily import FDRDisplayDailyLandingStore, FDRDisplayDailyResponse, FDRDisplayDailyRefresher


class _Response:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code, self.content = status_code, content


def _body(*, volume: str = "100") -> bytes:
    return f'<item data="20260821|70000|71000|69000|70500|{volume}" />'.encode()


def test_exact_one_get_landing_promotion_checkpoint_and_api_zero_replay(tmp_path) -> None:
    calls = []

    def get(url, *, timeout):
        calls.append((url, timeout))
        return _Response(200, _body())

    clock = lambda: datetime(2026, 8, 21, 2, tzinfo=timezone.utc)
    result = execute_fdr_current_display_operation(tmp_path, http_get=get, clock=clock)

    assert result.status == "COMPLETE_VALIDATED"
    assert result.raw_get_count == 1 and result.api_zero_replay_calls == 0
    assert calls == [("https://fchart.stock.naver.com/sise.nhn?timeframe=day&count=6000&requestType=0&symbol=005930", 10)]
    assert result.landing_file and result.landing_sha256 and result.landing_bytes == len(_body())
    assert (tmp_path / result.landing_file).read_bytes() == _body()
    stored = FDRDisplayDailyRefresher(
        store=CurrentObservationFileStore(tmp_path / PROJECTION_PATH),
        landing=FDRDisplayDailyLandingStore(tmp_path / "unused"), now=clock,
    ).replay(IDENTITY).observation
    assert stored is not None and stored.value == 70500.0 and stored.provider_timestamp_utc == "2026-08-21T00:00:00+00:00"
    assert (tmp_path / CHECKPOINT_PATH).is_file()
    replay = execute_fdr_current_display_operation(tmp_path, http_get=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no replay GET")))
    assert replay.status == "API_ZERO_REPLAY" and replay.raw_get_count == 0


def test_http_failure_is_body_free_single_use_and_preserves_prior_observation(tmp_path) -> None:
    clock = lambda: datetime(2026, 8, 21, 2, tzinfo=timezone.utc)
    store = CurrentObservationFileStore(tmp_path / PROJECTION_PATH)
    refresher = FDRDisplayDailyRefresher(
        store=store, landing=FDRDisplayDailyLandingStore(tmp_path / "prior-landing"), now=clock,
    )
    prior = refresher.refresh(
        identity=IDENTITY, start=date(2026, 8, 20), end=date(2026, 8, 20),
        transport=lambda *_: FDRDisplayDailyResponse(200, b"prior", __import__("pandas").DataFrame({
            "Open": [70000], "High": [71000], "Low": [69000], "Close": [70500], "Volume": [100], "Change": [float("nan")],
        }, index=__import__("pandas").to_datetime(["2026-08-20"]))),
    )
    assert prior.observation is not None
    result = execute_fdr_current_display_operation(tmp_path, http_get=lambda *_args, **_kwargs: _Response(500, b"must-not-retain"), clock=clock)

    assert result.status == "FAILED_BOUNDED" and result.raw_get_count == 1
    assert result.landing_file is None and result.safe_code == "FDR_DISPLAY_HTTP_500"
    assert not list((tmp_path / "data/landing/fdr_display_daily").rglob("response.bin"))
    assert execute_fdr_current_display_operation(tmp_path, http_get=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no repeat GET"))).raw_get_count == 0


def test_fractional_naver_volume_fails_typed_validation_after_successful_landing(tmp_path) -> None:
    result = execute_fdr_current_display_operation(
        tmp_path, http_get=lambda *_args, **_kwargs: _Response(200, _body(volume="1.5")),
        clock=lambda: datetime(2026, 8, 21, 2, tzinfo=timezone.utc),
    )

    assert result.status == "FAILED_BOUNDED" and result.safe_code == "FDR_DISPLAY_DAILY_VOLUME"
    assert result.landing_file is not None
