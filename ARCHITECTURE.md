# Bazaar — architecture & engineering

One sentence, one invariant: **the model proposes, deterministic policy verifies, and only then does anything execute** — for every layer that touches money, and for every protocol an AI buyer might speak.

This document is the engineer's map. It names the real modules, walks one signed order through the code function by function, and states the guarantee each layer owns and the boundary each one refuses to cross. The visual diagrams (system, order sequence, compile pipeline, policy gate, protocol fan-out) live in the [README](README.md); the attack-by-attack analysis in [THREAT_MODEL.md](THREAT_MODEL.md).

---

## 1. The shape

Four products, one spine (`propose → verify → execute`), and a payments rail that is only ever reached after the gate passes:

```
Catalog Compiler ─▶ Seller Agent ─▶ Bazaar Gateway ─▶ Trust Fabric ─▶ Razorpay (test mode)
  Sheet/CSV/photo    propose/verify/   discover, sessions,   signatures, mandates,   orders, UPI links,
  → agent-ready      execute, offers   ACP/UCP/Beckn, MCP    grants, policy, audit    refunds, webhooks
```

Every package is a Python module under `bazaar/`; the merchant console is a Vite/React app under `console/`. Nothing in the money path is a stub with a fake interface — the sandbox and the real Razorpay client implement the *same* narrow `PaymentsClient`, and the offline LLM implements the *same* `complete_json` contract as gpt-4o, so switching a backend never changes the control flow.

---

## 2. One order, end to end (the path that matters)

A tier-2 buyer agent buys 5 kg of basmati. Each arrow is a real call; the function that owns it is named.

1. **Discover** — `POST /bazaar/v1/discover` → `gateway/discover.py`. Ranking is a deterministic function of relevance, serviceability, stock, budget fit, readiness and trust. No model is in this loop; catalog prose cannot reorder it.
2. **Open a session** — `POST /bazaar/v1/sessions` (RFC 9421-signed, tag `agent-browse`). `gateway/auth.py::identify` verifies the signature → a `Caller(keyid, tier)`. The **pricing segment is derived server-side** (`app.py::server_segment`) from facts Bazaar owns — an untrusted caller cannot self-declare `b2b` or `new`.
3. **Propose** — `seller_agent/agent.py::handle` runs the turn. `propose.py::propose` asks the model for ONE `Proposal {tool, args, rule_id}`; buyer and catalog text are wrapped by `llm/base.py::wrap_untrusted` inside `<data>` blocks. The model may name a tool and a `rule_id` — never a price.
4. **Verify** — `agent.py` normalises model arg aliases, then the offer/quote maths run in **integer paise** in `seller_agent/offer_engine.py::build_quote`. A proposal that carries its own number is rejected (`rule_not_invented`).
5. **Grant + mandates** — `POST /bazaar/v1/grants` (tag `agent-pay`) issues a `ScopedPaymentGrant` (`trust/grants.py`), capped at the agent's tier ceiling; the buyer signs AP2-shaped Checkout and Payment mandates (`trust/mandates.py`), closed to this exact quote.
6. **Complete** — `POST /bazaar/v1/sessions/{id}/complete` + `Idempotency-Key`. The signature is verified **before** any cached payload is served (`app.py::complete`). `gateway/checkout.py::complete_session` reserves stock and the grant's pending amount, then runs the gate.
7. **The gate** — `trust/policy.py::check_checkout` runs the named checks (kill switch first). Any failure → `422 {reason, checks[]}`, no side effect, session stays retryable.
8. **Execute** — only now does `razorpay_client/*` create a UPI payment link. `gateway/state.py::handle_webhook_event` accepts `payment.captured` **only** for an in-progress session at the exact amount, converts the grant's pending → used, writes the fairness-ledger row and the audit entry.

If the model is down at step 3, the circuit breaker (`llm/resilience.py`) answers steps 3–4 from the deterministic backend through the same tools and the same gate. Steps 5–8 never involve a model at all.

---

## 3. Package by package

