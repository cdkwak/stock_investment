# FINRA short-sale and short-interest data

## Status

- Project status: `PILOT` / Landing-only.
- Accepted candidates: Reg SHO daily short-sale volume and consolidated short interest.

## Official reference

- [FINRA API documentation](https://developer.finra.org/docs)
- [Daily short-sale volume files](https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files)

Checked-in pilot routes:

```text
https://cdn.finra.org/equity/regsho/daily/CNMSshvol<YYYYMMDD>.txt
https://api.finra.org/data/group/otcMarket/name/EquityShortInterest
```

## Authentication

The daily file route is public. FINRA query API access and limits must follow
the official dataset documentation; do not invent credentials or query fields.

## Safe read example

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\pilot_finra_short_data_landing.py --help
```

Daily files are pipe-delimited and expected to contain `Date`, `Symbol`,
`ShortVolume`, `ShortExemptVolume`, `TotalVolume`, and `Market`. Validate the
header, requested date, non-negative volumes, and `ShortVolume <= TotalVolume`.

## Project route

- Parser/provider: `src/stock_data/providers/finra.py`
- Pilot: `scripts/manual/pilot/pilot_finra_short_data_landing.py`

## Boundaries

- Daily short-sale volume is not short interest, a short balance, or a trading signal.
- Short-interest settlement date, publication date, and capture date are distinct.
- No normalized/dashboard promotion exists until a dedicated contract and availability rule are approved.
