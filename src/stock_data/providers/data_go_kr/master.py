from __future__ import annotations

from typing import Mapping, Sequence
import pandas as pd

from stock_data.contracts.kr_equity import KR_EQUITY_MASTER
from stock_data.validation.kr_equity import validate_equity_master


def _nullable(value):
    if value is None or pd.isna(value):
        return None
    text = str(value or "").strip()
    return None if text in {"", "NULL"} else text


def _number(value, integer=False):
    text = _nullable(value)
    if text is None:
        return None
    return int(text) if integer else float(text)


def build_enriched_master(legacy: pd.DataFrame, canonical: pd.DataFrame,
                          issuance_items: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    legacy_identity = legacy[["market","symbol","name"]].copy()
    daily_identity = canonical[["market","symbol","name","isin"]].drop_duplicates(["market","symbol"])
    seed = legacy_identity.merge(daily_identity, on=["market","symbol"], how="outer",
                                 suffixes=("_legacy","_daily"), validate="one_to_one")
    seed["name"] = seed["name_legacy"].fillna(seed["name_daily"])
    rows = pd.DataFrame(issuance_items).copy()
    if rows.empty:
        raise ValueError("issuance snapshot is empty")
    if rows.duplicated("isinCd").any():
        raise ValueError("issuance snapshot has duplicate ISIN")
    fields = ["basDt","crno","isinCd","stckIssuCmpyNm","scrsItmsKcd","scrsItmsKcdNm",
              "stckParPrc","issuStckCnt","lstgDt","lstgAbolDt","dpsgRegDt","dpsgCanDt"]
    joined = seed.merge(rows[fields], left_on="isin", right_on="isinCd", how="left", validate="many_to_one")
    matched = joined["isinCd"].notna()
    result = pd.DataFrame({
        "symbol":joined["symbol"], "name":joined["name"], "market":joined["market"],
        "isin":joined["isin"], "corp_no":joined["crno"].map(_nullable),
        "company_name":joined["stckIssuCmpyNm"].map(_nullable),
        "security_type_code":joined["scrsItmsKcd"].map(_nullable),
        "security_type_name":joined["scrsItmsKcdNm"].map(_nullable),
        "par_value":joined["stckParPrc"].map(_number),
        "issued_shares":joined["issuStckCnt"].map(lambda value:_number(value, True)),
        "listing_date":joined["lstgDt"].map(_nullable), "delisting_date":joined["lstgAbolDt"].map(_nullable),
        "deposit_registration_date":joined["dpsgRegDt"].map(_nullable),
        "deposit_cancellation_date":joined["dpsgCanDt"].map(_nullable),
        "source":matched.map(lambda value:"data_go_kr_stock_issuance" if value else "daily_source_identity"),
        "source_date":joined["basDt"].map(_nullable),
    })
    result = result[list(KR_EQUITY_MASTER.column_names)].sort_values(
        list(KR_EQUITY_MASTER.sort_key), kind="stable").reset_index(drop=True)
    validate_equity_master(result)
    return result
