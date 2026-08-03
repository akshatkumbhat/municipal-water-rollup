# Municipal Water & Wastewater Asset-Integrity Services Rollup

An application-ready candidate project for a long-duration holding company. The package joins three workstreams that are often presented separately: proprietary sourcing, transaction underwriting, and post-close operating control.

## Platform definition

The platform provides recurring and programmatic field services to municipal water, wastewater, and stormwater systems:

- CCTV and condition assessment
- Sewer cleaning and jetting
- Leak detection and utility locating
- Valve and hydrant inspection/exercising
- Inflow-and-infiltration diagnostics and smoke testing
- Compliance sampling and asset-data management

The strategy deliberately excludes ownership of regulated utilities, commodity-heavy civil construction, and equipment manufacturing.

## Repository

- `sourcing_pipeline.py` — public-directory scraping, cleaning, website enrichment, deduplication, and 0-100 lead scoring.
- `buy_and_build_model.py` — five-year platform/add-on model, debt sweep, cash conversion, and gross return outputs.
- `operations_kpis.py` — UI-free operating-data validation, KPI computation, exceptions, and modelled views.
- `operations_dashboard.py` — thin Streamlit/Plotly COO cockpit over `operations_kpis`.
- `requirements.txt` — pinned dependency ranges.

## Installation

Requires Python 3.11+. Create the virtualenv with an explicit 3.11+ interpreter
(`python3.11`) — a bare `python`/`python3` may resolve to an older version and
fail on numpy 2.x / pandas 2.x wheels.

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 1. Build the target database

```bash
python sourcing_pipeline.py --output outputs/targets.csv --min-targets 50
```

For brittle or redesigned directories, add a permitted source configuration:

```csv
name,url,item_selector,name_selector,url_selector,address_selector,next_selector,max_pages
Regional contractor list,https://example.org/directory,.company-card,h3,a[href],.address,a.next,10
```

Then run:

```bash
python sourcing_pipeline.py \
  --source-config sources.csv \
  --output outputs/targets.csv \
  --min-targets 50
```

The script respects robots.txt and does not bypass authentication, CAPTCHAs, rate limits, or access controls. Review source terms before commercial use. Replace the example User-Agent contact address before deployment.

### Offline demo (no network)

To generate a realistic target file from bundled synthetic fixtures — no scraping, no network — use:

```bash
python sourcing_pipeline.py --offline-demo --output outputs/targets_demo.csv --min-targets 50
```

This runs 50+ fictional companies through the identical normalization, deduplication, enrichment, and scoring path used for live sources. All fixture companies are synthetic (reserved `example.com` domains); nothing in the demo is a claim about a real company. The same fixtures drive the network-free automated test suite.

### Deduplication audit trail

Records are merged when they share any of four signals — registrable **domain**, canonical **phone**, normalized **name**, or normalized **address** — using a deterministic union-find. Merges are never silent: each surviving row carries `duplicate_count`, `merged_from` (the names it absorbed), and `merge_reason` (which signals matched). Note that address-only matches can merge distinct firms sharing a building; treat `merge_reason == "address"` merges as review candidates.

## 2. Run the buy-and-build model

```bash
python buy_and_build_model.py --output-dir outputs/model          # all scenarios
python buy_and_build_model.py --scenario downside                 # one scenario
python buy_and_build_model.py --scenario-file scenarios.json      # custom cases
```

Each scenario writes a directory under the output directory:

- `five_year_pro_forma.csv` — operating, debt, and cash schedule
- `sources_and_uses.csv` — per-closing purchase price, fees, debt, and equity
- `return_bridge.csv` — entry equity to exit equity walk
- `sensitivity_moic.csv` / `sensitivity_irr.csv` — exit multiple × organic growth
- `return_summary.json`, `assumptions.json`

`scenario_comparison.csv` sits at the top level.

### Scenarios

| Scenario | Growth | SG&A synergy capture | Interest | Exit mark | Source |
|---|---:|---:|---:|---:|---|
| `base` | 5.0% | 15% | 8.0% | 6.5x | Blueprint operating assumptions |
| `downside` | 3.0% | 7.5% | 9.0% | 6.0x | Blueprint IC guardrails |
| `upside` | 7.0% | 20% | 7.5% | 6.5x | Author-defined |

The base case uses a $10.0M-revenue platform and three add-ons. The upside case
is deliberately **operational only** — it holds the exit mark at the base-case
6.5x, because multiple re-rating is the least defensible driver in the
value-creation bridge. It is marked as author-defined in `assumptions.json`;
the blueprint does not specify an upside.

Custom scenarios need no code change. A `--scenario-file` entry supplies
overrides only; omitted keys keep their base value, and unknown keys raise:

```json
{"scenarios": [{"name": "credit-stress",
                "description": "12% interest, no add-ons",
                "assumptions": {"interest_rate": 0.12},
                "add_ons": []}]}
```

### Modeling conventions

- The platform closes at time zero; add-ons close at the start of their year.
- Add-on uses are funded with debt up to `max_pro_forma_leverage` (4.0x) and
  with sponsor equity beyond it, in close order. The governor does not bind in
  the base case — year-end leverage peaks at 2.30x — so the documented
  economics are unchanged, but it prevents leverage-stress cases from being
  financed on debt that no lender would provide.
- Interest accrues on average debt; the resulting interest/tax/cash-sweep
  circularity is solved in closed form, not iterated. Every schedule line
  reconciles to an identity that the test suite checks.
- All levered free cash flow sweeps to debt. Cash accumulates only after debt
  is fully repaid; a cash shortfall draws the revolver rather than producing
  negative cash.
- IRR is solved on the actual equity cash-flow vector, so staged equity is
  priced correctly. The Year-5 value is a valuation mark, not a forced sale.

