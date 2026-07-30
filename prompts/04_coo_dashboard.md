Implement Phase 4 — COO Dashboard.

Create an executive operating cockpit for the post-acquisition platform while keeping Streamlit and Plotly.

The six governing KPIs are:
1. route density,
2. technician billable utilization,
3. gross margin by service line,
4. recurring-revenue mix,
5. customer churn / gross revenue retention,
6. free-cash-flow conversion.

Also surface DSO as a supporting cash metric.

Required capabilities:
- Thin UI layer over testable KPI functions.
- Deterministic sample-data mode and validated CSV upload.
- Date, region, and service-line filters that update all outputs consistently.
- KPI cards with current value, prior-period comparison, target, and clear metric definitions.
- Trend and service-line views plus a management-exception table tied to thresholds.
- Download of filtered data and exception actions.
- Graceful handling of empty filters, zero denominators, missing months, and invalid values.
- Manual launch/visual verification plus automated calculation tests.

Do not add authentication, a database, or a React rewrite. State the plan first, implement only this phase, verify, update docs/backlog, and stop.
