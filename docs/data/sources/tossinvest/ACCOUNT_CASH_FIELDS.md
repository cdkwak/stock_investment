# Toss Account Cash Fields

## Evidence and mapping

The retained identifier-free Toss account Landing projection and the Normalized
snapshot both keep `cash_balance` as null. The provider normalizer accepts the
buying-power response keys `result.currency` and `result.cashBuyingPower`, and
maps `cashBuyingPower` only to
`buying_power[].cash_buying_power`. It does not map that source field to
`cash_balance`.

The holdings response supplies securities valuation and position fields but no
cash field. The buying-power response therefore supplies the only retained cash-
related field: currency-specific order buying power. For KRW, the selected raw
field is `cashBuyingPower` with the meaning **KRW cash buying power**.

## Display assumption

`cashBuyingPower` is assumed to mean order-available cash buying power, as named
by the response and the existing Toss read-only operation contract. It is not
assumed to be settled deposit cash, withdrawable cash, or a cash balance. Until
Toss supplies and documents one of those distinct fields, the generic `현금`
column must receive null and render `—` for Toss.

The account-value calculation may continue to use securities value plus cash
buying power under its existing `OBSERVABLE_COMPONENT_SUM` label. That component
must not be relabelled as cash balance or total assets. `unsupported_fields`
therefore continues to include `cash_balance` and `realized_pnl`.
