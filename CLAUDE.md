# Project Copperline — Claude Code Instructions

## Mission
Build an investment-committee-grade municipal water and wastewater asset-integrity services rollup case. The repository must demonstrate proprietary sourcing, disciplined buy-and-build underwriting, and post-close operating control. It is a lower-middle-market private-equity project, not a generic SaaS demo.

## Source of truth
1. `PROJECT_BLUEPRINT.md` defines the investment thesis and base assumptions.
2. `BACKLOG.md` defines phase scope and acceptance gates.
3. Existing Python scripts are working prototypes, not untouchable architecture.
4. Do not change economic assumptions silently. Update documentation and tests in the same change.

## Non-negotiable domain rules
- Keep the strategy focused on field services; exclude regulated utility ownership and commodity-heavy construction.
- Preserve seller-friendly, permanent-capital positioning.
- Never invent facts about real acquisition targets. Mark estimates and provenance explicitly.
- Treat public-directory scraping as compliance-sensitive: obey robots.txt, rate limits, source terms, and access controls. Never bypass authentication or CAPTCHAs.
- Keep model units explicit. Operating values are USD millions unless labeled otherwise.
- Platform closes at time zero; add-ons close at the beginning of their stated year; all positive levered FCF sweeps to debt in the base case.
- A Year-5 valuation is a mark, not a forced-exit assumption.

## Engineering standards
- Python 3.11+.
- Prefer small pure functions, dataclasses or typed models, explicit validation, and deterministic tests.
- Keep Streamlit rendering thin; put reusable calculations outside UI code where practical.
- No network calls in unit tests. Use fixtures or saved HTML.
- Preserve source URL, scrape timestamp, and data-confidence fields through the sourcing pipeline.
- Avoid premature cloud infrastructure, authentication, paid APIs, or databases unless a phase explicitly requires them.
- Never commit secrets, credentials, personal data, or proprietary directory exports.
- Do not weaken tests to make a change pass.

## Standard commands
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
make lint
make test
make model
make dashboard
```

## Working method
For each phase:
1. Read the relevant prompt under `prompts/` and inspect existing code.
2. State assumptions and produce a file-level plan before editing.
3. Implement only the active phase; avoid opportunistic rewrites.
4. Run targeted tests, then `make lint` and `make test`.
5. Update `BACKLOG.md`, `README.md`, and any affected assumptions or schemas.
6. Summarize changed files, commands run, results, remaining risks, and the next recommended phase.

## Definition of done
A phase is complete only when its acceptance criteria pass, documentation matches behavior, outputs are reproducible from a clean environment, and the result can be explained to both an investment partner and an operating executive.
