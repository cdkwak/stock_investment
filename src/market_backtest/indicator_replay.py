from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import date as calendar_date
from datetime import datetime
import hashlib
from io import BytesIO
import json
from math import isclose, isfinite
import os
from pathlib import Path
import shutil
from typing import Mapping

import pandas as pd

from market_features.frozen import verify_frozen_kospi200
from market_features.rsi import build_wilder_rsi14
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily

from .execution import (
    EXECUTION_CLAIM,
    EXECUTION_CONTRACT_VERSION,
    EXECUTION_STATUS,
    NEXT_OPEN_V1,
    ExecutionAssumptions,
    ExecutionLedgerRow,
    ExecutionMetrics,
    simulate_next_open_execution,
)
from .indicator_strategy import (
    MATCHED_HOLD_CONTRACT_VERSION,
    MATCHED_HOLD_STATUS,
    THRESHOLD_BAND_CONTRACT_VERSION,
    THRESHOLD_BAND_STATUS,
    MatchedHoldMetrics,
    ThresholdBandDecision,
    ThresholdBandPolicy,
    compare_threshold_band_to_matched_hold,
)
from .indicator_study import (
    INDICATOR_STUDY_CONTRACT_VERSION,
    INDICATOR_STUDY_STATUS,
    IndicatorCandidate,
    IndicatorStudyMetrics,
    evaluate_predefined_indicators,
)
from .labels import build_forward_labels
from .portfolio import KOSPI200_FROZEN_HOLDOUT_V1


INDICATOR_REPLAY_SCHEMA = "indicator-scenario-replay/v1"
INDICATOR_REPLAY_STATUS = "DEVELOPMENT_ONLY_FIXED_RSI14_30_70"
EXPECTED_FROZEN_DIGEST = (
    "a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713"
)
DEFAULT_OUTPUT_RELATIVE = Path("artifacts/backtest/indicator_scenario_replay")
_ARTIFACT_NAMES = ("result.json", "rsi14.csv", "scenario.json", "study.json")
_OWNED_NAMES = frozenset((*_ARTIFACT_NAMES, "bundle.json"))
_CODE_PATHS = (
    Path("src/market_backtest/execution.py"),
    Path("src/market_backtest/holdout.py"),
    Path("src/market_backtest/indicator_replay.py"),
    Path("src/market_backtest/indicator_strategy.py"),
    Path("src/market_backtest/indicator_study.py"),
    Path("src/market_backtest/labels.py"),
    Path("src/market_features/frozen.py"),
    Path("src/market_features/rsi.py"),
)


class IndicatorReplayError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IndicatorReplayRequest:
    project_root: Path
    output_root: Path | None = None


@dataclass(frozen=True, slots=True)
class IndicatorArtifactReceipt:
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class IndicatorReplayReceipt:
    schema: str
    status: str
    output_root: Path
    frozen_input_digest: str
    bundle_digest: str
    artifacts: tuple[IndicatorArtifactReceipt, ...]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ) + "\n").encode("utf-8")


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _receipt(name: str, body: bytes) -> IndicatorArtifactReceipt:
    return IndicatorArtifactReceipt(name, len(body), _digest(body))


def _receipt_digest(receipts: tuple[IndicatorArtifactReceipt, ...]) -> str:
    return _digest(_json_bytes([asdict(item) for item in receipts]))


