---
description: Performs a skeptical investment-committee and engineering review of Project Copperline without changing files. Use before a phase is accepted or committed.
disable-model-invocation: true
---

Review the current repository and uncommitted diff. Do not edit files.

Use the three project subagents when their domains are relevant. Evaluate:
- thesis consistency and acquisition-criteria discipline,
- sources-and-uses and debt/return reconciliation,
- unsupported assumptions or false precision,
- data provenance, scoring logic, and scraping compliance,
- KPI definitions and dashboard aggregation,
- test quality, failure handling, and reproducibility.

Rank findings as Critical, High, Medium, or Low. For each finding include the exact file/line, why it matters to an IC or COO, and the smallest credible remediation. End with a go/no-go recommendation for committing the phase.
