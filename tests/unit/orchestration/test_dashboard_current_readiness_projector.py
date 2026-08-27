import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stock_data.orchestration.dashboard_current_readiness_projector import (
    ACTIVE_PRODUCTION_STATUS,
    CSV_PATH,
    PRODUCTION_MANIFEST_PATH,
    RUNBOOK_PATH,
    project,
)


NOW = datetime(2026, 8, 21, 10, 5, tzinfo=timezone.utc)
FIELDS = (
    "surface_id", "section", "label", "description", "gui_used",
    "current_numeric_visible", "provider", "route", "interval",
    "source_timestamp_kst", "age_gate", "status", "unit",
    "display_boundary", "backtest_used", "exact_reason", "evidence_or_followup",
)
TARGETS = {
    "tape_soxx", "coverage_fdr_soxx", "tape_kospi", "tape_kosdaq",
    "usd_krw_official_row", "coverage_korean_equity_000660",
    "korean_equity_current_header_005930", "usd_krw_60m_detail",
    "ust2_futures_60m", "ust10_futures_60m", "ust30_futures_60m",
}


def _row(surface_id: str) -> dict[str, str]:
    return {field: (surface_id if field == "surface_id" else "prior") for field in FIELDS}


def _csv(root: Path) -> Path:
    path = root / "fixtures/readiness.csv"; path.parent.mkdir(parents=True)
    ids = list(TARGETS) + [f"unrelated_{number:02d}" for number in range(64 - len(TARGETS))]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader(); writer.writerows([_row(item) for item in ids])
    return path.relative_to(root)


def _write(root: Path, relative: str, payload: object) -> None:
    path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _ur232(root: Path, series: str, *, unit: str | None = None, source: str = "2026-08-21T10:00:00+00:00") -> None:
    specs = {
        "USD_KRW_60M": ("GLOBAL_FX", "KRW=X", "KRW per USD"),
        "UST2_FUTURES_60M": ("CBOT", "ZT=F", "provider native continuous futures price"),
        "UST10_FUTURES_60M": ("CBOT", "ZN=F", "provider native continuous futures price"),
        "UST30_FUTURES_60M": ("CBOT", "ZB=F", "provider native continuous futures price"),
    }
    market, symbol, exact_unit = specs[series]; path = root / "data/state/current_observations/global60m_ur232" / f"{series.lower()}.json"; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "operation_id": "UR-232", "recovery_classification": "RETAINED_LANDING_API_ZERO_RECOVERY", "series_id": series, "observation": {"route_id": f"yahoo-global60m-ur232:{market}:{symbol}", "identity": {"dataset_id": "MARKET_PRICE_60M_CURRENT", "market": market, "symbol": symbol}, "interval": "60m", "value": 100.5, "unit": unit or exact_unit, "provider": "YAHOO", "upstream_provider": "YAHOO_CHART_API", "source_route": "YAHOO_CHART_GLOBAL60M_RETAINED_LANDING_API_ZERO_RECOVERY", "provider_timestamp_utc": source, "retrieved_at_utc": NOW.isoformat(), "finality": "AS_RETRIEVED", "display_only": True, "pit_safe": False}, "immutable_landing": {"run_id": "global60m-20260821T121202Z-ee2361078a99446399486fb17359d2a5", "body_path": "data/landing/x/response.body", "body_sha256": "a" * 64}}, indent=2), encoding="utf-8")


