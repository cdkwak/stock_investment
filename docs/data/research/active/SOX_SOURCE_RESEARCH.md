# SOX source research

Status: `RESEARCH_ONLY / LICENSE_AND_OPERATION_BLOCKED` (2026-08-18).

SOX is the PHLX Semiconductor Sector Index. SOXX is an iShares ETF and is not a
substitute, fallback, or continuous proxy for SOX. SOXX changed its tracked
benchmark in 2021, which is an additional reason not to splice the two series.

## Candidate routes

1. Nasdaq Global Index Watch history is the official-owner route. It requires
   subscriber entitlement and an assigned GIDS instrument identifier. Exact EOD
   availability time, holiday/finality, restatement/vintage behavior, and
   repository storage rights are not yet accepted.
2. FRED `NASDAQSOX` distributes a daily close series from 2004-09-02. It is
   explicitly revision-capable and carries Nasdaq copyright/use restrictions;
   it is therefore a research/validation candidate only, not an accepted
   production source.

No source call, sample capture, contract, collector, backfill, or production
promotion is authorized by this note. Reopen only after entitlement/licensing,
identifier, EOD finality, vintage retention, and a close-only index contract are
reviewed. Any later sample must remain Landing-first and research-isolated.
