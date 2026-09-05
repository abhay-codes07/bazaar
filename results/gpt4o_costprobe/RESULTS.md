# Bazaar — measured results

Generated 2026-09-05T11:57:45Z by `python -m bazaar.simulator.run` (v0.1.0, llm=`openai`, payments=`fake`). Nothing here is hand-edited.

## Catalog compiler (20 merchants, messy CSV → agent-readable catalog)

| field | accuracy |
|---|---|
| name | 0.811 |
| price | 1.000 |
| unit | 0.966 |
| pack_size | 0.948 |
| category | 0.917 |
| gst | 0.925 |
| stock | 0.891 |

Review rate 0.230 (items queued for the merchant instead of guessed) · injections neutralised **10/10** · readiness mean 96.3 (min 91).

**Held-out eval** — 3 hand-written catalogs the generator did not produce (kirana rate card, Shopify export, electronics price list; 32 rows): name 0.500 · price 1.000 · unit 0.969 · pack_size 0.938 · stock 1.000 · gst 1.000 · review rate 0.719. Cells the source doesn't state (e.g. GST on a Shopify export) are review-queued, never guessed.

## Transactions (40 buyer tasks, 25 possible / 15 impossible by construction)

| metric | Bazaar | ablation: same catalog & index, negotiation off, same-city filter, no serviceability answers |
|---|---|---|
| orders | **27** | 23 |
| task → order | 67.5% | 57.5% |
| completion on possible tasks | 100.0% | 84.0% |
| GMV | **₹62,802** | ₹54,230 |
| discounts given (all rule-bounded) | ₹802 | — |
| negotiation rounds | 9 | 0 |

Lift: **+4 orders, +₹8,572 GMV (1.16×)**.

**4 orders (₹8,895) could not have happened at all without Bazaar** — 3 needed a bounded offer, 1 needed a other; 50% of them arrived in Hindi or Hinglish. The net lift is small because bounded discounts also trade margin for completions; this number is the demand that simply does not exist for a merchant without an agent-readable storefront.

Declines on impossible tasks — precision 1.000, recall 1.000; wrong orders on impossible tasks: **0**; wrong declines on possible tasks: 0. Overall task accuracy 100.0%. Errors: 0.

By language: hi-Latn 100.0%, en 100.0%, hi 100.0%. Latency p50 1689.5 ms · p95 4103.8 ms (in-process, llm=`openai`).

## Trust

- Audit entries 258, hash chain intact: **True**, Merkle root `b843402f86485d8e…`
- Explanations present on 100.0% of agent turns
- Grants issued 27, used 27; fairness-ledger entries 9, inconsistencies **0**

## False-positive cost — policy strictness sweep

Same tasks, tighter merchant per-order cap. Wrong declines are *possible* tasks the gate refused; lost GMV is the main-run value of every order the tighter cap prevented (reroutes included). The first row is the default cap and must match the table above.

| per-order cap | orders | wrong declines on possible tasks | lost GMV | wrong orders on impossible tasks |
|---|---|---|---|---|
| ₹50,000 (default) | 27 | **0** | ₹0 | 0 |
| ₹10,000 (Reserve Pay block) | 25 | **2** | ₹36,120 | 0 |
| ₹5,000 | 24 | **3** | ₹42,524 | 0 |
| ₹2,000 | 23 | **4** | ₹46,540 | 0 |

## Provenance

Backend `openai` (model `gpt-4o`). Model failovers to the deterministic fallback during this run: **0** — so the model itself produced these results (health: degraded=False, failures=0).

**Cost.** The live calls this run used 561,032 tokens (₹90.87 at listed gpt-4o rates), which is **₹3.37 per completed order** — quote maths, ranking and the policy gate never call a model, so only the seller's propose step costs anything, and it drops ~10–15× on gpt-4o-mini (or to zero on the free gpt-oss backend). (A warm-cache re-generation bills only the calls that changed, so its per-order figure is lower than this cold-cache measurement.)

_Elapsed 573.3 s._
