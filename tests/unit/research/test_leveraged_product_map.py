"""Real-product mapping guard (review 2026-09-05: 123320 calibrated two underlyings)."""
import warnings

import pytest

from stock_data.research import leveraged_product as lp


def test_current_map_reports_the_shared_kospi_product_as_a_warning() -> None:
    shared = lp.shared_real_products()
    assert shared == {"123320": ("KOSPI", "KOSPI200")}
    with pytest.warns(RuntimeWarning, match="123320"):
        lp.validate_real_product_map()


def test_one_to_one_map_is_silent_and_strict_mode_raises_on_sharing() -> None:
    clean = {("A", 2): "P1", ("B", 2): "P2", ("A", 3): "P3"}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert lp.validate_real_product_map(clean) == {}
    shared = {("A", 2): "P1", ("B", 2): "P1"}
    with pytest.raises(ValueError, match="P1"):
        lp.validate_real_product_map(shared, strict=True)
    assert lp.validate_real_product_map(shared, allowlist=frozenset({"P1"})) == {}