def _observation(symbol: str = "SOXX", source: str = "2026-08-21T10:00:00+00:00") -> dict[str, object]:
    if symbol == "SOXX":
        return {"identity": {"dataset_id": "US_ETF_CURRENT", "market": "NASDAQ", "symbol": "SOXX"}, "value": 529.0132, "unit": "USD per share", "provider": "NASDAQ_OFFICIAL", "upstream_provider": "NASDAQ_OFFICIAL", "route_id": "nasdaq-soxx-info-api:NASDAQ:SOXX", "source_route": "NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf", "interval": "snapshot", "finality": "PROVISIONAL", "display_only": True, "pit_safe": False, "provider_timestamp_utc": source}
    return {"identity": {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": symbol}, "value": 250000.0, "unit": "KRW per share", "provider": "NAVER_FINANCE_WEB", "upstream_provider": "NAVER_FINANCE_WEB", "route_id": f"naver-mobile-basic-current:XKRX:{symbol}", "source_route": f"NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/{symbol}/basic", "interval": "snapshot", "finality": "PROVISIONAL", "display_only": True, "pit_safe": False, "provider_timestamp_utc": source}


def _toss_nxt_close(symbol: str) -> dict[str, object]:
    route = (
        f"toss-stock-price:{symbol}:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
        if symbol == "000660" else
        f"toss-stock-price:{symbol}:snapshot:PROVISIONAL:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
    )
    source_route = "/api/v1/prices" if symbol == "000660" else "/api/v1/prices:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
    finality = "POST_CLOSE_SNAPSHOT" if symbol == "000660" else "PROVISIONAL"
    return {"identity": {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": symbol}, "value": 72000.0, "unit": "KRW per share", "provider": "tossinvest_open_api", "upstream_provider": "tossinvest_open_api", "route_id": route, "source_route": source_route, "interval": "snapshot", "finality": finality, "display_only": True, "pit_safe": False, "provider_timestamp_utc": "2026-08-21T10:59:59+00:00"}


def _rows(root: Path, relative: Path) -> dict[str, dict[str, str]]:
    with (root / relative).open("r", encoding="utf-8", newline="") as stream:
        return {row["surface_id"]: row for row in csv.DictReader(stream)}


def _production_csv(root: Path) -> Path:
    fixture = _csv(root)
    production = root / CSV_PATH
    production.parent.mkdir(parents=True, exist_ok=True)
    production.write_bytes((root / fixture).read_bytes())
    return CSV_PATH


def _activate_production_fixture(root: Path, *, owned_path: str = "docs/data/DASHBOARD_64_CURRENT_READINESS.csv") -> None:
    _write(root, RUNBOOK_PATH.as_posix(), f"# fixture\n\nStatus: `{ACTIVE_PRODUCTION_STATUS}`\n")
    _write(root, PRODUCTION_MANIFEST_PATH.as_posix(), {
        "schema_version": 1,
        "operation_id": "UR-225",
        "status": ACTIVE_PRODUCTION_STATUS,
        "owned_csv_path": owned_path,
        "active_runbook": RUNBOOK_PATH.as_posix(),
        "allow_production": True,
    })


def test_projector_accepts_fresh_exact_soxx_and_preserves_unrelated_rows(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    _write(tmp_path, "data/state/current_observations/nasdaq_soxx_info_current.json", {"schema_version": 1, "observations": [_observation()]})
    before = _rows(tmp_path, relative)["unrelated_00"]
    result = project(tmp_path, now=NOW, csv_path=relative)
    rows = _rows(tmp_path, relative)
    assert result["status"] == "PROJECTED_API_ZERO"
    assert rows["tape_soxx"]["current_numeric_visible"] == "true"
    assert rows["tape_soxx"]["source_timestamp_kst"] == "2026-08-21T19:00:00+09:00"
    assert rows["tape_soxx"]["unit"] == "USD per share"
    assert rows["unrelated_00"] == before


def test_projector_marks_stale_and_malformed_states_numeric_free(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    _write(tmp_path, "data/state/current_observations/nasdaq_soxx_info_current.json", {"schema_version": 1, "observations": [_observation(source="2026-08-21T08:00:00+00:00")]})
    project(tmp_path, now=NOW, csv_path=relative)
    assert _rows(tmp_path, relative)["tape_soxx"]["age_gate"] == "CURRENT_SOURCE_AGE_OVER_60M"
    _write(tmp_path, "data/state/current_observations/nasdaq_soxx_info_current.json", "not-an-object")
    project(tmp_path, now=NOW, csv_path=relative)
    assert _rows(tmp_path, relative)["tape_soxx"]["exact_reason"] == "CURRENT_OBSERVATION_UNAVAILABLE_OR_INVALID"


def test_global60m_failure_preserves_prior_timestamp_and_never_publishes_current(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    rows = _rows(tmp_path, relative); rows["usd_krw_60m_detail"]["source_timestamp_kst"] = "prior-timestamp"
    with (tmp_path / relative).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, quoting=csv.QUOTE_ALL, lineterminator="\n"); writer.writeheader(); writer.writerows(rows.values())
    _write(tmp_path, "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_60M_last.json", {"status": "FAIL"})
    _write(tmp_path, "data/state/global_market_60m.json", {"status": "PASS", "latest_bar_end_utc": {}})
    project(tmp_path, now=NOW, csv_path=relative)
    row = _rows(tmp_path, relative)["usd_krw_60m_detail"]
    assert row["current_numeric_visible"] == "false" and row["source_timestamp_kst"] == "prior-timestamp"
    assert row["exact_reason"].startswith("GLOBAL60M_NO_CURRENT_PUBLICATION")


def test_global60m_semantic_finality_rejection_is_not_reported_as_transport_failure(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    rows = _rows(tmp_path, relative); rows["usd_krw_60m_detail"]["source_timestamp_kst"] = "prior-timestamp"
    with (tmp_path / relative).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, quoting=csv.QUOTE_ALL, lineterminator="\n"); writer.writeheader(); writer.writerows(rows.values())
    _write(tmp_path, "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_60M_last.json", {
        "status": "FAIL",
        "series_terminal_outcomes": [
            {"series_id": series, "outcome": "SEMANTIC_FINALITY_REJECTION"}
            for series in ("USD_KRW_60M", "UST2_FUTURES_60M", "UST10_FUTURES_60M", "UST30_FUTURES_60M")
        ],
    })
    _write(tmp_path, "data/state/global_market_60m.json", {"status": "PASS", "latest_bar_end_utc": {}})
    project(tmp_path, now=NOW, csv_path=relative)
    row = _rows(tmp_path, relative)["usd_krw_60m_detail"]
    assert row["current_numeric_visible"] == "false" and row["source_timestamp_kst"] == "prior-timestamp"
    assert row["exact_reason"].startswith("GLOBAL60M_SEMANTIC_FINALITY_REJECTION")


def test_projector_prefers_fresh_exact_ur232_fx_and_futures_without_official_fx_replacement(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    for series in ("USD_KRW_60M", "UST2_FUTURES_60M", "UST10_FUTURES_60M", "UST30_FUTURES_60M"):
        _ur232(tmp_path, series)
    project(tmp_path, now=NOW, csv_path=relative)
    rows = _rows(tmp_path, relative)
    assert rows["usd_krw_60m_detail"]["current_numeric_visible"] == "true"
    assert rows["usd_krw_official_row"]["current_numeric_visible"] == "false"
    assert rows["ust10_futures_60m"]["unit"] == "provider native continuous futures price"
    assert "yield" not in rows["ust10_futures_60m"]["unit"]


def test_projector_rejects_malformed_or_wrong_unit_ur232_envelope(tmp_path: Path) -> None:
    relative = _csv(tmp_path); _ur232(tmp_path, "USD_KRW_60M", unit="percent")
    project(tmp_path, now=NOW, csv_path=relative)
    assert _rows(tmp_path, relative)["usd_krw_60m_detail"]["current_numeric_visible"] == "false"


def test_projector_projects_latest_closed_session_nxt_close_with_not_live_status(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    for symbol, filename in (("000660", "toss_000660_nxt_session_close_ur240.json"), ("005930", "toss_005930_nxt_close_ur241.json")):
        _write(tmp_path, f"data/state/current_observations/{filename}", {"schema_version": 1, "observations": [_toss_nxt_close(symbol)]})
    project(tmp_path, now=datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc), csv_path=relative)
    rows = _rows(tmp_path, relative)
    for surface_id in ("coverage_korean_equity_000660", "korean_equity_current_header_005930"):
        assert rows[surface_id]["current_numeric_visible"] == "true"
        assert rows[surface_id]["status"] == "NXT_SESSION_CLOSE_INFERRED"
        assert "NOT_LIVE" in rows[surface_id]["exact_reason"]
    project(tmp_path, now=datetime(2026, 8, 22, 0, 30, tzinfo=timezone.utc), csv_path=relative)
    rows = _rows(tmp_path, relative)
    for surface_id in ("coverage_korean_equity_000660", "korean_equity_current_header_005930"):
        assert rows[surface_id]["current_numeric_visible"] == "true"
        assert rows[surface_id]["status"] == "MARKET_CLOSED_LAST_FINAL"
        assert "NOT_LIVE" in rows[surface_id]["exact_reason"]


def test_projector_is_idempotent_and_rolls_back_failed_replace(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    _write(tmp_path, "data/state/current_observations/nasdaq_soxx_info_current.json", {"schema_version": 1, "observations": [_observation()]})
    project(tmp_path, now=NOW, csv_path=relative)
    first = (tmp_path / relative).read_bytes()
    project(tmp_path, now=NOW, csv_path=relative)
    assert (tmp_path / relative).read_bytes() == first
    with pytest.raises(OSError):
        project(tmp_path, now=NOW, csv_path=relative, replace=lambda _src, _dst: (_ for _ in ()).throw(OSError("replace failed")))
    assert (tmp_path / relative).read_bytes() == first


def test_projector_requires_aware_clock_and_never_targets_production_by_default(tmp_path: Path) -> None:
    relative = _csv(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        project(tmp_path, now=datetime(2026, 8, 21, 10, 5), csv_path=relative)
    assert CSV_PATH.as_posix() == "docs/data/DASHBOARD_64_CURRENT_READINESS.csv"


def test_production_path_is_inactive_without_current_manifest_and_never_writes(tmp_path: Path) -> None:
    production = _production_csv(tmp_path)
    before = (tmp_path / production).read_bytes()
    with pytest.raises(PermissionError, match="manifest/runbook"):
        project(tmp_path, now=NOW, csv_path=production, production_confirmed=True)
    assert (tmp_path / production).read_bytes() == before


def test_production_path_requires_matching_manifest_and_explicit_confirmation(tmp_path: Path) -> None:
    production = _production_csv(tmp_path)
    _activate_production_fixture(tmp_path)
    before = (tmp_path / production).read_bytes()
    with pytest.raises(PermissionError, match="confirm"):
        project(tmp_path, now=NOW, csv_path=production)
    assert (tmp_path / production).read_bytes() == before
    _activate_production_fixture(tmp_path, owned_path="docs/data/not-owned.csv")
    with pytest.raises(PermissionError, match="inactive or mismatched"):
        project(tmp_path, now=NOW, csv_path=production, production_confirmed=True)
    assert (tmp_path / production).read_bytes() == before


def test_active_manifest_authorizes_only_temp_owned_canonical_path_simulation(tmp_path: Path) -> None:
    production = _production_csv(tmp_path)
    _activate_production_fixture(tmp_path)
    _write(tmp_path, "data/state/current_observations/nasdaq_soxx_info_current.json", {"schema_version": 1, "observations": [_observation()]})
    result = project(tmp_path, now=NOW, csv_path=production, production_confirmed=True)
    assert result["status"] == "PROJECTED_API_ZERO"
    assert _rows(tmp_path, production)["tape_soxx"]["current_numeric_visible"] == "true"


def test_production_backup_is_preimage_and_cannot_be_overwritten(tmp_path: Path) -> None:
    production = _production_csv(tmp_path)
    _activate_production_fixture(tmp_path)
    before = (tmp_path / production).read_bytes()
    backup = Path("artifacts/preimage.csv")
    project(tmp_path, now=NOW, csv_path=production, production_confirmed=True, backup_path=backup)
    assert (tmp_path / backup).read_bytes() == before
    with pytest.raises(FileExistsError, match="preimage backup"):
        project(tmp_path, now=NOW, csv_path=production, production_confirmed=True, backup_path=backup)
