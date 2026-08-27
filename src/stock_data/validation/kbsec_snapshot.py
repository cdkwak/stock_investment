import pandas as pd


def validate_kb_snapshot(dataframe: pd.DataFrame, contract) -> None:
    if list(dataframe.columns) != list(contract.column_names) or dataframe.empty:
        raise ValueError(f"{contract.name} schema is invalid or empty")
    if pd.to_datetime(dataframe["capture_date"], format="%Y-%m-%d", errors="coerce").isna().any():
        raise ValueError("invalid capture_date")
    for name in ("market_date", "reference_date"):
        present = dataframe[name].notna()
        if pd.to_datetime(dataframe.loc[present, name], format="%Y-%m-%d", errors="coerce").isna().any():
            raise ValueError(f"invalid {name}")
    timestamps = pd.to_datetime(dataframe["collected_at"], utc=True, errors="coerce")
    if timestamps.isna().any(): raise ValueError("invalid collected_at")
    if not dataframe["provider"].eq("KB_SECURITIES").all() or not dataframe["source_operation"].eq("IVSA0070").all(): raise ValueError("invalid provenance")
    if not dataframe["is_provisional"].eq(True).all(): raise ValueError("KB snapshot must be provisional")
    if not dataframe["availability_status"].isin({"CURRENT_DAY_CLOSE", "PREVIOUS_DAY_CLOSE", "INTRADAY_NIGHT", "LAGGED_SOURCE_DATE", "DATE_UNRESOLVED"}).all():
        raise ValueError("invalid availability_status")
    if not dataframe["value_status"].isin({"SOURCE_VALUE", "PARTIAL_UNAVAILABLE", "UNAVAILABLE"}).all():
        raise ValueError("invalid value_status")
    if contract.name == "kb_investor_flow_snapshot":
        status = dataframe["derivatives_flow_status"]
        if not status.isin({"SOURCE_VALUE", "UNAVAILABLE_FROM_IVSA0070"}).all():
            raise ValueError("invalid derivatives_flow_status")
        derivative_fields = (
            "futures_net_buy", "call_option_net_buy", "put_option_net_buy", "star_futures_net_buy",
        )
        if dataframe.loc[status.eq("UNAVAILABLE_FROM_IVSA0070"), list(derivative_fields)].eq(0).any(axis=None):
            raise ValueError("unavailable IVSA0070 derivative zero was not quarantined")
    if dataframe.duplicated(list(contract.primary_key)).any(): raise ValueError("duplicate primary key")
    expected = dataframe.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    if not dataframe.reset_index(drop=True).equals(expected): raise ValueError("rows are not sorted")
