# UR-246 recurring Toss domestic 30-minute operation

Status: `AUTOMATION_ACTIVE / OUTCOME_RECEIPT_STATE_MACHINE_VALIDATED / NATURAL_20260826_1300_TERMINAL_COMPLETE_4_OF_4`.

The supported coordinator composes the UR-244 equity identities `000660`,
`005930` and the UR-245 index identities `KOSPI`, `KOSDAQ`; it does not call or
infer KOSPI200/KPI200. Every invocation begins with the offline
`ExchangeTradingCalendar(KR)` date gate. A closed date or an off-window returns
API zero before a daily manifest, runtime `.env` load, credential construction,
or transport.

On an open Korean session date, it creates and verifies exactly one immutable
date-scoped manifest and state ledger. It selects only the present half-open
30-minute KST boundary in `[09:00,15:30)`. Earlier boundaries never backfill.
Equities and indices are eligible only inside that same active collection
window; an off-window invocation preserves the last verified values and returns
API zero.

Each run has global OAuth `<=1`, serial business GET `<=4` in immutable order
`000660`, `005930`, `KOSPI`, `KOSDAQ`, timeout10/retry0/redirect0/fallback0.
Every route is durably claimed before transport; orphaned and terminal claims
are fail-closed/no-repeat. Successful bodies go Landing-first through SHA-256
readback, strict timestamp/unit/identity validation, independent atomic
display-only/PIT-blocked projections, prior preservation, and API-zero replay.
No account/order, GUI, canonical/history, Backtest, or KOSPI200 action exists.

The scheduler wrapper binds the current floored half-hour KST occurrence before
runtime credential or transport construction. It publishes one immutable,
sanitized claim under
`data/state/provider_scheduler/toss_domestic_ur246_occurrences/` and then one
immutable terminal receipt for the same UTC occurrence key. The terminal
receipt contains only the eligible/ineligible classification, fixed anonymous
route slots, bounded typed outcomes, OAuth/business call counts, terminal
status/exit code, failure-reason code, and UTC finish time. It never contains a
provider value, public route identity, URL, exception text, credential, token,
or account identifier. A verified atomic
`data/state/provider_scheduler/toss_domestic_ur246_last.json` pointer advances
only to a strictly newer occurrence and binds the exact immutable terminal
receipt. An equal occurrence is an idempotent no-op only when its entire
validated pointer is identical; an equal-time conflict, non-canonical receipt
path, or terminal/receipt mismatch fails closed before mutation. A
crash-released process-shared advisory lock is acquired before candidate
terminal validation or any pointer read, and serializes the complete
validate/compare/atomic-replace transaction. After the first updater releases
the lock, the second updater validates its candidate and re-reads the latest
pointer while holding the lock; it therefore observes a newly published pointer
instead of comparing against pre-lock state. A strictly older occurrence is an
exact no-op and cannot overwrite the newer pointer.
Disposable same-volume staging files exist only under
`.tmp/agents/toss-domestic-ur246/`; no required claim, terminal, pointer, or
replay evidence is kept there.

An exact terminal replay validates the immutable receipt and pointer before
returning the original typed outcomes with invocation OAuth/business counts
zero. An incomplete pre-existing claim, malformed claim/terminal/pointer, or
result-contract mismatch fails closed before another transport. A runtime or
result-contract exception after this invocation publishes its claim produces a
bounded terminal failure receipt without copying exception text. Windows
process result alone is never accepted as route-completion evidence.

The supported CLI is:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_toss_domestic_ur246.py --project-root . --confirm-ur246-window
```

The registration script targets `STOCK_DATA_TOSS_DOMESTIC_30M`: Monday–Friday
start 09:00 KST, 30-minute repetition for 6 hours, producing wakes from 09:00
through the duration endpoint at 15:00 while excluding 15:30. It applies `IgnoreNew` and a
25-minute execution limit, strictly shorter than the recurrence interval; the
durable process lock remains a second overlap guard. Registration performs a
semantic readback and does not trigger the task. The task
action uses the short supported wrapper `scripts/run_toss_domestic_ur246_task.cmd`
to stay below the Windows `schtasks.exe /TR` length limit while preserving the
verified PowerShell/Python runner chain. Its runtime still performs the
exchange-calendar and manifest checks, so a weekday holiday is API zero.

The 2026-08-26 installation readback was `Ready`, one trigger and one action,
with start `09:00`, repetition `PT30M`, duration `PT6H`, execution limit
`PT25M`, and `IgnoreNew`. The power-resilient definition allows battery starts,
does not stop when switching to battery, and wakes the computer. It also uses
`StartWhenAvailable=true`: a delayed wake never backfills an earlier boundary;
the coordinator selects only the current half-hour while the exact KRX window
is open, and otherwise returns API zero before credentials or transport.

The prior endpoint-inclusive `PT6H30M` definition produced one observed 15:30
`INELIGIBLE / API_ZERO` terminal and advanced the monotonic pointer beyond the
valid 15:00 due receipt. No provider call or numeric projection occurred. The
installed duration is now `PT6H`, and release readiness requires the exact
15:00 immutable terminal while allowing a strictly newer pointer only when its
own exact receipt is fully validated `INELIGIBLE`, OAuth/business calls are
both zero, and the exact due receipt remains present. Focused scheduler,
release, and Toss validation passes 161 tests; the provider-free release smoke
passes 10/10 definitions, 8/8 due groups, and cold GUI shutdown. Provider-free
pointer coverage additionally proves cross-process newer/older serialization
with a post-unlock latest-pointer re-read, abrupt `os._exit` lock release,
lock-timeout preservation, atomic-replacement failure preservation,
equal-occurrence conflict rejection, and exact terminal-to-receipt binding.

The first post-implementation natural occurrence ran at 13:00 KST without a
manual trigger. Windows returned process result `0`, while independent
provider-free readback of its immutable claim, terminal, and last pointer proved
the stronger result: `ELIGIBLE / TERMINAL_SUCCESS`, all four anonymous route
slots `COMPLETE`, OAuth `1`, business calls `4`, exit `0`, failure reason `NONE`,
and pointer/terminal scheduled time exactly `2026-08-26T13:00:00+09:00`. The
three-file forbidden-string scan found no public route identity, URL,
authentication/account key, or exception marker. The staging directory was
empty after publication. No Landing body or provider value was opened for this
inspection, and no manual replay was invoked.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_toss_domestic_ur246_task.ps1 -Action Install
```
