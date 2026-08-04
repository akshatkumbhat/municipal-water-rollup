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
**Status:** NOT STARTED

Acceptance criteria:
- Data engineering, finance, and COO reviewers each complete an independent review.
- Critical and high-severity findings are resolved or documented.
- `/verify` and `/code-review` complete successfully where supported.
- Repository contains no secrets, generated clutter, dead code, or unsupported factual claims.
