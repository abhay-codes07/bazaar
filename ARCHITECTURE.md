# Bazaar — architecture

One sentence: **the model proposes, deterministic policy verifies, and only then does anything execute** — for every layer that touches money, and for every protocol an AI buyer might speak.

The diagrams live in the [README](README.md) (system, order sequence, compile pipeline, policy gate, protocol fan-out); the threat model in [THREAT_MODEL.md](THREAT_MODEL.md). This document is the layer-by-layer map with the guarantees each layer owns.

## Layers and their guarantees

| Layer | Module | What it guarantees |
|---|---|---|
| Data model | `bazaar/schemas` | Integer paise everywhere; `bazaar.india` extension fields (GST, HSN, pincode serviceability, COD, pack units); all merchant/buyer free text is typed as untrusted data |
| Payments | `bazaar/razorpay_client` | One narrow `PaymentsClient` interface is the only money path. Sandbox client (orders, UPI links, refunds, HMAC webhooks) for offline runs; official-SDK client on **Razorpay test-mode keys** with a standard-link fallback when a fresh account has UPI links disabled; a Reserve-Pay mandate ledger (NPCI OC-228 defaults: ₹10,000 / 90 days) that blocks on grant issue, debits on use, releases on revoke |
| LLM | `bazaar/llm` | Single `complete_json(task, system, user, schema)` contract; OpenAI (gpt-4o, with gpt-4o-mini routed for catalog work) and Anthropic backends; SQLite call cache; **circuit breaker** — on failure the deterministic offline backend answers through the same tools and gate, failovers audited, fallback answers never cached |
| Compiler | `bazaar/compiler` | Parsers own price/stock/GST — the model may only name, categorise and enrich; confidence < 0.8 goes to a merchant review queue, never guessed; injected instructions stripped and flagged; one compile exports Bazaar + UCP manifests, ACP feed, Beckn catalog, llms.txt, JSON-LD; agent-readiness score |
| Seller Agent | `bazaar/seller_agent` | Propose → verify → execute, enforced in code; offers only by merchant-approved `rule_id` (a proposal carrying a value is rejected); bounded observe loop for real-model tool wobble; EN/HI/Hinglish; per-merchant MCP server over stdio |
| Trust Fabric | `bazaar/trust` | Ed25519 agent registry with tiers T0–T3; RFC 9421 request signatures (nonce, skew, browse/pay tags); AP2-shaped digest-chained mandates; scoped payment grants; **policy gate of named machine-readable checks** (incl. a human-present threshold above ₹15,000 per the RBI e-mandate framing); hash-chained audit log + Merkle root + replay; fairness ledger + cohort auditor that gates rule publishing; `trust/uap.py` is the seam where NPCI's UAP binding lands |
| Gateway | `bazaar/gateway` | Discover (deterministic ranking), ACP-shaped session state machine, checkout → Razorpay link → webhook (real payload shapes accepted: entity-wrapped, `payment_link.paid`, link-reference session matching); adapters `/acp` `/ucp` `/beckn`; global `bazaar-catalog` MCP at `/mcp`; merchant-mutating routes gated by `X-Admin-Token`; CORS restricted; refuses to boot in prod on dev secrets |
| Console | `console/` | Vite + React + TS + Tailwind v4; six pages, light/dark, keyboard nav; the playground drives the same signed, mandated path an external agent takes |
| Evidence | `bazaar/simulator`, `bazaar/conformance`, `results/` | 200 tasks with expected outcomes, baseline comparison, false-positive cost sweep; 19-probe hand-written red team **plus** a 190-attack model-generated corpus scored end-to-end; 24-check conformance kit runnable against any live gateway (`python -m bazaar.conformance <url>`); replay CLI; a model-driven buyer; `RESULTS.md` generated, never hand-edited |

## Measured (both committed, both generated)

| | offline engine (`results/`) | live gpt-4o (`results/gpt4o/`) |
|---|---|---|
| 200 buyer tasks | 100% accuracy | 99.0% — both misses were impossible tasks it still refused |
| wrong orders / wrong declines | 0 / 0 | 0 / 0 |
| red team · fairness · conformance | 19/19 hand-written + 190/190 generated · 159,840 cohorts clean · 24/24 | same |
| latency p50 / p95 | 47 / 62 ms (deterministic) | cache hit ≈ offline; a live gpt-4o proposal adds ~1.5–4 s |

91 tests, fully offline, green in CI.

## Honest limitations

- The synthetic corpus is a closed loop (the generator writes both the messy CSVs and the truth labels), so offline compiler accuracy is the parsers' ceiling; the live-model row reports the real exact-match numbers.
- Beckn `on_*` callbacks are returned inline for P0, not POSTed to the BAP; ONDC certification is a later phase.
- State is in-memory behind narrow methods (`gateway/state.py` is the single swap point for Postgres/Redis).
- The Anthropic backend is wired but has not produced committed results.
