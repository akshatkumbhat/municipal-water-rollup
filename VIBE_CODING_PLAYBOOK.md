# Vibe-Coding Project Copperline with Claude Code

## The operating principle
Do not ask Claude to "build the whole project." Give it one bounded phase, explicit acceptance criteria, and a required verification loop. The goal is fast iteration without losing financial-model integrity or data provenance.

## 1. Start the repository

```bash
unzip tuckers_farm_water_rollup_claude_ready.zip
cd tuckers_farm_water_rollup

git init
git add .
git commit -m "chore: initialize Project Copperline"

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
```

Install and launch Claude Code, then start in read-only plan mode:

```bash
claude --permission-mode plan
```

## 2. First prompt

Paste the contents of `prompts/00_repository_audit.md`. Do not approve implementation until Claude has reproduced the current outputs, identified gaps, and proposed a file-level plan.

After reviewing the plan, use Shift+Tab to move to an edit-enabled mode and say:

```text
Implement the approved Phase 0 and Phase 1 plan only. Preserve the current base-case economics. Run every required check, fix failures, update BACKLOG.md, and stop after reporting evidence of completion.
```

## 3. Phase loop

For each later phase, run:

```text
/implement-phase prompts/02_sourcing_engine.md
```

Then repeat with the next prompt file. Keep each phase in its own git commit:

```bash
git status
git diff --stat
git add .
git commit -m "feat: complete sourcing engine phase"
```

## 4. Review loop

After every meaningful phase:

```text
Use the relevant project subagent to review the implementation independently. Do not edit files. Rank findings by severity and cite exact files and lines.
```

Available reviewers:
- `data-engineering-reviewer`
- `pe-underwriter`
- `coo-dashboard-reviewer`

After fixes, run:

```text
/code-review
/verify
```

If `/verify` cannot infer the application launch, run `/run-skill-generator` once and commit the generated project skill.

## 5. High-leverage interaction pattern

Use this four-message cycle:

1. **Plan:** "Read the phase spec, inspect the repo, and propose the smallest coherent implementation plan. Do not edit."
2. **Build:** "Implement the approved plan only. Add tests before or with the code."
3. **Challenge:** "Act as a skeptical IC partner and senior engineer. Find incorrect assumptions, reconciliation gaps, and fragile code. Do not edit."
4. **Repair:** "Fix only the validated findings, rerun checks, and show evidence."

## 6. What not to do

Avoid prompts such as:
- "Make this production ready."
- "Improve the dashboard."
- "Build the whole PE platform."
- "Use whatever stack is best."

Those prompts create uncontrolled scope. Name the user, decision, files, formulas, constraints, acceptance tests, and stopping point.

## 7. Suggested session boundaries

Start a new Claude Code session after each major phase. The repository's `CLAUDE.md`, backlog, tests, and git history preserve state more reliably than one extremely long conversation.
