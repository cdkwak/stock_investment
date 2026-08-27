from __future__ import annotations

from datetime import date
import json

import pytest

from stock_data.providers.pykrx.kr_equity_fundamental_observation import (
    EquityFundamentalObservationError,
    capture_equity_fundamental_observation,
    find_valid_equity_fundamental_observation,
)


def _body(*, duplicate: bool = False) -> bytes:
    row = {
        "ISU_SRT_CD": "005930", "ISU_ABBRV": "삼성전자",
        "TDD_CLSPRC": "70,000", "EPS": "5,000", "PER": "14.0",
        "BPS": "55,000", "PBR": "1.27", "DPS": "1,500",
        "DVD_YLD": "2.14",
    }
    rows = [row, dict(row)] if duplicate else [row]
    return json.dumps({"output": rows}, ensure_ascii=False).encode()


def test_credentials_fail_before_default_provider_access(tmp_path, monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *_args, **_kwargs: pytest.fail("network called before credential gate"),
    )
    with pytest.raises(EquityFundamentalObservationError, match="credentials"):
        capture_equity_fundamental_observation(
            date(2026, 8, 25), run_id="missing-creds",
            landing_root=tmp_path / "landing", env_file=tmp_path / "absent.env",
        )
    assert not (tmp_path / "landing").exists()


def test_injected_capture_is_immutable_retry_zero_and_descriptive_only(tmp_path):
    calls = []

    def fetch(target):
        calls.append(target)
        return _body(duplicate=True)

    result = capture_equity_fundamental_observation(
        date(2026, 8, 25), run_id="bounded",
        landing_root=tmp_path / "landing", env_file=tmp_path / "unused",
        body_fetcher=fetch,
    )
    assert calls == [date(2026, 8, 25)]
    assert result.business_calls == 1 and result.retry_count == 0
    assert result.rows == 2 and result.distinct_security_codes == 1
    assert result.duplicate_groups == 1 and result.predictive_use is False
    provenance = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert provenance["finality"] == "UNKNOWN"
    assert provenance["pit_status"] == "PIT_LIMITED_FIRST_OBSERVED_ONLY"
    assert provenance["normalized_writes"] is False
    replay = find_valid_equity_fundamental_observation(
        tmp_path / "landing", date(2026, 8, 25),
    )
    assert replay is not None and replay.sha256 == result.sha256
    with pytest.raises(EquityFundamentalObservationError, match="already"):
        capture_equity_fundamental_observation(
            date(2026, 8, 25), run_id="bounded",
            landing_root=tmp_path / "landing", env_file=tmp_path / "unused",
            body_fetcher=fetch,
        )
    assert len(calls) == 1


def test_invalid_body_is_retained_without_provenance(tmp_path):
    with pytest.raises(EquityFundamentalObservationError, match="valid-empty"):
        capture_equity_fundamental_observation(
            date(2026, 8, 25), run_id="empty",
            landing_root=tmp_path / "landing", env_file=tmp_path / "unused",
            body_fetcher=lambda _target: b'{"output": []}',
        )
    run_root = tmp_path / "landing/date=2026-08-25/empty"
    assert (run_root / "response.json").is_file()
    assert not (run_root / "provenance.json").exists()
    assert find_valid_equity_fundamental_observation(
        tmp_path / "landing", date(2026, 8, 25),
    ) is None
