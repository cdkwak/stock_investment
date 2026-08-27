# Phase 1 Validation Policy

Status: `ACTIVE_FOR_OFFLINE_FOUNDATION`

Validation fails closed when the frozen digest, contract identity, ordered date
key, source semantics, finite positive close, PIT metadata, or feature version
differs. No current universe, forward fill, provider substitution, or source
refresh is allowed.

Required tests:

1. deterministic feature bytes/values for the same input;
2. T feature `usable_from` equals the next retained trading date;
3. incomplete lookbacks are absent, not imputed;
4. future source edits do not alter earlier feature rows;
5. labels remain outside feature columns and appear only after their horizon;
6. purge and embargo leave no train/test overlap;
7. result serialization and replay are deterministic;
8. any non-finite, duplicate, unsorted, wrong-identity, or wrong-semantic input
   fails before evaluation.
9. the frozen manifest must match dataset identity, coverage, row/file/byte
   counts, and the sorted path-plus-file-byte SHA-256 digest exactly; a one-file
   mutation fails replay;
10. label namespace columns are rejected at Feature and Signal boundaries;
11. the final-five-calendar-year holdout is derived from coverage dates only,
    and its labels, metrics, crisis outcomes, and baseline rankings remain
    uninspected during development replay.
12. experiment identity binds SHA-256 digests for the owned code tree, canonical
    threshold values, exact persisted signal bytes, and exact persisted result
    bytes; purge must cover the declared maximum label horizon and persisted
    signals must retain exact `PIT_SAFE_EOD_T_PLUS_1` status.
13. an ML runner must slice the source before the holdout boundary before it
    constructs features or labels; holdout predictions and metrics are forbidden;
14. every ML fold must prove that all training labels are available before the
    earliest test decision, with purge at least the maximum label horizon;
15. model preprocessing must fit on each training fold only and may not use a
    global fitted scaler, imputer, selector, or target statistic;
16. every trial must bind its exact parameters, input/feature/label/split/code
    identity, development-only metrics, and explicit `holdout_results_reviewed=false`;
17. interrupted time-boxed studies must resume from a local durable trial store
    without provider access or repeated completed trials;
18. a best development trial remains a candidate only. It may feed only a new,
    explicitly versioned and clearly labelled development signal or portfolio
    simulation after its owning technical validation; it must not become a
    recommendation, production model, or live-performance claim. The existing
    sealed holdout remains unopened unless the user explicitly opens it, but no
    separate phase approval is required for development-only work.

Historical crisis windows are diagnostic slices only. Their dates or outcomes
must not choose thresholds, features, purge, embargo, or the final holdout.
