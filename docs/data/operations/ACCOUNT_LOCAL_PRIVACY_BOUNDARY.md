# Account Local Privacy Boundary

## Scope

This boundary applies only to the accepted read-only Toss snapshot, the
offline KB projection, and the local/manual family attribution view. It does
not authorize account connection, remote sync, orders, correction/cancel,
transfers, or withdrawals.

## Field inventory

| Field class | Handling |
| --- | --- |
| Credentials, access tokens, authentication payloads | Process memory only; never read from `.env`, persisted, logged, exported, or shown |
| Full account number / provider account selector | Process memory only; never included in snapshot contracts; any presentation is last-four masked |
| Full provider response | Never retained; converted directly to the validated sanitized projection |
| Balances, valuation, P/L, positions | Retained only in the local contract projection and sanitized Landing provenance; hidden on demand in both Account and Dashboard views |
| Registered holder | Stored only as the generic scope `SELF` or `FAMILY_MEMBER`; no person identity |
| Economic attribution | Stored separately as `SELF` or `USER_DECLARED_FUNDS`; never represented as legal ownership |
| Runtime cache | In-process typed views only; discarded at shutdown and replaced with `NOT_AVAILABLE` on read failure |
| Errors and update events | Stable reason codes and redacted bounded text only; no paths, account identifiers, balances, symbols, payloads, or responses |
| Exports and screenshots | No account export exists; the hide control removes monetary values and position symbols from rendered account surfaces |
| Synthetic tests | Temporary-directory fixtures only; no retained or live account values |

## Retention and removal

- Normalized and state each retain only the latest validated Toss projection.
- Toss Landing retains the newest sanitized projection only. Failure to clean
  an older projection cannot invalidate the newly committed current snapshot.
- The user removal control is allowlisted to the exact Toss Landing,
  Normalized, state, account transaction-journal files, and the two named local
  KB/family snapshot files. Orphaned files are removed only from the exact Toss
  account staging shape. It uses no recursive delete and cannot target
  credentials or market datasets.
- An incomplete account promotion blocks removal. Missing, locked, unreadable,
  or corrupt snapshots render `NOT_AVAILABLE`; no previously rendered value is
  reused.
- Removal stops in-process periodic refresh and disables the current refresher
  instance so the same session cannot immediately recreate the deleted data.

## Local threat model

The controls reduce accidental disclosure through GUI shoulder-surfing,
screenshots, diagnostics, logs, stale widgets, corrupt artifacts, and
over-broad deletion. They rely on the signed-in Windows user's filesystem
boundary and existing ACLs. They do not claim protection from a compromised OS,
malware, an unlocked user session, memory inspection, or a user with equivalent
filesystem access.

No project-supported OS key-storage or established encryption integration was
present at this boundary, so this change does not invent encryption or embed a
key. If encrypted local persistence is later required, it must use a separately
approved OS-backed design without changing the read-only provider contract.
