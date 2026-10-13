---
description: Append an entry to notes/ai-log.md about where the AI was wrong and what was changed
argument-hint: [short description of what went wrong, optional]
---

Add one entry to `notes/ai-log.md` under "## During implementation".

Context from me (may be empty): $ARGUMENTS

Use the recent conversation to fill in, in one line each:
- time (HH:MM) and which part (Part 1 ingest / Part 1 alert / Part 2 / Part 3 / docs)
- what I asked
- what the AI did
- what was wrong and how I noticed (test failed, review, contradicted DECISIONS.md, etc.)
- what I changed

Rules:
- Be honest and specific. Do not soften the AI's mistake and do not invent one.
- If nothing actually went wrong, write what the AI did well and what I verified instead.
- Append only. Do not edit earlier entries.
- Show me the entry after writing it.
