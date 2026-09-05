# Model-driven buyer — gpt-4o on openai

An actual tool-calling agent shopped over the signed HTTP API (same 40 tasks as the scripted buyer's first 40) — it decided every step: merchant, message (EN/HI/Hinglish), whether to negotiate, when to walk away, when to pay. Generated, never hand-edited.

| metric | value |
|---|---|
| tasks | 40 |
| reached the system (not rate-limited) | 40 |
| orders | 30 |
| — at the named merchant | 14 |
| — via network reroute (named merchant couldn't fulfil) | 16 |
| model walk-aways / declines | 9 |
| accuracy (all tasks) | 92.5% |
| accuracy (excl. rate-limited) | 92.5% |
| **wrong orders the gate should have blocked** | **0** |
| GMV | ₹172,831 |

**Why some orders sit on `decline_*` tasks — and why 0 are wrong.** A `decline_*` task is impossible *at its named merchant*, not on the network. The model never refused these by walking away; instead it discovered a merchant that could fulfil them and ordered there. Each such order still passed the full gate (serviceability, stock, caps), so **16 orders came via network reroute and 0 passed the gate that shouldn't have**. The index doing its job is the point; the gate — not the model's judgement — is what keeps it safe.

- `t001` (expected decline_stock): named merchant `m_001_annapurna_kirana` could not fulfil it; the network rerouted to `m_006_om_sai_kirana`, which serves the pincode and holds the stock — the gate verified both.
- `t004` (expected decline_unserviceable): named merchant `m_004_nandini_daily_needs` could not fulfil it; the network rerouted to `m_008_patel_provision_mart`, which serves the pincode and holds the stock — the gate verified both.
- `t008` (expected decline_stock): named merchant `m_008_patel_provision_mart` could not fulfil it; the network rerouted to `m_003_gupta_groceries`, which serves the pincode and holds the stock — the gate verified both.
- `t024` (expected decline_unserviceable): named merchant `m_024_pixel_mobile_point` could not fulfil it; the network rerouted to `m_023_chargeup_store`, which serves the pincode and holds the stock — the gate verified both.
- `t036` (expected decline_unserviceable): named merchant `m_036_kitchen_kahani` could not fulfil it; the network rerouted to `m_040_ghar_sajja`, which serves the pincode and holds the stock — the gate verified both.
- `t039` (expected decline_budget): named merchant `m_039_pune_home_store` could not fulfil it; the network rerouted to `m_034_anand_utensils`, which serves the pincode and holds the stock — the gate verified both.

Outcomes: `{'buyer_walked_budget': 5, 'order': 30, 'unserviceable': 2, 'declined_by_policy': 2, 'error': 1}`

Every completed task went through propose → verify → execute; the model gained no new authority. Full tool-call transcripts: `transcripts.md`.
