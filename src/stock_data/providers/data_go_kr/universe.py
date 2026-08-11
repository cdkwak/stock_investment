from typing import Mapping, Sequence
import pandas as pd

from stock_data.contracts.kr_equity import KR_EQUITY_UNIVERSE_DAILY
from stock_data.validation.data_v1 import validate_data_v1


UNIVERSE_ENDPOINT = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"


def normalize_universe_items(items: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    rows = []
    for item in items:
        market = str(item.get("mrktCtg", "")).strip()
        if market not in {"KOSPI", "KOSDAQ"}:
            continue
        parsed = pd.to_datetime(str(item.get("basDt", "")), format="%Y%m%d", errors="coerce")
        if pd.isna(parsed):
            raise ValueError("data.go.kr universe basDt is invalid")
        symbol = str(item.get("srtnCd", "")).strip().removeprefix("A")
        required = {"isinCd", "itmsNm", "corpNm"}
        if not symbol or any(not str(item.get(field, "")).strip() for field in required):
            raise ValueError("data.go.kr universe identity field is missing")
        corporate_number = str(item.get("crno", "")).strip() or None
        source_date = parsed.strftime("%Y-%m-%d")
        rows.append({"date":source_date, "market":market, "symbol":symbol,
                     "isin":str(item["isinCd"]).strip(), "name":str(item["itmsNm"]).strip(),
                     "short_name":None, "english_name":None, "security_group":None,
                     "security_type":None, "listing_date":None, "listed_shares":None,
                     "par_value":None, "corporate_number":corporate_number,
                     "corporate_name":str(item["corpNm"]).strip(), "source":"data_go_kr",
                     "source_operation":"getItemInfo", "source_date":source_date})
    frame = pd.DataFrame(rows, columns=KR_EQUITY_UNIVERSE_DAILY.column_names)
    frame = frame.sort_values(list(KR_EQUITY_UNIVERSE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_data_v1(frame, KR_EQUITY_UNIVERSE_DAILY)
    return frame
