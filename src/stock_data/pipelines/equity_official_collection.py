from pathlib import Path
import pandas as pd

from stock_data.contracts.kr_equity import KR_EQUITY_MARKET_CAP_DAILY, KR_EQUITY_PRICE_DAILY
from stock_data.providers.data_go_kr.client import DataGoKrClient, service_key_from_environment, write_landing_pages_atomic
from stock_data.providers.data_go_kr.stock_price import STOCK_PRICE_ENDPOINT, normalize_stock_price_items
from stock_data.pipelines.backfill_state import BackfillState
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_equity import validate_equity_market_cap, validate_equity_price


def _upsert(frame, root, contract, validator):
    if root.exists():
        existing = read_dataset(root, contract, validator)
        frame = pd.concat([existing, frame], ignore_index=True).drop_duplicates(
            list(contract.primary_key), keep="last").sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    validator(frame)
    write_dataset_atomic(frame, root, contract, validator)


def collect_equity_price_cap_date(project_root: Path, base_date: str, *, resume=True):
    state = BackfillState.load(project_root/"data/state/kr_equity_price_cap_daily.json", "kr_equity_price_cap_daily")
    if resume and base_date not in state.pending([base_date]):
        return {"status":"COMPLETE", "calls":0}
    try:
        result = DataGoKrClient(endpoint=STOCK_PRICE_ENDPOINT,
            service_key=service_key_from_environment(project_root), max_attempts=1).fetch_all(
                filters={"basDt":base_date}, num_of_rows=9999, max_pages=1)
        if result.total_count == 0:
            state.mark_valid_empty(base_date); return {"status":"VALID_EMPTY", "calls":1, "rows":0}
        write_landing_pages_atomic(result.pages, project_root/"data/landing/data_go_kr/stock_price"/f"{base_date}.json")
        eligible = [item for item in result.items if str(item.get("mrktCtg", "")).strip() in {"KOSPI","KOSDAQ"}]
        normalized = normalize_stock_price_items(eligible)
        _upsert(normalized.price, project_root/"data/normalized/kr_equity_price_daily",
                KR_EQUITY_PRICE_DAILY, validate_equity_price)
        _upsert(normalized.market_cap, project_root/"data/normalized/kr_equity_market_cap_daily",
                KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap)
        state.mark_completed(base_date)
        return {"status":"COMPLETE", "calls":len(result.pages), "source_rows":result.total_count,
                "normalized_rows":len(normalized.price)}
    except Exception as error:
        state.mark_failed(base_date, type(error).__name__); raise