| Package | Files | What it owns / guarantees |
|---|---|---|
| **schemas** | `models.py` | Every money value is integer paise; the `bazaar.india` extension fields (GST, HSN, pincode serviceability, COD, pack units); all merchant/buyer free text is typed as untrusted data. One source of truth for the whole system. |
| **compiler** | `ingest · sanitize · normalize · enrich · exports · readiness · evaluate · heldout` | Parsers own price/stock/GST — the model may only name, categorise and enrich. `sanitize.py` strips instruction-like text at compile and flags it. Confidence < 0.8 → merchant review queue, never guessed. One compile emits Bazaar + UCP manifests, ACP feed, Beckn catalog, llms.txt, JSON-LD (`exports.py`) and an agent-readiness score (`readiness.py`). `evaluate.py`/`heldout.py` score field accuracy on the synthetic corpus and on hand-written held-out sheets. |
| **llm** | `base · fake · openai_client · anthropic_client · cache · resilience` | Single `complete_json(task, system, user, schema)` contract. `resilience.py` (circuit breaker) wraps `cache.py` (SQLite call cache) wraps a backend, delegating unknown attributes through. `fake.py` is deterministic and doubles as the model-down fallback; fallback answers are never cached. `openai_client.py` carries per-model token counters so cost is *measured*, not claimed. |
| **seller_agent** | `intent · offer_engine · propose · agent · tools · explain · rto · mcp_server` | `propose → verify → execute` enforced in code. `intent.py` is a conservative EN/HI/Hinglish parser (also the model-down fallback). `offer_engine.py` computes every rupee deterministically; offers apply only by merchant-approved `rule_id`; stacked discounts are floored at the subtotal. `agent.py` runs a bounded observe→re-propose loop (max 3 steps) so real-model tool wobble is corrected before the gate. `mcp_server.py` exposes one per-merchant MCP server over stdio. |
| **trust** | `registry · http_sig · keys · mandates · grants · policy · ledger · audit · fairness_auditor · uap` | The Trust Fabric (§4). Ed25519 registry with tiers T0–T3; RFC 9421 seven-step verify; AP2-shaped digest-chained mandates; scoped, single-use grants with a pending-reservation ledger; the policy gate; a hash-chained audit log with a Merkle root and replay; a fairness ledger + cohort auditor that gates rule publishing. `uap.py` is the seam where NPCI's Unified Agent Protocol binding lands. |
| **gateway** | `app · auth · discover · sessions · checkout · state · client · playground · catalog_mcp · adapters/{acp,ucp,beckn}` | Discover (deterministic), an ACP-shaped session state machine, checkout → Razorpay link → webhook (real payload shapes accepted). Adapters `/acp` `/ucp` `/beckn` and a global `bazaar-catalog` MCP at `/mcp`. Merchant-mutating routes gated by `X-Admin-Token`; `BAZAAR_ENV=prod` refuses to boot on dev secrets. |
| **razorpay_client** | `base · fake · real · reserve_pay` | One narrow `PaymentsClient` is the only money path. `fake.py` is a full in-memory sandbox (orders, UPI links, refunds, HMAC webhooks); `real.py` runs on Razorpay test-mode keys with a standard-link fallback when a fresh account has UPI links disabled; `reserve_pay.py` is the blocked-funds mandate ledger (NPCI OC-228 defaults ₹10,000 / 90 days). |
| **simulator / synthetic / conformance** | `run · tasks · buyer_agent · model_buyer · redteam · redteam_gen` · `corpus` · `checks` | The evidence pipeline (§8). Generates the 52-merchant corpus and the 200-task suite, runs a scripted buyer and a real tool-calling model buyer, two red teams, the fairness audit, and the 24-check conformance kit — all into `results/`, generated, never hand-edited. |
| **console** | `App · api · store · pages/{Overview,Catalog,Offers,Sessions,Audit,Playground}` | Vite + React + TS + Tailwind v4. Six pages, light/dark, keyboard nav. The playground drives the *same* signed, mandated path an external agent takes; a "Model down" toggle exercises the circuit breaker live; the compiler falls back to a token-free preview so a judge can try it without credentials. |

---

## 4. The Trust Fabric in detail

This is the product. Everything financial is deterministic and auditable.

- **Identity** (`http_sig.py`, `keys.py`, `registry.py`) — every buyer request is an RFC 9421 HTTP Message Signature over Ed25519. `verify_request` runs seven named steps in order (headers → key → timestamp → nonce → tag → base → signature) and fails closed on malformed input. The registry issues `keyid`s and tiers T0–T3; re-registering an existing key is idempotent (no tier reset), and an admin `revoke` cuts a compromised key off immediately — a revoked key's public key is withheld, so its next signature fails at the `key` step.
- **Mandates** (`mandates.py`) — AP2-shaped Checkout and Payment mandates, `open → closed`, signed over a canonical digest by the buyer key. The gate checks signature, stage, expiry, merchant, quote binding and amount equality.
- **Grants** (`grants.py`) — merchant-scoped, amount-capped, time-boxed, revocable, single-use, every use evented (`grant.used` / `grant.revoked`). A `pending` reservation is taken at checkout and converted to `used` on capture, so the TOCTOU window between checkout and capture cannot be double-spent. A grant cannot be issued above the agent's own tier ceiling.
- **The policy gate** (`policy.py::check_checkout`) — a sequence of named, machine-readable checks, kill-switch first, each emitting a `Check(name, passed, detail)` onto the audit trail. The exact count depends on the cart: signature, tier, grant scope, both mandates, stock, pincode, per-order caps, kill switch, COD/RTO, `total ≥ ₹1`, and a human-present threshold (default ₹15,000, per the RBI e-mandate framing). Any failure is a graceful decline with a reason — never a partial financial action.
- **Fairness** (`ledger.py`, `fairness_auditor.py`) — every applied offer logs `(rule_id, version, segment_predicate, inputs_hash, output)`. The auditor replays cohorts that differ only in irrelevant attributes and blocks a rule set that produces different outputs; publishing a rule set that fails the audit is refused.
- **Audit** (`audit.py`) — append-only JSONL, SHA-256 hash chain, Merkle root, `replay` endpoint and CLI. Chain-field keys are reserved so a caller cannot corrupt the chain by passing `seq`/`prev`.

