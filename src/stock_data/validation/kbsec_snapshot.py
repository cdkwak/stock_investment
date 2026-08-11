import pandas as pd


def validate_kb_snapshot(dataframe: pd.DataFrame, contract) -> None:
    if list(dataframe.columns) != list(contract.column_names) or dataframe.empty:
        raise ValueError(f"{contract.name} schema is invalid or empty")
    for name in ("snapshot_date", "market_date"):
        if pd.to_datetime(dataframe[name], format="%Y-%m-%d", errors="coerce").isna().any(): raise ValueError(f"invalid {name}")
    timestamps = pd.to_datetime(dataframe["collected_at"], utc=True, errors="coerce")
    if timestamps.isna().any(): raise ValueError("invalid collected_at")
    if not dataframe["source"].eq("kb_securities_open_api").all() or not dataframe["source_operation"].eq("IVSA0070").all(): raise ValueError("invalid provenance")
    if not dataframe["is_provisional"].eq(True).all(): raise ValueError("KB snapshot must be provisional")
    if dataframe.duplicated(list(contract.primary_key)).any(): raise ValueError("duplicate primary key")
    expected = dataframe.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    if not dataframe.reset_index(drop=True).equals(expected): raise ValueError("rows are not sorted")
