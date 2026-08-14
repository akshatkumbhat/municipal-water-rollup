# Municipal Water & Wastewater Asset-Integrity Services Rollup

A private-equity buy-and-build case study joining three workstreams usually
presented separately: proprietary sourcing, transaction underwriting, and
post-close operating control. Built as a reproducible, offline, fully tested
pipeline rather than a spreadsheet.

> ### Read this first
>
> **Every company in this repository is synthetic.** The sourcing dataset is
> generated on reserved `example.com` domains — no target here is a real
> business, and no claim is made about any real company, market, or owner.
> **All operating data is generated, not observed.** The financial model is
> parameterised from `PROJECT_BLUEPRINT.md`, *not* derived from the candidate it
> selects, because the sourcing data contains no revenue or EBITDA for anyone.
>
> This demonstrates a repeatable method. It is not diligence, not market
> validation, and not evidence about any business. Assumptions are labelled by
> provenance throughout — blueprint, modelled, fixture, synthetic, or
> author-defined — and `RESEARCH_BENCHMARKS.md` records which are supported by
> published evidence, which are merely indicative, and which are asserted.

**What it does**

- Sources and scores 50+ targets offline, with a measured deduplication
  accuracy (precision/recall against known ground truth) rather than an assumed
  one.
- Underwrites a five-year platform-plus-add-on model where every reported line
  reconciles to an identity the test suite checks, across base, downside, and
  severe-downside scenarios.
- Runs a COO dashboard over 60 months of operating data with six governing
  KPIs plus DSO as a supporting cash metric, exception thresholds, and stated
  data lineage.
- Assembles all of it into one checksummed, byte-reproducible candidate package
  via a single command.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
make package        # ~15 seconds, fully offline
```

Then read `outputs/candidate_package/IC_SUMMARY.md`.

366 tests, no network access anywhere in the suite.

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
- `sourcing_fixtures.py` — synthetic offline directory and company pages; the only data source the test suite touches.
- `sourcing_evaluation.py` — measures deduplication precision/recall against known ground truth.
- `buy_and_build_model.py` — five-year platform/add-on model, debt sweep, cash conversion, and gross return outputs.
- `operations_kpis.py` — UI-free operating-data validation, KPI computation, exceptions, and modelled views.
- `operations_dashboard.py` — thin Streamlit/Plotly COO cockpit over `operations_kpis`.
- `candidate_package.py` — orchestration only; assembles every module's output into the checksummed deliverable.
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

---

# Quick start: build the whole deliverable

From a clean checkout, three commands produce the complete candidate package.
Everything runs **offline** — no network access is required or attempted.

```bash
python3.11 -m venv .venv                      # Python 3.11+ required
source .venv/bin/activate                     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt           # dev extras include the test tooling
make package                                  # ~15 seconds
```

Then read `outputs/candidate_package/IC_SUMMARY.md` and follow
`outputs/candidate_package/DEMO_WALKTHROUGH.md` for the five-minute demo.

| Command | Produces |
|---|---|
| `make package` | The full integrated deliverable in `outputs/candidate_package/` |
| `make verify` | Re-checksums every artifact against `MANIFEST.json` |
| `make dashboard` | Launches the live COO dashboard |
| `make smoke` | Regenerates everything, verifies checksums, runs the test suite |
| `make model` / `make operating` / `make sourcing` | Individual components, unchanged |
| `make evaluate` | Scores deduplication precision/recall against ground truth |
| `make lint` / `make test` | `ruff check` + mypy; the test suite |
| `make format` | Applies `ruff format`. `make lint` does **not** check formatting, so run this before committing |

### What the package contains

```
outputs/candidate_package/
├── IC_SUMMARY.md              investment committee summary
├── DEMO_WALKTHROUGH.md        five-minute demo script
├── MANIFEST.json              every artifact with a SHA-256 checksum
├── 01_sourcing/               target universe, top 15, selected candidate,
│                              tie disclosure, funnel
├── 02_model/                  base / downside / upside: pro forma, sources and
│                              uses, return bridge, sensitivities
├── 03_operating/              dashboard input data and generated KPI views
└── 04_reference/              assumptions with provenance, KPI definitions,
                               known limitations
