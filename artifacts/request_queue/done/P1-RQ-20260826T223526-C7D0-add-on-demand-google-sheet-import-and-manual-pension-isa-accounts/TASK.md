# Add on-demand Google Sheet import and manual pension/ISA accounts

## Problem
The Account tab cannot ingest the user-authorized Google Sheet on demand and offers no manual account editor for API-less pension/ISA accounts.

## Evidence
The intended Drive spreadsheet/tab and bounded headings are verified read-only, but repository search finds no Sheets/import runtime; the expected family snapshot file is absent and 33A4 intentionally implemented only a fixed synthetic parser.

## Scope
allow:
- Implement provider-free on-demand export import, strict local manual-account registry and AccountPage management UI using atomic local persistence.

deny:
- No daily scheduler, Google Sheet write, embedded connector credential, spreadsheet/account identifier in snapshots or logs, provider/account API call, silent current-price inference, FX merge, order, transfer, or production user-data deletion in tests.

## Done When
Account page exposes explicit on-demand import from an exported 아빠-tab file and add/edit/remove controls for independent API-less manual pension/ISA accounts; strict parsing preserves exact source date/currency/quantity/cost/null/zero semantics, atomic local-user storage retains last valid data, refresh shows independent identifier-free sources, privacy masks values/holdings, and no scheduler/provider/order/transfer path is added.

## Verify
Use synthetic CSV and registry fixtures only; cover exact 아빠 ISA/종합 mapping, malformed/private fields, atomic rollback, multiple manual accounts, explicit deletion, selected-source preservation, privacy, 1600x900 fit, and clean close. Run owning account service/widget suites and secret/private identifier scan.
