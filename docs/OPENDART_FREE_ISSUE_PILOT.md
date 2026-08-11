# B007 OpenDART free-issue source-observation readiness

## Decision

Status: **IMPLEMENTATION_READY_FOR_BOUNDED_PILOT**, not DATA_COMPLETE.

The repository now has an unexecuted, Landing-only manual pilot and an
offline-tested parser for the documented OpenDART `fricDecsn` and
`pifricDecsn` response fields. No OpenDART, KRX, pykrx, or other data endpoint
was called. No canonical corporate-action event, adjustment factor, Normalized
dataset, or Published dataset is created.

Official documentation:

- [`fricDecsn` free-capital-increase decision](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020024)
- [`pifricDecsn` combined paid/free-capital-increase decision](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020025)
- [OpenDART disclosure search and revision indicators](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)

## Source-observation contract

The contract is append-only and response-occurrence based:

| Property | Rule |
|---|---|
| Candidate family | `kr_opendart_free_issue_source_observation` |
| Layer | source observation staged from immutable Landing; no economic-event canonicalization |
| Immutable key | `(source_operation, landing_response_body_sha256, source_item_ordinal)` |
| Filing identity | source `rcept_no` (14 digits); not a canonical event key |
| Issuer identity | source `corp_code` (8 digits), source `corp_cls`, and source name; `stock_code` only from disclosure-list observations |
| Revision identity | retain every `rcept_no`, `report_nm`, and `rm`; no inferred `supersedes_rcept_no` |
| Valid empty | source status `013`, preserved as a terminal response with zero rows |
| Source failure | any status other than `000`/`013`, non-JSON, missing documented fields, non-200, or pagination beyond the bounded first page |
| Numeric representation | preserve source strings, including decimal precision, `"0"`, empty string, and null; no float coercion in the observation layer |

The response body hash identifies the exact Landing response. A repeated
`rcept_no` in a later captured response is a new observation, not an overwrite.
Identical receipt numbers across `fricDecsn` and `pifricDecsn` remain
operation-specific source occurrences until source behavior is validated.

## Documented free-issue terms

`fricDecsn` documents:

- ordinary/other new-share counts;
- ordinary/other pre-issue issued-share counts;
- par value per share in won;
- ordinary/other new shares allocated per existing share, with up to 20
  decimal places described by the guide;
- allocation record date, dividend-accrual date, expected certificate-delivery
  date, expected listing date, and board decision date;
- director/auditor attendance fields.

`pifricDecsn` contains separately prefixed paid-issue and free-issue terms. The
pilot parser preserves all documented fields, but this task uses only the
`fric_*` group as free-issue evidence. Paid-issue fields are retained losslessly
and are not silently projected into a Rights contract.

Neither guide documents a free-issue subscription/issue **price** because this
family is a free issue. `fv_ps`/`fric_fv_ps` is par value, not an offer price.
No price or cash consideration is invented. Dates are source event/decision
dates, not filing observation times.

## Revision, withdrawal and PIT rules

- The disclosure-list request fixes `last_reprt_at=N`; OpenDART documents that
  this includes all submitted reports, including amendments. `Y` is prohibited
  because it discards history.
- Each list row retains `report_nm`, `rm`, `rcept_no`, `rcept_dt`, and filer.
  `rm` may say that a later correction exists or that the report is withdrawn,
  but the API does not document an explicit revision-parent receipt.
- The system must not pair original/correction records by issuer, date, name,
  ratio, or proximity alone. A canonical supersession chain remains nullable
  until an explicit, validated filing/document relationship exists.
- `rcept_dt` is a date, not a public timestamp. Local `captured_at` is a capture
  time, not proof of the exchange's intraday publication time.
- For predictive daily use, the earliest defensible default is T+1 after
  `rcept_dt`, additionally constrained by actual local capture. Event record,
  board, dividend-accrual, delivery and listing dates must not be substituted
  for knowledge time.
- Withdrawn and corrected observations remain immutable. A later canonical
  layer may assign status, but must retain the full observation lineage.

## Exact future pilot matrix

The manual runner accepts one preselected 8-digit `corp_code` and a maximum
32-calendar-day inclusive window beginning no earlier than 2015-01-01.
Before execution, D must select an issuer/window known from the official DART
portal to contain an original/correction pair if revision behavior is the
pilot objective.

| Sequence | Endpoint | Fixed parameters | Purpose |
|---:|---|---|---|
| 1 | `list.json` | issuer/window, `last_reprt_at=N`, `pblntf_ty=B`, ascending date, page 1, count 100 | capture filing identities and correction/withdrawal indicators |
| 2 | `fricDecsn.json` | same issuer/window | capture standalone free-issue terms |
| 3 | `pifricDecsn.json` | same issuer/window | capture combined paid/free filing terms or valid empty |

Hard limits: **3 business requests, 3 raw HTTP requests, parallelism 1, retries
0**. If the disclosure list reports more than one page, the pilot stops; it
does not paginate. A non-200, credential echo, non-JSON body, source error,
schema mismatch or count mismatch stops the run. There is no automatic resume
of a stopped run because blindly repeating a completed request would violate
exact call accounting. D must reconcile Landing, ledger and checkpoint, then
approve a new or explicitly repaired plan.

## Credential and artifact rules

- Credential: environment variable `OPENDART_API_KEY` only; exactly 40
  characters. Never accept it through command-line arguments or commit it.
- The key is present only in the request parameter passed to `requests`; URLs
  recorded in the manifest/ledger have no query string.
- The response is checked for a credential echo before Landing write.
- Landing bytes are atomically created and never overwritten.
- Ledger JSONL is append-only and fsynced. Checkpoint and manifest are atomic.
- Every successful HTTP response is landed before it is parsed.
- The final credential scan covers every run artifact.

The runner refuses execution unless `--confirm-live-manual-pilot` is supplied.
The required future invocation shape is:

```text
python scripts/manual/pilot_opendart_free_issue.py \
  --corp-code 00000000 --begin-date YYYYMMDD --end-date YYYYMMDD \
  --confirm-live-manual-pilot
```

The placeholder values are deliberate. Do not execute them or select a company
by guesswork. Execution requires D's approved issuer/window and call budget.

## Offline fixtures and validation

Synthetic fixtures contain only documented field names. They do not claim to
be source values or coverage evidence. Tests prove:

- fixed three-call, keyless public request matrix;
- scope/date/call bounds;
- immutable body-hash and item-ordinal identity;
- preservation of source zero, null, empty string and decimal text;
- retention of original/correction receipts without fabricated supersession;
- `013` valid-empty handling and fail-closed source errors;
- documented-field and single-page count validation.

## Gates before any dataset build

1. Obtain and configure the OpenDART key outside Git.
2. D approves the exact issuer/window and 3-call manual pilot.
3. Independently reconcile all Landing hashes, three ledger calls and the
   checkpoint; validate actual nullability and unexpected fields.
4. Verify at least one original/correction or withdrawal sequence and determine
   whether an explicit parent receipt can be extracted from an official source.
5. Define an Arrow schema only from captured responses; do not infer numeric
   nullability from the guide's display examples.
6. Validate security-class mapping. `corp_code` and current `stock_code` do not
   alone prove historical ordinary/other-share identity.
7. Only then implement the source-observation dataset. Adjustment factors and
   canonical events require a separate reviewed methodology.


