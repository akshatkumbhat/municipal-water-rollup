Perform Phase 6 — Final Institutional Review. Begin read-only.

Run three independent reviews using the project subagents:
- data-engineering-reviewer,
- pe-underwriter,
- coo-dashboard-reviewer.

Then consolidate findings by severity. Focus on:
- incorrect or misleading investment assumptions,
- formula and reconciliation errors,
- data provenance and compliance failures,
- dashboard KPI inconsistencies,
- security, reliability, and reproducibility,
- unsupported factual claims,
- candidate-demo failure modes.

Do not edit until presenting the consolidated findings and a minimal remediation plan. After approval, fix critical/high findings only, run all checks, use `/code-review` and `/verify` where available, update `BACKLOG.md`, and produce a final readiness memo with residual risks.
