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
- `operations_dashboard.py` — self-contained Streamlit/Plotly COO dashboard with deterministic sample data and CSV upload.
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

## 2. Run the buy-and-build model

```bash
python buy_and_build_model.py --output-dir outputs/model
```

Outputs:

- `five_year_pro_forma.csv`
- `return_summary.json`
- `assumptions.json`

The base case uses a $10.0M-revenue platform, three add-ons, 5% organic growth, 15% add-on SG&A consolidation, an 8% interest rate, and a 6.5x year-five valuation mark.

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
