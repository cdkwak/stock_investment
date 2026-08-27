from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_data.providers.pykrx.kr_equity_fundamental_observation import (
    capture_equity_fundamental_observation,
)

from stock_research.exploratory_scanner import (
    EXPLORATORY_SCANNER_VERSION,
    LocalExploratoryCandidateScanner,
    _wilder_rsi_last,
)


def retained_inputs(root: Path, *, split_jump: bool = False) -> None:
    dates = pd.bdate_range("2026-04-01", periods=80)
    rows = []
    for market, symbol, close in (
        ("KOSPI", "005930", np.linspace(100.0, 55.0, len(dates))),
        ("KOSDAQ", "000250", np.linspace(50.0, 90.0, len(dates))),
    ):
        values = close.copy()
        if split_jump and symbol == "005930":
            values[-20] = values[-21] * 0.2
        rows.extend({
            "date": date.date(), "market": market, "symbol": symbol,
            "close": int(max(value, 1.0) * 100), "volume": 1000,
        } for date, value in zip(dates, values))
    price = pd.DataFrame(rows)
    latest = dates[-1].date()
    universe = pd.DataFrame((
        {"date": latest, "market": "KOSPI", "symbol": "005930", "name": "삼성전자",
         "listed_info_present": True, "price_present": True},
        {"date": latest, "market": "KOSDAQ", "symbol": "000250", "name": "삼천당제약",
         "listed_info_present": True, "price_present": True},
    ))
    for market in ("KOSPI", "KOSDAQ"):
        price_path = root / f"data/normalized/kr_equity_price_daily/market={market}/year=2026/data.parquet"
        universe_path = root / f"data/published/kr_equity_canonical_universe_daily/market={market}/year=2026/data.parquet"
        price_path.parent.mkdir(parents=True, exist_ok=True)
        universe_path.parent.mkdir(parents=True, exist_ok=True)
        price.loc[price.market.eq(market)].to_parquet(price_path, index=False)
        universe.loc[universe.market.eq(market)].to_parquet(universe_path, index=False)


def retained_current_valuation(root: Path, *, mutation: str | None = None) -> None:
    rows = ({
        "ISU_SRT_CD": "005930", "ISU_ABBRV": "삼성전자",
        "TDD_CLSPRC": "70,000", "EPS": "5,000", "PER": "14.00",
        "BPS": "55,000", "PBR": "1.27", "DPS": "1,500",
        "DVD_YLD": "2.14",
    }, {
        "ISU_SRT_CD": "000250", "ISU_ABBRV": "삼천당제약",
        "TDD_CLSPRC": "90,000", "EPS": "-", "PER": "-",
        "BPS": "36,000", "PBR": "2.50", "DPS": "0",
        "DVD_YLD": "0",
    })
    body = json.dumps({"output": rows}, ensure_ascii=False).encode()
    result = capture_equity_fundamental_observation(
        date(2026, 7, 21), run_id="bounded",
        landing_root=(
            root / "data/landing/kr_equity_fundamental_current_observation"
        ),
        env_file=root / "unused.env", body_fetcher=lambda _target: body,
    )
    if mutation is None:
        return
    provenance = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    if mutation == "bad_hash":
        provenance["response_sha256"] = "0" * 64
    elif mutation == "missing_source_bld":
        provenance.pop("source_bld")
    elif mutation == "provider_error":
        payload = json.loads(result.path.read_text(encoding="utf-8"))
        payload["_error_code"] = "E"
        tampered = json.dumps(payload, ensure_ascii=False).encode()
        result.path.write_bytes(tampered)
        provenance["response_sha256"] = hashlib.sha256(tampered).hexdigest()
    else:
        raise AssertionError(mutation)
    result.provenance_path.write_text(json.dumps(provenance), encoding="utf-8")


def test_practical_scanner_displays_available_technical_axis_without_other_axes(tmp_path):
    retained_inputs(tmp_path)
    view = LocalExploratoryCandidateScanner(tmp_path).scan(limit=20)

    assert view.contract_version == EXPLORATORY_SCANNER_VERSION
    assert view.availability == "READY"
    assert view.scanned_instruments == 2
    assert view.eligible_instruments == 1
    assert len(view.candidates) == 1
    candidate = view.candidates[0]
    assert candidate.symbol == "005930"
    assert candidate.technical_state == "과매도"
    assert candidate.earnings_state == "NOT_CONNECTED"
    assert candidate.valuation_state == "NOT_CONNECTED"
    assert view.recommendation_state == "DESCRIPTIVE_NOT_A_RECOMMENDATION"


