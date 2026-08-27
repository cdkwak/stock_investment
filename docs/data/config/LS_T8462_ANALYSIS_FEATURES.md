# LS t8462 analysis feature policy

Status: `ACTIVE_FEATURE_DEFINITION_WITH_PIT_LIMITS`

This policy defines analysis-time use of retained LS `t8462` values. It does
not create a Raw field, Dataset Contract, Normalized dataset, Canonical dataset,
or permission to rewrite retained artifacts.

## Source fields

All provider values remain immutable and independently available:

| Analysis label | LS source field | Policy |
|---|---|---|
| `individual` | `sv_08` | Use unchanged as an LS-native signed net-contract value. |
| `foreign` | `sv_17` | Use unchanged as an LS-native signed net-contract value. |
| `institution` | `sv_18` | Preserve unchanged and label `LS_NATIVE_CATEGORY`. |
| `other_corp` | `sv_07` | Preserve unchanged and label `LS_NATIVE_CATEGORY`. |

Do not repair, redistribute, overwrite, or replace `institution` or
`other_corp` from another provider.

## Derived feature

`institutional_complex` is a non-persisted, analysis-layer feature:

```text
institutional_complex = institution + other_corp
                      = sv_18 + sv_07
```

- Unit: signed net contracts.
- Inputs: two unchanged fields from the same LS row and scope.
- Null rule: if either input is missing, the feature is missing; do not coerce
  missing input to zero.
- Layer: analysis/derived only. It must not be written back into Landing Raw or
  represented as a provider-supplied field.
- Provenance: retain the LS source, market date, product, and raw `tm_rng` code
  with any materialized analysis output.

## Validated scope and use

The category-boundary evidence covers KOSPI200 futures on three trading dates
for each of `D`, `N`, and `U` (nine scopes). In that scope:

- LS and KRX `institution + other_corp` match exactly in 9/9 scopes;
- the individual category differences are equal and opposite in 9/9 scopes;
- `U` also matches each individual category on all three dates;
- the observed `D/N` differences are classified as a provider category
  allocation boundary.

Accordingly:

- `individual` and `foreign` may be used unchanged as LS-native features;
- `institution` and `other_corp` remain available as separate
  `LS_NATIVE_CATEGORY` features;
- separate-category analysis is allowed for validated KOSPI200-futures `U`
  scope, while retaining provider-native labels;
- prefer `institutional_complex` for KOSPI200-futures `D/N` cross-provider
  comparisons and as the default institution-side backtest feature;
- never claim that the LS-native `institution` or `other_corp` category is
  definitionally identical to the corresponding KRX category.

The same combined-category policy is supported for mini-KOSPI200 futures on the
same three dates: `institutional_complex` matches KRX for all nine `D/N/U`
scopes, while individual institution/other allocations differ in four `D/N`
scopes. KOSPI200 CALL individual categories match in all nine tested scopes.

Historical KOSPI200 Option `U` rows with a documented institution aggregate
versus detail mismatch are excluded from a default `institutional_complex`
feature. KRX matches the retained LS component sum rather than LS `sv_18` on
the tested mismatch rows. Preserve every field and surface
`AGGREGATE_FIELD_SEMANTICS`; do not silently substitute the component sum.

KOSPI200 PUT and mini-KOSPI200 CALL/PUT now also have three-date `D/N/U`
validation. All 135 category/combined comparisons are contract-exact, including
27/27 `institutional_complex` scopes and 45/45 `U=D+N` checks. The retained
six-product matrix may use the same source-native and derived-feature rules;
untested dates and products outside that matrix are not generalized.

Historical backfill rows are `RESEARCH_ONLY_NON_PREDICTIVE` because their
historical publication times were not retained. They may be used for
descriptive research and feature-behavior analysis, but not as-of historical
predictive backtest inputs. A future daily row becomes eligible no earlier than
its actual accepted `captured_at`; conditional D+1 use also requires capture
after the full regular/night/ALL cycle. Same-day use and `PIT_SAFE` claims are
forbidden.

An in-memory or otherwise non-persisted analysis projection using these labels
and flags is allowed for descriptive research. A persisted Normalized research
dataset is not allowed until finality/revision and prospective capture policy
are operationally accepted; Canonical production use remains forbidden.

Evidence:
[KRX multi-product validation](../../archive/data/evidence/2026-08-data-phase/ls/LS_T8462_KRX_MULTI_PRODUCT_VALIDATION_20260817.md).
