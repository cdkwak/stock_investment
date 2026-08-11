from pathlib import Path

from scripts.manual import diagnose_pykrx_login as diagnostic


def test_pykrx_diagnostic_budget_and_probe_matrix_are_bounded():
    assert diagnostic.MAX_HTTP_REQUESTS == 14
    assert diagnostic.HTTP_TIMEOUT_SECONDS == 20
    assert len(diagnostic.PROBES) == 5
    names = [probe[0] for probe in diagnostic.PROBES]
    functions = [probe[1] for probe in diagnostic.PROBES]
    assert len(names) == len(set(names))
    assert functions == [
        "get_shorting_status_by_date",
        "get_market_trading_value_by_date",
        "get_market_fundamental_by_date",
        "get_etf_ohlcv_by_ticker",
        "get_exhaustion_rates_of_foreign_investment_by_date",
    ]


def test_safe_url_discards_query_and_fragment():
    assert diagnostic._safe_url("https://example.test/path?secret=value#fragment") == (
        "https://example.test/path"
    )


def test_probe_classification_requires_every_probe_to_be_nonempty():
    complete = [{"status": "SUCCESS", "rows": 1} for _ in diagnostic.PROBES]
    assert diagnostic._classify_probes(complete) == "AUTHENTICATED_SOURCE_FEASIBLE"
    complete[-1]["rows"] = 0
    assert diagnostic._classify_probes(complete) == "AUTHENTICATED_SOURCE_PARTIAL_OR_EMPTY"


def test_credential_loader_reads_only_required_names(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "UNRELATED=ignored\nKRX_ID=fake-id\nKRX_PW='fake-password'\n",
        encoding="utf-8",
    )
    assert diagnostic._load_krx_credentials(env_file) == (True, True)
