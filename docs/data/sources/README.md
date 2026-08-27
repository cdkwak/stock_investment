# Data source guide

This directory is the fast source-orientation layer for agents. It documents
provider identity, accepted uses, semantic limits, and the next authoritative
file to read. It is not a second status registry and must not be used to relax a
gate in [Data Status](../DATA_STATUS.md).

## Read order

1. `AGENTS.md`
2. [Project Status](../../project/PROJECT_STATUS.md)
3. [Data Status](../DATA_STATUS.md)
4. This index and the relevant provider README
5. [Endpoint Catalog](ENDPOINT_CATALOG.md) and [Agent Runbook](AGENT_RUNBOOK.md)
6. The linked contract and operation document only

Do not scan archived evidence unless an active document explicitly routes to a
specific file. Never inspect `.env`, log credentials, or infer an undocumented
endpoint, field, unit, publication time, or success code.

## Fast task routing

- API 주소·인증·기존 client 찾기: [Endpoint Catalog](ENDPOINT_CATALOG.md)
- Dashboard 지표가 어느 source를 써야 하는지 확인: [Dashboard Source Map](DASHBOARD_SOURCE_MAP.md)
- 새 source 조사·pilot·promotion 순서: [Datasource Agent Runbook](AGENT_RUNBOOK.md)
- 새 공급자 폴더 만들기: [Source Template](SOURCE_TEMPLATE.md)

## Provider routing

`ACTIVE` means at least one approved project route exists, not that every API
offered by that vendor is approved. Open the provider folder before browsing.

| Provider | Status | Fast reference | Important boundary |
|---|---|---|---|
| KRX / pykrx | ACTIVE / RETAINED | [KRX](krx/README.md) | Direct KRX, pykrx, manual files, and data.go.kr remain distinct identities |
| KB Securities | ACTIVE snapshot | [KB](kb/README.md) | Snapshot is not canonical history; brokerage use stays read-only |
| LS OpenAPI | RETAINED / PILOT | [LS](ls/README.md) | LS-native categories/sessions are not official KRX aggregates |
| Yahoo | ACTIVE empirical | [Yahoo](yahoo/README.md) | No stable official API contract; continuous futures are descriptive prices |
| FRED / ALFRED | ACTIVE / PILOT | [FRED](fred/README.md) | Current values do not establish original historical vintages |
| Toss Securities | ACTIVE selected paths | [Toss](toss/README.md) | Only the checked-in read-only allowlist is approved |
| data.go.kr | ACTIVE / PILOT | [data.go.kr](data_go_kr/README.md) | Each service has its own schema, date, pagination, and publication rule |
| BOK ECOS | PILOT | [BOK ECOS](bok_ecos/README.md) | Series frequency, unit, revision, and publication stay attached |
| OpenDART | PILOT | [OpenDART](opendart/README.md) | Current disclosure presence is not complete PIT history |
| CFTC | RETAINED RAW | [CFTC](cftc/README.md) | Report families/market codes stay separate; release evidence is required |
| FINRA | PILOT / Landing-only | [FINRA](finra/README.md) | Short-sale volume and short interest are different datasets |
| Cboe | CANDIDATE / BLOCKED | [Cboe](cboe/README.md) | No runtime endpoint until product rights and license are verified |
| ORATS | CONTRACT_ONLY / SUBSCRIPTION_REQUIRED | [ORATS](orats/README.md) | Strict offline contracts, parser, and ratio projection exist; no runtime registry, entitlement, transport, or data |
| FinanceData Marcap | RETAINED file adapter | [Marcap](financedata_marcap/README.md) | Third-party annual files are not direct official KRX API evidence |

## Shared source rules

- Landing is immutable evidence; Normalized, Published, and Derived outputs
  require their own contracts and validation.
- A current snapshot never fills a daily-history gap.
- Values from different providers are not averaged, spliced, forward-filled,
  rescaled, or substituted without an explicit Published contract.
- `RAW_BACKFILL_COMPLETE` does not mean Dashboard-ready, automated, or PIT-safe.
- Dashboard numbers must carry provider, source date, freshness, and PIT meaning.
- Brokerage integrations are read-only by default. Orders, transfers, and
  withdrawals require separate explicit approval.

## Adding another source

Copy [SOURCE_TEMPLATE](SOURCE_TEMPLATE.md). A candidate folder may document
official discovery links and an entry gate, but it must not contain a guessed
endpoint or imply runtime approval. Keep downloadable vendor manuals under
`<provider>/official/` only when a specific task needs a local reference; do
not bulk-download entire documentation archives.

## Where current truth lives

- Coverage, latest dates, and active gates: [Dataset Index](../DATASET_INDEX.md)
- Selected refresh paths: [Data Operations](../operations/)
- Dashboard variable ownership: [Dashboard Daily Source Routing](../../gui/DASHBOARD_DAILY_SOURCE_ROUTING.md)
- Dataset registry and runtime paths: `src/stock_data/orchestration/dataset_universe.py`
