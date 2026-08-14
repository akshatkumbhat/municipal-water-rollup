Implement Phase 1 — Engineering Foundation.

Required outcomes:
- Preserve current base-case model outputs unless a verified bug requires a documented change.
- Make pure sourcing, modeling, and KPI calculations independently testable.
- Add deterministic fixtures and focused unit tests.
- Ensure clean-environment installation and stable developer commands.
- Keep the Streamlit app launchable and visually unchanged unless separation requires minor edits.

Constraints:
- No cloud services, database, authentication, Docker, or frontend rewrite.
- No live web calls in tests.
- Do not add abstractions without a concrete testability or reuse benefit.

Before editing, state the file-level plan. Then implement, run `make lint`, `make test`, and `make model`, update `BACKLOG.md` and `README.md`, and stop with evidence.
