# Align native GUI release page contract with accepted Research Workspace

## Problem
Native release assessment rejects the accepted ten-page GUI because EXPECTED_GUI_PAGES still represents the pre-Research-Workspace topology.

## Evidence
Post-0290 independent offscreen smoke: all ten actual tabs visible, no expected tab missing, Research Workspace sole extra tab, QUIESCENT 3150 ms, zero active threads, all workers closed, no clipping; assessment FAIL page_contract=False.

## Scope
allow:
- Modify only the release-readiness expected page contract and its owning tests.

deny:
- No GUI tab implementation, data/provider/account/backtest/scheduler/status changes; no relaxation to subset matching or other readiness gates; no provider calls.

## Done When
The exact accepted GUI page contract includes Research Workspace in its actual tab order and native assessment passes the page contract without weakening exact topology, visibility, worker, isolation, or clipping gates.

## Verify
Update owning regressions for exact ten-page order, run test_release_readiness.py, then one provider-disabled offscreen native smoke proving page_contract true, zero active threads, workers closed, and no clipping; screen baseline may remain unsupported offscreen.
