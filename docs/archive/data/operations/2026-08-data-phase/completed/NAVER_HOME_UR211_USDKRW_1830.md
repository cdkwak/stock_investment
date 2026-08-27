# UR-211 Naver home USD/KRW 18:30 window

Only `2026-08-21T18:30:00+09:00` through before 19:00 is eligible. The runner
is `naver_mobile_home_ur211_window.collector(root)` and its projection allowlist
contains only `FX_USDKRW`; KOSPI, KOSDAQ, Gold and WTI are never written.
It reuses the existing durable claim/lock, one GET timeout-10, Landing hash
readback, strict parser, atomic prior preservation, and API-zero replay rules.

An absent state file is the exact empty initial ledger for this one new window;
the collector durably claims it before constructing the sole GET callback. An
unreadable/malformed state or any current-window record (including
`ATTEMPTING` or terminal) remains callback/API-zero. The 2026-08-21 18:35 KST
attempt consumed its one GET and ended `COMPLETE_FAILURE` before an HTTP body,
with sanitized `ConnectionError`, no Landing, and prior projection preservation;
do not retry this window.