def test_exact_date_current_per_pbr_is_attached_without_relative_value_claim(tmp_path):
    retained_inputs(tmp_path)
    retained_current_valuation(tmp_path)
    view = LocalExploratoryCandidateScanner(tmp_path).scan(limit=20)

    candidate = view.candidates[0]
    assert candidate.valuation_state == "AVAILABLE_CURRENT_TRAILING"
    assert candidate.per == 14.0 and candidate.pbr == 1.27
    assert candidate.valuation_as_of == view.as_of == "2026-07-21"
    assert candidate.earnings_state == "NOT_CONNECTED"


def test_untrusted_current_valuation_is_ignored_without_blocking_technical_axis(tmp_path):
    retained_inputs(tmp_path)
    retained_current_valuation(tmp_path, mutation="bad_hash")
    view = LocalExploratoryCandidateScanner(tmp_path).scan(limit=20)

    candidate = view.candidates[0]
    assert candidate.valuation_state == "NOT_CONNECTED"
    assert candidate.per is None and candidate.pbr is None


@pytest.mark.parametrize("mutation", ("missing_source_bld", "provider_error"))
def test_producer_invalid_current_valuation_never_reaches_display(
    tmp_path, mutation,
):
    retained_inputs(tmp_path)
    retained_current_valuation(tmp_path, mutation=mutation)
    candidate = LocalExploratoryCandidateScanner(tmp_path).scan(limit=20).candidates[0]

    assert candidate.valuation_state == "NOT_CONNECTED"
    assert candidate.per is None and candidate.pbr is None


def test_current_universe_alignment_excludes_stale_or_unlisted_rows(tmp_path):
    retained_inputs(tmp_path)
    path = tmp_path / "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2026/data.parquet"
    frame = pd.read_parquet(path)
    frame["listed_info_present"] = False
    frame.to_parquet(path, index=False)

    view = LocalExploratoryCandidateScanner(tmp_path).scan()
    assert view.availability == "UNAVAILABLE"
    assert view.unavailable_reason == "CURRENT_UNIVERSE_NOT_ALIGNED"
    assert view.candidates == ()


def test_missing_market_partition_and_future_date_fail_typed(tmp_path):
    retained_inputs(tmp_path)
    missing = tmp_path / (
        "data/published/kr_equity_canonical_universe_daily/"
        "market=KOSDAQ/year=2026/data.parquet"
    )
    missing.unlink()
    view = LocalExploratoryCandidateScanner(tmp_path).scan()
    assert view.availability == "UNAVAILABLE"
    assert view.unavailable_reason == "CURRENT_MARKET_PARTITION_INCOMPLETE"

    retained_inputs(tmp_path)
    for base in (
        "data/normalized/kr_equity_price_daily",
        "data/published/kr_equity_canonical_universe_daily",
    ):
        for path in (tmp_path / base).rglob("*.parquet"):
            frame = pd.read_parquet(path)
            frame["date"] = pd.to_datetime(frame["date"]) + pd.DateOffset(years=10)
            frame.to_parquet(path, index=False)
    view = LocalExploratoryCandidateScanner(tmp_path).scan()
    assert view.availability == "UNAVAILABLE"
    assert view.unavailable_reason == "FUTURE_DATED_INPUT"


def test_original_price_jump_is_a_visible_caution_not_a_global_block(tmp_path):
    retained_inputs(tmp_path, split_jump=True)
    view = LocalExploratoryCandidateScanner(tmp_path).scan(limit=20)
    candidate = next(item for item in view.candidates if item.symbol == "005930")
    assert candidate.data_caution == "원가격 급변/분할 영향 가능"


def test_missing_inputs_and_invalid_parameters_fail_typed(tmp_path):
    view = LocalExploratoryCandidateScanner(tmp_path).scan()
    assert view.availability == "UNAVAILABLE"
    assert view.candidates == ()
    with pytest.raises(ValueError, match="parameters are invalid"):
        LocalExploratoryCandidateScanner(tmp_path).scan(rsi_ceiling=100.0)
    retained_inputs(tmp_path)
    with pytest.raises(ValueError, match="parameters are invalid"):
        LocalExploratoryCandidateScanner(tmp_path).scan(limit=81)


def test_fast_wilder_last_preserves_rsi_limits_and_flat_state():
    assert _wilder_rsi_last(np.full(60, 100.0)) == 50.0
    assert _wilder_rsi_last(np.arange(1.0, 61.0)) == 100.0
    assert _wilder_rsi_last(np.arange(60.0, 0.0, -1.0)) == 0.0
