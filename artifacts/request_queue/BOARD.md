# Request Queue Board

updated_at: 2026-08-29T09:55:05+09:00
generated_from_digest: 1420be33800c425fb4e250c3f69039f211966d77e3af3b1168ccfa2ed19d8e66
mode: domain-parallel
writer_limit: 3

## Active
- none

## Review
- P2-RQ-20260826T003644-E9A5-define-an-evidence-bound-local-daily-korean-market-summary-contract | domain=- | lead=- | reviewer=lead | profiles=strong/critical | orca=-

## Waiting
- none

## Ready
1. P1-RQ-20260826T014826-B608-add-a-korean-government-bond-yield-curve-to-the-dashboard | domain=- | lead=- | profiles=strong/critical | depends_on=RQ-20260826T004102-BB30,RQ-20260826T020213-744E,RQ-20260825T232533-76A1,RQ-20260826T011334-F6D5
2. P1-RQ-20260829T003946-70A9-add-a-fail-closed-workflow-policy-evaluation-and-promotion-lifecycle | domain=infra | lead=workflow_policy_lead_20260829 | profiles=critical/critical | depends_on=RQ-20260829T003900-24A5,RQ-20260829T003912-025B,RQ-20260829T003930-0BD9
3. P1-RQ-20260829T093730-C118-transfer-workflow-operation-authority-from-orca-to-the-python-control-pl | domain=infra | lead=workflow_cutover_lead_20260829 | profiles=critical/critical | depends_on=RQ-20260829T003946-70A9
4. P2-RQ-20260826T004917-0AF5-define-a-pit-vintage-aware-macro-regime-and-market-transmission-contract | domain=- | lead=- | profiles=strong/critical | depends_on=RQ-20260826T004102-BB30
5. P2-RQ-20260826T005041-B6C3-define-a-secure-always-on-read-only-application-service-and-remote-acces | domain=- | lead=- | profiles=strong/critical | depends_on=RQ-20260826T004445-B2ED,RQ-20260826T004630-7CC5,RQ-20260826T003644-E9A5,RQ-20260826T012155-BB66
6. P2-RQ-20260826T012155-BB66-define-a-holdings-driven-multi-currency-account-nav-and-valuation-freshn | domain=- | lead=- | profiles=strong/critical | depends_on=RQ-20260826T004445-B2ED

## New Discoveries
- count: 2

## Blocked
- P1-RQ-20260826T020213-744E-establish-korean-government-bond-yield-curve-data-contract-and-local-pro | next=Execute batch 2 with the active runbook in the next 17:00-18:00 KST provider-publication window, then validate field/canonical-row comparison and API0 replay.
- P1-RQ-20260826T052521-3082-define-a-secure-scheduled-daily-toss-read-only-account-refresh | next=Observe the installed task naturally after 07:00 KST, then validate the exact identifier-free occurrence and last receipt, provider-call budget, snapshot atomicity/privacy, and task result without a manual provider retry.
