from pathlib import Path
import pandas as pd
import pytest

from stock_data.contracts.global_market import GLOBAL_INDEX_PRICE_DAILY
from stock_data.storage import contract_parquet
from stock_data.validation.global_market import validate_global_index


def frame(close=105.0):
    return pd.DataFrame([["2026-08-07","SP500","^GSPC",100.,110.,90.,close,1000]],columns=GLOBAL_INDEX_PRICE_DAILY.column_names)


def test_contract_storage_failure_preserves_existing(monkeypatch,tmp_path:Path) -> None:
    root=tmp_path/"global"
    contract_parquet.write_dataset_atomic(frame(),root,GLOBAL_INDEX_PRICE_DAILY,validate_global_index)
    target=next(root.rglob("data.parquet")); before=target.read_bytes()
    original=Path.replace
    calls=0
    def fail_replace(self,target_path):
        nonlocal calls
        if self.name.endswith(".parquet.tmp"):
            calls+=1
            if calls==1: raise OSError("simulated replace failure")
        return original(self,target_path)
    monkeypatch.setattr(Path,"replace",fail_replace)
    with pytest.raises(OSError,match="simulated"):
        contract_parquet.write_dataset_atomic(frame(106.),root,GLOBAL_INDEX_PRICE_DAILY,validate_global_index)
    assert target.read_bytes()==before