### Return bridge

The bridge is an identity, not an attribution heuristic — its components sum to
the exit equity value exactly. In the base case, $6.24M of entry equity becomes
$28.32M: $4.01M platform organic growth, $0.66M add-on organic growth, $1.22M
synergies, $7.05M multiple change, $9.47M net debt paydown, less $0.34M of
transaction fees. EBITDA growth is valued at the **blended** entry multiple
(4.97x), so multiple arbitrage between the 6.0x platform and the 3.5x add-ons
is embedded in that entry multiple rather than double-counted as its own line.

## 3. Launch the operating dashboard

```bash
streamlit run operations_dashboard.py
```

The app launches with sample data. For live use, upload monthly data with these columns:

```text
month, region, service_line, revenue, gross_profit, ebitda,
paid_hours, billable_hours, completed_jobs, route_miles,
active_customers, lost_customers, recurring_revenue, capex,
cash_taxes, cash_interest, delta_nwc, accounts_receivable
```

Two columns are **optional** and unlock extra views when present:

- `business_unit` — enables the platform-versus-add-on tab.
- `lost_recurring_revenue` — enables gross revenue retention.

When either is absent the dashboard says so explicitly rather than
substituting a proxy. One row per `month / region / service_line`
(plus `business_unit` when present); duplicate segments are rejected with a
message telling you to aggregate first.

### Architecture

`operations_kpis.py` holds all data preparation and KPI computation and imports
no UI framework, so every displayed number is unit-tested. `operations_dashboard.py`
is presentation only. Financial figures are read from the Phase 3 model through
`buy_and_build_model`; no financial formula is reimplemented in the dashboard.

### Governing KPIs

The six governing KPIs are route density, billable utilization, gross margin,
recurring-revenue mix, customer churn, and FCF conversion, with DSO as a
supporting cash metric. Two definitional points matter for review:

- Multi-month values are **ratios of sums**, not averages of monthly ratios, so
  a small branch cannot outweigh a large one.
- Churn uses the **opening** customer base (prior month's close), per the
  blueprint. The first month in any window is therefore undefined rather than
  silently divided by the current-period base.
- Customer counts are summed across segments, so a customer buying several
  service lines — or served from several regions — is counted more than once,
  which distorts the churn rate. De-duplicating requires a customer-level
  identifier that this schema does not carry, so the dashboard discloses the
  caveat rather than inventing a correction.

### Model-period anchoring

Calendar months are mapped to model years from a **fixed anchor** resolved once
from the full dataset, before any filter is applied: an explicit model start
date if supplied, otherwise the earliest month present. Every row is stamped
with its model year up front, so narrowing the reporting period to a
mid-horizon range keeps its true numbering — selecting July 2026 onward reports
Model Years 3–5, not a fresh Year 1. The anchor is shown in the
plan-versus-actual section and travels with the CSV in a `Model Anchor` column.
Set it with `--model-start` on the CLI or the "Model Year 1 begins" control in
the sidebar; unparseable dates are rejected with an actionable error.

### Actual versus modelled

Governing KPIs are computed **only** from operating data. Plan-versus-actual,
capital structure, leverage headroom, liquidity, and synergy realization are
**modelled** from the selected Phase 3 scenario and are labelled as such
throughout. Targets are author-defined operating thresholds and are not
specified in `PROJECT_BLUEPRINT.md`.

### Headless generation

```bash
python operations_kpis.py --output-dir outputs/operations
python operations_kpis.py --scenario downside --input my_actuals.csv
```

Writes `operating_data.csv`, `monthly_kpis.csv`, `kpi_summary.csv`,
`exceptions.csv`, `metric_definitions.csv`, `organic_growth.csv`,
`plan_vs_actual.csv`, `capital_structure.csv`, `synergy_realization.csv`, and a
`performance_by_*.csv` per available dimension.

### Sample data

The sample dataset is entirely synthetic. Its **annual revenue and EBITDA are
calibrated to the Phase 3 base case** so plan-versus-actual compares like with
like; the region / service-line / business-unit split within a year is invented
and makes no claim about any real business. Business units follow the model's
acquisition schedule, and one branch (Mountain West) is deliberately generated
as an underperformer so the management-exception workflow is demonstrable when
filtering to it.

## 4. Recommended candidate presentation

1. **Thesis page:** recurring, regulatory, fragmented, route-based, data-rich.
2. **Target funnel:** 50+ sourced names, top 15 scored, top five hand-researched.
3. **IC case:** platform/add-on underwriting, leverage, synergy bridge, downside cases.
4. **100-day plan:** pricing, dispatch, field utilization, contract renewal, working capital.
5. **Live demo:** dashboard filters and one management exception converted into an action plan.

## Scoring logic

The 0-100 proprietary outreach priority index is:

- Company age: 35 points
- Field workforce fit: 40 points
- Digital whitespace: 25 points

Digital whitespace rewards a credible but under-optimized website: enough information to validate the business, but limited marketing sophistication and social presence. A separate `data_confidence` field prevents false precision when age or employee estimates are missing.

## Claude Code workflow

This repository includes a Claude Code operating layer:

- `CLAUDE.md` — persistent project rules and financial/data guardrails.
- `BACKLOG.md` — phase gates and acceptance criteria.
- `VIBE_CODING_PLAYBOOK.md` — exact session workflow.
- `prompts/` — bounded phase prompts.
- `.claude/skills/` — `/implement-phase` and `/ic-review`.
- `.claude/agents/` — independent data-engineering, underwriting, and COO reviewers.

Start conservatively:

```bash
claude --permission-mode plan
```

Then paste `BOOTSTRAP_PROMPT.txt`. After approving the Phase 1 plan, run phases one at a time using:

```text
/implement-phase prompts/01_engineering_foundation.md
```
