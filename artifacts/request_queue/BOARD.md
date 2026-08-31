# Request Queue Board

updated_at: 2026-08-31T21:39:49+09:00
generated_from_digest: 129a3f69a2d892be47488440df78b5421a9cf76b676d5430e99a18bd7e853281
mode: domain-parallel
writer_limit: 3

## Active
- P0-RQ-20260831T202140-AF83-diagnose-managed-health-stale-rows-and-propagate-decision-holds-without- | owner=quant_data_truth_lead | domain=data | lead=quant_data_truth_lead | worker=strong | reviewer=critical | lane=data | orca=- | phase=claimed | heartbeat=2026-08-31T20:24:35+09:00 | next=Seal Data Truth Worker/Reviewer packets and start provider-free stale/dependency diagnosis.
- P1-RQ-20260831T202140-FA53-build-a-read-only-investment-decision-cockpit-with-catalog-recovery-and- | owner=quant_decision_ux_lead | domain=gui | lead=quant_decision_ux_lead | worker=strong | reviewer=critical | lane=gui | orca=- | phase=integrated | heartbeat=2026-08-31T21:26:28+09:00 | next=PM submit immutable scoped candidate for Queue review lifecycle.
- P2-RQ-20260831T202140-9D3C-define-read-only-portfolio-risk-and-backtest-evidence-display-contract | owner=quant_validation_lead | domain=backtest | lead=quant_validation_lead | worker=balanced | reviewer=strong | lane=backtest | orca=- | phase=FIX | heartbeat=2026-08-31T21:39:49+09:00 | next=Resume the same quant_validation_worker for ordinary FIX round 1/2 when one agent session is available; then freeze a fresh candidate and obtain fresh independent review.

## Review
- none

## Waiting
- P1-RQ-20260826T014826-B608-add-a-korean-government-bond-yield-curve-to-the-dashboard | domain=gui | lead=gui_lead | next_check_at=none | resume=A canonically triaged, independently reviewed Data-owned projection/finality result defines the exact local path, schema, six-tenor identity, percent units, common provider date, finality/state, row-count/hash/freshness invariants, and authorizes descriptive numeric Dashboard consumption.

## Ready
1. P1-RQ-20260828T213813-A3D5-investigate-unowned-rolling-kospi200-option-wall-artifact-mutation | domain=data | lead=data_ops_lead | profiles=strong/critical | depends_on=-
2. P1-RQ-20260829T173816-BC24-establish-korea-forward-eps-revision-and-roe-free-observation-eligibilit | domain=research | lead=forward_valuation_lead | profiles=strong/critical | depends_on=-
3. P1-RQ-20260830T044635-B1F6-repair-corrupt-retained-inputs-for-current-stock-candidate-discovery | domain=data | lead=data_ops_lead | profiles=strong/critical | depends_on=-
4. P1-RQ-20260830T180250-37C0-keep-net-worth-reload-and-maintenance-actions-visible-at-laptop-widths | domain=gui | lead=gui_lead | profiles=strong/critical | depends_on=RQ-20260830T180249-5E12
5. P1-RQ-20260830T180251-FC3E-preserve-complete-research-source-and-status-content-at-1280x720 | domain=gui | lead=gui_lead | profiles=strong/critical | depends_on=RQ-20260830T180250-37C0
6. P1-RQ-20260830T180252-8AB5-render-shared-provenance-failures-in-plain-language-with-a-direct-recove | domain=gui | lead=gui_lead | profiles=strong/critical | depends_on=RQ-20260830T180251-FC3E
7. P1-RQ-20260830T180253-3CBA-include-the-account-display-range-selector-in-the-real-tab-chain | domain=gui | lead=gui_lead | profiles=strong/critical | depends_on=RQ-20260830T180252-8AB5
8. P1-RQ-20260831T185426-5BB9-recover-stale-codex-worktree-queue-manager-identity-without-destructive- | domain=infra | lead=queue_orchestration_lead | profiles=strong/critical | depends_on=-
9. P2-RQ-20260826T004917-0AF5-define-a-pit-vintage-aware-macro-regime-and-market-transmission-contract | domain=- | lead=- | profiles=strong/critical | depends_on=RQ-20260826T004102-BB30
10. P2-RQ-20260826T005041-B6C3-define-a-secure-always-on-read-only-application-service-and-remote-acces | domain=- | lead=- | profiles=strong/critical | depends_on=RQ-20260826T004445-B2ED,RQ-20260826T004630-7CC5,RQ-20260826T003644-E9A5,RQ-20260826T012155-BB66
11. P2-RQ-20260826T012155-BB66-define-a-holdings-driven-multi-currency-account-nav-and-valuation-freshn | domain=- | lead=- | profiles=strong/critical | depends_on=RQ-20260826T004445-B2ED
12. P2-RQ-20260826T012440-3679-establish-kospi-forward-per-pbr-source-licensing-and-aggregation-evidenc | domain=research | lead=forward_valuation_lead | profiles=strong/critical | depends_on=RQ-20260825T232533-76A1
13. P2-RQ-20260829T003106-DDBB-deduplicate-shadowed-request-queue-review-snapshot-test | domain=infra | lead=queue_orchestration_lead | profiles=balanced/strong | depends_on=-
14. P2-RQ-20260830T180254-65D1-separate-account-safe-reads-from-local-maintenance-and-destructive-actio | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180253-3CBA
15. P2-RQ-20260830T180256-6F2D-give-the-data-status-lifecycle-table-a-semantic-accessible-name | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180254-65D1
16. P2-RQ-20260830T180257-E642-keep-the-index-current-price-pill-inside-the-plot | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180256-6F2D
17. P2-RQ-20260830T180258-6CE4-make-equity-and-us-etf-guided-examples-catalog-aware | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180257-E642
18. P2-RQ-20260830T180259-3B57-make-backtest-preserved-and-empty-result-states-consistent | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180258-6CE4
19. P2-RQ-20260830T180300-7ECA-localize-watchlist-create-and-rename-dialog-actions | domain=gui | lead=gui_lead | profiles=fast/balanced | depends_on=RQ-20260830T180259-3B57
20. P2-RQ-20260830T180301-5B88-allow-dashboard-cards-to-reflow-long-and-large-font-content | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180300-7ECA
21. P2-RQ-20260830T180303-D130-allow-equity-and-us-etf-result-feedback-to-wrap-fully | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180301-5B88
22. P2-RQ-20260830T180304-C76A-preserve-account-chart-legend-identity-and-date-meaning | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180303-D130
23. P2-RQ-20260830T180305-EEC8-add-a-visible-symbol-selection-action-to-research-empty-states | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180304-C76A
24. P2-RQ-20260830T180307-C028-preserve-backtest-page-context-after-local-validation | domain=gui | lead=gui_lead | profiles=balanced/strong | depends_on=RQ-20260830T180305-EEC8

## New Discoveries
- count: 0

## Blocked
- none
