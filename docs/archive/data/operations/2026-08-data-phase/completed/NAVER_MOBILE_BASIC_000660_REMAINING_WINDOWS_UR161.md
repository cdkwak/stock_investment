# UR-161 Naver mobile-basic 000660 remaining KST windows

Status: **ALL_MANIFESTED_WINDOWS_FAILED_PRE_RESPONSE_20260821 / NO_REPEAT**

The immutable public activation manifest
`data/state/naver_mobile_basic_000660_ur161_activation.json` authorizes only:

| KST window ID | Raw GET cap | Status before due |
|---|---:|---|
| `2026-08-21T14:30:00+09:00` | 1 | no call |
| `2026-08-21T15:00:00+09:00` | 1 | no call |
| `2026-08-21T15:30:00+09:00` | 1 | no call |

All prior 13:00, 13:30 and 14:00 windows are immutable no-repeat. The
manifest-aware entrypoint calls `NaverMobileBasicWindowedCollector.run` with
the exact allowlist, returning `WINDOW_NOT_MANIFESTED` with API/raw `0` outside
it. Each approved window has an independent durable claim; an orphan/terminal
record is no-repeat and a failed window never triggers a future one early.

Every attempt keeps the unchanged accepted UR-145 000660 mobile-basic contract,
one GET, timeout 10 seconds, retry/redirect/fallback/auth/cookie/environment
zero, successful-body Landing-first, atomic UR-118 projection/prior/circuit and
same-window API-zero replay. No GUI, scheduler, history/canonical, Backtest,
credentials, account or order behavior is authorized.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_naver_mobile_basic_ur161_windows.py `
  --project-root . --confirm-ur161-window
```

## Completed manifest outcome

Each of `14:30`, `15:00`, and `15:30` was independently claimed and invoked
once only. All three stopped with sanitized `ConnectionError` before an HTTP
response, so no successful Landing or new observation exists. Every same-window
replay is API zero; the retained UR-145 observation remains at its original
provider timestamp and the route circuit is open `NAVER_TRANSPORT_ERROR`,
generation `5`. No further UR-161 window is authorized. See individual
[14:30](../../../artifacts/agent_runs/ur161/window_20260821T143000+0900.md),
[15:00](../../../artifacts/agent_runs/ur161/window_20260821T150000+0900.md),
and [15:30](../../../artifacts/agent_runs/ur161/window_20260821T153000+0900.md)
evidence.
