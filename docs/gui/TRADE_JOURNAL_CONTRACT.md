# Trade Journal Contract

## Purpose and boundary

The 내 계좌 trade journal implements the user-selected A plan: Toss and KB
activity is auto-derived from retained daily account snapshots, while accounts
without an API (for example 미래에셋) use explicit manual entries. The journal
is explanatory and estimated; it is not a broker statement, tax ledger, order
history, or execution interface.

The service is provider-free and network-free. It prefers the privacy-minimized
daily positions history, uses retained Landing snapshots only for dates absent
from that history, and also reads normalized dividend identity/reference data
and the local cash-flow ledger. It never calls or mutates a broker. Manual
journal writes and the derivation cache are limited to `artifacts/local_user/`
and use atomic replacement. Balances, holdings, entries, or direct account
identifiers must not be copied to Obsidian, a vault, or any location outside
this project's `artifacts/` and `data/` trees.

## Inputs, privacy boundary, and daily selection

- Preferred Toss positions: `data/local/account_positions_history/toss_self/YYYY-MM-DD.json`
- Preferred KB positions: `data/local/account_positions_history/kb_self/YYYY-MM-DD.json`
- Toss fallback: `data/landing/tossinvest/account_snapshot/*.json`
- KB fallback: `data/landing/kbsec/account_snapshot/*.json`
- Cash flows: `artifacts/local_user/cash_flows.json`
- Korean equity identity: `data/normalized/kr_equity_master`
- Current Korean ETF identity: `data/normalized/kr_etf_universe_daily`
- Korean dividends: `data/normalized/kr_equity_dividend`
- Manual journal: `artifacts/local_user/trade_journal_manual.json`

Each history file contains only `schema_version`, `source_id`, `observed_at`,
and `positions`. Toss position rows contain only `symbol`, security `name`,
`currency`, `market_country`, `quantity`, and `average_purchase_price`; KB rows
replace `market_country` with `classification`. The history contains no cash,
balances, totals, account identifiers, current prices, market values, costs,
fees, taxes, or P&L fields. The existing remove-retained-account-snapshots
privacy action deletes both brokers' history files.

`observed_at` or fallback `collected_at` is converted to the KST calendar date.
History wins for an exact `(source_id, KST date)`; Landing is read only for a
date not covered by valid history. If more than one Landing file is present for
a fallback date, only the newest collection timestamp is used. A comparison
is made only when the two selected dates are adjacent calendar days. A missing
day creates a reported gap; the service does not interpolate across it.

## Trade derivation

For each source, day, and symbol, the selected snapshots are reduced to one
position with quantity, average purchase price, currency, and display identity.
Landing-only fallback dates may additionally provide a snapshot current/last
price and cash. Position changes produce at most one event per
`(source, day, symbol, side)`, so multiple intraday fills and Toss 모으기 fills
collapse into one daily estimate.

- Quantity increase: `BUY` for `q1 - q0`.
- New symbol: `BUY` for the full `q1` at the new average purchase price.
- Quantity decrease: `SELL` for `q0 - q1`. A fallback Landing current/last
  price may support an estimated amount and realized P&L; minimal-history pairs
  retain the quantity event with `price_basis: unavailable` and null price,
  amount, and realized P&L.
- Disappeared symbol: `SELL` for the full `q0`, with the same price-availability
  rule.

Fractional quantities are retained to six decimal places. Every auto-derived
event has `estimated: true`, the two `snapshot_dates`, a machine-readable
`price_basis`, and a Korean `basis` explanation.

### Buy price basis

For an increase in an existing position, price is inferred from the average
cost identity:

`(average1 × q1 - average0 × q0) / (q1 - q0)`

The identity is used only when both averages exist and the inferred price is
positive. Otherwise day 1's snapshot current/last price is used and
`price_basis` is `last_price`. A new position uses its day 1 average purchase
price when positive; otherwise it also falls back to day 1's snapshot price.
Sell estimates use snapshot prices only when a Landing fallback supplies one,
because daily holdings do not reveal the actual sale fill. Minimal history
does not retain a current price and therefore does not invent one.

### 모으기/소액 hint

