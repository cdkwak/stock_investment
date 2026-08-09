from __future__ import annotations

from typing import Mapping, Sequence

import pandas as pd

from stock_data.contracts.kr_equity import KR_EQUITY_CANONICAL_UNIVERSE_DAILY


class CanonicalUniverseError(ValueError):
    pass


def price_identity_from_items(items: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    rows = []
    for item in items:
        market = str(item.get("mrktCtg", "")).strip()
        if market not in {"KOSPI", "KOSDAQ"}:
            continue
        parsed = pd.to_datetime(str(item.get("basDt", "")), format="%Y%m%d", errors="coerce")
        symbol = str(item.get("srtnCd", "")).strip().removeprefix("A")
        isin = str(item.get("isinCd", "")).strip()
        name = str(item.get("itmsNm", "")).strip()
        if pd.isna(parsed) or not symbol or not isin or not name:
            raise CanonicalUniverseError("price identity is incomplete")
        rows.append({"date":parsed.strftime("%Y-%m-%d"), "market":market, "symbol":symbol,
                     "isin":isin, "name":name})
    result = pd.DataFrame(rows, columns=["date","market","symbol","isin","name"])
    if result.duplicated(["date","market","symbol"]).any():
        raise CanonicalUniverseError("price identity has duplicate keys")
    return result.sort_values(["date","market","symbol"], kind="stable").reset_index(drop=True)


def _metadata(master: pd.DataFrame) -> pd.DataFrame:
    required = {"market", "symbol"}
    if not required.issubset(master.columns):
        raise CanonicalUniverseError("master identity columns are missing")
    if master.duplicated(["market", "symbol"]).any():
        raise CanonicalUniverseError("master has duplicate identity keys")
    result = master[["market", "symbol"]].copy()
    result["security_type"] = (
        master["security_type"] if "security_type" in master
        else master["security_type_name"] if "security_type_name" in master else None
    )
    for column in ("listing_date", "delisting_date"):
        result[column] = master[column] if column in master else None
    result["master_present"] = True
    return result


def _observed_security_type(name: str, isin: str, master_value) -> str:
    if master_value is not None and not pd.isna(master_value) and str(master_value).strip():
        return str(master_value).strip()
    if "우" in name:
        return "preferred_observed_name"
    if not isin.startswith("KR"):
        return "foreign_equity_observed_isin"
    return "unclassified"


def validate_canonical_universe(frame: pd.DataFrame) -> None:
    contract = KR_EQUITY_CANONICAL_UNIVERSE_DAILY
    if tuple(frame.columns) != contract.column_names or frame.empty:
        raise CanonicalUniverseError("canonical universe schema is invalid or empty")
    if frame.duplicated(list(contract.primary_key)).any():
        raise CanonicalUniverseError("canonical universe duplicate key")
    if frame[["date","market","symbol","isin","name","universe_source","security_type"]].isna().any().any():
        raise CanonicalUniverseError("canonical universe required value is missing")
    if not frame["market"].isin({"KOSPI","KOSDAQ"}).all():
        raise CanonicalUniverseError("canonical universe market is invalid")
    for column in ("listed_info_present","price_present","master_present"):
        if frame[column].dtype != bool:
            raise CanonicalUniverseError(f"{column} must be boolean")
    if (~(frame["listed_info_present"] | frame["price_present"])).any():
        raise CanonicalUniverseError("master-only row entered daily universe")
    expected = frame.apply(lambda row: "listed_info+price" if row.listed_info_present and row.price_present
                           else "listed_info" if row.listed_info_present else "price_only", axis=1)
    if not expected.equals(frame["universe_source"]):
        raise CanonicalUniverseError("provenance does not match presence flags")
    if frame.duplicated(["date","isin"]).any():
        raise CanonicalUniverseError("ISIN collision within date")
    ordered = frame.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    if not ordered.equals(frame.reset_index(drop=True)):
        raise CanonicalUniverseError("canonical universe is not sorted")


def build_canonical_universe(listed: pd.DataFrame, price_identity: pd.DataFrame,
                             master: pd.DataFrame) -> pd.DataFrame:
    keys = ["date","market","symbol"]
    listed_required = keys + ["isin","name"]
    if not set(listed_required).issubset(listed.columns):
        raise CanonicalUniverseError("listed universe identity columns are missing")
    if listed.duplicated(keys).any() or price_identity.duplicated(keys).any():
        raise CanonicalUniverseError("daily source duplicate key")
    left = listed[listed_required].rename(columns={"isin":"listed_isin","name":"listed_name"})
    right = price_identity[listed_required].rename(columns={"isin":"price_isin","name":"price_name"})
    merged = left.merge(right, on=keys, how="outer", validate="one_to_one", indicator=True)
    both = merged["_merge"].eq("both")
    conflict = both & merged["listed_isin"].ne(merged["price_isin"])
    if conflict.any():
        raise CanonicalUniverseError("daily sources disagree on ISIN")
    merged["listed_info_present"] = merged["_merge"].isin(["left_only","both"])
    merged["price_present"] = merged["_merge"].isin(["right_only","both"])
    merged["isin"] = merged["listed_isin"].fillna(merged["price_isin"])
    merged["name"] = merged["listed_name"].fillna(merged["price_name"])
    merged = merged.merge(_metadata(master), on=["market","symbol"], how="left", validate="many_to_one")
    merged["master_present"] = merged["master_present"].eq(True)
    merged["universe_source"] = merged.apply(
        lambda row: "listed_info+price" if row.listed_info_present and row.price_present
        else "listed_info" if row.listed_info_present else "price_only", axis=1)
    merged["security_type"] = merged.apply(
        lambda row: _observed_security_type(row["name"], row["isin"], row["security_type"]), axis=1)
    result = merged[list(KR_EQUITY_CANONICAL_UNIVERSE_DAILY.column_names)].sort_values(
        list(KR_EQUITY_CANONICAL_UNIVERSE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_canonical_universe(result)
    return result
