# How Project Copperline Was Built

This repository was built with Claude Code under a phase-gated method: bounded
scope per phase, a plan approved before any edit, and an adversarial review pass
before anything was considered done. This file documents that method so the
result is reproducible and auditable rather than merely asserted.

## The operating principle

Do not ask for "the whole project." Give one bounded phase, explicit acceptance
criteria, and a required verification loop. The goal is fast iteration without
losing financial-model integrity or data provenance.

## 1. Set up the repository

```bash
git clone https://github.com/akshatkumbhat/municipal-water-rollup.git
cd municipal-water-rollup

python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
```

Python 3.11+ is required. Use an explicit `python3.11`: a bare `python` or
`python3` may resolve to an older interpreter and fail on the numpy 2.x /
pandas 2.x wheels.

Confirm the baseline before changing anything:

```bash
make test
make model
```

## 2. First session

Launch in read-only plan mode:

```bash
claude --permission-mode plan
```

Then open with the repository audit:

```text
Read CLAUDE.md, BACKLOG.md, PROJECT_BLUEPRINT.md, README.md, and
specs/00_repository_audit.md. Work in plan mode. Reproduce the current model
output and compile all scripts. Then provide the complete Phase 0 audit and the
smallest file-level plan for Phase 1. Do not edit files and do not begin later
phases.
```

Do not approve implementation until the current outputs have been reproduced,
gaps identified, and a file-level plan proposed. After reviewing the plan, use
Shift+Tab to move to an edit-enabled mode and say:

```text
Implement the approved Phase 0 and Phase 1 plan only. Preserve the current
base-case economics. Run every required check, fix failures, update BACKLOG.md,
and stop after reporting evidence of completion.
```

## 3. Phase loop

For each later phase:

```text
/implement-phase specs/02_sourcing_engine.md
```

Then repeat with the next prompt file. Keep each phase in its own commit:

```bash
git status
git diff --stat
git add .
git commit -m "feat: complete sourcing engine phase"
```

## 4. Review loop

After every meaningful phase:

```text
Use the relevant project subagent to review the implementation independently.
Do not edit files. Rank findings by severity and cite exact files and lines.
```

Available reviewers in `.claude/agents/`:

- `data-engineering-reviewer`
- `pe-underwriter`
- `coo-dashboard-reviewer`

For an investment-committee pass over the economics, `.claude/skills/` also
provides `/ic-review`.

Then run `/code-review` on the working diff, fix only validated findings, and
re-run `make lint` and `make test` before committing.

## 5. High-leverage interaction pattern

A four-message cycle:

1. **Plan:** "Read the phase spec, inspect the repo, and propose the smallest
   coherent implementation plan. Do not edit."
2. **Build:** "Implement the approved plan only. Add tests before or with the
   code."
3. **Challenge:** "Act as a skeptical IC partner and senior engineer. Find
   incorrect assumptions, reconciliation gaps, and fragile code. Do not edit."
4. **Repair:** "Fix only the validated findings, rerun checks, and show
   evidence."

## 6. What not to do

Avoid prompts such as:

- "Make this production ready."
- "Improve the dashboard."
- "Build the whole PE platform."
- "Use whatever stack is best."

Those create uncontrolled scope. Name the user, decision, files, formulas,
constraints, acceptance tests, and stopping point.

## 7. Session boundaries

Start a new session after each major phase. `CLAUDE.md`, `BACKLOG.md`, the test
suite, and git history preserve state more reliably than one very long
conversation.
