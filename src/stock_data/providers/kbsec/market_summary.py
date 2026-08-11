from __future__ import annotations

from datetime import datetime
import math
import re
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.providers.kbsec.client import KBSecResponse, KBSecResponseError

OPERATION = "IVSA0070"
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


def number(value):
    if value is None or (isinstance(value, str) and not value.strip()): return None
    if isinstance(value, bool): raise KBSecResponseError("boolean numeric field")
    raw = str(value).strip().replace(",", "")
    if not _NUMBER.fullmatch(raw): raise KBSecResponseError("invalid numeric field")
    result = float(raw) if "." in raw else int(raw)
    if isinstance(result, float) and not math.isfinite(result): raise KBSecResponseError("non-finite numeric field")
    return result


def _date(raw):
    digits = str(raw or "").strip()
    if len(digits) < 8: raise KBSecResponseError("inq_dy_tm is invalid")
    try: return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()
    except ValueError: raise KBSecResponseError("inq_dy_tm is invalid") from None


def normalize_market_summary(response: KBSecResponse, *, collected_at: datetime) -> dict[str, pd.DataFrame]:
    if collected_at.tzinfo is None: raise ValueError("collected_at must be timezone-aware")
    b = response.data_body; market_date = _date(b.get("inq_dy_tm"))
    common = {"snapshot_date": collected_at.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat(), "market_date": market_date,
              "collected_at": collected_at.isoformat(), "source": "kb_securities_open_api",
              "source_operation": OPERATION, "is_provisional": True}
    required = ("kspi_up_is_c", "kspi_unchng_is_c", "kspi_dwn_is_c", "ksdq_up_is_c", "ksdq_unchng_is_c", "ksdq_dwn_is_c")
    missing = [x for x in required if x not in b]
    if missing: raise KBSecResponseError("missing IVSA0070 fields: " + ", ".join(missing))

    breadth = []
    for market, prefix in (("KOSPI", "kspi"), ("KOSDAQ", "ksdq")):
        breadth.append({**common, "market": market, "upper_limit": number(b.get(prefix + "_ulmt_is_c")),
            "advancing": number(b[prefix + "_up_is_c"]), "unchanged": number(b[prefix + "_unchng_is_c"]),
            "declining": number(b[prefix + "_dwn_is_c"]), "lower_limit": number(b.get(prefix + "_llmt_is_c"))})
    program = [{**common, "arbitrage_net_buy": number(b.get("mprft_nt_b")), "non_arbitrage_net_buy": number(b.get("nmp_nt_b"))}]
    investor = []
    for row in _rows(b, "out5"):
        code = str(row.get("invstr_cd", "")).strip()
        if not code: continue
        investor.append({**common, "investor_code": code, "investor_name": str(row.get("invstr_clsf_nm", "")).strip(),
            **{name: number(row.get(field)) for name, field in (("kospi_net_buy","kspi_nt_b"),("kosdaq_net_buy","ksdq_nt_b"),("futures_net_buy","fts_nt_b"),("call_option_net_buy","call_opt_nt_b"),("put_option_net_buy","put_opt_nt_b"),("star_futures_net_buy","star_fts_nt_b"),("stock_futures_net_buy","stk_fts_nt_b"))}})
    liquidity = [{**common, **{name: number(b.get(field)) for name, field in (("customer_deposit","cs_dpst_5"),("customer_deposit_change","cs_dpst_cmpr_amt_5"),("receivables","rcvamt_5"),("receivables_change","rcvamt_cmpr_amt_5"),("credit_balance","crdt_blnc_5"),("credit_balance_change","crdt_blnc_cmpr_amt_5"),("futures_deposit","fts_tfnd_5"),("futures_deposit_change","fts_tfnd_cmpr_amt_5"))}}]
    derivatives = [_quote(common, r, "instrument", (("instrument_code","is_cd"),("instrument_name","is_nm"),("current_price","now_prc_p2"),("change_direction_code","bdy_cmpr_ccd"),("change","bdy_cmpr_p2"),("change_pct","up_dwn_r_p2"),("volume","vlm"),("open_interest","nstmt_agr_q"))) for r in _rows(b,"out3") if str(r.get("is_cd","")).strip()]
    domestic = [_quote(common, r, "index", (("index_code","indx_id"),("index_name","indx_nm"),("current_index","now_indx_p2"),("change_direction_code","bdy_cmpr_ccd"),("change","bdy_cmpr_p2"),("change_pct","up_dwn_r_p2"),("volume","vlm"),("trading_value","dl_tw_amt"))) for r in _rows(b,"out2") if str(r.get("indx_id","")).strip()]
    global_rows = [_quote(common, r, "symbol", (("symbol_code","symbl_cd"),("symbol_name","symbl_nm"),("source_datetime","dt_tm"),("current_price","now_prc"),("change_direction_code","bdy_cmpr_ccd"),("change","bdy_cmpr_p2"),("change_pct","up_dwn_r_p2"))) for r in _rows(b,"out4") if str(r.get("symbl_cd","")).strip()]
    names = ("kb_market_breadth_snapshot","kb_program_trading_snapshot","kb_investor_flow_snapshot","kb_market_liquidity_snapshot","kb_derivatives_summary_snapshot","kb_domestic_index_snapshot","kb_global_symbol_snapshot")
    return {name: pd.DataFrame(rows) for name, rows in zip(names, (breadth,program,investor,liquidity,derivatives,domestic,global_rows))}


def _rows(body, name):
    value = body.get(name, [])
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value): raise KBSecResponseError(f"{name} must be an array of objects")
    return value


def _quote(common, row, prefix, mapping):
    result = dict(common)
    for name, field in mapping:
        raw = row.get(field)
        result[name] = str(raw).strip() if name.endswith(("code","name","datetime")) else number(raw)
    return result
