# UR-233 exact local current-readiness projection

Status: `TERMINAL_CONSUMED_UR233_API_ZERO`

This one-use, API-zero production projection reads only the canonical 64-row
readiness CSV, terminal UR-193 SOXX local state, and exact UR-232 local
`global60m_ur232` envelopes. It uses the shared timezone-aware today-KST and
source-age-at-most-60-minute gate at an explicit audit clock. It may change only
the four mapped 60m rows: `usd_krw_60m_detail`, `ust2_futures_60m`,
`ust10_futures_60m`, and `ust30_futures_60m`. The separate
`usd_krw_official_row` stays untouched.

UR-232 FX is indicative `KRW per USD`; the three Treasury rows are
provider-native continuous futures prices, never official Treasury yields. No
provider, scheduler, GUI worker, history, canonical, or Backtest operation is
allowed. The supported projector must receive explicit confirmation and creates
one durable preimage before atomic CSV replacement/readback. After one result,
this manifest and runbook are terminalized; replay is no-write/API-zero.

## Completed result

At `2026-08-21T21:27:16.2063306+09:00`, the sole production projection used
API calls zero and atomically read back the 64-row UTF-8 RFC4180 CSV. It updated
the four UR-232 mapped 60m surfaces from their exact local envelopes and
reconciled allowlisted local SOXX/Naver rows under the shared gate. Its durable
preimage was 제거됨 (backup/repo-cleanup-phase2-20260903 브랜치에 보존).
The UR-230 terminal manifest remains untouched; this UR-233 manifest is now
terminal and does not authorize another write.
