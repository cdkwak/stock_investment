# Make the Dashboard ten-card strip readable in one row and surface market PER/PBR first

## Problem
The Dashboard must remain readable at the user's common logical widths and must surface broad-market valuation context directly rather than hiding it behind the less useful default context surface.

## Evidence
Read-only audit reproduces top-card wrapping below 1450 logical px, 9px body text, a 14px sparkline with about 6px drawable height, valuation behind the second tab, and only historical median comparison despite the user's arithmetic-average request.

## Scope
allow:
- Provider-free GUI/service/layout changes over the accepted local KRX valuation contract; add arithmetic historical mean with exact observation count/date span; make valuation the visible default while preserving independent market flow; keep exactly ten cards in one row at logical widths 1280,1365,1440 with readable text and useful sparkline bounds; focused native/offscreen tests and status truth.

deny:
- No provider/API/account/scheduler/data/history writes; no market breadth fabrication; no trading/recommendation signal; no median relabel as mean; no fallback/fill; no change to source identity, freshness, PIT, card metric meaning, or unrelated GUI surfaces; no holdout access.

## Done When
At logical widths 1280,1365,1440 and DPR 1/1.5 all ten configured cards occupy exactly one row with visible title/body/status text and nonzero useful sparkline plot area when typed data exists; KOSPI/KOSDAQ weighted PER and PBR are the default visible market-context surface and show exact as-of, arithmetic historical mean, signed difference from mean, observation count/date span, plus existing median/percentile descriptive context without signal wording; market flow remains separately reachable and market breadth is not promoted; invalid/missing/nonfinite/mismatched valuation history stays numeric-free.

## Verify
Unit-test arithmetic mean on exact retained-style rows and fail-closed invalid histories; offscreen/native render 1280,1365,1440 at DPR 1 and 1.5; assert ten columns/one row, readable minimum font and sparkline plot height, valuation default, market flow reachability, no horizontal overflow, clean QThread/window shutdown, provider calls 0 and data writes 0; run full owning GUI service/widget suites.