```

### Reproducibility

Every artifact is byte-identical across runs from a clean output directory. The
only volatile value is the run date, isolated in the manifest's `as_of` block
and pinnable:

```bash
python candidate_package.py --output-dir outputs/candidate_package --as-of 2026-01-31
python candidate_package.py --verify outputs/candidate_package/MANIFEST.json
```

Generated artifacts are **not** committed; `outputs/` is gitignored.

### Architecture map

```
sourcing_fixtures.py ─┐
                      ├─> sourcing_pipeline.py ──┐   scoring, dedupe, evidence
                      ┘                          │
buy_and_build_model.py ──────────────────────────┤   scenarios, returns, bridge
                                                 ├─> candidate_package.py ─> outputs/
operations_kpis.py ──────────────────────────────┤   KPIs, exceptions, lineage
        └─> operations_dashboard.py (Streamlit) ─┘   presentation only
```

`candidate_package.py` is orchestration only. It reimplements no scoring rule,
financial formula, or KPI definition — each stays owned by its module.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError: streamlit` (or pandas, plotly) | Dependencies not installed. Run `pip install -r requirements-dev.txt` inside the activated venv. |
| Wheel build errors on install | The venv is on Python 3.10 or older. Recreate it with an explicit `python3.11 -m venv .venv`; numpy 2.x / pandas 2.x have no wheels for older versions. |
| `make: command not found` | Run the underlying command directly: `python candidate_package.py --output-dir outputs/candidate_package`. |
| `error: Operating data is missing required column(s): …` | An uploaded CSV lacks required columns. The message names them; see the schema in section 3. |
| `… duplicate row(s) share the same month/region/service_line` | The upload has more than one row per segment per month. Aggregate to that grain first. |
| `Verification FAILED — CHECKSUM MISMATCH` | An artifact changed after generation. Rebuild with `make package`, then `make verify`. |
| `Verification FAILED — UNLISTED FILE` | A file was added to the package directory after the build. Remove it or rebuild. |
| `error: Invalid --as-of value` | Use an ISO date, e.g. `--as-of 2026-01-31`. |
| `Refusing to build into …: it looks like a repository root` | `--output-dir` points at a directory containing `.git`. Choose a path under `outputs/`. |
| Dashboard shows "No rows match the selected filters" | The filter combination is empty. Widen the date range or reselect regions. |

### Verify the installation

```bash
make smoke
```

Regenerates the model, operating, and candidate-package outputs, verifies every
checksum, and runs the full test suite.

---

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

The script respects robots.txt and does not bypass authentication, CAPTCHAs, rate limits, or access controls. Review source terms before commercial use.

Live scraping advertises a contact address so site operators can reach you. Set it before running against any site you do not control:

```bash
export COPPERLINE_SCRAPER_CONTACT="you@yourdomain.com"
```

Left unset, the User-Agent falls back to a reserved `.invalid` address that cannot receive mail, and `build_session()` logs a warning. The offline paths below need no contact and emit no warning.

### Offline demo (no network)

To generate a realistic target file from bundled synthetic fixtures — no scraping, no network — use:

```bash
python sourcing_pipeline.py --offline-demo --output outputs/targets_demo.csv --min-targets 50
```

This runs 50+ fictional companies through the identical normalization, deduplication, enrichment, and scoring path used for live sources. All fixture companies are synthetic (reserved `example.com` domains); nothing in the demo is a claim about a real company. The same fixtures drive the network-free automated test suite.

### Deterministic ordering

Scored targets carry one documented **total order**, defined by
`sourcing_pipeline.order_scored_targets` and consumed by both the standalone
pipeline and `candidate_package.py` so the two cannot drift apart:

1. `priority_score` descending — the scoring methodology decides first;
2. `data_confidence` descending — better-evidenced records rank higher;
3. normalized company name ascending;
4. registrable domain, then source URL, ascending — a stable identifier for
   records that normalize to the same name.

Score and confidence alone leave ties unresolved, which previously made row
order depend on thread-pool completion order and differ between runs. Ordering
recomputes no score. Blueprint technician-band preference is deliberately *not*
part of this order — that is candidate-selection logic, applied on top by
`candidate_package.select_candidate`.

### Deduplication audit trail

Records are merged when they share any of four signals — registrable **domain**, canonical **phone**, normalized **name**, or normalized **address** — using a deterministic union-find. Merges are never silent: each surviving row carries `duplicate_count`, `merged_from` (the names it absorbed), and `merge_reason` (which signals matched). Note that address-only matches can merge distinct firms sharing a building; treat `merge_reason == "address"` merges as review candidates.

## 2. Run the buy-and-build model

