# LS historical source boundaries

Status: `T8428_SOURCE_ERROR_BOUNDARY_STOP / PREDICTIVE_USE_BLOCKED`.

The retained `t8428` Raw history reaches the observed 2006-06-01 boundary. Its
next continuation returned source error `IGW40014` with no rows; that response
is retained and must not be retried without new LS evidence. The provider's
calendar, publication/revision timing, and earliest reachable source date remain
unresolved, so this stays a source observation rather than a normalized
predictive dataset.

Detailed retained source inventory and t8428 pagination evidence is archived at
`docs/archive/data/evidence/2026-08-data-phase/ls/LS_OPENAPI_SOURCE_INVENTORY.md`.
