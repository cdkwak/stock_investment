# Recover stale Codex worktree Queue-manager identity without destructive cleanup

## Problem
A stale linked Codex worktree contains a local Queue manager, so the canonical Queue integrity gate prevents the unattended-runner parent from entering Review; the owning Codex task identity is not yet resolvable.

## Evidence
Canonical Queue Doctor reproducibly reports the single linked-worktree Queue-manager copy; bounded archived-task lookup found no match and the provisional client identifier is not a resolvable task identity. No direct cleanup or worktree mutation has occurred.

## Scope
allow:
- Bounded read-only Codex task/worktree identity lookup; identity-checked recoverable lifecycle handling for the exact stale worktree; canonical linked-worktree detection and focused tests only if a repository defect is proven.

deny:
- Direct file or worktree deletion, reset, cleanup, access-control bypass, Queue Doctor weakening or ignore-list bypass, unrelated worktrees/files, noncanonical Queue mutation, provider/account/broker/secret operations, and any action before exact inactive identity is proven.

## Done When
The exact linked worktree is bound to a supported Codex task/worktree identity and proven inactive; its Queue-manager path is neutralized only through an identity-checked, history-preserving supported lifecycle operation. Queue Doctor then returns OK, the parent task can safely enter Review, no unresolved or active worktree is altered, and no Queue integrity rule is weakened.

## Verify
Re-run canonical request_queue.py doctor; verify the exact worktree/task identity and inactive-state evidence; verify supported lifecycle receipt and preserved history; verify no linked-worktree Queue manager remains, no direct filesystem deletion/reset/cleanup occurred, and focused Queue integrity tests pass.
