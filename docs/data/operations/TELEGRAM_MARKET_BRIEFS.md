# Telegram Market Briefs

## Purpose and boundary

`scripts/maintenance/telegram_agent_bridge.py` sends compact Korean market
briefs to the allowlisted private Telegram chat. Morning and close reports use
an ephemeral read-only Codex process with live web research. The conditions
report uses retained local watchlist data only: no Codex and no web request.
None of these routes may place orders, mutate accounts, or promote market data.

## Active report routes

| Kind | Windows task | KST schedule | Output |
|---|---|---:|---|
| `morning` | `STOCK_TELEGRAM_MORNING_BRIEF` | Weekdays 07:30 | A-style morning brief, at most 22 lines |
| `close` | `STOCK_TELEGRAM_KR_CLOSE_BRIEF` | Weekdays 16:10 | A-style close brief, at most 18 generated lines, plus any local condition block |
| `conditions` | `STOCK_TELEGRAM_KR_CONDITIONS` | Weekdays 20:50 | Local condition block only; no message when there are no hits |

Before building the 16:10 close message's condition block, the bridge performs
an in-process same-day refresh through the existing
`KR_EQUITY_PROVISIONAL_DAILY` and `KR_ETF_PRICE_DAILY` scheduler lane runners.
It never spawns or changes a Windows task. The refresh is eligible only on an
XKRX trading day at or after 15:40 KST, is API-zero when the retained Korean
watchlist maximum already equals that session, and refuses any next lane whose
addition would exceed the eight-call ceiling. Earlier completed lanes remain
retained and the affected older rows use the mixed-basis labels below. A refresh
exception is fail-open for Telegram delivery and is recorded as
`sameday_refresh: failed · <type>` in the persisted brief. The separate 20:50
conditions route is unchanged: it performs no same-day pre-refresh and sends
only current retained hits after the 20:30 bundle.

## Message contract

- Use number-table style with one fact per line and numbers before words.
- Keep every generated brief line at 40 characters or fewer.
- Use `▲` and `▼` for percentage direction; preserve signed flow amounts.
- Separate sections with a line containing only `─`.
- Use emoji only on section headings. Omit the project/system health section.
- Put source names only, without URLs, on the penultimate line.
- End generated briefs exactly with `※ 사실·시나리오 구분, 투자 조언 아님`.
- The deterministic normalizer removes Markdown link targets, collapses blank
  runs, normalizes unambiguous index percentage signs, and truncates only at a
  complete-line boundary within the Telegram message limit.

The local condition block starts with `📌 관심종목 (MM/DD 마감 기준)`, emits
at most eight matching rows, and ends with `설명용 · 신호 아님`. A maximum-date
row with `price_basis="provisional"` changes the header to
`(MM/DD 잠정 마감 기준)`. When displayed rows use earlier dates, the header adds
` · 일부 전일` and each earlier price adds its own `(MM/DD)` date. Persisted
brief front matter includes `basis_date` (ISO date or `null`) and
`sameday_refresh`; morning uses `not_applicable`, and the conditions message
body remains unchanged. A conditions block is persisted to
`artifacts/local_user/briefs/YYYY-MM-DD-conditions.md` only after a successful
send. A no-hit run logs `report conditions skipped=no_hits` and creates no file.

## Manual execution

From the repository root:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.venv\Scripts\python.exe scripts\maintenance\telegram_agent_bridge.py report morning
.venv\Scripts\python.exe scripts\maintenance\telegram_agent_bridge.py report close
.venv\Scripts\python.exe scripts\maintenance\telegram_agent_bridge.py --report conditions
```

## Register the post-close task

No `scripts/maintenance/register_*telegram*.ps1` registration script exists in
this checkout. Run the following from PowerShell under the same Windows account
that owns the existing close-brief task. Its action is the close-brief bridge
action with `report close` replaced by `report conditions`:

```powershell
schtasks.exe /Create /F /TN "STOCK_TELEGRAM_KR_CONDITIONS" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 20:50 /TR 'cmd.exe /d /c "set PYTHONIOENCODING=utf-8&&C:\Users\k4545\Desktop\stock_investment_rev1\.venv\Scripts\python.exe C:\Users\k4545\Desktop\stock_investment_rev1\scripts\maintenance\telegram_agent_bridge.py --report conditions"'
```

This command creates or replaces only the named conditions task. Do not run it
from tests; tests mock both Codex and Telegram and require no network.
