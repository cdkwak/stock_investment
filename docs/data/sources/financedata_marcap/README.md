# FinanceData Marcap

## Status

- Project status: `RETAINED FILE ADAPTER`.
- Source class: third-party, year-partitioned market-cap dataset; not an official KRX API.
- Accepted use is limited to the checked-in normalizer and its explicit provenance.

## Upstream reference

- [FinanceData Marcap repository](https://github.com/FinanceData/marcap)

The upstream repository publishes year-partitioned data derived from KRX market
capitalization information. Repository availability or daily updates do not
make the files official KRX evidence or PIT-safe history.

## Authentication and acquisition

No project API credential is defined. This guide intentionally does not run
`git clone` or `git pull`: external files must enter through a named acquisition
operation with source URL, upstream revision/hash, capture time, file hash, and
license review recorded.

## Safe local example

Normalize one already reviewed annual source file; never scan an arbitrary
download directory or mutate the input:

```python
from pathlib import Path
import pandas as pd
from stock_data.providers.financedata_marcap import normalize_annual

source = Path("<reviewed-annual-file>")
raw = pd.read_parquet(source)
result = normalize_annual(raw, source)
```

Before promotion, validate exact upstream revision, required columns, date and
symbol uniqueness, numeric ranges, quarantined rows, and dataset licensing.

## Project route

- Adapter: `src/stock_data/providers/financedata_marcap/equity.py`

## Boundaries

- Do not silently update historical partitions when upstream rewrites them.
- Preserve `source_file`, hash, capture date, and upstream revision.
- Do not use it to overwrite a contracted official-source dataset without an approved reconciliation policy.
