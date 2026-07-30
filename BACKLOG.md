# Project Copperline Build Backlog

Status values: `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `DONE`.

## Phase 0 — Repository audit
**Status:** NOT STARTED

Acceptance criteria:
- Existing scripts compile and current model outputs are reproduced.
- Gaps, defects, assumptions, and architectural risks are documented.
- No production code changes occur before the audit plan is approved.

## Phase 1 — Engineering foundation
**Status:** NOT STARTED

Acceptance criteria:
- Clean environment installs from `requirements-dev.txt`.
- `make lint`, `make test`, and `make model` pass.
- Core calculations are unit-testable without launching Streamlit or making network calls.
- Sample fixtures are deterministic and small.

## Phase 2 — Sourcing engine
**Status:** NOT STARTED

Acceptance criteria:
- Directory adapters, normalization, enrichment, scoring, and export are separable modules or clearly separated functions.
- At least 50 deterministic synthetic/fixture records can flow through the pipeline.
- Deduplication is tested for company-name and domain collisions.
- Priority score remains bounded 0–100 and components reconcile.
- Every output retains provenance, verification date, and data confidence.
- Network failures, robots denial, and malformed pages fail gracefully.

## Phase 3 — Buy-and-build model
**Status:** NOT STARTED

Acceptance criteria:
- Base, downside, and optional upside scenarios are configurable without editing formulas.
- Sources and uses, acquisition schedule, debt roll-forward, cash sweep, return bridge, and sensitivities reconcile.
- Debt cannot become negative; FCF conversion and leverage are transparent.
- Tests cover no-add-on, delayed-add-on, high-interest, and invalid-input cases.
- CSV/JSON outputs remain available; an Excel export is optional but desirable.

## Phase 4 — COO dashboard
**Status:** NOT STARTED

Acceptance criteria:
- CSV schema validation returns actionable errors.
- Six governing KPIs reconcile to source data.
- Filters update every chart and KPI consistently.
- Threshold breaches create a management-exception table.
- Sample data mode works without external services.
- Dashboard launches successfully and receives a manual visual review.

## Phase 5 — Integrated candidate deliverable
**Status:** NOT STARTED

Acceptance criteria:
- One command generates model outputs and sample dashboard data.
- README supports a clean-machine setup and five-minute demo.
- An IC summary identifies thesis, target funnel, returns, downside, operating plan, and key risks.
- A reviewer can trace each displayed metric to its source or formula.

## Phase 6 — Final institutional review
**Status:** NOT STARTED

Acceptance criteria:
- Data engineering, finance, and COO reviewers each complete an independent review.
- Critical and high-severity findings are resolved or documented.
- `/verify` and `/code-review` complete successfully where supported.
- Repository contains no secrets, generated clutter, dead code, or unsupported factual claims.
