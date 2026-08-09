from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from stock_data.contracts.data_v1 import (
    KR_CREDIT_BALANCE_DAILY, KR_DERIVATIVES_FUTURES_DAILY, KR_DERIVATIVES_OPTIONS_DAILY,
    KR_MARKET_LIQUIDITY_DAILY, KR_STOCK_LENDING_DAILY, KR_STOCK_LENDING_MARKET_DAILY,
    KR_STOCK_LENDING_PARTICIPANT_DAILY,
)
from stock_data.contracts.kr_equity import KR_EQUITY_UNIVERSE_DAILY
from stock_data.providers.data_go_kr.data_v1 import (
    ENDPOINTS, normalize_credit_balance, normalize_futures, normalize_market_liquidity,
    normalize_options, normalize_stock_lending, normalize_stock_lending_market,
    normalize_stock_lending_participant,
)
from stock_data.providers.data_go_kr.universe import UNIVERSE_ENDPOINT, normalize_universe_items
from stock_data.pipelines.data_v1_collection import collect_date, collect_full_history
from stock_data.pipelines.equity_official_collection import collect_equity_price_cap_date


def run_phase(project_root: Path, phase: int, *, live: bool, resume: bool, max_calls: int, skip_krx: bool = True):
    if phase == 6 and skip_krx:
        return {"phase": 6, "status": "BLOCKED", "reason": "krx_explicitly_skipped"}
    if not live:
        return {"phase":phase, "status":"BLOCKED", "reason":"live_disabled"}
    results = []
    try:
        if phase == 1:
            for key, contract, normalizer in (
                ("market_liquidity", KR_MARKET_LIQUIDITY_DAILY, normalize_market_liquidity),
                ("credit_balance", KR_CREDIT_BALANCE_DAILY, normalize_credit_balance),
            ):
                results.append(asdict(collect_full_history(project_root=project_root, endpoint=ENDPOINTS[key],
                    contract=contract, normalizer=normalizer, max_calls=max_calls, resume=resume)))
        elif phase == 2:
            for key, contract, normalizer, needed in (
                ("futures", KR_DERIVATIVES_FUTURES_DAILY, normalize_futures, 1),
                ("options", KR_DERIVATIVES_OPTIONS_DAILY, normalize_options, 2),
            ):
                if needed > max_calls:
                    results.append({"dataset":contract.name,"status":"BLOCKED","reason":"call_cap"}); continue
                results.append(asdict(collect_date(project_root=project_root, endpoint=ENDPOINTS[key], contract=contract,
                    normalizer=normalizer, base_date="20220919", max_calls=needed, resume=resume)))
        elif phase == 3:
            for key, contract, normalizer in (
                ("stock_lending", KR_STOCK_LENDING_DAILY, normalize_stock_lending),
                ("stock_lending_market", KR_STOCK_LENDING_MARKET_DAILY, normalize_stock_lending_market),
                ("stock_lending_participant", KR_STOCK_LENDING_PARTICIPANT_DAILY, normalize_stock_lending_participant),
            ):
                results.append(asdict(collect_date(project_root=project_root, endpoint=ENDPOINTS[key], contract=contract,
                    normalizer=normalizer, base_date="20231005", max_calls=1, resume=resume)))
        elif phase == 4:
            return {"phase":4,"status":"PARTIAL","reason":"HTTPS verified; coverage/call volume requires explicit dates"}
        elif phase == 5:
            results.append(collect_equity_price_cap_date(project_root, "20260806", resume=resume))
            results.append(asdict(collect_date(project_root=project_root, endpoint=UNIVERSE_ENDPOINT,
                contract=KR_EQUITY_UNIVERSE_DAILY, normalizer=normalize_universe_items,
                base_date="20260806", max_calls=1, resume=resume)))
        elif phase == 6:
            return {"phase":6,"status":"BLOCKED","reason":"KRX Open API approval gate returned 401"}
        else:
            return {"phase":phase,"status":"COMPLETE","reason":"baseline is test-driven"}
    except Exception as error:
        return {"phase":phase,"status":"PARTIAL","error":type(error).__name__}
    statuses = {item.get("status") for item in results}
    status = "COMPLETE" if statuses <= {"COMPLETE","VALID_EMPTY"} else "PARTIAL"
    return {"phase":phase,"status":status,"results":results}
