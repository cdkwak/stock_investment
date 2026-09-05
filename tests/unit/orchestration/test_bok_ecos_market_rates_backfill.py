from datetime import date

import pytest

from scripts.manual.collect.backfill_bok_ecos_market_rates import plan_windows


def test_backfill_window_plan_is_contiguous_bounded_and_complete() -> None:
    windows = plan_windows(date(1987, 1, 1), date(1989, 3, 11))
    assert windows == (
        (date(1987, 1, 1), date(1988, 2, 4)),
        (date(1988, 2, 5), date(1989, 3, 10)),
        (date(1989, 3, 11), date(1989, 3, 11)),
    )
    assert all((end - start).days + 1 <= 400 for start, end in windows)
    assert all(
        left_end.toordinal() + 1 == right_start.toordinal()
        for (_, left_end), (right_start, _) in zip(windows, windows[1:])
    )


def test_backfill_window_plan_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="start"):
        plan_windows(date(2026, 1, 2), date(2026, 1, 1))
    with pytest.raises(ValueError, match="between 1 and 400"):
        plan_windows(date(2026, 1, 1), date(2026, 1, 2), max_window_days=401)
