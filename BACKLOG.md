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
