# Request Queue Workflow Changelog

This is an append-only record of material operating-model changes. Add new
dated entries at the end; update [WORKFLOW.md](WORKFLOW.md) separately so it
continues to describe only the current workflow.

## 2026-08-28 — Queue v2.1 operating model

- Separated durable Queue request authority from Orca execution authority:
  Queue owns routing, reservations, review policy, checkpoint, and business
  lifecycle; Orca owns supervised conversations, Dispatch attempts, terminals,
  wakeups, and completion delivery.
- Made the Domain Lead the operational owner of decomposition, scoped recovery,
  Worker/Reviewer lifecycle, accepted discovery intake, and Submit. Workers and
  Reviewers report to the Lead; MAIN retains global intake, deduplication,
  triage, priority, dependencies, routing, and cross-domain integration.
- Added managed discovery provenance for Coordinator, Lead, Goal Planner, and
  runtime-monitor intake while preserving the original User, Worker, Reviewer,
  Lead, Goal Planner, or runtime-monitor reporter role.
- Replaced the raw live-item Goal throttle with an operational low-water mark:
  a live P0, six dependency-ready Lead-routed tasks, or six untriaged discoveries
  pauses unsolicited planning, while an explicit user Goal sync may run once.
- Separated urgency from difficulty by adding complexity and risk-based Worker
  and Reviewer profiles, with current Orca model/effort mappings exposed by the
  Lead status view.
- Retained Queue v2/F3C6 migration compatibility: existing tasks may omit new
  provenance/profile fields, legacy Orca reconciliation remains readable
  telemetry, and historical Done receipts are not rewritten.

## 2026-08-29 — Durable Orca role and session recovery

- Added `PIPELINE.md` as the navigable, Doctor-recognized recovery guide for
  durable Manager and Domain Lead Runs, resumable Codex sessions, Task identity,
  per-attempt Dispatch identity, Worker cleanup, and fresh review generations.
- Kept Queue as business authority and Orca as execution authority: recovery
  reuses verified Run/Task/session provenance, reconciles stale Dispatches, and
  never treats `reset --all` or blanket process recreation as routine startup.

## 2026-08-29 — Queue v2.2 offline workflow-policy lifecycle

- Added content-addressed, versioned policy proposals bound to an accepted
  workflow-event snapshot generation, canonical event digest, event IDs, and
  acceptance-receipt digest, with deterministic offline replay and stale-
  generation/event-substitution rejection.
- Required an immutable `PASS` receipt from an identity independent of the
  implementation identity. Any candidate change creates a new proposal
  generation and invalidates prior review evidence.
- Added bounded canary criteria that are disabled by default plus explicit
  refusal, promotion, and rollback receipts. Receipt evaluation is side-effect
  free and cannot activate a scheduler, promote production, or mutate Queue or
  external state.
- Added fail-closed authority tiers: local proposal/replay work is allowed;
  account and standing-authority lifecycle actions require review and standing
  authority; broker/order, transfer/withdrawal, financial, access-control,
  secret, paid-service, and destructive-migration actions remain prohibited.
- Established Python workflow-control as the target policy authority while
  retaining Orca only as optional supervised transport. Live cutover remains a
  separate reviewed operation.
