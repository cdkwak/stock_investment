# GUI usability review — 2026-08-25

Scope: the nine registered read-only pages in the current build, captured at
1600×900. Account and net-worth pages were constructed with deliberately missing
private-data paths, so the evidence contains no user balances or holdings.

## Overall conclusion

The GUI has enough functional surface to move toward backtest iteration, but its
default presentation makes capability look like complexity. The dominant
usability rule for the next pass should be: show the user's next decision first,
then reveal source/contract/tool detail on demand.

## Screen-by-screen findings

1. **Dashboard** — It is the strongest daily-use screen, but the top market strip,
   session text, chart controls, side panels, and derivatives compete for first
   attention. Preserve the existing preference system and let a compact “today”
   view become the default. The captured horizontal overflow is already tracked
   by `RQ-20260824T233837-CEEA`.
2. **Index Graph** — The chart is useful and information-dense, but index/period,
   indicators, measurement, reload, detach, provenance, legend, price, and volume
   all start expanded. Keep index, period, and reload primary; move measurement,
   indicators, and detach into a clearly labelled chart-tools drawer.
3. **Korean equity** — Before selection, a large empty black chart and unavailable
   tools dominate. Replace it with an action-first starter state: focused search,
   recent/favorite symbols, and one-click examples; reveal chart tools only after
   a valid selection.
4. **U.S. ETF** — It repeats the same premature tool density as Korean equity.
   The starter state should additionally expose a few local catalog categories
   (broad market, dividend, sector, leveraged/inverse) without making a provider
   call.
5. **Watchlist** — Viewing and list administration share one always-visible row.
   Default to viewing; put rename/delete/reorder behind an explicit “목록 편집”
   mode, and provide a prominent add/search action. Preserve double-click/open.
6. **Data Status** — The semantics are strong, but two routing tables precede the
   filterable dataset list. Make the four summary cards clickable filters, add
   dataset text search, and collapse routing matrices by default so an issue can
   lead directly to its exact next action.
7. **Account** — The private-safe empty state uses most of the viewport but keeps
   the useful actions in the header. Put source readiness and primary
   refresh/import actions inside the empty panel. Once populated, lead with total,
   currency selector, allocation/history, then holdings; keep source/account
   diagnostics collapsible.
8. **Net worth** — The empty state should contain the “새 스냅샷” action and a short
   example of what belongs here. With history present, lead with net-worth trend
   and change since previous snapshot, then asset/liability detail. Destructive
   date deletion should remain secondary.
9. **Backtest** — The current page makes contract/configuration evidence primary
   and results secondary; the capture requires over one extra viewport of scroll
   to reach later results. Lead with run state, core metrics, NAV/drawdown, and
   comparison. Collapse frozen digest, receipts, feature details, and scope into
   an “evidence and reproducibility” section.

## Recommended order

1. Action-first empty states for Korean equity and U.S. ETF.
2. Results-first Backtest layout with collapsible evidence.
3. Clickable/searchable issue-first Data Status.
4. Compact Dashboard default and chart-tool drawers.
5. Watchlist edit mode, then Account/Net Worth empty-state polish.

## Evidence

The page captures and machine-readable scroll measurements are under `pages/`
and `capture_manifest.json`. Offscreen Qt did not render Korean glyph shapes in
the PNGs on this worker, so typography was assessed from widget source and the
current native acceptance evidence, while geometry and control hierarchy were
assessed from these captures. No provider call, scheduler mutation, account
refresh, backtest execution, or production code change was performed.
