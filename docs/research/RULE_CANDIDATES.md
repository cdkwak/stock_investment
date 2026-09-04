# Rule Candidates

This is a provider-free, descriptive research workflow. It reads retained Parquet only, uses close-T inputs, joins future outcomes only during evaluation, and does not model orders, fills, costs, taxes, FX, or investment suitability.

## Registry and versioning

`config/research/rule_candidates.json` is schema version 1. Each add, edit, retire, or remove operation requires a date and reason, appends one history event, and increments `attempt_count`. The exact file-byte SHA-256 is the `rules_version` used by leaderboard and forward-test records. Use `python -m stock_data.research.rule_candidates --project-root . validate` or its `add`, `edit`, `retire`, and `remove` subcommands; programmatic callers may use the matching helpers.

Statuses are `active`, `experimental`, and `retired`. The evaluator reports all three, while the daily recorder writes only active and experimental candidates. Supported baskets are KR (KOSPI200 primary and KOSPI secondary), US_TECH (NASDAQ100), SEMIS (SOX), and POOLED (the named primary series combined). The loopback Research UI registers every saved experiment with `status=experimental`, appends an `add` history event, and increments `attempt_count` exactly once.

## Rule semantics

A ladder awards one equal-weight point for every contemporaneous indicator comparison that is true. Its integer level is therefore 0..k. Missing required indicators produce no state rather than an assumed false value. `volidx_pct` is the causal trailing-252 valid-session midrank percentile of VIX for US baskets or VKOSPI for KR. RSI14 uses Wilder smoothing.

Volatility targeting uses `base = min(1, target_vol / realised_vol_window)`, with zero realised volatility mapped to 1 and missing volatility preserved. A pure volatility-target candidate has level 0.

For a hybrid with ladder level `L` and maximum `K`:

- drawdown ladder: `exposure = min(1, base × (1 + L/K))`;
- overheat ladder: `exposure = base × (1 - L/K)`.

Thus drawdown evidence restores exposure toward 100%, while overheat evidence scales it toward zero. Exposure is bounded to [0, 1]. A plain ladder's home-card exposure is `L/K` for drawdown and `1-L/K` for overheat.

## Evaluation and clocks

Fit rows require the 90-session outcome end date on or before 2015-12-31. Hold-out rows start at observation date 2016-01-01 and require a complete 90-session outcome. Ladder and hybrid headline results use their maximum level; level tables retain every level. Vol-target returns, volatility, and drawdown are scaled by the close-T exposure held fixed over the descriptive forward window. Baselines are unscaled unconditional same-series means.

Cycle dates are diagnostics only and never select thresholds. A drawdown cycle is a hit when signal-date mean 60-session return exceeds the cycle's unconditional mean; an overheat cycle is a hit when it is lower. Hybrids inherit the nested ladder direction. Existing pure volatility-target candidates with `side=hybrid` retain the descriptive drawdown comparison convention; a newly registered UI experiment may preserve an explicit `drawdown` or `overheat` side.

`evaluate_definition(project_root, definition, basket, side, horizons=(20,60,90))` is the reusable ad-hoc path. It returns the same candidate result structure as the batch leaderboard without writing a candidate or artifact. Its per-basket evaluation frame is cached in-process and invalidated by the path, modification time, and size of every relevant retained Parquet file. The batch builder continues through the same candidate evaluator, so its schema and numeric outputs remain unchanged.

The daily log key is `(as_of, candidate_id, rules_version)`. Rerunning the same unchanged observation is a no-op; a conflicting replay fails closed. Realised returns are joined later by retained trading-session offsets 20/60/90. A close-T state is observational and only usable from the next retained session.

## Commands and outputs

```text
.venv\Scripts\python.exe scripts/research/run_rule_leaderboard.py --project-root .
.venv\Scripts\python.exe scripts/research/record_forward_signals.py --project-root . --as-of YYYY-MM-DD
```

The leaderboard atomically writes `artifacts/research/rule_leaderboard/latest.json` and a `YYYYMMDD.json` copy. Daily observations accumulate at `data/local/research/forward_test/signals.jsonl`. The `RESEARCH_FORWARD_TEST_DAILY` lane is the final member of the 20:30 KR market bundle, makes zero API calls, and fails independently of preceding lanes.

The web experiment GET is provider-free, supports only the four documented indicators and fixed side operator, and limits each client to ten evaluations per minute. Candidate POST is loopback-only. It regenerates the leaderboard in-process; work still running after 60 seconds continues in a daemon thread while the page polls the artifact version.
