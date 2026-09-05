# Korean issuer fundamentals source

Status: `NORMALIZED_ACTIVE / DISPLAY_AND_SCANNER_ONLY / WEEKLY_AUTOMATION_ACTIVE / PIT_BLOCKED`

Review date: 2026-09-05 KST. This reconciliation used checked-in
documentation and code plus a read-only PyArrow scan of the retained dataset.
It made no provider call and did not read or print any credential value.

## Current decision

OpenDART is the implemented source for Korean quarterly issuer fundamentals.
The official `corpCode.xml` and `fnlttSinglAcntAll.json` APIs feed the
Landing-first collector and the Normalized `kr_corp_code_map` and
`kr_fundamentals_quarterly` datasets. At this review,
`pyarrow.dataset.dataset(..., partitioning=None)` reports 1,506 retained
fundamentals rows for 158 unique securities.

The accepted use remains local display and scanner support only. Filing
availability, complete revision history, non-calendar fiscal periods, and
historical point-in-time behavior are not closed, so Backtest and predictive
use remain blocked.

## Source and rights basis

The [OpenDART source record](opendart/README.md) lists the official developer
guide, corporation-code API, all-financial-statements API, and OpenDART terms as
checked on 2026-09-03. It also records the user's 2026-09-03 acceptance of
personal-use retention for this project. That is the repository's current
rights basis for the retained local display/scanner route; it does not authorize
redistribution.

The API key is loaded from the project environment as `OPENDART_API_KEY`, with
the documented compatibility spelling handled by the collector. Never print
the key, a key-bearing URL, query parameters containing it, or `.env` contents.

## Implemented contract and consumers

| Area | Current route |
|---|---|
| Provider | OpenDART official API: `corpCode.xml`, `fnlttSinglAcntAll.json` |
| Collector | `scripts/manual/collect/refresh_kr_fundamentals.py` |
| Contract/provider/orchestration | `kr_fundamentals.py`, `opendart_fundamentals.py`, `kr_fundamentals_quarterly.py` |
| Storage | immutable `data/landing/opendart/kr_fundamentals_quarterly/`; `data/normalized/{kr_corp_code_map,kr_fundamentals_quarterly}/` |
| Stock page | `src/stock_web/api/stock_detail.py` reads the dataset with `partitioning=None` and supplies the quarterly financial table: revenue, operating income, net income, operating margin, and debt ratio |
| Scanner | `src/stock_web/api/scanner.py` reads `kr_fundamentals_quarterly` for `debt_ratio_pct`, four-quarter operating/net-income signs, revenue trend, financial coverage, and the debt-ratio/value-trap path |

The revision-preserving key is
`(symbol, bsns_year, reprt_code, fs_div, rcept_no)`. CFS is preferred; OFS is
used only after a captured CFS `013`. Q1/Q2/Q3 use the documented three-month
amount. Q4 is annual less Q3 cumulative within the same scope and currency;
missing or incompatible operands yield null. `debt_ratio_pct` is liabilities
divided by equity times 100 and is null for missing or non-positive equity.

## Collector commands

The real bounded two-step entry point is documented in the
[OpenDART source record](opendart/README.md). A live capture uses:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe .\scripts\manual\collect\refresh_kr_fundamentals.py --project-root . --years 2024,2025,2026 --max-calls 200 --confirm-live-landing-only
```

After reviewing the returned checkpoint and candidate fingerprints, promotion
is offline and makes no provider call:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe .\scripts\manual\collect\refresh_kr_fundamentals.py --project-root . --promote-checkpoint <checkpoint.json> --confirm-offline-promotion --approval-digest <approval_digest>
```

The active cadence is the [weekly fundamentals lane](../operations/KR_FUNDAMENTALS_WEEKLY.md):
`KR_FUNDAMENTALS_WEEKLY` runs inside the existing 20:30 `KR_MARKET_DAILY`
bundle only on the last XKRX session of the ISO week. It prioritizes Korean
watchlist stocks, unions retained fundamentals symbols, caps the plan at 200
symbols and 2,600 calls, and otherwise returns `SKIPPED_NOT_REFRESH_DAY` before
credentials or network. The runbook records the exact dry-run and live lane
commands.

## Other source decisions

- KRX/pykrx `MDCSTAT03501` supplies daily PER/PBR/EPS/BPS/DPS/dividend-yield
  facts, not the quarterly liabilities, equity, revenue, operating-income, and
  net-income statements used here.
- KRX KIND remains a reference candidate, not the implemented structured
  machine route.
- Naver/FnGuide scraping remains rejected. A licensed documented API product
  would require its own source review and contract.

## Boundaries still in force

- Join only on exact six-digit `stock_code`, never issuer-name text.
- Keep Raw account rows in immutable Landing responses.
- Append later receipts as vintages; derive the latest correction at read time.
- Preserve source scope, currency, period, receipt, and retrieval identity.
- Do not use the retained data for Backtest, prediction, or redistribution
  without closing the corresponding PIT or rights question.
