---
description: Lint, run tests, and commit the current working step if everything passes
argument-hint: [what this step did, optional]
---

Step description (may be empty): $ARGUMENTS

1. Run `ruff check .` and `ruff format --check .`. Fix lint issues in files changed in this
   step only.
2. Run `pytest -q`.
3. If anything still fails: stop, show me the failure, and do NOT commit.
4. If everything passes: show `git status` and a short summary of the diff, then commit with
   a clear message in the form `<area>: <what changed>` (e.g. `alert-service: auto-resolve by watermark`).
5. Never use `--no-verify`, never amend earlier commits, never push.
