# Korean issuer fundamentals source options

Status: `NO_COMPLIANT_NORMALIZED_SOURCE / NO_LIVE_COMMAND`

Review date: 2026-09-03 KST. This review used checked-in documentation, code,
contracts, retained Landing directory names, and credential-name presence only.
It made no network call and did not read or print any credential value.

## Decision

No source currently registered in this repository can lawfully and
semantically supply scanner-ready quarterly debt, operating-income, net-income,
and revenue facts. Do not create a Normalized dataset, Dataset Universe row,
collector, or scanner values until one source passes the rights and statement
semantics gates below.

OpenDART is the best candidate and its credential **name**
(`OPENDART_API_KEY`) exists in the local `.env`. The provider is already
approved for bounded disclosure and Raw research, but the active
[financial-statement pilot](../operations/OPENDART_FINANCIAL_STATEMENT_PILOT.md)
explicitly records `RIGHTS_AND_REDISTRIBUTION_UNVERIFIED`, unknown publication
and revision timing, null `usable_from`, and a prohibition on Normalized/GUI
promotion. That higher-priority gate prevents using the configured key as proof
of a licensed scanner feed.

## Retained investigation

| Source | Local approval and credential state | Relevant fields actually documented here | Rate limit recorded here | Licence/terms evidence recorded here | Result |
|---|---|---|---|---|---|
| OpenDART `fnlttSinglAcnt.json` | Bounded Raw pilot approved; `OPENDART_API_KEY` name present | `rcept_no`, `reprt_code`, `bsns_year`, `corp_code`, statement/account IDs and names, current/prior raw amounts, currency, order, `fs_div`; sufficient raw ingredients in principle, but no accepted quarterly metric mapping | No provider quota is recorded. The frozen project pilot is limited to one GET, 10-second timeout, retry 0. | Official API guide is linked in [OpenDART source notes](opendart/README.md), but retention/redistribution terms are not accepted in-repo. | `RAW_RESEARCH_ONLY`; no retained financial-statement Landing response was found |
| KRX/pykrx `get_market_fundamental*` / `MDCSTAT03501` | Existing KRX routes and KRX-related key name present | Close, EPS, PER, BPS, PBR, DPS, dividend yield only; no liabilities, equity, revenue, operating income, or net income | No safe quota is recorded for this route | KRX terms are linked in [KRX notes](krx/README.md); pykrx is an adapter, not a licence or stable official contract | Not functionally sufficient |
| data.go.kr checked-in `1160100` routes | Active approved routes; `DATA_GO_KR_SERVICE_KEY` name present | Prices/universe, derivatives, lending, market liquidity/credit, dividends, and rights; no issuer quarterly statement route is registered | Endpoint-specific; no issuer-statement quota exists because no such checked-in endpoint exists | Public/government inputs are accepted only per registered endpoint contract | Not functionally sufficient |
| KRX KIND | Official disclosure reference only; no machine endpoint, credential route, parser, or retained statement Landing | Potential filing documents, but no accepted structured field mapping | Not documented | No checked-in automated-use/retention terms decision | Candidate only; not registered |
| Naver / FnGuide | No approved financial-statement provider route | Screened financial figures may be visible, but no contract-stable fields are retained | Not documented | Scraping/redistribution rights are not accepted | Scraping is not acceptable |

Retained Landing roots include `data/landing/data_go_kr/` and
`data/landing/krx_open_api/`. Under OpenDART diagnostics, only a corporate-action
free-issue pilot was found; there is no retained
`opendart_financial_statement_pilot` response to normalize offline.

## Registration and evidence needed

### Recommended: OpenDART

1. Register for the official free OpenDART API and issue a key. This machine
   already has the expected key name, so no key value should be sent through
   chat, logs, documentation, or a command line.
2. The user or licence owner must retain an authoritative terms decision that
   permits the intended local storage and scanner display/derived use. Public
   access and a free key alone are not proof of redistribution rights.
3. Record the provider's current daily/request quota and choose a bounded
   per-run issuer/report budget below it.
4. Establish issuer identity (`symbol` to `corp_code`) and report availability,
   correction/revision lineage, receipt timestamp, consolidation preference,
   fiscal-year/calendar-quarter handling, cumulative-to-discrete-quarter
   conversion, and account-ID fallback rules.
5. Only then add the Landing-first collector, Normalized contract, tests, and a
   Dataset Universe row with `automation_enabled=False` for the first manual run.

### Alternative: KRX KIND

Register for an official machine-readable service only if KRX offers the exact
structured statement fields and grants the required storage/use rights. Retain
the service identifier, quota, terms, schema, filing timestamp, and correction
rules before writing code. Manual HTML/PDF scraping is not a substitute.

### Rejected: Naver / FnGuide scraping

No registration step makes an undocumented scraper acceptable here. A
licensed, documented API product would require a new source review and explicit
contract; do not reuse website fields or session cookies.

## Intended Normalized boundary after the gate

A future dataset should retain issuer/report identity, consolidated/separate
scope, fiscal period, filing receipt and availability timestamp, revision
lineage, currency/unit, standardized account identity, and source amounts. The
scanner-facing `debt_ratio`, four discrete-quarter profit booleans, and revenue
trend must be Derived values with their calculation version and
`fundamentals_as_of`; they must not be guessed in the collector.

## First live run

There is **no bounded live command yet**. Running the existing Raw pilot would
not satisfy the scanner licence or Normalized-contract gate, and no compliant
collector was created. After the evidence above is accepted, implementation
must add a real `scripts/manual/collect/` entry point whose first command pins an
explicit issuer set, business year/report code, maximum request count, timeout,
retry count, and manual-only mode. Until then, any purported command would be an
invented and unsafe interface.