A BUY is tagged `recurring_like: true` when its estimated KRW-equivalent amount
is below KRW 100,000, or the same source and symbol was bought on at least three
of the five most recent snapshot days ending on that event date. USD small-buy
classification requires a retained USD/KRW reference; when it is unavailable,
only the frequency rule is applied. The tag is a display hint, not a claim
about the broker order type.

## Dividend estimate

For each adjacent snapshot pair and currency:

`residual = cash1 - cash0 - net trade cash - registered cash flows`

BUY cash is negative and SELL cash is positive. KRW cash-flow ledger entries
whose account label matches the source and whose date is in `(d0, d1]` are
subtracted, so a registered deposit is not presented as income. The service
does not convert a KRW ledger entry into USD.

A cash residual can be calculated only for an adjacent pair whose selected
fallback Landing snapshots both contain cash-like fields; positions-history
files deliberately contain none. A positive residual is considered only at
KRW 1,000 or USD 1 and above. When a
Korean symbol held at day 0 has a dividend `cash_payment_date` in `(d0, d1]`, a
`DIVIDEND` estimate is emitted with `shares × ordinary_dividend_amount` as the
pre-tax expected amount and the cash residual as separate observational
evidence. If no Korean reference match exists, a `DIVIDEND?` event may expose
the otherwise unexplained positive residual as `추정(미확인)`. A residual is
never labelled certain. US ETF dividends therefore remain unconfirmed until a
retained reference source exists.

## Manual-entry contract

`trade_journal_manual.json` has `schema_version: 1` and `entries[]`. Each entry
contains `id`, ISO date, identifier-free `account_label`, symbol, name, side
(`BUY`, `SELL`, `DIVIDEND`, `TRANSFER_IN`, `TRANSFER_OUT`, or `OTHER`), positive
quantity, currency (`KRW` or `USD`), and optional memo. Price is positive and
required for buy, sell, and dividend entries; it is nullable for transfer-in,
transfer-out, and other entries. POST and DELETE are loopback-only. Validation
fails closed with HTTP 400, and writes atomically replace the ledger. Manual
entries are marked `estimated: false` and are never inferred from account
snapshots.

The 종목명 control searches the same provider-free `/api/stocks/search` index as
the stocks page. The index combines the Korean equity master, current Korean ETF
universe, retained small ETF master, and accepted static U.S. ETF identities.
The client waits 350 ms and ignores out-of-order responses. Selecting a result
fills code, canonical display name, and KRW for Korean markets or USD for the
U.S. ETF catalog; a code may still be entered directly.

When POST receives a name and a blank code, the server accepts one exact name
match or one unique search match and stores its canonical code and short name.
Multiple candidates fail with HTTP 400 and list at most three `code name`
choices; no match also fails closed. A supplied code with a blank name may fill
the name only when that exact code is present in the same local index.

The price label is semantic rather than generic: buy and sell identify their
respective per-share fill price, dividend identifies the pre-tax per-share
amount, and transfer/other sides say that price is optional. After a successful
write, the client keeps a one-line summary of the saved date, account, identity,
side, quantity, and price instead of returning only a generic success message.

## Cache and response

`trade_journal_cache.json` is keyed by the sorted Landing file names plus a
content digest for each small positions-history file. The digest makes a later
same-day history overwrite invalidate the cache. It retains sanitized day-level
derivation inputs and events so normal requests do not reparse the full JSON
history.
Cash-flow and dividend matching are performed against current retained inputs
after cache loading. `GET /api/trade-journal?days=N` merges derived trades,
dividend estimates, and manual entries, filters by KST date, sorts newest first,
and returns summary counts/totals plus any skipped gaps.

## Limitations

- Daily snapshots cannot identify intraday fill count, fill time, order type,
  execution venue, or the order in which buys and sells occurred.
- Multiple fills are grouped into one daily quantity change.
- Fees, commissions, taxes, and withholding are not included in trade P&L;
  matched Korean dividend expectations are explicitly pre-tax.
- A same-day round trip with no closing quantity change is invisible.
- Transfers between accounts can resemble a buy and a sell.
- Splits, reverse splits, mergers, spin-offs, symbol changes, tender events,
  stock dividends, and other corporate actions can appear as phantom trades.
- Snapshot current/last prices are not actual sell fills, and cost-basis
  changes can be affected by broker accounting rules or FX treatment.
- Cash residuals can have explanations not represented in the retained data;
  they are always estimates and may remain `DIVIDEND?`.
