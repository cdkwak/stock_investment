# D004 KOSDAQ150 Retained-Landing Promotion Handoff

## Identity

- Task: D004
- Base: `master` at `defffc89fe3e9c67569c87b4d3cac37537bd4452`
- Worktree: `C:\Users\k4545\Desktop\stock_investment_rev1\.worktrees\d004_kosdaq150_promotion`
- Classification: `DATA_COMPLETE_WITH_LIMITS`
- Coverage limit: one retained source snapshot, `2022-09-19` only

## Scope

Promote the retained official Data.go.kr derivative Landing responses into the
two registered KOSDAQ150 Normalized contracts. No provider request, source
refresh, registry edit, shared status edit, or A007-owned path is in scope.

## Inputs

| Dataset | Retained Landing | Pages | Declared/source rows | SHA-256 |
|---|---|---:|---:|---|
| `kr_kosdaq150_options_daily` | `C:\Users\k4545\Desktop\stock_investment_rev1\data\landing\data_go_kr\kr_derivatives_options_daily\20220919.json` | 2 | 10,126 / 10,126 | `d601fb0eb85124ce6c7817a23eb395d8179618f62e948ad42b426f69b604611b` |
| `kr_kosdaq150_futures_daily` | `C:\Users\k4545\Desktop\stock_investment_rev1\data\landing\data_go_kr\kr_derivatives_futures_daily\20220919.json` | 1 | 3,591 / 3,591 | `c15b717109681e2a7352b122547047ce9b394b53e3a970fb99507641f52c543c` |

The promotion rejects incomplete retained pagination when the extracted row
count differs from the source `totalCount`.

## Contract, schema, and primary key

Both datasets use the existing registered version-1 contracts, existing
`normalize_derivatives` parser, and `validate_data_v1` validator.

- Options PK: `(date, contract)`
- Options schema: `date:date32; underlying:string; contract:string; isin:string;
  name:string; product_category:string; maturity_month:string; call_put:string;
  strike:float64; open:float64; high:float64; low:float64; close:float64;
  next_day_base_price:float64; implied_volatility:float64; volume:int64;
  trading_value:int64; open_interest:int64; source:string;
  source_operation:string`
- Futures PK: `(date, contract)`
- Futures schema: `date:date32; underlying:string; contract:string; isin:string;
  name:string; product_category:string; maturity_month:string; open:float64;
  high:float64; low:float64; close:float64; underlying_value:float64;
  settlement_price:float64; volume:int64; trading_value:int64;
  open_interest:int64; source:string; source_operation:string`

Schema order, required nullability, source date, and PK uniqueness are checked
before the first persistent output is touched.

## Deterministic promotion and exclusions

- Options: 10,126 source rows; 316 exact-category rows; 316 promoted; zero excluded.
- Futures: 3,591 source rows; 13 exact-category rows; 7 outright contracts promoted.
- Six exact-category calendar spreads remain Landing-only under the existing
  parser rule: `406SCT3S`, `406SCT6S`, `406SCT9S`, `406SCTCS`, `406SCV6S`,
  `406SCVCS`.
- Any exact-category exclusion that is not a recognized futures `SP` row fails
  the promotion before writes.

## Outputs

| Dataset | Rows | Coverage | Output | SHA-256 |
|---|---:|---|---|---|
| `kr_kosdaq150_options_daily` | 316 | 2022-09-19 | `data/normalized/kr_kosdaq150_options_daily/year=2022/data.parquet` | `eaf45a7ef2db97d7ed58e5406a5c1d11b315dc0eb6fdd56e46d3c60de80bbe33` |
| `kr_kosdaq150_futures_daily` | 7 | 2022-09-19 | `data/normalized/kr_kosdaq150_futures_daily/year=2022/data.parquet` | `57cc0c6f529bc86803856e2770a9e54a0c002c1272e48e0781443a9756b86bda` |

The per-dataset Parquet writes use the existing atomic contract writer. Both
are read back and compared before the atomic state manifest is written at
`data/state/d004_kosdaq150_retained_promotion.json`.

## Validation

- Contract schema: PASS
- Required-field nullability: PASS
- Primary-key uniqueness: PASS (`316/316` options; `7/7` futures)
- Landing declared-total completeness: PASS (`10,126/10,126`; `3,591/3,591`)
- Promotion denominators: PASS (`316+0=316`; `7+6=13`)
- Atomic Parquet read-back: PASS
- Input/output SHA manifests: PASS
- Coverage: exactly `2022-09-19`

## Tests

- Focused: `14 passed`
- Full suite: `307 passed in 13.24s`
- Tests prohibit network access during promotion and cover incomplete Landing,
  undocumented exclusions, exact schema/PK, manifests, state, and atomic output.

## Changed files

- `src/stock_data/pipelines/retained_derivatives_promotion.py`
- `scripts/manual/promote_retained_kosdaq150_derivatives.py`
- `tests/test_retained_derivatives_promotion.py`
- `docs/D004_KOSDAQ150_RETAINED_PROMOTION_HANDOFF.md`

No contract, registry, `project/DATA_STATUS.md`, shared task-state, or provider code was
changed. No commit or push was made.

## Reproduction

Run from the isolated D004 worktree:

```powershell
C:\Users\k4545\Desktop\stock_investment_rev1\.venv\Scripts\python.exe `
  .\scripts\manual\promote_retained_kosdaq150_derivatives.py `
  --project-root . `
  --options-input C:\Users\k4545\Desktop\stock_investment_rev1\data\landing\data_go_kr\kr_derivatives_options_daily\20220919.json `
  --futures-input C:\Users\k4545\Desktop\stock_investment_rev1\data\landing\data_go_kr\kr_derivatives_futures_daily\20220919.json
```

## Limits and next boundary

`DATA_COMPLETE_WITH_LIMITS` applies only to the retained one-day snapshot. It
does not assert historical completeness. Extending date coverage requires new
authorized Landing acquisition and is outside D004.
