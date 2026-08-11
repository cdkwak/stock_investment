from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.investor_bridge import KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY


NET_COLUMNS = (
    "institution_net_purchase",
    "other_corporation_net_purchase",
    "individual_net_purchase",
    "foreign_net_purchase",
    "total_net_purchase",
)


def validate_investor_bridge(frame: pd.DataFrame) -> None:
    contract = KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY
    if frame.empty or tuple(frame.columns) != contract.column_names:
        raise ValueError("investor bridge schema is invalid or empty")
    if frame.duplicated(list(contract.primary_key)).any():
        raise ValueError("investor bridge has duplicate primary keys")
    if frame.isna().drop(columns=["availability_date"]).any().any():
        raise ValueError("investor bridge has invalid nulls")
    dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise ValueError("investor bridge has invalid dates")
    if not frame["market"].isin({"KOSPI", "KOSDAQ"}).all():
        raise ValueError("investor bridge has invalid markets")
    numeric = frame[list(NET_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError("investor bridge has invalid numeric values")
    if not numeric.apply(lambda column: column.mod(1).eq(0).all()).all():
        raise ValueError("investor bridge values must be integral")
    if not numeric[list(NET_COLUMNS[:-1])].sum(axis=1).eq(numeric["total_net_purchase"]).all():
        raise ValueError("investor bridge category sum differs from total")

    legacy = frame["provider_segment"].eq("legacy_pre_a001")
    toss = frame["provider_segment"].eq("toss_a001")
    if not (legacy | toss).all():
        raise ValueError("investor bridge has unknown provider segment")
    if not frame.loc[legacy, "date"].astype(str).le("2014-06-30").all():
        raise ValueError("legacy bridge row crosses provider boundary")
    if not frame.loc[toss, "date"].astype(str).ge("2014-07-01").all():
        raise ValueError("Toss bridge row crosses provider boundary")
    if not frame.loc[legacy, "market"].eq("KOSPI").all():
        raise ValueError("legacy bridge market must be KOSPI")
    if not frame.loc[legacy, "value_unit"].eq("unit_unknown").all():
        raise ValueError("legacy bridge unit must remain unknown")
    if frame.loc[legacy, "availability_date"].notna().any():
        raise ValueError("legacy bridge availability must remain unknown")
    if not frame.loc[legacy, "predictive_use_status"].eq("blocked_unknown_unit_and_availability").all():
        raise ValueError("legacy bridge predictive status is invalid")
    legacy_provenance = {
        "source_dataset": "kr_market_investor_net_purchase_daily",
        "source_provider": "legacy_stock_investment_pykrx_1.2.8",
        "source_operation": "MDCSTAT02202",
    }
    if any(not frame.loc[legacy, column].eq(value).all() for column, value in legacy_provenance.items()):
        raise ValueError("legacy bridge provenance is invalid")
    if not frame.loc[toss, "value_unit"].eq("KRW").all():
        raise ValueError("Toss bridge unit must be KRW")
    toss_availability = pd.to_datetime(frame.loc[toss, "availability_date"], format="%Y-%m-%d", errors="coerce")
    if toss_availability.isna().any():
        raise ValueError("Toss bridge availability is invalid")
    if not frame.loc[toss, "predictive_use_status"].eq("eligible_from_availability_date").all():
        raise ValueError("Toss bridge predictive status is invalid")
    toss_provenance = {
        "source_dataset": "kr_market_investor_trading_daily",
        "source_provider": "tossinvest_open_api",
        "source_operation": "getMarketIndicatorInvestorTrading",
    }
    if any(not frame.loc[toss, column].eq(value).all() for column, value in toss_provenance.items()):
        raise ValueError("Toss bridge provenance is invalid")
    ordered = frame.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    if not ordered.equals(frame.reset_index(drop=True)):
        raise ValueError("investor bridge is not sorted")
