# AI log (raw notes, turned into AI_USAGE.md at the end)

Format: time | part | what I asked | what the AI did | wrong? how I noticed | what I changed

## Before implementation (design session in Claude chat)
- Used Claude chat to understand the domain (RF monitoring, dBm, persistence, event time) through an analogy.
- AI proposed options for each open question; I chose: gap-based persistence with optional `max_gap_s`, watermark for late events, single consumer, pending state in memory, YAML as seed only, ingest + consumer idempotency.
- I wanted to split alerts for the same frequency at different sites. We compared per-sensor, zone mapping and geo clustering; after checking the task (location is not used by any rule or endpoint) I kept a single `compute_signal_key` function and documented zones as a future improvement.

## During implementation
