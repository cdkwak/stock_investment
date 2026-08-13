from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.manual import a007_investor_access_recovery_support as support
from scripts.manual import diagnose_a007_investor_access_recovery as runner
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped


ROOT = Path(__file__).resolve().parents[1]


def test_plan_is_exactly_one_retry_free_request():
    assert support.SCOPE == {
        "strtDd": "20170519", "endDd": "20170522",
        "inqCondTpCd": 2, "mktTpCd": 1,
    }
    assert support.MAX_BUSINESS_REQUESTS == 1
    assert support.MAX_RAW_HTTP_REQUESTS == support.EXPECTED_RAW_HTTP_REQUESTS == 6
    assert support.REQUIRE_ZERO_RETRY_AUTH_SESSION
    assert support.expected_dates(ROOT) == ("20170519", "20170522")


def test_cooldown_and_prior_403_are_bound():
    prior = datetime.fromisoformat(support.PRIOR_RESTRICTION_AT_UTC.replace("Z", "+00:00"))
    with pytest.raises(PilotStopped, match="COOLDOWN_NOT_ENDED"):
        support.verify_prior_restriction(ROOT, now=prior + timedelta(hours=1))
    assert support.verify_prior_restriction(
        ROOT, now=prior + timedelta(seconds=support.MINIMUM_COOLDOWN_SECONDS)
    ) == support.MINIMUM_COOLDOWN_SECONDS


def test_cli_guard_prevents_execution(monkeypatch):
    called = False
    def fail(**kwargs):
        nonlocal called
        called = True
    monkeypatch.setattr(runner, "run_diagnostic", fail)
    monkeypatch.setattr("sys.argv", ["x"])
    assert runner.main() == 2
    assert not called
