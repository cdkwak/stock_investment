from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.audit.manual_krx_derivatives_investor import retain_inventory
from stock_data.contracts.krx_derivatives_investor import (
    KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY,
)
from stock_data.pipelines.manual_krx_futures_investor_net_purchase import (
    audit_promoted_history,
    build_normalized_candidate,
    promote_manual_history,
)
from stock_data.validation.krx_derivatives_investor import validate_futures_investor_net_purchase


def _source(root: Path) -> str:
    inbox = root / "docs/krx_data"
    inbox.mkdir(parents=True)
    text = (
        "일자,기관 합계,기타법인,개인,외국인 합계,전체\n"
        '"1999/04/27","1.0","2.0","-3.0","0.0","0.0"\n'
        '"1999/04/26","4.0","5.0","-9.0","0.0","0.0"\n'
    )
    (inbox / "data_선물순매수.csv").write_bytes(text.encode("cp949"))
    return retain_inventory(root)["inventory_sha256"]


def test_promote_and_audit_exact_manual_history(tmp_path: Path) -> None:
    digest = _source(tmp_path)
    candidate = build_normalized_candidate(tmp_path, digest)
    assert len(candidate) == 10
    assert set(candidate["session"]) == {"ALL"}
    assert set(candidate["trading_value_unit_source"]) == {"백만원"}
    first = promote_manual_history(tmp_path, digest)
    second = promote_manual_history(tmp_path, digest)
    assert first["promotion_status"] == "PROMOTED"
    assert second["promotion_status"] == "ALREADY_PROMOTED"
    audit = audit_promoted_history(tmp_path, digest)
    assert audit["result"] == "PASS"
    assert audit["landing_to_normalized_exact"] is True
    state = json.loads(
        (tmp_path / "data/state/kr_kospi200_futures_investor_net_purchase_daily.json").read_text(encoding="utf-8")
    )
    assert state["measure"] == "NET_PURCHASE_TRADING_VALUE"
    assert state["unit"] == "백만원"


def test_validator_rejects_semantic_drift(tmp_path: Path) -> None:
    digest = _source(tmp_path)
    candidate = build_normalized_candidate(tmp_path, digest)
    candidate.loc[0, "session"] = "REGULAR"
    with pytest.raises(ValueError, match="session"):
        validate_futures_investor_net_purchase(candidate)


def test_contract_is_narrow_and_registered() -> None:
    assert KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY.primary_key == (
        "date", "investor_type_source"
    )
    assert "sell_volume" not in KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY.column_names
    assert "buy_volume" not in KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY.column_names
