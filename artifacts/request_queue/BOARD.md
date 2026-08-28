# Request Queue Board

updated_at: 2026-08-28T12:14:03+09:00
generated_from_digest: e1f14087a551282004720032bf6a2ca109a3133db0e57765c27562827845041c
mode: domain-parallel
writer_limit: 3

## Active
- none

## Review
- P2-RQ-20260826T003644-E9A5-define-an-evidence-bound-local-daily-korean-market-summary-contract | reviewer=lead

## Ready
1. P1-RQ-20260826T014826-B608-add-a-korean-government-bond-yield-curve-to-the-dashboard | depends_on=RQ-20260826T004102-BB30,RQ-20260826T020213-744E,RQ-20260825T232533-76A1,RQ-20260826T011334-F6D5
2. P1-RQ-20260826T204115-F8F7-restore-compact-one-row-dashboard-market-card-height | depends_on=RQ-20260826T055328-C938
3. P2-RQ-20260826T004917-0AF5-define-a-pit-vintage-aware-macro-regime-and-market-transmission-contract | depends_on=RQ-20260826T004102-BB30
4. P2-RQ-20260826T005041-B6C3-define-a-secure-always-on-read-only-application-service-and-remote-acces | depends_on=RQ-20260826T004445-B2ED,RQ-20260826T004630-7CC5,RQ-20260826T003644-E9A5,RQ-20260826T012155-BB66
5. P2-RQ-20260826T012155-BB66-define-a-holdings-driven-multi-currency-account-nav-and-valuation-freshn | depends_on=RQ-20260826T004445-B2ED

## New Discoveries
- count: 0

## Blocked
- P0-RQ-20260828T115727-F3C6-add-durable-orca-lifecycle-reconciliation-to-request-queue | next=Inspect Orca dispatch ctx_3360ef20de13 and the reused terminal mapping; do not extend this failed Run with another retry or recovery chain.
- P1-RQ-20260826T020213-744E-establish-korean-government-bond-yield-curve-data-contract-and-local-pro | next=Execute batch 2 with the active runbook in the next 17:00-18:00 KST provider-publication window, then validate field/canonical-row comparison and API0 replay.
- P1-RQ-20260826T052521-3082-define-a-secure-scheduled-daily-toss-read-only-account-refresh | next=Observe the installed task naturally after 07:00 KST, then validate the exact identifier-free occurrence and last receipt, provider-call budget, snapshot atomicity/privacy, and task result without a manual provider retry.
