"""Frozen three-call H4 market/metric boundary parity plan."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from scripts.manual import a007_investor_h4_boundary_diagnostic_support as boundary
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped
PYKRX_VERSION="1.2.8"; BUSINESS_BLD=boundary.BUSINESS_BLD; BUSINESS_ENDPOINT_PATH=boundary.BUSINESS_ENDPOINT_PATH
MAX_BUSINESS_REQUESTS=3; MAX_RAW_HTTP_REQUESTS=EXPECTED_RAW_HTTP_REQUESTS=8; REQUIRE_ZERO_RETRY_AUTH_SESSION=True
SCOPE_ID="20170519_20170522_investor_boundary_parity"; SCOPE=boundary.SCOPE
SCOPES=(
 {"name":"KOSPI_trading_value","strtDd":"20170519","endDd":"20170522","inqCondTpCd":2,"mktTpCd":1},
 {"name":"KOSDAQ_volume","strtDd":"20170519","endDd":"20170522","inqCondTpCd":1,"mktTpCd":2},
 {"name":"KOSDAQ_trading_value","strtDd":"20170519","endDd":"20170522","inqCondTpCd":2,"mktTpCd":2},)
SCOPE_IDS=tuple(x["name"] for x in SCOPES)
EXPECTED_BUSINESS_DATA=tuple({"bld":BUSINESS_BLD,**{k:str(v) for k,v in x.items() if k!="name"}} for x in SCOPES)
EXPECTED_DATE_COUNT=2; EXPECTED_DATE_SHA256=boundary.EXPECTED_DATE_SHA256
def expected_dates(root:Path):
 d=boundary.expected_dates(root)
 # Both retained market calendars must contain the same pair.
 import pyarrow.parquet as pq
 resolved=root.resolve(); p=(resolved/"data/published/kr_equity_canonical_universe_daily/market=KOSDAQ/year=2017/data.parquet").resolve()
 try: p.relative_to(resolved)
 except ValueError as error: raise PilotStopped("KOSDAQ_CANONICAL_SOURCE_PATH_ESCAPE") from error
 if not p.is_file() or p.is_symlink(): raise PilotStopped("KOSDAQ_CANONICAL_SOURCE_MISSING")
 try: raw=p.read_bytes()
 except OSError as error: raise PilotStopped("KOSDAQ_CANONICAL_SOURCE_UNREADABLE") from error
 if len(raw)!=1224194 or hashlib.sha256(raw).hexdigest()!="ac4cf7679b9692208fb1158a5a8ba1aa529b833f787cc47941b9894ea049b0a3": raise PilotStopped("KOSDAQ_CANONICAL_SOURCE_CHANGED")
 kd=tuple(sorted({str(v).replace('-','') for v in pq.read_table(p,columns=['date'])['date'].to_pylist() if "20170519"<=str(v).replace('-','')<="20170522"}))
 if kd!=d: raise PilotStopped("MARKET_CALENDAR_PAIR_MISMATCH")
 return d
def scope_sha256(dates): return hashlib.sha256(json.dumps({"bld":BUSINESS_BLD,"dates":dates,"scopes":SCOPES},sort_keys=True,separators=(",",":")).encode()).hexdigest()
def manifest_payload(*,run_id,created_at_utc,dates): return {"version":1,"run_id":run_id,"created_at_utc":created_at_utc,"purpose":"H4_boundary_market_metric_parity","scope_id":SCOPE_ID,"scopes":list(SCOPES),"expected_dates":list(dates),"expected_date_count":2,"expected_date_sha256":EXPECTED_DATE_SHA256,"scope_sha256":scope_sha256(dates),"business_request_limit":3,"raw_http_request_limit":8,"raw_http_requests_expected":8,"retry_count":0,"parallelism":1,"checkpoint_writes":False,"normalized_writes":False,"pykrx_version":PYKRX_VERSION}
def classify_responses(bodies,dates):
 results=[boundary.classify_response(b,dates) for b in bodies]
 names=[r.classification for r in results]
 if names==["BOUNDARY_SHAPED_CONFIRMED"]*3: return "SHARED_BOUNDARY_SHAPED_CONFIRMED",results
 if all(n in {"BOUNDARY_SHAPED_CONFIRMED","RANGE_WINDOW_EFFECT"} for n in names): return "METRIC_OR_MARKET_SPECIFIC_WINDOW_EFFECT",results
 raise PilotStopped("AMBIGUOUS_STOP:PARITY")

def aggregate_classifications(results):
 names=[r.classification for r in results]
 if names==["BOUNDARY_SHAPED_CONFIRMED"]*3: return "SHARED_BOUNDARY_SHAPED_CONFIRMED"
 if len(names)==3 and all(n in {"BOUNDARY_SHAPED_CONFIRMED","RANGE_WINDOW_EFFECT"} for n in names): return "METRIC_OR_MARKET_SPECIFIC_WINDOW_EFFECT"
 raise PilotStopped("AMBIGUOUS_STOP:PARITY")
