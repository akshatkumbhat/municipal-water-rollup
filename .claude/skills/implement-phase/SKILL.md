---
description: Implements one Project Copperline phase from a prompt file with strict scope, tests, documentation, and evidence. Use when asked to build or continue a numbered project phase.
argument-hint: <path-to-phase-prompt>
disable-model-invocation: true
---

Implement exactly one project phase using `$ARGUMENTS` as the phase specification.

1. Read `CLAUDE.md`, `BACKLOG.md`, the supplied phase prompt, and affected files.
2. Inspect git status and do not overwrite unrelated user changes.
3. State a concise file-level plan before editing.
4. Implement only the requested phase. Preserve documented economics and schemas unless the phase explicitly changes them.
5. Add or update tests with the implementation.
6. Run the narrowest relevant checks, then `make lint`, `make test`, and any phase-specific command.
7. Fix failures rather than hiding them.
8. Update `BACKLOG.md`, `README.md`, and affected documentation.
9. Report changed files, commands, results, reconciliations, unresolved risks, and the recommended next phase.
10. Stop. Do not begin the next phase.
