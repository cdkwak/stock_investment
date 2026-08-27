from __future__ import annotations

from datetime import date, datetime, timezone
from io import StringIO

import pandas as pd

from stock_data.providers.financedatareader_fred import fetch_vixcls


class _Response:
    status_code = 200
    content = b"observation_date,VIXCLS\n2026-08-11,14.7\n2026-08-12,14.9\n"
    text = content.decode()
    headers = {"Content-Type": "text/csv", "content-disposition": 'attachment; filename="VIXCLS.csv"'}


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


def test_exact_fdr_fred_route_counts_two_requests_and_retains_provenance(tmp_path):
    session = _Session()

    def reader(transport):
        first = transport.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?"
            "id=VIXCLS&cosd=2026-08-11&coed=2026-08-12"
        )
        second = transport.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?"
            "id=VIXCLS&cosd=2026-08-11&coed=2026-08-12"
        )
        assert first.content == second.content
        frame = pd.read_csv(StringIO(second.text), parse_dates=["observation_date"])
        frame = frame.set_index("observation_date")
        frame.index.rename("DATE", inplace=True)
        return frame

    observation = fetch_vixcls(
        start=date(2026, 8, 11),
        end=date(2026, 8, 12),
        capture_root=tmp_path,
        session=session,
        reader=reader,
        now=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    assert len(session.calls) == 2
    assert all(call[1]["timeout"] == 10 and call[1]["allow_redirects"] is False for call in session.calls)
    assert observation.provenance.request_count == 2
    assert observation.provenance.retry_count == 0
    assert observation.provenance.upstream_provider == "FRED"
    assert list(observation.value.columns) == ["date", "vixcls"]
    assert observation.value.date.tolist() == ["2026-08-11", "2026-08-12"]
    captures = list(tmp_path.rglob("call.json"))
    assert len(captures) == 2
