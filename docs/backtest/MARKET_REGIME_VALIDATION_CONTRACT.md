# PIT-Safe Market-Regime Validation Contract

Status: `V1_ENGINE_IMPLEMENTED / NUMERIC_RUN_BLOCKED_PENDING_COMPLETE_EARNINGS_AXIS`

## Purpose

`market-regime-validation/v1` evaluates predeclared combinations of three
independent market-context axes against later KOSPI outcomes:

1. price, trend and volatility state;
2. KOSPI valuation-history state;
3. Forward EPS/revision/Forward ROE earnings-momentum state.

It is a development-only descriptive study. It selects no winner, produces no
ranking or recommendation, makes no order, and never inspects the sealed final
holdout. Missing axes remain missing; they are not assigned zero or a neutral
state.

## Fixed outcome horizons

The version-1 horizons are fixed before any result is seen:

| User horizon | Trading-session identity | Outcomes |
|---|---:|---|
| 3 months | 63 | terminal return and true path maximum drawdown |
| 6 months | 126 | terminal return and true path maximum drawdown |
| 12 months | 252 | terminal return and true path maximum drawdown |

True path maximum drawdown includes the observation close, maintains the
running peak through the next `h` retained sessions, and takes the minimum
`price / running_peak - 1`. It is not silently replaced by the worst return
from the initial close.

All outcomes live only in the label frame. The combined label becomes available
at the final retained session close required by the 252-session horizon. The
feature frame rejects `forward_`, `future_`, `label_`, and `outcome_` columns.

## Input contract

The feature frame requires a unique, ordered `observation_date`, constant
`ticker` and `date_semantics`, aware `usable_from`, exact
`PIT_SAFE_EOD_T_PLUS_1`, and complete typed values for:

- `price_axis_state`;
- `valuation_axis_state`;
- `earnings_axis_state`.

Each axis also carries its own exact `*_axis_pit_status`; all three must equal
`PIT_SAFE_EOD_T_PLUS_1`. A global feature PIT label cannot certify a blocked or
limited valuation/earnings input.

`UNKNOWN`, `UNAVAILABLE`, `MISSING`, `N_A`, or `NA` in any axis rejects the
entire evaluation. Candidate combinations are immutable, unique, uppercase
typed identities supplied before evaluation. The engine never discovers or
optimizes combinations and exposes `winner_selected=false` only.

The current project cannot perform a production-root numeric run because the
Forward EPS/revision/Forward ROE axis is not yet licensed and PIT-validated.
Current KRX PER/PBR, trailing ROE, price momentum, or a constant neutral value
cannot substitute for that axis.

## Split and holdout policy

- expanding development-only walk-forward;
- purge at least 252 retained sessions;
- default embargo 5 retained sessions;
- every label availability timestamp is recomputed against the full ordered
  retained feature-session calendar and must equal the exact 252nd successor
  session at 15:30 KST; a self-declared premature timestamp is rejected;
- each fold then proves its latest training label is available no later than
  its earliest test decision;
- any feature date, label date, or label-availability date entering the sealed
  holdout fails before axis or outcome numeric inspection;
- the accepted Phase-1 five-file generation and its 1,222-observation final
  holdout remain unchanged and uninspected.

## Reported evidence

For each predeclared candidate and each fixed horizon, the engine reports:

- test and matching observation counts and signal rate;
- conditional mean/median return and positive rate;
- conditional mean true path maximum drawdown;
- the same unconditional development-test baseline;
- conditional-minus-unconditional return and drawdown differences.

An insufficient candidate has typed
`INSUFFICIENT_SIGNAL_OBSERVATIONS` and null conditional statistics. Baseline
differences provide opportunity context; they are not a transaction-cost or
executable-portfolio estimate. Costs, turnover, taxes, FX, fills, capacity and
instrument feasibility remain outside this descriptive state study.

## Implementation and tests

- engine: `src/market_backtest/market_regime_validation.py`;
- unit coverage: `tests/unit/backtest/test_market_regime_validation.py`;
- current focused validation: 11 tests covering exact 63/126/252 labels, true
  path drawdown, purged folds, availability clocks, holdout preflight,
  outcome-namespace separation, missing-axis rejection, typed insufficiency and
  immutable no-winner results.
