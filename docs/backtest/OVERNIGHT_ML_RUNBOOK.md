# Overnight ML Runbook

Status: `ACTIVE_BOUNDED_OFFLINE_EXPERIMENT / HOLDOUT_UNTOUCHED`

This is the supported local entry point for an at-most-eight-hour, provider-free
ML study over the accepted frozen KOSPI200 input. It is a development experiment,
not a strategy recommendation, portfolio optimizer, executable backtest, or
holdout validation.

## Fixed safety boundary

| Field | Contract |
|---|---|
| Frozen input | `a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713` |
| Source use | Slice before `2021-08-17` before building features or labels |
| Holdout | 1,222 observations; no features, labels, predictions, metrics, ranking, or inspection |
| Features | Exact six `PIT_SAFE_EOD_T_PLUS_1` Phase-1 features |
| Outcome | Development-only `forward_max_drawdown_20d <= -0.10` event |
| Validation | Expanding walk-forward; 60-session purge; 5-session embargo |
| Models | Logistic regression, histogram gradient boosting, random forest |
| Objective | Development out-of-fold average precision |
| Parallelism | One study process and one estimator job |
| Persistence | Local Optuna SQLite plus atomic config/state/summary JSON |
| Time limit | At most 28,800 seconds across graceful resumes |
| Network | Forbidden during the experiment |

Every trial retains its exact parameters and development metrics in
`study.sqlite3`. `config.json` binds the input, feature/label versions, split,
library versions, code-tree digest, and time budget. A different configuration
must use a different output directory.

## Foreground run

```powershell
.\.venv\Scripts\python.exe .\scripts\run_overnight_ml.py --duration-hours 8 --keep-awake
```

`--keep-awake` uses a process-scoped Windows execution-state request and restores
the normal state when the process exits. It does not permanently alter a power
plan. Keep the machine connected to power.

## Status

```powershell
.\.venv\Scripts\python.exe .\scripts\run_overnight_ml.py --status
```

Current artifacts are under `artifacts/backtest/ml_overnight/`:

- `config.json`: immutable experiment identity;
- `study.sqlite3`: resumable Optuna trial database;
- `state.json`: process state, consumed/remaining seconds, trial counts, best
  development candidate, and explicit holdout flag;
- `summary.json`: bounded human/GUI-readable summary;
- `stdout.log` and `stderr.log`: launcher output when run in the background.

## Resume and stop

Rerun the exact foreground command to continue the remaining budget after a
graceful or abrupt interruption. Completed SQLite trials are not repeated.
An abrupt process stop may leave `state.json` labelled `RUNNING`, but the next
exclusive invocation resumes from the last atomically recorded checkpoint.

To stop a known background process:

```powershell
Stop-Process -Id <PID>
```

Do not delete or edit the SQLite/config/state files to force a restart. Choose a
new explicit output directory for a genuinely new experiment identity.

## Interpretation boundary

The best row is labelled `DEVELOPMENT_CANDIDATE_NOT_HOLDOUT_VALIDATED`. It may
support later research design only. It must not be presented as expected return,
crash prediction, buy/sell timing, leverage permission, portfolio allocation,
or a production model. The existing final holdout stays sealed until the user
explicitly opens that exact boundary. New versioned development-only model
studies, signals, executable-instrument simulations, portfolio experiments, and
typed GUI consumers may proceed under standing offline authority with their own
contracts and technical validation; they need no separate phase approval and
must not relabel a development result as production or a recommendation.
