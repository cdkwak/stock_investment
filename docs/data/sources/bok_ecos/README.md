# Bank of Korea ECOS

## Status

- Project status: `PILOT` / contract-specific backfill.
- Accepted candidate scopes: Korean Treasury and macroeconomic observations.

## Official reference

- [BOK ECOS Open API](https://ecos.bok.or.kr/api/)
- [817Y002 publication/finality evidence](817Y002_PUBLICATION_FINALITY.md)
- [731Y001 USD/KRW daily source contract](731Y001_USD_KRW_DAILY.md)

Project URL shape:

```text
https://ecos.bok.or.kr/api/StatisticSearch/<API_KEY>/json/kr/1/2/<TABLE>/<CYCLE>/<START>/<END>/<ITEM>/
```

## Authentication

- Environment variable: `BOK_ECOS_API_KEY`

Because the key is embedded in the path, never print full URLs, exception
request objects, or HTTP histories.

## Safe read example

Use the checked-in pilot to construct, bound, and redact requests:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\pilot_bok_ecos_treasury.py --help
```

Do not copy a table/item code from an unrelated example. First verify it using
the ECOS item-list operation, then record table, cycle, item, unit, and period
in the dataset contract.

## Project route

- Pilot: `scripts/manual/pilot/pilot_bok_ecos_treasury.py`
- Backfill: `scripts/manual/backfill/backfill_bok_ecos_treasury.py`
- Contract: `src/stock_data/contracts/bok_ecos_treasury.py`
- USD/KRW collector: `scripts/manual/collect/refresh_bok_ecos_fx_daily.py`
- USD/KRW contract: `src/stock_data/contracts/bok_ecos_fx.py`

## Boundaries

- A valid response may still describe a revised current observation, not its original vintage.
- `StatisticSearch` does not document a publication timestamp, preliminary/final
  flag, revision identifier, or revision window. Do not derive expected latest
  from an exchange calendar.
- Preserve frequency and unit; never silently convert a price, rate, index, or percentage.
- Validate row count, date order, duplicate period/item keys, missing values, and response result metadata.
