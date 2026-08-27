import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts/manual/research/validate_ls_t1633_quantity_with_toss.py"
SPEC = importlib.util.spec_from_file_location("toss_ls_quantity_validation", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _payload(*, day="2025-01-02", arb=(20, 8), nonarb=(30, 10)):
    return {"result": {"records": [{"date": day, "arbitrage": {"buyVolume": str(arb[0]), "sellVolume": str(arb[1]), "netBuyVolume": str(arb[0] - arb[1])}, "nonArbitrage": {"buyVolume": str(nonarb[0]), "sellVolume": str(nonarb[1]), "netBuyVolume": str(nonarb[0] - nonarb[1])}}]}}


def test_exact_record_and_multiplier_accept_only_exact_common_integer_ratio():
    toss = MODULE.validate_exact_record(_payload(), "2025-01-02")
    ls = {key: value // 2 for key, value in toss.items()}
    assert MODULE.exact_multiplier(toss, ls) == 2


def test_exact_multiplier_rejects_nonuniform_or_fractional_ratios():
    toss = MODULE.validate_exact_record(_payload(), "2025-01-02")
    bad = {key: value // 2 for key, value in toss.items()}
    bad["cha1"] += 1
    assert MODULE.exact_multiplier(toss, bad) is None


def test_exact_record_rejects_wrong_date_and_net_invariant():
    import pytest
    with pytest.raises(ValueError, match="date differs"):
        MODULE.validate_exact_record(_payload(day="2025-01-01"), "2025-01-02")
    payload = _payload()
    payload["result"]["records"][0]["arbitrage"]["netBuyVolume"] = "13"
    with pytest.raises(ValueError, match="invariant"):
        MODULE.validate_exact_record(payload, "2025-01-02")
