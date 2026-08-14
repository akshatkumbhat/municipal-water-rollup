Implement Phase 3 — Buy-and-Build Financial Model.

Preserve the documented base case while making the model investment-committee grade.

Required capabilities:
- Configurable base, downside, and upside scenarios.
- Explicit sources and uses at platform close and for each add-on.
- Platform and add-on revenue/EBITDA build, synergy schedule, acquisition fees, debt draw, interest, taxes, capex, NWC, cash sweep, and ending leverage.
- No negative debt and no unsupported circular references.
- Return bridge separating EBITDA growth, debt paydown, and multiple change.
- Sensitivity table for exit multiple and organic growth or margin.
- Tests for no add-ons, delayed add-ons, high interest, leverage stress, invalid inputs, and formula reconciliation.
- Machine-readable CSV/JSON outputs; optional Excel export only if it remains transparent and tested.

The Year-5 value is a valuation mark, not a required sale. Do not engineer returns by silently changing assumptions.

State the plan first, implement only this phase, run all checks, update documentation/backlog, and stop with reconciliations and output evidence.
