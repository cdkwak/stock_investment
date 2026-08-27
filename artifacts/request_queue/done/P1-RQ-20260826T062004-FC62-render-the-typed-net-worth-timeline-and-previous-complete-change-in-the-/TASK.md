# Render the typed Net Worth timeline and previous-complete change in the GUI

## Problem
The Net Worth page ignores the existing typed multi-date timeline and previous-complete delta, reducing validated append-only history to a date selector and one-date details.

## Evidence
build_net_worth_timeline already selects the deterministic latest revision per date, emits explicit GAP points, and allows a delta only between same-currency complete snapshots; NetWorthPage.set_history does not render that projection.

## Scope
allow:
- After F2B9, modify only NetWorthPage rendering in main_window.py and its existing test_net_worth_page.py coverage, consuming the accepted build_net_worth_timeline API unchanged.

deny:
- No service-contract or persistence changes, no interpolation/forward fill, no cross-currency delta or conversion, no provider/account call, no identifier exposure, no automatic account reconciliation, and no data mutation.

## Done When
NetWorthPage builds only the existing typed timeline and renders chronological dates, displayable KRW net-worth points, explicit non-interpolated gaps, and the selected point's previous-complete delta/date only when delta_state is AVAILABLE. GAP, incomplete, invalid, currency-mismatched, empty, and single-point states remain numeric-free with stable typed copy; the date selector and timeline selection stay synchronized. Privacy mode hides every amount, delta, and plotted value without mutating history, while dates and non-sensitive availability state may remain visible.

## Verify
Extend the existing NetWorthPage test with multiple revisions, two complete dates and one gap; assert chronological latest-revision points, broken line/no fill, exact same-currency delta, selector synchronization, privacy masking, currency mismatch and empty/single states; run the owning page/service regression and provider-free native GUI close smoke.
