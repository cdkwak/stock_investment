# Historical Free-Source Discovery

Status: `DISCOVERY_COMPLETE_WITH_NO_PRODUCTION_BACKFILL`

This inventory is research routing, not collection authorization. No source was
called by repository code and no production or research dataset was created.

| Variable | Official/public candidate | Coverage/evidence | Classification | PIT / remaining gate |
|---|---|---|---|---|
| SOX price index | Nasdaq SOX historical download and official methodology | Index began 1993-12-01; official page exposes MAX/download controls | `VALIDATION_REQUIRED` | Download terms, automated-access permission, stable schema, finality, actual-vs-backtested flags |
| SOX identity/methodology | Nasdaq SOX methodology/factsheet | 30 large US-listed semiconductor companies; modified market-cap weighting | `DISCOVERY_ONLY` | Methodology is identity evidence, not a historical redistribution license |
| US high-yield credit spread | FRED `BAMLH0A0HYM2` | Daily percent, observed from 1996-12-31 | `VALIDATION_REQUIRED` | FRED operation approval, vintage/realtime availability, and predictive PIT resolver remain mandatory |
| US long-run valuation context | Robert Shiller/Yale monthly U.S. market data | Public monthly price, dividend, earnings, CPI, and CAPE history begins in 1871 | `RESEARCH_BACKFILL_APPROVAL_REQUIRED` | This is broad U.S. market research data, not S&P 500 daily forward valuation; freeze source version, publication/vintage rule, and license before any bounded research copy |
| Cross-sectional trailing valuation | Aswath Damodaran/NYU current and archived market-multiple workbooks | Public country/sector PE, PBV, sales and EV multiple snapshots; archive cadence varies by workbook | `VALIDATION_REQUIRED` | Cross-sectional snapshot methodology and workbook vintages do not establish a daily index series or point-in-time forward-consensus history |
| S&P 500 trailing P/E/P/B | S&P DJI Market Attributes/dashboard material | Current/report snapshots define trailing calculations | `VALIDATION_REQUIRED` | No free stable vintage-complete daily history established |
| S&P 500 forward P/E/P/B | No official free vintage series established | Search completed against S&P official material | `DISCOVERY_ONLY` | Forecast fiscal year, consensus period, publication date, vendor entitlement |
| EPS consensus/revisions/breadth/dispersion | Business Quant free estimates API is a candidate; commercial S&P Capital IQ, Bloomberg, SIX, FactSet and similar products confirm the required data class | Candidate API exposes current annual/quarterly consensus, high/low and actuals, but inspected documentation does not establish daily historical estimate vintages, contributor-set history, revision breadth, or immutable snapshots | `VALIDATION_REQUIRED` | Free access is not sufficient: provider rights, stable identifiers, historical perspective dates, correction policy and reproducible vintages must close before research use |
| Filing actuals / trailing fundamentals | SEC EDGAR submissions and XBRL Company Facts APIs | Public filer history and extracted reported facts; filings update throughout the day and bulk archives nightly | `DISCOVERY_ONLY` for valuation support | Official filing facts are not analyst estimates or consensus revisions. A future PIT fundamental contract must bind filing acceptance time, amendments, units, taxonomy and security identity |
| KOSPI/KOSPI200 valuation | KRX/BOK official portals remain candidates | No accepted exact historical-vintage endpoint was established here | `DISCOVERY_ONLY` | Schema, coverage, publication timing, revisions, bulk-use terms |
| Nasdaq/NDX/S&P index levels | Existing accepted global-price routes | Already represented by retained market-price infrastructure | `REJECTED` as duplicate acquisition in this task | Do not add a second source without a resilience/equivalence contract |

Official evidence inspected:

- `https://indexes.nasdaq.com/docs/methodology_SOX.pdf`
- `https://www.nasdaq.com/market-activity/index/sox/historical`
- `https://indexes.nasdaq.com/Index/Overview/SOX`
- `https://fred.stlouisfed.org/series/BAMLH0A0HYM2`
- `https://fred.stlouisfed.org/docs/api/fred/series_observations.html`
- `https://fred.stlouisfed.org/docs/api/fred/realtime_period.html`
- `https://www.econ.yale.edu/~shiller/data.htm`
- `https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/data.html`
- `https://businessquant.com/docs/api/estimates`
- `https://www.sec.gov/search-filings/edgar-application-programming-interfaces`
- `https://www.spglobal.com/spdji/en/indices/equity/sp-500/`

The SEC result is evidence for reported actuals only and does not relax the
consensus/revision blocker. Search also reconfirmed that commercial estimate
products expose historical perspective-date or revision data, but that is not
evidence of a stable free source.

No row is `PRODUCTION_BACKFILL_APPROVED` or `RESEARCH_BACKFILL_APPROVED` yet.
