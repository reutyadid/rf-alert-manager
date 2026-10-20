# AI Usage

## Tools

| Tool | Used for |
|---|---|
| Claude (chat) | Understanding the domain, comparing design options, drafting DECISIONS.md |
| Claude Code (in Cursor) | Implementing services, tests, Dockerfiles, compose, nginx, VM scripts and workflows, under plan-then-approve; running the stack, the pipeline and the checks |
| Cursor (Tab, chat) | Small manual edits, asking questions about code I was reviewing |

## How I worked

- I made the design decisions before implementation (see DECISIONS.md). AI proposed options;
  I chose and documented the reasoning.
- Project rules and the domain spec live in `CLAUDE.md`, so every AI session follows the same
  decisions; a session that thinks a decision is wrong must stop and say so, not change it.
- Larger changes went through plan mode: I reviewed the plan before any code was written.
- Core-logic tests (min duration, short burst, dedup, auto-resolve) were written before the
  implementation, against in-memory fakes and a fake clock.
- **Parallel sessions:** Part 1 ran as three Claude Code sessions in separate git worktrees
  (`ingest-service`, `alert-service`, `infra`), each allowed to touch only its own folder, with
  the shared event contract fixed in `CLAUDE.md`. The branches were merged into `main`.
- **Guardrails in the repo** (`.claude/`):
  - `settings.json`: allow-list for routine commands; denies force-push, `reset --hard`,
    `rm -rf`, deleting volumes or the VM, and reading `.env`.
  - A hook runs `ruff format` after every AI edit; pre-commit runs ruff and **gitleaks**.
  - Custom commands: `/checkpoint` (lint + tests, commit only if green), `/ai-log` (record an AI
    mistake while it is fresh), `/interview-check` (AI explains what was built and quizzes me).
  - A read-only `reviewer` agent checks the repo against the task and DECISIONS.md at the end of
    each part.
- Every AI mistake was logged when it happened (`/ai-log`); the table below is built from that log.

## Where the AI was wrong and what I changed

| # | Part | What I asked | What the AI did wrong | How I noticed | What I changed |
|---|---|---|---|---|---|
| 1 | Setup | Get Multipass working for a new Windows user (`client is not authenticated`) | Said renaming the daemon's `authenticated-certs` folder and restarting would make Multipass auto-trust the first client | `multipass list` still failed; the new folder was empty, because the existing client cert was never re-registered | Copied the client certificate into the daemon's `authenticated-certs` as admin and restarted the service; `multipass list` worked |
| 2 | Part 1 alert | Plan alert-service before coding | The plan said that after a restart there is no watermark until the first event, so no auto-resolve, and called this "the safe direction" (DECISIONS said the same) | **I caught it reviewing the plan:** an OPEN alert whose emitter disappeared during the downtime never gets another event, so it would never resolve | Required the idle fallback to apply from startup too, plus a test (restart with an OPEN alert, no events, advance the fake clock → resolves). Updated CLAUDE.md and DECISIONS §4.6; verified on a real run |
| 3 | Part 1 ingest | Implement ingest-service, tests first | (a) A test sent a mismatched `rule_id` in the body to `PUT /rules/nope` and expected 404; the API correctly returned 422. (b) A `sed` one-liner also collapsed blank lines in another test file | AI caught both: pytest failed, and the grep output showed the file change | Test fixed to send the matching id (the test was wrong, not the code); file restored with `ruff format`. Lesson for me: check that a fix to a failing test keeps the test's intent and does not weaken the assertion |
| 4 | Part 3 | Build CI with unit and integration tests as separate jobs | Installed both services' requirements into one environment, although they pin different FastAPI versions, and pushed after testing only in my already-mixed local environment | The first CI run on GitHub failed at `pip install` (ResolutionImpossible); Deploy was skipped, so the gate held | CI now runs per service in its own environment (like the images). Comparing test counts afterwards showed 18 simulator tests would have silently dropped out; they were added to the ingest job (231 = 156 + 75). Lesson: test in a clean environment that matches CI, and compare test counts after splitting jobs |
| 5 | Part 4 (bonus) | Add Prometheus + Grafana (login) and two alert-service metrics, test locally, deploy through the pipeline | Tested only with curl on the laptop. (a) `vm-release.sh` never copied `observability/` to the VM; (b) auto-rollback did not cover a failed deploy, only a failed smoke test; (c) nginx had kept its Part 2 config: copied files get a new inode and compose does not recreate an unchanged container, so no nginx change had reached the VM since Part 2; (d) the `/grafana/` location dropped the `Host` header, so Grafana blocked every browser query with "origin not allowed" | (a)–(c): the pipeline failed (deploy, then smoke test; the second one rolled back automatically, a real rollback). (d): **I opened Grafana in the browser** and every panel had no data. The AI's curl checks used basic auth, which skips Grafana's origin check | Copy `observability/`; roll back on deploy failure too, both rollbacks using the old commit's own config; recreate a container when its mounted config changes; pass `Host` to Grafana; the smoke test now logs in like a browser (cookie + Origin) and runs a query. Lesson: test with the real client and the real target (curl ≠ browser, laptop ≠ VM) |

Smaller slips the AI caught itself while working (not logged as separate entries): a VM reboot
check that kept polling the old IP after Hyper-V assigned a new one; nginx `upstream` blocks
that would have kept stale container IPs after a deploy (fixed with `resolve` before the first
deploy); and checking Grafana panels against a container still running the previous
day's image.

## What I did not delegate

- Design decisions and trade-offs (DECISIONS.md), including the ones that changed during the
  work: the restart/watermark fix (#2 above) and keeping the repository private and documenting
  the missing branch protection instead of making it public (DECISIONS §10).
- Reviewing every plan before code was written, and every diff before committing.
- Approving every push to `main` (each one deploys to the VM).
- Registering the self-hosted runner (the token came from my GitHub account) and running the
  rollback demo myself.
- Checking that I can explain every part: `/interview-check` after each part.
