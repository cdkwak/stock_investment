from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardSplit:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    purge: int
    embargo: int


def expanding_walk_forward(*, observations: int, minimum_train: int, test_size: int,
                           purge: int, embargo: int) -> tuple[WalkForwardSplit, ...]:
    if min(observations, minimum_train, test_size) < 1 or min(purge, embargo) < 0:
        raise ValueError("invalid walk-forward dimensions")
    splits = []
    test_start = minimum_train + purge
    while test_start < observations:
        test_end = min(test_start + test_size, observations)
        train_end = test_start - purge
        splits.append(WalkForwardSplit(0, train_end, test_start, test_end, purge, embargo))
        test_start = test_end + embargo
    if not splits:
        raise ValueError("not enough observations for one walk-forward split")
    return tuple(splits)
