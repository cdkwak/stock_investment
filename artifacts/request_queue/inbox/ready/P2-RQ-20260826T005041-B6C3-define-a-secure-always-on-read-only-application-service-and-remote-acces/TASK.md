# Define a secure always-on read-only application-service and remote-access contract

## Problem
No versioned always-on read-only application-service and remote-access boundary lets desktop/web/mobile clients consume the same validated market, account, Health, issue and summary projections without direct file/provider access or unsafe secret/account exposure.

## Evidence
PROJECT_GOAL requires laptop-independent computation, one read-only service, truthful stale/offline state, authentication, encrypted transport, short revocable sessions, least privilege, audit and server-only secrets. Project Status is local-only; Roadmap requires GUI->application services and says file sharing is not that boundary. Existing tasks define local producers/consumers only.

## Scope
allow:
- After B2ED/7CC5/E9A5/BB66 acceptance, create the future Project-owned architecture/security documentation contract and update Project Status routing only.

deny:
- No hosting/network/API implementation, vendor selection, public exposure, credential creation/migration, provider/account call, scheduler/Data mutation, automatic recovery, UI implementation, order/transfer/trading, or secret/private value in docs/logs/artifacts.

## Done When
A documentation-only remote-readonly-service/v1 contract defines versioned resource/envelope identities for accepted market/account/Health/issue/summary projections; GET/HEAD-only semantics with no read-triggered refresh; summary-to-reason-to-evidence disclosure; source/as-of/last-success/freshness/partial-failure/stale/offline fields; explicit identity, per-resource deny-by-default authorization, short revocable sessions, encrypted transport, CSRF/replay/rate-limit boundaries, redacted access audit, server-only secret custody, client/cache privacy, backup/RPO/RTO and fail-closed restart behavior; direct client file/provider access is forbidden; a deployment decision gate compares cost, operations, licensing, security, backup and ownership without selecting a vendor; PROJECT_STATUS links it without implementation/exposure authority.

## Verify
Map every Project Goal remote/security requirement and Roadmap dependency rule to a field/invariant; threat-model unauthenticated, cross-account, token theft/replay, cache leakage, stale replay, direct provider/file access and outage cases; prove all methods are read-only and cannot trigger provider/account/scheduler/Data mutation; verify prerequisite projection contracts, links and queue doctor.
