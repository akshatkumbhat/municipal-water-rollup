# Project Copperline Build Backlog

Status values: `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `DONE`.

## Phase 0 — Repository audit
**Status:** DONE (2026-07-30 — see `AUDIT_PHASE0.md`)

Acceptance criteria:
- Existing scripts compile and current model outputs are reproduced.
- Gaps, defects, assumptions, and architectural risks are documented.
- No production code changes occur before the audit plan is approved.

## Phase 1 — Engineering foundation
**Status:** DONE (2026-07-30)

Delivered: ruff `exclude` now covers `.venv`/`.git`/`__pycache__`; `requires-python
>= 3.11` declared; 7 source lint issues fixed (imports, `datetime.UTC`, B008
singleton); venv-creation docs standardized on `python3.11`. `ruff check .`,
`py_compile`, `pytest` (13 passed), `make model`, and `scripts/smoke_test.sh` all
green from a clean 3.11 venv; base-case returns unchanged (MOIC 4.54x / IRR 35.3%).
Deferred to Phase 2: 19 mypy errors in `sourcing_pipeline.py` and adding mypy to
the lint gate.

Acceptance criteria:
- Clean environment installs from `requirements-dev.txt`.
- `make lint`, `make test`, and `make model` pass.
- Core calculations are unit-testable without launching Streamlit or making network calls.
- Sample fixtures are deterministic and small.

## Phase 2 — Sourcing engine
**Status:** DONE (2026-07-30)

Delivered: `HtmlFetcher` seam (network + offline) makes the whole pipeline
testable without network and powers `--offline-demo`; `sourcing_fixtures.py`
provides 54 deterministic synthetic companies (49 ok / 3 blocked / 2 error) run
end to end; phone/address normalization added; deduplication rewritten as a
deterministic union-find over domain/phone/name/address with a retained
`duplicate_count`/`merged_from`/`merge_reason` audit trail; provenance
(`verification_date`, `evidence_summary`, `address_normalized`) preserved through
every transformation; the `data_confidence` missing-column crash fixed (scoring
methodology locked by golden tests). All 19 mypy errors in production source
resolved and mypy added to `make lint`. 46 tests (was 13), network-free.

Also fixed a latent Phase-0-missed defect: `BeautifulSoup(html, "lxml")` required
an undeclared `lxml` dependency; switched to the stdlib `html.parser` (no new
deps). Deferred/non-goals recorded in the Phase 2 plan (full `sourcing/` package
split; live-directory selector maintenance; robots-cache efficiency rework).

Acceptance criteria:
- Directory adapters, normalization, enrichment, scoring, and export are separable modules or clearly separated functions.
- At least 50 deterministic synthetic/fixture records can flow through the pipeline.
- Deduplication is tested for company-name and domain collisions.
- Priority score remains bounded 0–100 and components reconcile.
- Every output retains provenance, verification date, and data confidence.
- Network failures, robots denial, and malformed pages fail gracefully.

## Phase 3 — Buy-and-build model
**Status:** DONE (2026-07-31)

Delivered: scenarios are now data (`Scenario` registry plus `--scenario-file`
JSON with override-only semantics and unknown-key rejection), so base/downside/
upside are configurable without touching a formula. `downside` is the
blueprint's IC guardrail case verbatim (3% growth, half synergy capture, 9%
interest, 6.0x mark); `upside` is author-defined and marked as such, and is
operational only — the exit mark is held at 6.5x rather than re-rated.
Added explicit per-closing sources and uses, a return bridge that reconciles to
the exit equity value as an identity, and exit-multiple × organic-growth
sensitivity grids for MOIC and IRR. Test count 46 → 97, all network-free.

Six defects found and fixed, all with the base case held at MOIC 4.5388573508 /
IRR 0.3532851206 / ending debt 1.5602039747 (golden test):
1. No sources and uses existed; add-on funding was implicit.
2. Add-ons were unconditionally 100% debt-funded while the docstring claimed
   they were "subject to the modeled pro-forma leverage profile" — an
   unsupported claim. Added a `max_pro_forma_leverage` governor (4.0x default,
   inert in the base case) that funds the overflow with sponsor equity.
3. `gross_irr` was `moic ** (1/n) - 1`, a CAGR valid only for a single t0
   equity flow. Replaced with a bisection IRR over the actual equity vector;
   identical in the base case, materially different once equity is staged.
4. Free cash flow vanished once debt hit zero (no cash balance). Cash now
   accumulates and a shortfall draws the revolver.
5. `Net Leverage` was gross leverage; both are now reported and correct.
6. The circularity solve computed FCF twice and discarded the first result.
   Consolidated into `solve_year_cash`, with a documented closed form, a
   zero-tax re-solve branch, and per-year reconciliation tests.

Evidence: `ruff check .`, `mypy` (4 files), `pytest` (97 passed),
`make model`, and `scripts/smoke_test.sh` all green. Base 4.54x / 35.3%,
downside 3.58x / 29.1%, upside 5.12x / 38.6%.

Open risk for Phase 6: the blueprint's downside still returns 3.58x because it
stresses only growth, synergy capture, interest, and the exit mark — not the
entry multiple, platform margin, or add-on execution. An IC would push on that.
Also note year-end leverage peaks at 2.30x (the blueprint's "below 2.5x" claim
holds) but close-date leverage is 3.0x by construction; the blueprint sentence
should be read as post-close.

Acceptance criteria:
- Base, downside, and optional upside scenarios are configurable without editing formulas.
- Sources and uses, acquisition schedule, debt roll-forward, cash sweep, return bridge, and sensitivities reconcile.
- Debt cannot become negative; FCF conversion and leverage are transparent.
- Tests cover no-add-on, delayed-add-on, high-interest, and invalid-input cases.
- CSV/JSON outputs remain available; an Excel export is optional but desirable.

## Phase 4 — COO dashboard
**Status:** DONE (2026-08-02)

Delivered: computation split out of the UI into `operations_kpis.py`, which
imports no UI framework and is fully unit-tested; `operations_dashboard.py` is
now presentation only. Six governing KPIs plus DSO, each with current value,
prior-window comparison, author-defined target, formula, and provenance.
Seven sections: performance (with plan-versus-actual), growth and margin,
service line and region, platform versus add-on, capital structure, exceptions,
and a definitions/lineage table. Filters (date, region, service line, business
unit) are applied once to the raw rows so every view derives from the same
frame. Downloads for filtered data, monthly KPIs, and exception actions.
Headless generation via `make operating` writes twelve CSVs.

Three scaffold defects fixed:
1. Period KPIs averaged monthly ratios, weighting a small month equally with a
   large one. Multi-month values are now ratios of sums.
2. Churn divided by the same-period active base; the blueprint specifies the
   opening base. Now uses the prior month's close, and is undefined (not
   silently substituted) for the first month in a window.
3. The dashboard had zero test coverage. It now has 59 tests, including a
   Streamlit `AppTest` render suite.

Lineage is kept strictly separate: governing KPIs are computed only from
operating data, while plan, capital structure, leverage headroom, liquidity,
and synergy realization are read from the Phase 3 model through
`buy_and_build_model` and labelled `Modelled`. No financial formula is
reimplemented in the dashboard. Phase 3 outputs are byte-identical.

New optional columns `business_unit` and `lost_recurring_revenue` unlock the
platform-versus-add-on and gross-revenue-retention views; when absent the
dashboard states so rather than substituting a proxy. The sample dataset is
synthetic but calibrated so annual revenue and EBITDA reconcile to the Phase 3
base case, which is what makes plan-versus-actual meaningful.

Open follow-ups: targets are author-defined and unvalidated against real
operating benchmarks; GRR falls back to customer churn without the optional
column; the sample data's segment split is invented.

Acceptance criteria:
- CSV schema validation returns actionable errors.
- Six governing KPIs reconcile to source data.
- Filters update every chart and KPI consistently.
- Threshold breaches create a management-exception table.
- Sample data mode works without external services.
- Dashboard launches successfully and receives a manual visual review.

## Phase 5 — Integrated candidate deliverable
**Status:** DONE (2026-08-03)

Delivered: `candidate_package.py` orchestrates the existing sourcing, scoring,
model, and KPI modules into one reviewable package via `make package`. It
reimplements no formula — every number is produced by the module that owns it.
Output is a four-directory tree plus `IC_SUMMARY.md`, `DEMO_WALKTHROUGH.md`,
and a `MANIFEST.json` carrying a SHA-256 checksum, provenance tag, and
description for all 44 artifacts. `make verify` re-checksums the package.

The IC summary is generated from the model results, so narrative and CSVs
cannot drift: returns, sources and uses, the bridge, and leverage are asserted
to reconcile in tests. It separates blueprint, modelled, fixture, synthetic,
and author-defined values with an explicit legend, states that the candidate is
a synthetic fixture and the model is not derived from it, declines to present
the downside as a floor, and records that dashboard targets are not externally
benchmarked.

Two determinism defects were found in the Phase 2 sourcing output and handled
in the new orchestration layer without changing Phase 2:
1. `sourcing_pipeline.py:726` sorts by score and confidence only. Five fixture
   companies tie at exactly (100.0, 100), so their order fell out of
   thread-pool completion order and changed between runs. `order_targets()`
   appends company name for a total order.
2. `sourcing_pipeline.py:931` stamps a wall-clock `scraped_at_utc` on every
   row. It is excluded from the package; the run date is isolated in the
   manifest's `as_of` block and pinnable with `--as-of`.

Candidate selection is ambiguous in the fixture set — five candidates tie at
100. The package does not choose silently: it applies a documented tiebreak
(anchor technician band, then confidence, then name), writes every tied
candidate to `selection_tie_disclosure.csv`, flags the ambiguity in the
manifest, and states in the summary that the final alphabetical tiebreak
carries no investment meaning.

README gains a quick start, architecture map, package layout, reproducibility
notes, and a troubleshooting table. Phase 3 golden outputs and Phase 4 default
KPI results are unchanged and asserted in tests. Suite grows 188 -> 242.

Open follow-ups: the packaged demo remains built on fixture and synthetic data
by design; `04_reference/limitations.md` carries the full disclosure list,
including the six pre-existing backlog items which were not addressed here.

Acceptance criteria:
- One command generates model outputs and sample dashboard data.
- README supports a clean-machine setup and five-minute demo.
- An IC summary identifies thesis, target funnel, returns, downside, operating plan, and key risks.
- A reviewer can trace each displayed metric to its source or formula.

## Phase 6 — Final institutional review
**Status:** DONE (2026-08-05)

Three review passes (data engineering and compliance, PE underwriting, COO
analytics) were run directly rather than through the project subagents, and
that deviation is recorded here rather than glossed. Verified clean: no
secrets across 39 tracked files, no dead code (AST sweep of all seven modules),
no generated clutter tracked, scoring bounded 0-100 with components summing
exactly, `robots_allows` fail-closed, and the network guard active across the
suite. `/code-review` is user-triggered and could not be launched from here;
`/verify` does not exist in this environment.

Remediated (severity as found):

| # | Finding | Fix |
|---|---|---|
| H1 | Standalone sourcing row **and column** order varied between runs — `as_completed` collected results in thread-completion order, and score/confidence ties were unresolved | `enrich_and_score` now collects in submission order; `order_scored_targets` defines one documented total order consumed by both the pipeline and the package |
| M2 | No leverage-limit reporting; revolver draws bypassed the governor entirely (282x observed under extreme stress, silently) | `leverage_limit`, `leverage_limit_exceeded`, `leverage_limit_exceeded_years`, `maximum_year_end_gross_leverage`; warning surfaced in the console report and IC summary. Deliberately **not** called a covenant breach |
| M1 | `peak_gross_leverage` is year-end only, hiding the 3.0x position at close | Retained for compatibility; added `gross_leverage_at_close`, `maximum_year_end_gross_leverage`, `exit_net_leverage`. No breaking rename |
| M3 | README documented `--scenario-file scenarios.json`, a file that did not exist | Added tracked `examples/scenarios.json`; README uses that exact path; the copy-paste command is executed by a test |
| — | Sensitivity grids always centred on base assumptions | `sensitivity_axes` centres on the active scenario; centre reconciles to that scenario's headline MOIC and IRR. Base axes reproduce the previously shipped values exactly |
| — | Negative organic growth rejected | Supported on `-1 < growth < 1`; the bound is the revenue multiplier staying positive |
| — | Synergies counted in full toward leverage capacity | `leverage_synergy_addback_fraction`, author-defined, lender-specific, default **0.0**, validated `[0, 1]`. Capacity uses operating EBITDA plus only the permitted fraction |
| L1 | Zero target produced a NaN relative gap and fell back to Medium | `_severity` compares absolutely when the target is zero; a breach against zero tolerance ranks High. Unavailable stays Unavailable |
| — | KPI targets and churn disclosure | Targets labelled "not externally benchmarked, not an industry standard" in the module, dashboard, definitions table, and package; the overlapping-customer churn caveat is now a persistent dashboard warning |

Approved economics are untouched. Base MOIC 4.5388573508072145, IRR
0.3532851205563101, terminal debt 1.5602039747129886, sponsor equity 6.24;
downside 3.58x / 29.1%; upside 5.12x / 38.6%. Comparing `return_summary.json`
against the pre-Phase-6 baseline: **zero pre-existing fields changed**, six
fields added, none removed. Phase 4 default KPIs are identical. The Phase 5
selected candidate (Redwood Aqua Group) and package structure are preserved.

Suite grows 242 -> 301. Formatting drift is unchanged at 11 files, the same
list as before this phase; no prior-phase file was reformatted for style.

### Documented follow-ups

Phase 6 was approved for commit with these items outstanding. They are recorded
here rather than silently closed.

**Review independence — open**

1. **Run an independent `/code-review` pass.** `/code-review` is user-triggered
   and billed and could not be launched from this session, so the acceptance
   criterion "`/verify` and `/code-review` complete successfully where
   supported" is only partially met. `/verify` does not exist in this
   environment at all.
2. **Optionally re-run the three project subagents** —
   `data-engineering-reviewer`, `pe-underwriter`, and `coo-dashboard-reviewer` —
   for a genuinely independent second opinion. The Phase 6 reviews were
   conducted directly rather than through those agents, so the findings are not
   independently produced.

**Underwriting — addressed 2026-08-06**

3. **Harsher downside case — DONE.** `examples/scenarios.json` now ships
   `severe-downside`, which varies the **entry multiple** (6.0x -> 7.0x
   overpay), **platform EBITDA margin** (20% -> 16.5% with expansion switched
   off), and **add-on integration failure** (A underperforms its margin case, B
   closes a year late and smaller, C never closes), alongside -3% organic
   growth for concentration loss, 10.5% interest, and a 5.0x exit. It returns
   **0.99x MOIC / -0.2% IRR** — a modeled capital-impairment case in which the
   sponsor does not recover its investment — while debt still amortises, so it
   models value destruction rather than a liquidity failure. It is a severe
   downside, **not a guaranteed lower bound**: a covenant default, a failed
   platform, or several stresses landing harder would all be worse.
   A companion `integration-failure` case isolates the tuck-in driver (3.69x),
   which the blueprint downside cannot express because it never varies the
   acquisition schedule.

   Modelling note recorded during the build: reducing `platform_revenue` is
   not a stress **under this model's construction**. With add-on sizes fixed in
   absolute terms and the entry multiple unchanged, a smaller platform needs
   proportionally less equity while the lower-multiple add-ons contribute a
   larger share of the deal, so modeled MOIC rises (+0.16x measured one factor
   at a time). That is an artefact of those held-constant inputs, not a general
   conclusion that smaller platforms improve returns. Concentration loss is
   therefore modelled as post-close revenue decline.

   All single-driver figures above are **one-factor-at-a-time** sensitivities
   against the base case. They are **not additive** and do not sum to the
   combined severe-downside result.

   Still open: the shipped `downside` scenario in `buy_and_build_model.py`
   deliberately remains the blueprint's IC guardrail case and was not changed.
   Promoting a severe case into the shipped registry, or into the candidate
   package's scenario set, is a separate decision.

**Low severity — accepted, not scheduled**

4. **Repository-wide formatting drift.** `ruff format --check` reports 13 files
   (11 at Phase 6; the count drifted with later commits). This is not an
   enforced gate (`make lint` runs `ruff check`, not `ruff format`).
   Prior-phase files were deliberately not reformatted for style.
5. ~~**Placeholder user-agent contact.**~~ RESOLVED at publication. The contact
   now reads from `COPPERLINE_SCRAPER_CONTACT`; unset, it falls back to a
   reserved `.invalid` address that cannot receive mail, and `build_session()`
   logs a warning. Offline paths are unaffected.
6. **Historical Phase 0 audit.** `AUDIT_PHASE0.md` cites the pre-Phase-3 flat
   output layout. It is a dated point-in-time audit record, not current
   documentation, and is intentionally left unedited.

Acceptance criteria:
- Data engineering, finance, and COO reviewers each complete an independent review.
- Critical and high-severity findings are resolved or documented.
- `/verify` and `/code-review` complete successfully where supported.
- Repository contains no secrets, generated clutter, dead code, or unsupported factual claims.