def _code_digest(project_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in _CODE_PATHS:
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise IndicatorReplayError("indicator replay code identity is unavailable")
        body = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(_digest(body).encode("ascii") + b"\n")
    return digest.hexdigest()


def _exact_project_root(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = path.resolve()
    if not path.is_dir() or path.is_symlink():
        raise IndicatorReplayError("project root is unavailable")
    return path


def _load_development_source(project_root: Path) -> tuple[object, pd.DataFrame]:
    dataset_root = (
        project_root / "artifacts/backtest/frozen_inputs/kr_kospi200_index_daily"
        / EXPECTED_FROZEN_DIGEST
    )
    manifest_path = project_root / "artifacts/backtest/kospi200_frozen_manifest.json"
    try:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IndicatorReplayError("frozen manifest is unavailable") from error
    if not isinstance(expected, dict) or expected.get("root_manifest_sha256") != EXPECTED_FROZEN_DIGEST:
        raise IndicatorReplayError("frozen manifest identity differs")
    try:
        manifest = verify_frozen_kospi200(dataset_root, expected)
        source = pd.concat(
            [pd.read_parquet(path) for path in sorted(dataset_root.rglob("data.parquet"))],
            ignore_index=True,
        )
        readback = verify_frozen_kospi200(dataset_root, expected)
    except Exception as error:
        raise IndicatorReplayError("frozen input verification failed") from error
    if readback != manifest or manifest.root_manifest_sha256 != EXPECTED_FROZEN_DIGEST:
        raise IndicatorReplayError("frozen input changed during read")

    # Only the canonical date key is inspected before the sealed slice. Numeric
    # validation and feature/label construction receive development rows only.
    dates = source.get("date")
    if not isinstance(dates, pd.Series):
        raise IndicatorReplayError("frozen source date key is unavailable")
    try:
        canonical = pd.to_datetime(dates, format="%Y-%m-%d", errors="raise")
    except (TypeError, ValueError) as error:
        raise IndicatorReplayError("frozen source date key is invalid") from error
    if (
        canonical.dt.strftime("%Y-%m-%d").tolist() != dates.astype(str).tolist()
        or canonical.duplicated().any()
        or not canonical.is_monotonic_increasing
    ):
        raise IndicatorReplayError("frozen source date key is invalid")
    development = source.loc[
        canonical.lt(pd.Timestamp(KOSPI200_FROZEN_HOLDOUT_V1.holdout_start))
    ].reset_index(drop=True)
    development["date"] = pd.to_datetime(
        development["date"], errors="raise"
    ).dt.strftime("%Y-%m-%d")
    if len(development) != KOSPI200_FROZEN_HOLDOUT_V1.development_observations:
        raise IndicatorReplayError("development source count differs")
    validate_kospi200_index_daily(development)
    return manifest, development


def _scenario_inputs(source: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = source.get("date")
    if not isinstance(dates, pd.Series) or dates.empty:
        raise IndicatorReplayError("scenario source date key is unavailable")
    # Fail before close/open or any derived numeric value is inspected.
    if dates.map(lambda value: type(value) is not str).any() or dates.ge(
        KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
    ).any():
        raise IndicatorReplayError("scenario source reaches untouched holdout")
    features = build_wilder_rsi14(source)
    labels = build_forward_labels(source)
    labels = labels.loc[
        labels["observation_date"].isin(set(features["observation_date"]))
    ].reset_index(drop=True)
    open_values = pd.to_numeric(source["open"], errors="raise")
    invalid_open = source.index[open_values.le(0.0)]
    start_index = int(invalid_open.max()) + 1 if len(invalid_open) else 0
    executable_proxy = source.iloc[start_index:].reset_index(drop=True)
    if executable_proxy.empty or pd.to_numeric(
        executable_proxy["open"], errors="raise"
    ).le(0.0).any():
        raise IndicatorReplayError("scenario has no contiguous positive-open coverage")
    market = pd.DataFrame({
        "session_date": executable_proxy["date"],
        "open": executable_proxy["open"],
        "close": executable_proxy["close"],
        "instrument_id": "KRX:1028",
        "currency": "KRW",
    })
    return features, labels, market


def _build_artifacts(project_root: Path) -> Mapping[str, bytes]:
    manifest, source = _load_development_source(project_root)
    features, labels, market = _scenario_inputs(source)
    candidates = (
        IndicatorCandidate("RSI14_LOW_30", "rsi_14", "LOW", 30.0, 20, 20),
        IndicatorCandidate("RSI14_HIGH_70", "rsi_14", "HIGH", 70.0, 20, 20),
    )
    study = evaluate_predefined_indicators(
        features, labels, candidates, KOSPI200_FROZEN_HOLDOUT_V1,
    )
    policy = ThresholdBandPolicy("RSI14_30_70", "rsi_14", 30.0, 70.0)
    strategy_features = features.loc[
        features["observation_date"].ge(market["session_date"].iloc[0])
        & features["observation_date"].lt(market["session_date"].iloc[-1])
    ].reset_index(drop=True)
    comparison = compare_threshold_band_to_matched_hold(
        market, strategy_features, policy, KOSPI200_FROZEN_HOLDOUT_V1,
    )
    code_digest = _code_digest(project_root)
    feature_body = features.to_csv(index=False, lineterminator="\n").encode("utf-8")
    study_body = _json_bytes({"study": asdict(study)})
    scenario_body = _json_bytes({"comparison": asdict(comparison)})
    result_body = _json_bytes({
        "schema": INDICATOR_REPLAY_SCHEMA,
        "status": INDICATOR_REPLAY_STATUS,
        "frozen_input_digest": manifest.root_manifest_sha256,
        "code_digest": code_digest,
        "feature": {
            "name": "WILDER_RSI14", "version": 1,
            "pit_status": "PIT_SAFE_EOD_T_PLUS_1",
            "observations": len(features),
        },
        "execution_proxy": {
            "instrument_id": "KRX:1028",
            "claim": "INDEX_OPEN_PROXY_NOT_OBTAINABLE_INSTRUMENT",
            "coverage_start": market["session_date"].iloc[0],
            "coverage_end": market["session_date"].iloc[-1],
            "observations": len(market),
        },
        "study_candidates": [asdict(item) for item in candidates],
        "execution_policy": asdict(policy),
        "holdout": asdict(KOSPI200_FROZEN_HOLDOUT_V1),
        "winner_selected": False,
        "recommendation_made": False,
        "results_scope": "DEVELOPMENT_ONLY_HOLDOUT_UNTOUCHED",
    })
    bodies = {
        "result.json": result_body,
        "rsi14.csv": feature_body,
        "scenario.json": scenario_body,
        "study.json": study_body,
    }
    receipts = tuple(_receipt(name, bodies[name]) for name in _ARTIFACT_NAMES)
    bodies["bundle.json"] = _json_bytes({
        "schema": INDICATOR_REPLAY_SCHEMA,
        "frozen_input_digest": manifest.root_manifest_sha256,
        "code_digest": code_digest,
        "thresholds": {"enter_at_or_below": 30.0, "exit_at_or_above": 70.0},
        "holdout_policy_id": KOSPI200_FROZEN_HOLDOUT_V1.policy_id,
        "artifact_set_sha256": _receipt_digest(receipts),
        "artifacts": [asdict(item) for item in receipts],
    })
    return bodies


def _strict_json(body: bytes, *, name: str) -> dict[str, object]:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise IndicatorReplayError(f"{name} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(body, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise IndicatorReplayError(f"{name} is invalid") from error
    if not isinstance(value, dict):
        raise IndicatorReplayError(f"{name} root must be an object")
    return value


def _exact_keys(value: object, keys: set[str], *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise IndicatorReplayError(f"{name} schema differs")
    return value


def _dataclass_keys(value: type[object]) -> set[str]:
    return {item.name for item in fields(value)}


def _canonical_date_text(value: object, *, name: str) -> str:
    if type(value) is not str:
        raise IndicatorReplayError(f"{name} date differs")
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as error:
        raise IndicatorReplayError(f"{name} date differs") from error
    if parsed.isoformat() != value:
        raise IndicatorReplayError(f"{name} date differs")
    return value


def _aware_timestamp_text(value: object, *, name: str) -> datetime:
    if type(value) is not str or "T" not in value:
        raise IndicatorReplayError(f"{name} timestamp differs")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise IndicatorReplayError(f"{name} timestamp differs") from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.isoformat() != value
    ):
        raise IndicatorReplayError(f"{name} timestamp differs")
    return parsed


def _exact_float(value: object, *, name: str) -> float:
    if type(value) is not float or not isfinite(value):
        raise IndicatorReplayError(f"{name} numeric value differs")
    return value


def _close(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=1e-12, abs_tol=1e-15)


def _validate_study_metrics(value: object, *, availability: object) -> None:
    metrics = _exact_keys(
        value, _dataclass_keys(IndicatorStudyMetrics), name="study metrics",
    )
    aligned = metrics["aligned_observations"]
    signals = metrics["signal_observations"]
    if (
        type(aligned) is not int or aligned < 1
        or type(signals) is not int or not 0 <= signals <= aligned
    ):
        raise IndicatorReplayError("study metric counts differ")
    signal_rate = _exact_float(metrics["signal_rate"], name="study signal rate")
    if not 0.0 <= signal_rate <= 1.0 or not _close(signal_rate, signals / aligned):
        raise IndicatorReplayError("study signal rate differs")
    unconditional = (
        "unconditional_mean_return", "unconditional_median_return",
        "unconditional_positive_rate", "unconditional_mean_max_drawdown",
    )
    for name in unconditional:
        _exact_float(metrics[name], name=f"study {name}")
    if not 0.0 <= metrics["unconditional_positive_rate"] <= 1.0:
        raise IndicatorReplayError("study unconditional positive rate differs")
    conditional = (
        "conditional_mean_return", "conditional_median_return",
        "conditional_positive_rate", "conditional_mean_max_drawdown",
        "conditional_mean_return_difference",
    )
    if availability == "EVALUATED":
        for name in conditional:
            _exact_float(metrics[name], name=f"study {name}")
        if not 0.0 <= metrics["conditional_positive_rate"] <= 1.0:
            raise IndicatorReplayError("study conditional positive rate differs")
        if not _close(
            metrics["conditional_mean_return_difference"],
            metrics["conditional_mean_return"] - metrics["unconditional_mean_return"],
        ):
            raise IndicatorReplayError("study return difference differs")
    elif availability == "INSUFFICIENT_SIGNAL_OBSERVATIONS":
        if any(metrics[name] is not None for name in conditional):
            raise IndicatorReplayError("study unavailable metrics differ")
    else:
        raise IndicatorReplayError("study availability differs")


def _validate_execution(value: object, *, name: str) -> dict[str, object]:
    execution = _exact_keys(
        value,
        {
            "contract_version", "status", "execution_claim", "instrument_id",
            "currency", "assumptions", "ledger", "metrics",
        },
        name=name,
    )
    if (
        execution["contract_version"] != EXECUTION_CONTRACT_VERSION
        or execution["status"] != EXECUTION_STATUS
        or execution["execution_claim"] != EXECUTION_CLAIM
        or execution["instrument_id"] != "KRX:1028"
        or execution["currency"] != "KRW"
        or execution["assumptions"] != asdict(NEXT_OPEN_V1)
    ):
        raise IndicatorReplayError(f"{name} identity differs")
    _exact_keys(
        execution["assumptions"],
        _dataclass_keys(ExecutionAssumptions),
        name=f"{name} assumptions",
    )
    ledger = execution["ledger"]
    if not isinstance(ledger, list) or not ledger:
        raise IndicatorReplayError(f"{name} ledger differs")
    dates: list[str] = []
    prior_nav = NEXT_OPEN_V1.initial_cash
    running_peak = NEXT_OPEN_V1.initial_cash
    trade_count = 0
    for index, raw_row in enumerate(ledger):
        row = _exact_keys(
            raw_row, _dataclass_keys(ExecutionLedgerRow),
            name=f"{name} ledger row",
        )
        session_date = _canonical_date_text(
            row["session_date"], name=f"{name} session",
        )
        if session_date >= KOSPI200_FROZEN_HOLDOUT_V1.holdout_start:
            raise IndicatorReplayError(f"{name} reaches holdout")
        dates.append(session_date)
        decision_session = row["decision_session"]
        if decision_session is not None:
            decision_session = _canonical_date_text(
                decision_session, name=f"{name} decision session",
            )
            if index == 0 or decision_session != dates[index - 1]:
                raise IndicatorReplayError(f"{name} decision timing differs")
        position_before = row["position_before"]
        target_position = row["target_position"]
        side = row["trade_side"]
        if (
            type(position_before) is not int or position_before not in {0, 1}
            or type(target_position) is not int or target_position not in {0, 1}
            or side not in {"NONE", "BUY", "SELL"}
        ):
            raise IndicatorReplayError(f"{name} position semantics differ")
        numeric_names = (
            "open", "close", "trade_notional", "transaction_cost", "cash",
            "units", "asset_value_close", "nav_pre_trade", "nav_post_trade",
            "nav_close", "turnover", "net_return", "drawdown",
        )
        numbers = {
            item: _exact_float(row[item], name=f"{name} {item}")
            for item in numeric_names
        }
        if (
            numbers["open"] <= 0.0
            or numbers["close"] <= 0.0
            or numbers["nav_pre_trade"] <= 0.0
            or numbers["nav_post_trade"] <= 0.0
            or numbers["nav_close"] <= 0.0
            or any(
                numbers[item] < 0.0 for item in (
                    "trade_notional", "transaction_cost", "cash", "units",
                    "asset_value_close", "nav_pre_trade", "nav_post_trade",
                    "nav_close", "turnover",
                )
            )
            or not -1.0 <= numbers["drawdown"] <= 0.0
        ):
            raise IndicatorReplayError(f"{name} ledger bounds differ")
        fill = row["fill_price"]
        if side == "NONE":
            if (
                fill is not None
                or numbers["trade_notional"] != 0.0
                or numbers["transaction_cost"] != 0.0
            ):
                raise IndicatorReplayError(f"{name} no-trade row differs")
        else:
            fill_value = _exact_float(fill, name=f"{name} fill price")
            if not _close(fill_value, numbers["open"]):
                raise IndicatorReplayError(f"{name} fill price differs")
            trade_count += 1
        running_peak = max(running_peak, numbers["nav_close"])
        expected_return = numbers["nav_close"] / prior_nav - 1.0
        checks = (
            (numbers["asset_value_close"], numbers["units"] * numbers["close"]),
            (numbers["nav_close"], numbers["cash"] + numbers["asset_value_close"]),
            (
                numbers["nav_post_trade"],
                numbers["nav_pre_trade"] - numbers["transaction_cost"],
            ),
            (
                numbers["transaction_cost"],
                numbers["trade_notional"]
                * NEXT_OPEN_V1.one_way_cost_bps / 10_000.0,
            ),
            (numbers["turnover"], numbers["trade_notional"] / numbers["nav_pre_trade"]),
            (numbers["net_return"], expected_return),
            (numbers["drawdown"], numbers["nav_close"] / running_peak - 1.0),
        )
        if any(not _close(actual, expected) for actual, expected in checks):
            raise IndicatorReplayError(f"{name} ledger accounting differs")
        prior_nav = numbers["nav_close"]
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise IndicatorReplayError(f"{name} ledger dates differ")
    metrics = _exact_keys(
        execution["metrics"], _dataclass_keys(ExecutionMetrics),
        name=f"{name} metrics",
    )
    observations = metrics["observations"]
    observed_trades = metrics["trade_count"]
    if (
        type(observations) is not int or observations != len(ledger)
        or type(observed_trades) is not int or observed_trades != trade_count
    ):
        raise IndicatorReplayError(f"{name} metric counts differ")
    metric_floats = {
        item: _exact_float(metrics[item], name=f"{name} metric {item}")
        for item in _dataclass_keys(ExecutionMetrics)
        - {"observations", "trade_count"}
    }
    expected_metrics = {
        "initial_cash": NEXT_OPEN_V1.initial_cash,
        "ending_nav": ledger[-1]["nav_close"],
        "total_return": ledger[-1]["nav_close"] / NEXT_OPEN_V1.initial_cash - 1.0,
        "max_drawdown": min(row["drawdown"] for row in ledger),
        "total_turnover": sum(row["turnover"] for row in ledger),
        "average_long_exposure": (
            sum(row["target_position"] for row in ledger) / len(ledger)
        ),
        "transaction_cost_paid": sum(row["transaction_cost"] for row in ledger),
    }
    if (
        any(
            not _close(metric_floats[item], expected)
            for item, expected in expected_metrics.items()
        )
        or metric_floats["annualized_volatility"] < 0.0
        or not 0.0 <= metric_floats["average_long_exposure"] <= 1.0
    ):
        raise IndicatorReplayError(f"{name} metric accounting differs")
    market = pd.DataFrame({
        "session_date": [row["session_date"] for row in ledger],
        "open": [row["open"] for row in ledger],
        "close": [row["close"] for row in ledger],
        "instrument_id": [execution["instrument_id"]] * len(ledger),
        "currency": [execution["currency"]] * len(ledger),
    })
    decision_rows = [
        (row["decision_session"], bool(row["target_position"]))
        for row in ledger if row["decision_session"] is not None
    ]
    decisions = pd.DataFrame({
        "decision_session": pd.Series(
            [item[0] for item in decision_rows], dtype="object",
        ),
        "target_long": pd.Series(
            [item[1] for item in decision_rows], dtype="bool",
        ),
    })
    try:
        expected_execution = _strict_json(_json_bytes(asdict(
            simulate_next_open_execution(market, decisions, NEXT_OPEN_V1),
        )), name=f"{name} deterministic replay")
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise IndicatorReplayError(f"{name} deterministic replay failed") from error
    if execution != expected_execution:
        raise IndicatorReplayError(f"{name} deterministic replay differs")
    return execution


def _validate_semantic_payloads(
    bodies: Mapping[str, bytes], *, project_root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    if set(bodies) != _OWNED_NAMES:
        raise IndicatorReplayError("indicator replay artifact set differs")
    bundle = _strict_json(bodies["bundle.json"], name="bundle.json")
    result = _strict_json(bodies["result.json"], name="result.json")
    study_payload = _strict_json(bodies["study.json"], name="study.json")
    scenario_payload = _strict_json(bodies["scenario.json"], name="scenario.json")
    current_code_digest = _code_digest(project_root)
    _exact_keys(
        result,
        {
            "schema", "status", "frozen_input_digest", "code_digest", "feature",
            "execution_proxy", "study_candidates", "execution_policy", "holdout",
            "winner_selected", "recommendation_made", "results_scope",
        },
        name="result.json",
    )
    candidates = [
        asdict(IndicatorCandidate("RSI14_LOW_30", "rsi_14", "LOW", 30.0, 20, 20)),
        asdict(IndicatorCandidate("RSI14_HIGH_70", "rsi_14", "HIGH", 70.0, 20, 20)),
    ]
    policy = asdict(ThresholdBandPolicy("RSI14_30_70", "rsi_14", 30.0, 70.0))
    feature = _exact_keys(
        result.get("feature"), {"name", "version", "pit_status", "observations"},
        name="result feature",
    )
    proxy = _exact_keys(
        result.get("execution_proxy"),
        {"instrument_id", "claim", "coverage_start", "coverage_end", "observations"},
        name="result execution proxy",
    )
    if (
        result.get("schema") != INDICATOR_REPLAY_SCHEMA
        or result.get("status") != INDICATOR_REPLAY_STATUS
        or result.get("frozen_input_digest") != EXPECTED_FROZEN_DIGEST
        or result.get("code_digest") != current_code_digest
        or result.get("holdout") != asdict(KOSPI200_FROZEN_HOLDOUT_V1)
        or result.get("study_candidates") != candidates
        or result.get("execution_policy") != policy
        or result.get("winner_selected") is not False
        or result.get("recommendation_made") is not False
        or result.get("results_scope") != "DEVELOPMENT_ONLY_HOLDOUT_UNTOUCHED"
        or feature != {
            "name": "WILDER_RSI14", "version": 1,
            "pit_status": "PIT_SAFE_EOD_T_PLUS_1",
            "observations": feature.get("observations"),
        }
        or type(feature.get("observations")) is not int
        or feature["observations"] <= 0
        or proxy.get("instrument_id") != "KRX:1028"
        or proxy.get("claim") != "INDEX_OPEN_PROXY_NOT_OBTAINABLE_INSTRUMENT"
        or type(proxy.get("observations")) is not int
        or proxy["observations"] <= 0
        or not isinstance(proxy.get("coverage_start"), str)
        or not isinstance(proxy.get("coverage_end"), str)
        or not proxy["coverage_start"] < proxy["coverage_end"] < KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
    ):
        raise IndicatorReplayError("result.json semantics differ")
    try:
        rsi = pd.read_csv(BytesIO(bodies["rsi14.csv"]))
    except Exception as error:
        raise IndicatorReplayError("rsi14.csv is invalid") from error
    expected_columns = [
        "observation_date", "ticker", "date_semantics", "instrument_id",
        "observation_time", "available_at", "usable_from", "rsi_14",
        "source_dataset", "source_contract_version", "feature_version", "pit_status",
    ]
    if list(rsi.columns) != expected_columns or len(rsi) != feature["observations"] or rsi.empty:
        raise IndicatorReplayError("rsi14.csv schema/count differs")
    try:
        dates = pd.to_datetime(rsi["observation_date"], format="%Y-%m-%d", errors="raise")
        observation = pd.to_datetime(rsi["observation_time"], utc=True, errors="raise")
        available = pd.to_datetime(rsi["available_at"], utc=True, errors="raise")
        usable = pd.to_datetime(rsi["usable_from"], utc=True, errors="raise")
        values = pd.to_numeric(rsi["rsi_14"], errors="raise")
    except (TypeError, ValueError) as error:
        raise IndicatorReplayError("rsi14.csv values are invalid") from error
    if (
        dates.dt.strftime("%Y-%m-%d").tolist() != rsi["observation_date"].tolist()
        or dates.duplicated().any() or not dates.is_monotonic_increasing
        or dates.ge(pd.Timestamp(KOSPI200_FROZEN_HOLDOUT_V1.holdout_start)).any()
        or not observation.eq(available).all()
        or not available.lt(usable).all()
        or rsi["observation_time"].tolist() != [
            f"{value}T15:30:00+09:00" for value in rsi["observation_date"]
        ]
        or rsi["available_at"].tolist() != rsi["observation_time"].tolist()
        or any(
            _aware_timestamp_text(value, name="RSI usable_from").strftime("%H:%M:%S%z")
            != "09:00:00+0900"
            for value in rsi["usable_from"]
        )
        or any(
            _aware_timestamp_text(value, name="RSI usable_from").date().isoformat()
            >= KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
            for value in rsi["usable_from"]
        )
        or any(
            current != f"{following}T09:00:00+09:00"
            for current, following in zip(
                rsi["usable_from"].iloc[:-1],
                rsi["observation_date"].iloc[1:], strict=True,
            )
        )
        or rsi["usable_from"].iloc[-1]
        != f"{proxy['coverage_end']}T09:00:00+09:00"
        or not values.map(lambda value: isfinite(float(value)) and 0.0 <= value <= 100.0).all()
        or not rsi["ticker"].astype(str).eq("1028").all()
        or not rsi["date_semantics"].eq("KRX_TRADING_DATE_DAILY_FINAL").all()
        or not rsi["instrument_id"].eq("KRX:1028").all()
        or not rsi["source_dataset"].eq("kr_kospi200_index_daily").all()
        or not rsi["source_contract_version"].eq(1).all()
        or not rsi["feature_version"].eq(1).all()
        or not rsi["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all()
    ):
        raise IndicatorReplayError("rsi14.csv semantics differ")
    study = _exact_keys(study_payload, {"study"}, name="study.json")["study"]
    study = _exact_keys(
        study,
        {
            "contract_version", "status", "ticker", "date_semantics",
            "holdout_policy_id", "holdout_start", "winner_selected", "results",
        },
        name="study",
    )
    study_results = study.get("results")
    if (
        study.get("contract_version") != INDICATOR_STUDY_CONTRACT_VERSION
        or study.get("status") != INDICATOR_STUDY_STATUS
        or study.get("ticker") != "1028"
        or study.get("date_semantics") != "KRX_TRADING_DATE_DAILY_FINAL"
        or study.get("holdout_policy_id") != KOSPI200_FROZEN_HOLDOUT_V1.policy_id
        or study.get("holdout_start") != KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
        or study.get("winner_selected") is not False
        or not isinstance(study_results, list) or len(study_results) != 2
        or [row.get("candidate") for row in study_results if isinstance(row, dict)] != candidates
        or any(set(row) != {"candidate", "availability", "metrics"}
               for row in study_results if isinstance(row, dict))
        or any(not isinstance(row, dict) for row in study_results)
    ):
        raise IndicatorReplayError("study.json semantics differ")
    for row in study_results:
        _validate_study_metrics(row["metrics"], availability=row["availability"])
    comparison = _exact_keys(
        scenario_payload, {"comparison"}, name="scenario.json",
    )["comparison"]
    comparison = _exact_keys(
        comparison,
        {
            "availability", "contract_version", "status", "winner_selected",
            "entry_observation_date", "entry_usable_from", "strategy", "baseline",
            "metrics",
        },
        name="scenario comparison",
    )
    strategy = comparison.get("strategy")
    baseline = comparison.get("baseline")
    if (
        comparison.get("availability") != "EVALUATED"
        or comparison.get("contract_version") != MATCHED_HOLD_CONTRACT_VERSION
        or comparison.get("status") != MATCHED_HOLD_STATUS
        or comparison.get("winner_selected") is not False
        or not isinstance(strategy, dict) or not isinstance(baseline, dict)
    ):
        raise IndicatorReplayError("scenario.json semantics differ")
    entry_date = _canonical_date_text(
        comparison["entry_observation_date"], name="scenario entry observation",
    )
    entry_time = _aware_timestamp_text(
        comparison["entry_usable_from"], name="scenario entry usable",
    )
    if (
        entry_time.date().isoformat() <= entry_date
        or entry_time.strftime("%H:%M:%S%z") != "09:00:00+0900"
        or entry_time.date().isoformat() >= KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
    ):
        raise IndicatorReplayError("scenario entry timing differs")
    strategy = _exact_keys(
        strategy,
        {
            "contract_version", "status", "holdout_policy_id", "holdout_start",
            "policy", "decisions", "execution",
        },
        name="scenario strategy",
    )
    decisions = strategy["decisions"]
    if (
        strategy["contract_version"] != THRESHOLD_BAND_CONTRACT_VERSION
        or strategy["status"] != THRESHOLD_BAND_STATUS
        or strategy["holdout_policy_id"] != KOSPI200_FROZEN_HOLDOUT_V1.policy_id
        or strategy["holdout_start"] != KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
        or strategy["policy"] != policy
        or not isinstance(decisions, list) or not decisions
    ):
        raise IndicatorReplayError("scenario strategy semantics differ")
    decision_dates: list[str] = []
    for raw_decision in decisions:
        decision = _exact_keys(
            raw_decision, _dataclass_keys(ThresholdBandDecision),
            name="scenario decision",
        )
        observation_date = _canonical_date_text(
            decision["observation_date"], name="scenario decision observation",
        )
        usable_at = _aware_timestamp_text(
            decision["usable_from"], name="scenario decision usable",
        )
        indicator_value = _exact_float(
            decision["indicator_value"], name="scenario decision indicator",
        )
        target = decision["target_long"]
        expected_reason = (
            "ENTER_AT_OR_BELOW" if target is True else "EXIT_AT_OR_ABOVE"
        )
        if (
            usable_at.date().isoformat() <= observation_date
            or usable_at.strftime("%H:%M:%S%z") != "09:00:00+0900"
            or usable_at.date().isoformat() >= KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
            or type(target) is not bool
            or decision["reason"] != expected_reason
            or not 0.0 <= indicator_value <= 100.0
            or (target and indicator_value > policy["enter_at_or_below"])
            or (not target and indicator_value < policy["exit_at_or_above"])
        ):
            raise IndicatorReplayError("scenario decision semantics differ")
        decision_dates.append(observation_date)
    if (
        len(set(decision_dates)) != len(decision_dates)
        or decision_dates != sorted(decision_dates)
        or decisions[0]["target_long"] is not True
        or any(
            current["target_long"] is following["target_long"]
            for current, following in zip(decisions, decisions[1:])
        )
        or entry_date != decisions[0]["observation_date"]
        or comparison["entry_usable_from"] != decisions[0]["usable_from"]
    ):
        raise IndicatorReplayError("scenario decision order differs")
    execution = _validate_execution(strategy["execution"], name="scenario execution")
    baseline = _validate_execution(baseline, name="scenario baseline")
    execution_dates = [row["session_date"] for row in execution["ledger"]]
    execution_index = {value: index for index, value in enumerate(execution_dates)}
    rsi_rows = {
        row["observation_date"]: row
        for row in rsi.to_dict(orient="records")
    }
    for decision in decisions:
        index = execution_index.get(decision["observation_date"])
        feature_row = rsi_rows.get(decision["observation_date"])
        if (
            index is None or index == len(execution_dates) - 1
            or feature_row is None
            or decision["usable_from"]
            != f"{execution_dates[index + 1]}T09:00:00+09:00"
            or decision["usable_from"] != feature_row["usable_from"]
            or not _close(decision["indicator_value"], float(feature_row["rsi_14"]))
        ):
            raise IndicatorReplayError("scenario decision/feature alignment differs")
    executed_decisions = [
        (row["decision_session"], bool(row["target_position"]))
        for row in execution["ledger"] if row["decision_session"] is not None
    ]
    if (
        execution_dates != [row["session_date"] for row in baseline["ledger"]]
        or len(execution_dates) != proxy["observations"]
        or execution_dates[0] != proxy["coverage_start"]
        or execution_dates[-1] != proxy["coverage_end"]
        or entry_date not in execution_dates
        or executed_decisions != [
            (row["observation_date"], row["target_long"]) for row in decisions
        ]
    ):
        raise IndicatorReplayError("scenario execution alignment differs")
    matched = _exact_keys(
        comparison["metrics"], _dataclass_keys(MatchedHoldMetrics),
        name="scenario comparison metrics",
    )
    for name in _dataclass_keys(MatchedHoldMetrics):
        _exact_float(matched[name], name=f"scenario comparison {name}")
    strategy_metrics = execution["metrics"]
    baseline_metrics = baseline["metrics"]
    expected_matched = {
        "strategy_ending_nav": strategy_metrics["ending_nav"],
        "baseline_ending_nav": baseline_metrics["ending_nav"],
        "ending_nav_difference": strategy_metrics["ending_nav"] - baseline_metrics["ending_nav"],
        "strategy_total_return": strategy_metrics["total_return"],
        "baseline_total_return": baseline_metrics["total_return"],
        "total_return_difference": strategy_metrics["total_return"] - baseline_metrics["total_return"],
        "strategy_annualized_volatility": strategy_metrics["annualized_volatility"],
        "baseline_annualized_volatility": baseline_metrics["annualized_volatility"],
        "annualized_volatility_difference": strategy_metrics["annualized_volatility"] - baseline_metrics["annualized_volatility"],
        "strategy_max_drawdown": strategy_metrics["max_drawdown"],
        "baseline_max_drawdown": baseline_metrics["max_drawdown"],
        "strategy_total_turnover": strategy_metrics["total_turnover"],
        "baseline_total_turnover": baseline_metrics["total_turnover"],
        "strategy_transaction_cost": strategy_metrics["transaction_cost_paid"],
        "baseline_transaction_cost": baseline_metrics["transaction_cost_paid"],
        "incremental_transaction_cost": strategy_metrics["transaction_cost_paid"] - baseline_metrics["transaction_cost_paid"],
    }
    if any(not _close(matched[name], expected) for name, expected in expected_matched.items()):
        raise IndicatorReplayError("scenario comparison accounting differs")
    return bundle, result


def _verify_directory(
    path: Path, *, project_root: Path,
) -> tuple[tuple[IndicatorArtifactReceipt, ...], str]:
    if not path.is_dir() or path.is_symlink():
        raise IndicatorReplayError("indicator replay bundle is unavailable")
    entries = tuple(sorted(item.name for item in path.iterdir()))
    if entries != tuple(sorted(_OWNED_NAMES)) or any(
        not item.is_file() or item.is_symlink() for item in path.iterdir()
    ):
        raise IndicatorReplayError("indicator replay artifact set differs")
    bodies = {name: (path / name).read_bytes() for name in _OWNED_NAMES}
    bundle, result = _validate_semantic_payloads(bodies, project_root=project_root)
    receipts = tuple(
        _receipt(name, (path / name).read_bytes()) for name in _ARTIFACT_NAMES
    )
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {
            "schema", "frozen_input_digest", "code_digest", "thresholds",
            "holdout_policy_id", "artifact_set_sha256", "artifacts",
        }
        or bundle.get("schema") != INDICATOR_REPLAY_SCHEMA
        or bundle.get("frozen_input_digest") != EXPECTED_FROZEN_DIGEST
        or bundle.get("code_digest") != _code_digest(project_root)
        or bundle.get("thresholds") != {
            "enter_at_or_below": 30.0, "exit_at_or_above": 70.0,
        }
        or bundle.get("holdout_policy_id") != KOSPI200_FROZEN_HOLDOUT_V1.policy_id
        or bundle.get("artifacts") != [asdict(item) for item in receipts]
        or bundle.get("artifact_set_sha256") != _receipt_digest(receipts)
        or result.get("schema") != INDICATOR_REPLAY_SCHEMA
        or result.get("status") != INDICATOR_REPLAY_STATUS
        or result.get("frozen_input_digest") != EXPECTED_FROZEN_DIGEST
        or result.get("code_digest") != bundle.get("code_digest")
        or result.get("holdout") != asdict(KOSPI200_FROZEN_HOLDOUT_V1)
        or result.get("winner_selected") is not False
        or result.get("recommendation_made") is not False
    ):
        raise IndicatorReplayError("indicator replay content binding differs")
    all_receipts = tuple(
        _receipt(name, (path / name).read_bytes()) for name in sorted(_OWNED_NAMES)
    )
    return all_receipts, _receipt_digest(all_receipts)


def _remove_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _publish(
    project_root: Path,
    output_root: Path,
    bodies: Mapping[str, bytes],
    *,
    promotion_hook: Callable[[str], None] | None = None,
) -> IndicatorReplayReceipt:
    transaction = project_root / ".tmp/agents/root/indicator_scenario_replay_transaction"
    stage = transaction / "stage"
    backup = transaction / "backup"
    marker = transaction / "journal.json"
    if transaction.exists():
        if not transaction.is_dir() or transaction.is_symlink():
            raise IndicatorReplayError("indicator replay transaction path is invalid")
        # Journal truth restores the exact pre-transaction state before a new
        # attempt. A valid interrupted live directory never supersedes prior.
        if marker.is_file() and not marker.is_symlink():
            journal = _strict_json(marker.read_bytes(), name="transaction journal")
            _exact_keys(
                journal,
                {"schema", "had_live", "output_root", "prior_digest", "expected_digest"},
                name="transaction journal",
            )
            if (
                journal.get("schema") != INDICATOR_REPLAY_SCHEMA
                or type(journal.get("had_live")) is not bool
                or journal.get("output_root") != str(output_root)
                or not isinstance(journal.get("expected_digest"), str)
            ):
                raise IndicatorReplayError("indicator replay transaction journal differs")
            if journal["had_live"]:
                if not isinstance(journal.get("prior_digest"), str):
                    raise IndicatorReplayError("indicator replay prior digest is unavailable")
                if backup.exists():
                    _, backup_digest = _verify_directory(
                        backup, project_root=project_root,
                    )
                    if backup_digest != journal["prior_digest"]:
                        raise IndicatorReplayError("indicator replay backup identity differs")
                    if output_root.exists():
                        if not output_root.is_dir() or output_root.is_symlink():
                            raise IndicatorReplayError("interrupted live path is invalid")
                        _remove_directory(output_root)
                    output_root.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, output_root)
                elif output_root.exists():
                    _, live_digest = _verify_directory(
                        output_root, project_root=project_root,
                    )
                    if live_digest != journal["prior_digest"]:
                        raise IndicatorReplayError(
                            "interrupted indicator replay has no exact prior recovery"
                        )
                else:
                    raise IndicatorReplayError(
                        "interrupted indicator replay lost its prior bundle"
                    )
            else:
                if journal.get("prior_digest") is not None or backup.exists():
                    raise IndicatorReplayError("prior-absence journal has an unexpected backup")
                if output_root.exists():
                    if not output_root.is_dir() or output_root.is_symlink():
                        raise IndicatorReplayError("interrupted live path is invalid")
                    _remove_directory(output_root)
        elif marker.exists():
            raise IndicatorReplayError("indicator replay transaction journal is invalid")
        else:
            # Before journal creation, output mutation has not begun.
            if backup.exists():
                raise IndicatorReplayError("unjournaled indicator replay backup exists")
            if output_root.exists():
                _verify_directory(output_root, project_root=project_root)
        _remove_directory(stage)
        if backup.exists():
            raise IndicatorReplayError("indicator replay backup recovery is incomplete")
        if marker.exists():
            marker.unlink()
        try:
            transaction.rmdir()
        except OSError:
            raise IndicatorReplayError("indicator replay transaction is not clean") from None
    transaction.mkdir(parents=True)
    stage.mkdir()
    had_live = output_root.exists()
    prior_digest: str | None = None
    if had_live:
        _, prior_digest = _verify_directory(output_root, project_root=project_root)
    try:
        for name in sorted(_OWNED_NAMES):
            (stage / name).write_bytes(bodies[name])
        expected_records, expected_digest = _verify_directory(
            stage, project_root=project_root,
        )
        marker.write_bytes(_json_bytes({
            "schema": INDICATOR_REPLAY_SCHEMA,
            "had_live": had_live,
            "output_root": str(output_root),
            "prior_digest": prior_digest,
            "expected_digest": expected_digest,
        }))
        if promotion_hook:
            promotion_hook("after_stage_readback")
        output_root.parent.mkdir(parents=True, exist_ok=True)
        if had_live:
            os.replace(output_root, backup)
        if promotion_hook:
            promotion_hook("after_live_backup")
        os.replace(stage, output_root)
        if promotion_hook:
            promotion_hook("after_live_publish")
        records, observed_digest = _verify_directory(
            output_root, project_root=project_root,
        )
        if observed_digest != expected_digest or records != expected_records:
            raise IndicatorReplayError("promoted indicator replay differs")
        _remove_directory(backup)
        marker.unlink()
        transaction.rmdir()
        return IndicatorReplayReceipt(
            INDICATOR_REPLAY_SCHEMA, "READY", output_root,
            EXPECTED_FROZEN_DIGEST, observed_digest, records,
        )
    except Exception:
        if output_root.exists() and backup.exists():
            _remove_directory(output_root)
        if backup.exists() and not output_root.exists():
            os.replace(backup, output_root)
        if not had_live and output_root.exists():
            _remove_directory(output_root)
        _remove_directory(stage)
        if marker.exists():
            marker.unlink()
        try:
            transaction.rmdir()
        except OSError:
            pass
        raise


def run_indicator_scenario_replay(
    request: IndicatorReplayRequest,
    *,
    _promotion_hook: Callable[[str], None] | None = None,
) -> IndicatorReplayReceipt:
    if type(request) is not IndicatorReplayRequest:
        raise TypeError("request must be an exact IndicatorReplayRequest")
    project_root = _exact_project_root(request.project_root)
    output_root = (
        Path(request.output_root).resolve()
        if request.output_root is not None
        else project_root / DEFAULT_OUTPUT_RELATIVE
    )
    if output_root == Path(output_root.anchor) or project_root.is_relative_to(output_root):
        raise IndicatorReplayError("indicator replay output scope is invalid")
    try:
        output_root.relative_to(project_root)
    except ValueError:
        # Isolated integration roots are permitted only beneath the OS temp
        # directory already selected by the caller/test harness.
        if request.output_root is None:
            raise IndicatorReplayError("indicator replay output must stay project-local") from None
    bodies = _build_artifacts(project_root)
    return _publish(
        project_root, output_root, bodies, promotion_hook=_promotion_hook,
    )


__all__ = [
    "DEFAULT_OUTPUT_RELATIVE", "EXPECTED_FROZEN_DIGEST",
    "INDICATOR_REPLAY_SCHEMA", "INDICATOR_REPLAY_STATUS",
    "IndicatorArtifactReceipt", "IndicatorReplayError",
    "IndicatorReplayReceipt", "IndicatorReplayRequest",
    "run_indicator_scenario_replay",
]
