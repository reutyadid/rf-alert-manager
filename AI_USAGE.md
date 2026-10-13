# AI Usage

## Tools

| Tool | Used for |
|---|---|
| Claude (chat) | Understanding the domain, comparing design options, drafting DECISIONS.md |
| Claude Code (in Cursor) | Implementing services, tests, Dockerfiles, compose, workflows, under plan-then-approve |
| Cursor (Tab, chat) | Small manual edits, asking questions about code I was reviewing |
| [TODO] | |

## How I worked

- I made the design decisions before implementation (see DECISIONS.md). AI proposed options;
  I chose and documented the reasoning.
- Project rules and the domain spec live in `CLAUDE.md`, so every AI session follows the same decisions.
- Larger changes went through plan mode: I reviewed the plan before any code was written.
- Core-logic tests (min duration, short burst, dedup, auto-resolve) were written before the implementation.
- After each part I asked the AI to quiz me on the code to make sure I can explain it.

## Where the AI was wrong and what I changed

[TODO: built from notes/ai-log.md]

| # | What I asked | What the AI did wrong | How I noticed | What I changed |
|---|---|---|---|---|
| 1 | | | | |

## What I did not delegate

- Design decisions and trade-offs.
- Reviewing every diff before committing.
- [TODO]