```bash
python buy_and_build_model.py --output-dir outputs/model          # all scenarios
python buy_and_build_model.py --scenario downside                 # one scenario
python buy_and_build_model.py --scenario-file examples/scenarios.json   # custom cases
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
overrides only; omitted keys keep their base value, and unknown keys raise.
A working example ships in `examples/scenarios.json` with five author-defined
cases: a **severe downside** stress case, an isolated **integration-failure**
case, a revenue-decline case, a no-add-on credit stress, and one that lets a
lender credit the full synergy add-back:

```bash
python buy_and_build_model.py --scenario-file examples/scenarios.json
```

```json
{"scenarios": [{"name": "credit-stress",
                "description": "12% interest, no add-ons",
                "assumptions": {"interest_rate": 0.12},
                "add_ons": []}]}
```

Organic growth may be **negative** — a shrinking business is a valid case to
underwrite. The bound is the revenue multiplier `(1 + growth)`, which must stay
positive, so growth is accepted on `-1 < growth < 1`.

#### Severe downside

The blueprint's `downside` scenario varies only growth, synergy capture,
interest, and the exit mark, and still returns 3.58x, so it explores a narrow
band of outcomes. `examples/scenarios.json` adds a **severe downside** that also
moves the **entry multiple** (overpay at 7.0x), **platform margin** (20% to
16.5% with no expansion), and **add-on integration** (one tuck-in underperforms,
one is late and smaller, one never closes), alongside a 3% annual revenue
decline, 10.5% interest, and a 5.0x exit. It returns **0.99x MOIC / -0.2%
IRR** — a modeled capital-impairment case in which the sponsor does not recover
its investment. Debt still amortises throughout, so it models value destruction
rather than a liquidity failure.

This is a severe downside, **not a guaranteed lower bound**. Worse outcomes are
possible: a covenant default, an unrecovered receivable position, a failed
platform, or several stresses landing harder than modelled here. It is one
plausible loss case, chosen to be internally coherent.

The `integration-failure` case isolates the tuck-in driver alone (3.69x), which
the blueprint downside cannot express at all because it never varies the
acquisition schedule.

One modelling note worth stating, because it is counterintuitive **and specific
to this model's construction**: reducing `platform_revenue` is not a downside
stress here. Under the current assumptions the add-on programme is a fixed
absolute size and the entry multiple is unchanged, so a smaller platform needs
proportionally less equity while the lower-multiple add-ons contribute a larger
share of the deal, and modeled MOIC rises. That is an artefact of holding
add-on size and entry pricing constant — **it is not a general conclusion that
smaller platforms earn better returns.** Concentration loss must therefore be
modelled as post-close revenue decline — you paid for EBITDA that then went
away — which is how the severe case expresses it.

Single-driver figures quoted anywhere in this repository are
**one-factor-at-a-time** sensitivities, each measured against the base case with
every other assumption held constant. They are **not additive** and do not sum
to the combined severe-downside result, which reflects interaction between the
stresses.

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
- Acquisition debt capacity is sized on delivered **operating** EBITDA plus
  only `leverage_synergy_addback_fraction` of realized synergies. That input is
  **author-defined and lender-specific**, defaults to `0.0`, and is *not* a
  covenant term: real credit agreements cap add-backs and impose documentation,
  timing, and realization requirements.
- Leverage is reported at three distinct points, because no single number
  describes all of them: `gross_leverage_at_close` (3.00x in the base case),
  `maximum_year_end_gross_leverage` (2.30x, also exposed as the retained alias
  `peak_gross_leverage`), and `exit_net_leverage` (0.34x). `peak_gross_leverage`
  sees reported year-**end** periods only and does not observe the closing
  position.
- `leverage_limit_exceeded` and `leverage_limit_exceeded_years` flag any year
  where year-end gross leverage exceeds `max_pro_forma_leverage` — for example
  after a revolver draw under stress. This is a **model-limit warning, not a
  covenant breach**; no covenant is modelled anywhere in this repository.
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
- `METHODOLOGY.md` — how this repository was built, phase by phase.
- `specs/` — bounded phase specifications with acceptance criteria.
- `.claude/skills/` — `/implement-phase` and `/ic-review`.
- `.claude/agents/` — independent data-engineering, underwriting, and COO reviewers.

Start conservatively:

```bash
claude --permission-mode plan
```

Then paste the audit prompt from [`METHODOLOGY.md`](METHODOLOGY.md) §2. After approving the Phase 1 plan, run phases one at a time using:

```text
/implement-phase specs/01_engineering_foundation.md
```
