# OpenDART

## Status

- Quarterly fundamentals: `MANUAL_TWO_STEP / AUTOMATION_DISABLED / DISPLAY_AND_SCANNER_ONLY`.
- Corporate-action observations remain bounded source research.
- Backtest and predictive use are blocked until filing-availability, revision,
  fiscal-calendar, and historical PIT review is complete.

## Official references checked 2026-09-03

- [OpenDART developer guide](https://opendart.fss.or.kr/guide/main.do)
- [Corporation code](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018)
- [Single-company all financial statements](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020)
- [OpenDART 이용약관](https://opendart.fss.or.kr/intro/terms.do)

The following are **VERIFIED** from those official guide pages:

- `GET https://opendart.fss.or.kr/api/corpCode.xml` takes only `crtfc_key`
  and returns a ZIP containing XML fields `corp_code`, `corp_name`,
  `corp_eng_name`, `stock_code`, and `modify_date`.
- `GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json` takes
  `crtfc_key`, `corp_code`, `bsns_year`, `reprt_code`, and `fs_div`.
- `bsns_year` is available from 2015; report codes are `11013` (Q1),
  `11012` (half-year), `11014` (Q3), and `11011` (annual); request scope is
  `CFS` or `OFS`.
- Documented response fields include `rcept_no`, `reprt_code`, `bsns_year`,
  `corp_code`, `sj_div`, `account_id`, `account_nm`, `thstrm_amount`,
  `thstrm_add_amount`, `frmtrm_amount`, and `frmtrm_add_amount`. The guide
  lists `BS`, `IS`, `CIS`, `CF`, and `SCE` statement divisions.
- For interim `(포괄)손익계산서` rows, `thstrm_amount` is the three-month
  amount and `thstrm_add_amount` is cumulative.
- Status `013` means no data. Status `020` means the request limit was
  exceeded and generally occurs above about 20,000 requests, while the guide
  warns that a different account limit may apply. The runner treats `020` as
  a hard stop for that provider day.

The user accepted personal-use retention for this project on 2026-09-03.
That decision does not authorize redistribution or relax the current
display/scanner-only and PIT-review boundaries.

## Authentication and request safety

- Primary environment lookup: `os.environ["OPENDART_API_KEY"]`.
- Compatibility fallback: `os.environ.get("OpenDART_API_KEY")` because the
  local `.env` uses that spelling and Windows environment lookup is normally
  case-insensitive.
- API query key: `crtfc_key`.
- Never print a prepared URL, query parameters containing the key, the key,
  or `.env` contents. Manifests and ledgers contain only endpoint paths and
  public request parameters.
- Timeout is 20 seconds, retry count is zero, requests are serial, and spacing
  is 0.2 seconds. Every response is written immutably to Landing before it is
  parsed. HTTP status is validated; financial JSON and non-ZIP corporation-map
  error XML must also carry validated OpenDART `status` and `message` values.

## Normalized datasets

| Dataset | Key | Policy |
|---|---|---|
| `kr_corp_code_map` | `corp_code` | Current exact identity map; refresh no more than once per seven days; blank unlisted `stock_code` becomes null |
| `kr_fundamentals_quarterly` | `(symbol, bsns_year, reprt_code, fs_div, rcept_no)` | Append filing vintages; never replace another receipt; derive latest correction and CFS-first scope only at read time |

The all-accounts guide documents `fs_div` as a **request parameter**, not as
an account-row response field. The collector therefore validates the request
scope and attaches it to every parsed row. If a payload happens to include an
`fs_div` field, it must agree with the request.

`period_end` uses the final date token in a non-blank response `thstrm_dt`
(including range forms such as `2026.01.01 ~ 2026.06.30`). The calendar-quarter
mapping by `reprt_code` is used only when `thstrm_dt` is absent. Every normalized
row must satisfy `period_end <= rcept_no[:8]`; an unsafe row is excluded and its
reason is counted in the run checkpoint/receipt before candidate promotion.

## Account mapping and quarter values

| Scanner fact | Preferred standard IDs | Explicit Korean-name fallback | Statement / amount |
|---|---|---|---|
| `revenue` | `ifrs-full_Revenue`, `dart_OperatingRevenue`, `dart_Revenue` | `매출액`, `영업수익` | `IS`, else `CIS`; interim `thstrm_amount` |
| `operating_income` | `dart_OperatingIncomeLoss`, `ifrs-full_ProfitLossFromOperatingActivities` | `영업이익`, `영업이익(손실)`, `영업손익` | `IS`, else `CIS`; interim `thstrm_amount` |
| `net_income` | `ifrs-full_ProfitLoss`, `dart_ProfitLoss` | `당기순이익`, `당기순이익(손실)`, `당기순손익` | `IS`, else `CIS`; interim `thstrm_amount` |
| `total_liabilities` | `ifrs-full_Liabilities` | `부채총계` | `BS`; point-in-time `thstrm_amount` |
| `total_equity` | `ifrs-full_Equity` | `자본총계` | `BS`; point-in-time `thstrm_amount` |

For Q1, Q2/half-year, and Q3 the normalized income facts use the officially
documented three-month `thstrm_amount` directly. Q4 uses:

```text
annual 11011 thstrm_amount - Q3 11014 thstrm_add_amount
```

Both operands must be in the same statement scope and currency. Missing Q3,
missing operands, or mixed currencies yields null rather than a guess.
`debt_ratio_pct` is liabilities/equity × 100 and is null when either input is
missing or equity is zero/negative.

## Explicitly UNVERIFIED assumptions

- The guide does not guarantee that the listed standard account IDs or Korean
  names are uniform across all issuers. The deterministic ID/name table above
  is project mapping policy and must be extended only with retained evidence.
- When a response omits `thstrm_dt`, `03-31`, `06-30`, `09-30`, and `12-31`
  remain an explicit fallback convention. This fallback is not evidence of a
  non-calendar-year issuer's actual fiscal period.
- `013` is officially no data, but using an OFS call after a CFS `013` is a
  project fallback rule, not an OpenDART guarantee that no consolidated filing
  exists or will later appear.
- `retrieved_at` proves when this collector possessed a response, not the
  filing's original publication instant. Historical availability, correction
  timing, and complete revision history remain unverified; therefore the data
  is not backtest/PIT eligible.
- The 20,000 figure is described as a general threshold, not a guaranteed
  account quota. The ledger's `calls_today` is the count observed by this local
  collector, not an account-wide server counter.

## Two-step operation

Default symbols are the exact KOSPI/KOSDAQ symbols in
`artifacts/local_user/watchlists.json`. `--universe` selects all retained
listed Korean symbols. The first watchlist run for 2024–2026 is expected to be
about 30 HTTP calls for a small local watchlist; the actual number depends on
symbol count, weekly corp-map reuse, and OFS fallbacks. The hard default bound
is 200.

Exact first live command (run by a human; this task did not run it):

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\manual\collect\refresh_kr_fundamentals.py --project-root . --years 2024,2025,2026 --max-calls 200 --confirm-live-landing-only
```

Review the returned checkpoint and candidate fingerprints, then promote with
zero network access:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\manual\collect\refresh_kr_fundamentals.py --project-root . --promote-checkpoint <checkpoint.json> --confirm-offline-promotion --approval-digest <approval_digest>
```

To repair retained unsafe period ends without a network call or API key, run:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -c "from pathlib import Path; from stock_data.orchestration.kr_fundamentals_quarterly import repair_period_end; print(repair_period_end(Path('.')))"
```

The repair recomputes an unsafe row from a matching immutable Landing response
when it contains one unambiguous safe `thstrm_dt`; otherwise it removes that row.
It validates the complete result and replaces the Normalized root atomically.

## Code routes

- Contracts: `src/stock_data/contracts/kr_fundamentals.py`
- Provider parser: `src/stock_data/providers/opendart_fundamentals.py`
- Operation and scanner helper: `src/stock_data/orchestration/kr_fundamentals_quarterly.py`
- Manual runner: `scripts/manual/collect/refresh_kr_fundamentals.py`
- Older corporate-action source parser: `src/stock_data/providers/opendart_free_issue.py`

The older corporate-action route continues to use `list.json`,
`fricDecsn.json`, and `pifricDecsn.json`; this fundamentals collector does not
change those endpoints or their source-observation boundary.

## Boundaries

- Join on exact six-digit `stock_code`; never issuer-name text.
- Raw account rows remain in immutable Landing response bodies.
- A later `rcept_no` appends a vintage. `is_latest` is never stored and is
  derived from `retrieved_at` at read time.
- CFS is selected when available; OFS is used only after a captured CFS `013`.
- Display/scanner use never implies predictive or backtest eligibility.
