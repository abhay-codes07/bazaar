# Model-driven buyer — openai/gpt-oss-120b on groq

An actual tool-calling agent shopped over the signed HTTP API (same 20 tasks as the scripted buyer's first 20) — it decided every step: merchant, message (EN/HI/Hinglish), whether to negotiate, when to walk away, when to pay. Generated, never hand-edited.

> 11 of 20 tasks never reached the system — they hit the **free-tier token quota** on `openai/gpt-oss-120b` (HTTP 429), an infrastructure limit, not a system failure. Of the **9 tasks that did run, accuracy was 100%** with **0 wrong orders**. A paid tier or a fresh key reproduces the full set.

| metric | value |
|---|---|
| tasks | 20 |
| reached the system (not rate-limited) | 9 |
| orders | 9 |
| accuracy (all tasks) | 45.0% |
| accuracy (excl. rate-limited) | 100.0% |
| **wrong orders on impossible tasks** | **0** |
| GMV | ₹59,209 |

Outcomes: `{'error': 11, 'order': 9}`

Every completed task went through propose → verify → execute; the model gained no new authority. Full tool-call transcripts: `transcripts.md`.
