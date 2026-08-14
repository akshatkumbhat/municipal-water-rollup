# Project Copperline — Phase 0 Repository Audit

> **Historical record — superseded.** This captures the repository as it stood
> on 2026-07-30, before Phases 1–6. The counts, file layout, and gaps below
> describe that starting point, not the current codebase: the project now has
> 361 tests and a restructured `outputs/` tree. It is kept as the baseline the
> later phases were measured against. For current status see `BACKLOG.md`; for
> how the build proceeded see `METHODOLOGY.md`.

Date: 2026-07-30. No source was changed during this audit.

## Current state

Fresh scaffold. All prototype scripts compile, all 13 tests pass, and the model
reproduces its documented returns. The project is runnable today; the gaps are
in tooling reproducibility and lint/type hygiene, not in core logic.

## Reproduced commands & results

| Command | Result |
|---|---|
| `python3.11 -m venv .venv` + `pip install -r requirements-dev.txt` | clean install (pandas 2.3.3, numpy 2.4.6, streamlit 1.60) |
| `py_compile` all three scripts | OK |
| `pytest` | 13 passed (~21s) |
| `python buy_and_build_model.py --output-dir outputs/model` | produced `five_year_pro_forma.csv`, `return_summary.json`, `assumptions.json` |
| Reproduced returns | gross MOIC 4.54x, gross IRR 35.3%, terminal EV $29.88M, terminal debt $1.56M |
| `ruff check .` | 26,849 errors — all from `.venv`; source-only = 7 |
| `mypy` (source) | 19 errors, all in `sourcing_pipeline.py` |

## Prioritized findings

### HIGH
1. **Python-version trap breaks "clean environment" reproducibility.** Default
   `python3` on the audit machine is 3.9.13; the project requires 3.11+
   (`CLAUDE.md`, `pyproject target-version = py311`). Every tooling entry point
   calls bare `python` — `make install/model`, `scripts/smoke_test.sh`, and
   `CLAUDE.md`'s `python -m venv .venv`. A reviewer following the README verbatim
   would build a 3.9 venv and likely fail on numpy 2.x wheels / any 3.10+ syntax.
   Blocks the Phase 1 "clean environment installs" gate.
2. **`make lint` is unusable as written.** `pyproject [tool.ruff] exclude =
   ["outputs"]` omits `.venv`, so `ruff check .` scans the virtualenv → 26,849
   errors. The lint gate breaks the moment anyone creates the venv the docs tell
   them to create.

### MEDIUM
3. **7 real ruff violations in source:** 2×F401 (unused imports), 2×UP017
   (`datetime.timezone.utc` → `datetime.UTC`), 2×UP035 (deprecated import),
   1×B008 (function call in default arg). `make lint` fails even scoped correctly.
4. **19 mypy errors, all in `sourcing_pipeline.py`** — BeautifulSoup `Tag` vs
   `BeautifulSoup`, `urljoin` receiving `Sequence[str]`, `Series` union-attr
   access. `mypy` ships in `requirements-dev.txt` but is not in the `make lint`
   gate today, so this is latent.
5. **Model isn't scenario-configurable.** `main()` hardcodes base `Assumptions()`
   / `DEFAULT_ADDONS` (only `--output-dir` is exposed), and `validate_assumptions`
   bounds only the 0–1 rate fields — multiples, `initial_debt_to_ebitda`, and
   `platform_margin_cap` are unvalidated. Phase 3 requires downside/upside
   "without editing formulas"; that foundation isn't present. Fix in Phase 3.

### LOW / notes
6. Hidden assumption: add-ons grow at the platform organic-growth rate post-close
   — not stated in README/`PROJECT_BLUEPRINT`.
7. FCF is computed in two passes (circularity solver → average-debt estimate →
   recomputed actual FCF). Correct, but under-commented for IC transparency.
8. `gross_irr = MOIC^(1/5) − 1` is valid only because there's a single
   equity-in/equity-out with debt-funded add-ons and no interim distributions —
   worth stating as a convention.
9. `USER_AGENT` contact is a placeholder (README already flags "replace before
   deployment").
10. Not a git repo at audit time (initialized as part of Phase 0 close-out).

## Assumptions to preserve (do not change silently)

Field-services focus, exclude regulated-utility ownership; platform closes t0,
add-ons at the start of their stated year, all levered FCF sweeps to debt; Year-5
value is a mark, not a forced exit; USD millions; provenance/verification-date/
confidence fields through sourcing; robots.txt + rate-limit compliance and
fail-safe behavior; base economics ($10M platform, 6.0x entry, 3.0x leverage, 8%
rate, 6.5x terminal, three add-ons).

## Smallest Phase 1 plan (files to change)

- `pyproject.toml` — add `.venv`, `.git`, `__pycache__` to `[tool.ruff] exclude`;
  add `requires-python = ">=3.11"`.
- `sourcing_pipeline.py` (+ wherever the others live) — apply the 7 ruff fixes
  (6 autofixable; the B008 one by hand).
- `CLAUDE.md`, `README.md`, `scripts/smoke_test.sh`, `Makefile` — standardize
  venv creation on `python3.11 -m venv .venv` and document the 3.11 requirement
  (keep bare `python` inside the activated venv).
- Tradeoff: whether Phase 1 also adds `mypy` to the lint gate and fixes the 19
  sourcing type errors, or defers that to Phase 2 (where the sourcing engine gets
  refactored anyway). Decision: **defer to Phase 2** — keep Phase 1 lean.

## Phase 1 acceptance-test plan

1. Fresh `python3.11 -m venv .venv` → `pip install -r requirements-dev.txt` succeeds.
2. `ruff check .` → 0 errors.
3. `py_compile` all three → OK.
4. `pytest` → 13 passed.
5. `python buy_and_build_model.py` → reproduces MOIC 4.54x / IRR 35.3%
   (already asserted by `test_base_case_reproduces_documented_returns`).
6. `scripts/smoke_test.sh` runs green inside the venv.
