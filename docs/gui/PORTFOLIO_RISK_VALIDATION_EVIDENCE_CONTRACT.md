# Portfolio Risk Validation Evidence Display Contract

역할: 이 문서는 표시 투영만 소유하며, 증거 의미의 권위는 [Backtest 계약](../backtest/PORTFOLIO_RISK_VALIDATION_EVIDENCE_CONTRACT.md)이다.

Status: `PRESENTATION_ONLY / READ_ONLY / NO_GUI_RECOMPUTATION`

Display contract version: `portfolio-risk-validation-evidence-display/v1`

## Authority and purpose

This GUI contract is presentation-only. Backtest's [`portfolio-risk-validation-evidence/v1`](../backtest/PORTFOLIO_RISK_VALIDATION_EVIDENCE_CONTRACT.md) is the sole authority for evidence semantics, identity, provenance, currency scope, concentration descriptors, validation limits, and unavailable reasons. The GUI accepts a typed projection of that envelope and renders it read-only.

The GUI must not recompute, infer, aggregate, convert, normalize, rank, or validate concentration values; it must not contact a provider, broker, account, or scheduler; and it must not execute a backtest, access/unseal a holdout, or alter an accepted bundle. It presents no risk budget, target sizing, VaR, expected shortfall, recommendation, suitability, executable action, or cross-currency portfolio total.

## Required typed projection

The renderer accepts exactly one `PortfolioRiskEvidenceDisplayProjection` with:

| Field | Type | Display rule |
| --- | --- | --- |
| `contract_version` | string | Exactly `portfolio-risk-validation-evidence/v1`. |
| `bundle_identity` | typed pass-through | Render opaque identity/provenance summary only. |
| `bundle_validation` | typed pass-through | Render development-only and validation-limit labels. |
| `overall_state` | closed enum | Render `AVAILABLE`, `PARTIALLY_AVAILABLE`, `UNAVAILABLE`, `BLOCKED`, or `INVALID`. |
| `unavailable_reasons` | closed enum array | Render exact typed reasons; do not replace with guessed prose. |
| `currency_sections` | ordered typed pass-through | Render each currency independently and in source order. |

Each displayed descriptor is the Backtest-projected `ConcentrationDescriptor`. The GUI may format a supplied finite value using its supplied unit, but cannot change its value, denominator, scope, timing, state, or descriptor kind. It must retain the supplied currency code, evidence/provenance references, and availability state with the rendered descriptor.

## Currency-safe rendering

One currency section is one visual scope. Currency sections must remain separate even when their labels or descriptor kinds match. The GUI must never sum, net, compare as a total, rank across, or convert currencies; it must not show a consolidated value, inferred FX rate, or base-currency equivalent. Missing FX evidence is displayed only through the typed unavailable or blocked state supplied by Backtest.

Concentration content is labelled `descriptive evidence only`. It must not be presented as a limit, diversification score, risk score, expected loss, portfolio recommendation, or suitability result.

## Empty, unavailable, blocked, and invalid states

`UNAVAILABLE`, `BLOCKED`, and `INVALID` render a state and the supplied closed reason codes instead of any numeric placeholder. `PARTIALLY_AVAILABLE` may render only valid descriptors in independently valid currency sections, while rendering each omitted/blocked descriptor's state and reason. The GUI must not substitute zero, neutral, previous, estimated, or current-provider data.

If the projection is absent, has an unknown version/state/reason, duplicates an identity, violates its typed schema, contains a nonfinite displayed value, or claims a cross-currency aggregate, the GUI renders only `INVALID` with no descriptor value. It does not attempt repair or fallback.

## Validated-backtest limitation label

Whenever a projection is rendered, the GUI must show that it is a read-only, development-only validated-bundle projection, limited to the Backtest-declared frozen inputs, identity, clocks, split, and validation evidence. It must not imply live or future performance, executable fills, capacity, costs, tax, financing, account reconciliation, or validation of a user's portfolio.

The GUI must visibly preserve the sealed-holdout boundary: it displays no holdout observations, labels, predictions, metrics, rankings, or outcomes, and never claims that the holdout was reviewed.
