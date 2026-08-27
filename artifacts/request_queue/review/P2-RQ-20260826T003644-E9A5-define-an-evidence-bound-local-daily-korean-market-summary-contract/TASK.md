# Define an evidence-bound local daily Korean market-summary contract

## Problem
Separate descriptive Dashboard surfaces lack one bounded daily Korean market summary that compares the prior observation, identifies what to watch, connects read-only account relevance, and separates fact, rule interpretation, uncertain inference, and opinion.

## Evidence
PROJECT_GOAL requires a short provenance-linked daily synthesis across regime, overbought/oversold, valuation, derivatives, volatility, flow, crash risk, and account impact. Current Project Status provides validated fail-closed cards but no synthesis contract or equivalent queue task.

## Scope
allow:
- Create the future Project-owned documentation contract and update PROJECT_STATUS routing only after prerequisite contracts are accepted.

deny:
- No GUI/runtime/model implementation, provider or external-AI call, invented number or narrative, account expansion/mutation, trade recommendation, order, scheduler/Data mutation, or wider application phase.

## Done When
A documentation-only daily-market-summary/v1 application-service contract defines exact validated local input identities, current/prior as-of comparison, required Korean sections, FACT/RULE_INTERPRETATION/UNCERTAIN_INFERENCE/OPINION labels, per-claim source/freshness references, missing/stale/conflicting-data language, read-only account relevance, deterministic fallback, bounded length, and no-output gates; PROJECT_STATUS links it without implementation authority.

## Verify
Map every Project Goal daily-summary requirement to one field/invariant; prove dependence on accepted refresh-status and crash-risk contracts; verify every numeric/factual statement is input-bound, unsupported narrative is impossible, and account/trade/provider boundaries remain closed; verify links and queue doctor.
