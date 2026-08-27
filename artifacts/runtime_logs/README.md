# Local runtime logs

`data_updates/` is the default local operational event-log root for bounded
manual and scheduled Data updates. Each event is a versioned, redacted JSON
document committed atomically below `data_updates/events/YYYY-MM-DD/`.
Interrupted complete writes are staged below `data_updates/.pending/` and are
recovered by the next writer; readers never treat a pending or malformed file as
an accepted event.

This path is an operational index only. It is outside Raw, Normalized, Derived,
Published, Landing, and checkpoint contracts and never replaces provider
provenance. Callers must preserve their provider-call budget, promotion result,
checkpoint result, and scheduler outcome when logging fails, while surfacing the
typed log-write error separately.

Runtime event files, pending files, and the writer lock are generated locally
and ignored by Git. Retention and maximum event count are bounded through
`EventLogPolicy`.

`application/` is the separate dependency-neutral GUI/Backtest diagnostic
store. Each `runtime-diagnostic/v1` JSON event is atomically committed and the
oldest files are removed after the fixed local bound. Its schema permits only
correlation IDs, stable domain/kind/code/stage tokens, sanitized exception class
names, and repository-relative Python frame locations. Exception messages,
traceback text, locals, arguments, user input, URLs, credentials, account or
holding values, orders, and raw payloads are not schema fields. Logging failure
is swallowed and cannot change the GUI or Backtest outcome. Inspect the latest
bounded entries read-only with
`python scripts/maintenance/inspect_runtime_failures.py --limit 20`.

The current `run_provider_scheduler.py` and `run_yahoo_market_current.py`
scheduled entrypoints append one `STARTED` and one terminal event with the same
run ID. They project only bounded lane/status/count fields; provider URLs,
payloads, credentials, account data, and response bodies are never passed to
the event log. A log-write failure emits only a typed safe status on stderr and
does not change the provider result, last-result JSON, call budget, or exit code.
