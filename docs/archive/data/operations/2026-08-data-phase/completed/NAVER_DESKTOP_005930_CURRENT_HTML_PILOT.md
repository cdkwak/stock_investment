# Naver desktop 005930 current HTML pilot

Status: `FAILED_BOUNDED_SCHEMA_GATE / NUMERIC_DATA_NOT_ACCEPTED / NO_REPEAT`

## Completed exact operation

The sole authorized operation was completed for 2026-08-21.

| Field | Completed result |
|---|---|
| URL / identity | `https://finance.naver.com/item/main.naver?code=005930` / `KR_EQUITY_CURRENT / XKRX / 005930` |
| Raw GET budget/result | `1 / 1` |
| Timeout / retry / redirect / fallback | `10 seconds / 0 / 0 / 0` |
| Durable claim | `ATTEMPTING` persisted before transport construction; terminal state is `FAILED` |
| Landing | `data/landing/naver_desktop_005930_current_html/ur174/2026-08-21/20260821T060733134582Z_ffd2ba4a1fb3c7885490beb197d2b0ce77b14fe27f7ba832669097ff40a9e136/response.html` |
| Landing bytes / SHA-256 | `195101` / `ffd2ba4a1fb3c7885490beb197d2b0ce77b14fe27f7ba832669097ff40a9e136` |
| Landing readback | digest matched durable state |
| Typed outcome | `NAVER_DESKTOP_005930_NAVERDESKTOPHTMLOBSERVATIONERROR`: required direct same-body observation schema missing |
| Projection | none; `data/state/current_observations/naver_desktop_005930_current.json` remains absent |
| Local replay | raw GET `0`; replay API calls `0` |

The retained HTML body did not directly bind the exact identity to a KRX venue,
`KRW per share` unit, timezone-aware provider timestamp on 2026-08-21 KST
within 60 minutes, and eligible session/delay state. No price, source time, or
session meaning was inferred from retrieval time, neighboring text, or market
convention.

## No-repeat boundary

Do not rerun this URL/date or substitute another host, path, symbol, polling,
mobile, FDR, broker, or historical route. This result is limited to the exact
desktop HTML route; it is not a Naver-wide or Korean-quote-wide conclusion.

Any materially different future route requires a new active runbook, separate
fixed budget, durable preclaim, Landing-first validation, and source-semantic
contract.

Detailed sanitized evidence is
`artifacts/agent_runs/ur174/naver_desktop_005930_current_html_pilot_20260821.md`.
