# Interaction Manifest — Qt GUI First Wave

- Baseline: Windows, Korean, 1600×900
- Constrained viewport: 1366×768
- Input: native Qt mouse/keyboard events
- Data: task-local fixtures and validated local bundles only
- Safety: no provider refresh, order, transfer, account mutation, scheduler activation, or protected-data access

| Surface | Typed proof | Click/action proof | Open/detail proof | Before/after | Post-action assertion | Qt messages | Strict complete |
|---|---|---|---|---|---|---:|---|
| Dashboard | No enabled safe text field | Nested market tab | Screen-preferences dialog opened and explicitly closed | Yes | Dialog closed | 0 | No — TYPE not applicable |
| Research Workspace | `005930` entered and read back | Local candidate reread | Both side panes toggled | Yes | Reread button re-enabled | 0 | Yes |
| Data Status | `CURRENT` entered and read back | Filters/search reset; local lifecycle reread | First visible row selected | Yes | Selection retained and action returned | 0 | Yes |
| Account / Net Worth | No safe text-entry contract | Both nested tabs visited | Privacy mode toggled in memory and restored | Yes | Privacy restored before capture | 0 | No — TYPE absent; keyboard focus unproven |
| Backtest | No safe text-entry contract | Validated local bundle reread | Details panel toggled | Yes | Panel state changed | 0 | No — TYPE absent; keyboard focus unproven |

## Coverage result

- Safe action coverage: 5/5 surfaces
- Text-entry coverage: 2/5 surfaces; the remaining three expose no safe text field
- Before/after capture: 5/5 surfaces
- Two-size visual capture: 5/5 surfaces
- Strict all-field manifest: 2/5 surfaces
- Keyboard focus trail: Dashboard and Research are useful; Data Status is partial; Account and Backtest are unproven
- Qt runtime messages after interaction: 0
- Managed background workers at close: 0
- Clean close: yes

The detailed machine evidence is in `ledger.json`; screenshots are in `evidence/`.