## 5. Input authenticity — the boundary the gate cannot see

A signature proves *who* sent a request; it does not prove that what they *claimed about the buyer* is true. Bazaar derives the facts it can, and documents the ones it cannot yet:

- **Pricing segment is derived, not declared** (`app.py::server_segment`) — `b2b` requires a merchant/admin designation; `new` vs `returning` follows the agent's completed-order history; `any` grants nothing extra. Only the merchant's own admin-authenticated console may set a segment directly.
- **Grant amount is bounded by tier**, not by the caller's ask.
- **Session ownership** — a signed session may only be driven or cancelled by its owning key; an unsigned (T0) session, which has no owner, may only be cancelled by an admin.
- **Documented demo shortcuts** (see THREAT_MODEL §"What this does not cover") — the buyer key that signs mandates is agent-asserted until a Login-with-Razorpay / UPI binding lands, and `human_present` above the threshold is a self-asserted flag until it is a buyer-signed or AFA token. These are named honestly rather than hidden, and the mandate layer is isolated (`trust/`) so the production binding is an adapter, not a rewrite.

## 6. Where a model runs — and where it is forbidden

| Job | Model | Fallback |
|---|---|---|
| propose (intent → tool + rule id) | gpt-4o, or gpt-oss-120b on Groq | deterministic intent parser (circuit breaker) |
| catalog normalise + enrich | gpt-4o-mini (routed) | curated dictionary |
| rate-card photo transcription | gpt-4o vision | none — transcribes nothing rather than inventing rows |
| **quote maths, GST, discounts** | **never** | — |
| **merchant ranking** | **never** | — |
| **policy gate, refunds, fairness audit** | **never** | — |

Money math, ranking and the gate are pure Python. The model only ever gains new kinds of *proposals*, never new authority — the same guarantee on HTTP and over MCP, because the MCP side-effect tools run the same gate.

## 7. Failure handling

Designed and tested, not an apology. `llm/resilience.py` skips the primary after 3 consecutive failures and answers from the offline backend (quotes, serviceability, checkout keep working; negotiation degrades to the best pre-approved rule; every failover is audited). Payment failure keeps the session retryable on the same link; a stock race re-quotes; a duplicate `complete` returns the original result via the caller-scoped idempotency cache; a forged webhook fails HMAC; the kill switch refuses new actions instantly. `POST /bazaar/v1/dev/chaos {"model_down": true}` (or the console toggle) demonstrates all of it live.

## 8. State & the single swap point

All state lives behind the narrow methods of `gateway/state.py::BazaarState` — sessions, grants, nonces, reservations, idempotency, registry, audit. It is in-memory for P0; that one file is the swap point for Postgres/Redis in Phase 1, and no caller reaches around it. This is why "distributed state" is a config change, not a rewrite.

## 9. Evidence pipeline

`python -m bazaar.simulator.run` regenerates `results/` and nothing else writes it. It runs the 200-task suite on the offline engine and on live gpt-4o, the false-positive sweep (three tighter caps), the 19-probe hand-written red team, the 190-attack model-generated red team, the fairness audit and the conformance kit. `results/RESULTS.md` and `results/gpt4o/RESULTS.md` carry a Provenance section (cache hit/miss, model-failover count, measured ₹/order). A CI test (`tests/test_results_consistency.py`) fails the build if any headline number in the README drifts from the generated JSON.

---

## Measured (both committed, both generated)

| | offline engine (`results/`) | live gpt-4o (`results/gpt4o/`) |
|---|---|---|
| 200 buyer tasks | 100% accuracy | 99.0% — both misses were impossible tasks it still refused |
| wrong orders / wrong declines | 0 / 0 | 0 / 0 |
| red team · fairness · conformance | 19/19 hand-written + 190/190 generated · 159,840 cohorts clean · 24/24 | same |
| model cost per completed order | — (no model) | **₹3.37** on gpt-4o (cold-cache probe, `results/gpt4o_costprobe/`) |
| latency p50 / p95 | 47 / 62 ms (deterministic) | cache hit ≈ offline; a live gpt-4o proposal adds ~1.5–4 s |

96 tests, fully offline, green in CI. Lint clean (`ruff`), console typechecks (`tsc`) and builds (`vite`).

## Honest limitations

- The synthetic corpus is a closed loop (the generator writes both the messy CSVs and the truth labels), so offline compiler accuracy is the parsers' ceiling; the live-model row reports the real exact-match numbers, and three hand-written held-out sheets test the parsers on data nobody tuned for.
- Beckn `on_*` callbacks are returned inline for P0, not POSTed to the BAP; ONDC certification is a later phase.
- State is in-memory behind narrow methods (`gateway/state.py` is the single swap point for Postgres/Redis).
- Buyer-key binding and human-present authentication are demo shortcuts, named in the threat model; the injection sanitiser is regex-first and English-leaning (Phase-1 hardening: NFKC normalisation + a classifier pass).
- The Anthropic backend is wired but has not produced committed results.
