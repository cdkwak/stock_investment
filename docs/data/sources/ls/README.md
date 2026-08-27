# LS OpenAPI source notes

## Official reference

- [LS Securities OpenAPI portal](https://openapi.ls-sec.co.kr/)
- [LS derivatives-investor API guide](https://openapi.ls-sec.co.kr/apiservice?group_id=2f1eea77-5606-4512-93c6-31b21d2ece90&api_id=47005ce6-8500-4a3d-ad6c-f96ec3251669)

Runtime credentials use the checked-in collector's `LS_*` environment-variable
contract. Do not copy secrets or tokens into examples, docs, command history,
Landing metadata, or errors.

## Retained scopes

| Endpoint/family | Retained role | Current use boundary |
|---|---|---|
| `t8462` | KOSPI200-family derivatives investor-flow Raw | LS-native analysis features only; not an official KRX replacement |
| `t1633` | Program-trading Raw candidate | Source finality and normalized promotion remain gated |
| `t8428` | Retained market/derivatives candidate | Use only through its existing contract and documented evidence |

For `t8462`, preserve product identity and the `D` regular, `N` night, and `U`
all-session scopes. Do not merge sessions or product families.

## Semantic rules

- `individual` (`sv_08`) and `foreign` (`sv_17`) may be used unchanged only as
  LS-native signed net-contract features.
- `institution` (`sv_18`) and `other_corp` (`sv_07`) retain
  `LS_NATIVE_CATEGORY`; do not claim definition-level equality with KRX labels.
- A documented `institutional_complex` may be calculated only from the two
  unchanged fields in the same LS row and scope.
- LS Raw never backfills official KRX futures-investor rows, short-selling
  aggregates, PCR, basis, or option walls.
- Historical Raw remains non-predictive where finality, revision, release, or
  PIT evidence is unresolved.

## Runtime route

- Analysis policy: [LS t8462 Analysis Features](../../config/LS_T8462_ANALYSIS_FEATURES.md)
- Raw operation: [LS t8462 Daily Raw Collection](../../operations/LS_T8462_DAILY_RAW_COLLECTION.md)
- Dataset registry: `src/stock_data/orchestration/dataset_universe.py`
- Dashboard routing: [Dashboard Daily Source Routing](../../../gui/DASHBOARD_DAILY_SOURCE_ROUTING.md)

Credentials and tokens are runtime-only. Never inspect `.env`, print auth
headers, or persist full account/authentication payloads. Brokerage behavior is
read-only unless the user separately authorizes a specific trading operation.

## Safe read example

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_ls_t8462_daily_raw.py --help
```

Use the collector's exact base URL, TR code, input block, session scope, timeout,
and response validator. Do not guess another LS endpoint by changing the TR code.
No order/correction/cancellation example belongs in this folder.
