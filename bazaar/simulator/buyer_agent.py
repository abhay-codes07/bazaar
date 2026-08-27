"""A buyer agent that drives the Bazaar protocol end to end for one task.

It behaves like a careful shopping assistant: discover → open a session with the merchant →
ask for a quote → (optionally) ask for a better price → walk away if over budget → otherwise
confirm, obtain a grant, sign mandates, complete, and pay.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from bazaar.gateway.client import BuyerAgentClient
from bazaar.simulator.tasks import Task


class TaskResult(BaseModel):
    task_id: str
    expected: str
    outcome: str  # order | declined_by_policy | buyer_walked_budget | no_merchant | unserviceable | stock | unknown_item | error
    correct: bool
    merchant_id: str = ""
    session_id: str = ""
    gmv_paise: int = 0
    discount_paise: int = 0
    negotiation_rounds: int = 0
    steps: int = 0
    latency_ms: float = 0.0
    explanations: list[str] = Field(default_factory=list)
    declined_checks: list[str] = Field(default_factory=list)
    rerouted: bool = False
    error: str = ""


def _classify(task: Task, res: TaskResult) -> bool:
    """Was the outcome the *right* one? A task that the named merchant cannot fulfil but that the
    network routes to another merchant who can is a success, not a false order."""
    exp, outcome = task.expected, res.outcome
    rerouted = outcome == "order" and res.merchant_id != task.merchant_id
    if exp == "order":
        return outcome == "order"
    if exp == "decline_unserviceable":
        # the named merchant must never fulfil it; a re-route (order or price walk-away elsewhere) is fine
        return outcome != "order" or rerouted
    if exp == "decline_budget":
        return outcome == "buyer_walked_budget"
    if exp == "decline_stock":
        # any decline is right (stock, or a merchant/agent order cap that trips first); a re-route is fine
        return outcome not in ("order", "error") or rerouted
    if exp == "decline_unknown_item":
        return outcome in ("unknown_item", "no_merchant")
    return False


def run_task(client: BuyerAgentClient, task: Task, state, baseline: bool = False) -> TaskResult:
    """``baseline=True`` simulates a merchant *without* Bazaar: static price list, no
    serviceability answers, no bounded offers — the buyer only proceeds if the listed price
    fits the budget and the merchant is in the buyer's own city."""
    t0 = time.perf_counter()
    res = TaskResult(task_id=task.task_id, expected=task.expected, outcome="error", correct=False, merchant_id=task.merchant_id)
    try:
        r = client.call("POST", "/bazaar/v1/discover", {"intent": task.message, "pincode": task.pincode, "budget_paise": task.budget_paise})
        res.steps += 1
        cands = r.json()["candidates"]
        if not cands:
            res.outcome = "no_merchant"
            return res
        cand = next((c for c in cands if c["merchant_id"] == task.merchant_id), cands[0])
        mid = cand["merchant_id"]
        res.merchant_id = mid
        if baseline:
            m = state.merchants[mid]
            listed = cand["products"][0]["estimated_total_paise"] + m.serviceability.delivery_fee_paise
            same_city = m.base_pincode[:4] == task.pincode[:4]
            if not same_city:
                res.outcome = "unserviceable"
                return res
            if listed > task.budget_paise:
                res.outcome = "buyer_walked_budget"
                return res
        r = client.call("POST", "/bazaar/v1/sessions", {"merchant_id": mid, "message": task.message, "segment": task.segment, "language": task.language})
        res.steps += 1
        if r.status_code != 201:
            res.outcome, res.error = "error", r.text[:200]
            return res
        body = r.json()
        sess = body["session"]
        sid = sess["session_id"]
        res.session_id = sid
        turn = body["turn"]
        res.explanations.append(turn["explanation"])
        if not turn["ok"] or sess["quote"] is None:
            reason = (turn["explanation"] or "").lower()
            if "deliver" in reason or "डिलीवर" in reason:
                res.outcome = "unserviceable"
            elif "available" in reason or "requested" in reason:
                res.outcome = "stock"
            elif "stock that item" in reason or "unknown" in reason:
                res.outcome = "unknown_item"
            else:
                res.outcome = "declined_by_policy"
                res.declined_checks = [c["name"] for c in turn["policy_checks"] if not c["passed"]]
            return res
        quote = sess["quote"]
        if task.negotiate and not baseline and quote["total_paise"] > 0:
            r = client.call("POST", f"/bazaar/v1/sessions/{sid}/messages", {"message": {"en": "any discount?", "hi": "कोई छूट मिलेगी?", "hi-Latn": "koi discount milega?"}[task.language]})
            res.steps += 1
            t = r.json()["turn"]
            res.explanations.append(t["explanation"])
            if t["ok"] and t["action"] == "apply_offer":
                quote = r.json()["session"]["quote"]
                res.negotiation_rounds += 1
        if quote["total_paise"] > task.budget_paise:
            res.outcome = "buyer_walked_budget"
            client.call("POST", f"/bazaar/v1/sessions/{sid}/cancel?reason=over_budget")
            return res
        r = client.pay_call("POST", "/bazaar/v1/grants", {"buyer_ref": f"buyer-{task.task_id}", "merchant_id": mid, "max_amount_paise": task.budget_paise})
        res.steps += 1
        grant_id = r.json()["grant_id"]
        cm, pm = client.mandates_for(quote, mid, f"buyer-{task.task_id}", task.budget_paise)
        r = client.pay_call("POST", f"/bazaar/v1/sessions/{sid}/complete", {"grant_id": grant_id, "checkout_mandate": cm, "payment_mandate": pm, "human_confirmation": True}, idempotency_key=f"{task.task_id}-complete")
        res.steps += 1
        out = r.json()
        if not out.get("allowed"):
            res.outcome = "declined_by_policy"
            res.declined_checks = [c["name"] for c in out.get("checks", []) if not c["passed"]]
            return res
        if out.get("needs_merchant_review"):
            client.http.request("POST", f"/bazaar/v1/merchants/{mid}/review-sessions/{sid}/approve")
            out = {"payment": client.http.request("GET", f"/bazaar/v1/sessions/{sid}").json()}
            order_id = out["payment"]["order_id"]
        else:
            order_id = out["payment"]["order_id"]
        state.payments.simulate_payment(order_id)
        res.steps += 1
        final = client.http.request("GET", f"/bazaar/v1/sessions/{sid}").json()
        if final["status"] == "completed":
            res.outcome = "order"
            res.gmv_paise = quote["total_paise"]
            res.discount_paise = quote["discount_paise"]
        else:
            res.outcome, res.error = "error", f"final status {final['status']}"
        return res
    except Exception as e:  # noqa: BLE001
        res.outcome, res.error = "error", f"{type(e).__name__}: {e}"[:200]
        return res
    finally:
        res.latency_ms = (time.perf_counter() - t0) * 1000
        res.correct = _classify(task, res)
        res.rerouted = res.outcome == "order" and res.merchant_id != task.merchant_id


def summarize(results: list[TaskResult]) -> dict[str, Any]:
    n = len(results)
    orders = [r for r in results if r.outcome == "order"]
    expected_orders = [r for r in results if r.expected == "order"]
    impossible = [r for r in results if r.expected != "order"]
    tp = sum(1 for r in impossible if r.correct)
    fp = sum(1 for r in expected_orders if r.outcome != "order")  # possible tasks we wrongly declined
    fn = sum(1 for r in impossible if r.outcome == "order" and not r.rerouted)  # impossible tasks that became orders at the *named* merchant
    return {
        "tasks": n,
        "orders": len(orders),
        "rerouted_orders": sum(1 for r in orders if r.rerouted),
        "task_to_order_rate": round(len(orders) / max(1, n), 3),
        "possible_tasks": len(expected_orders),
        "possible_completion_rate": round(sum(1 for r in expected_orders if r.outcome == "order") / max(1, len(expected_orders)), 3),
        "gmv_paise": sum(r.gmv_paise for r in orders),
        "discount_paise": sum(r.discount_paise for r in orders),
        "negotiation_rounds": sum(r.negotiation_rounds for r in results),
        "declines": {"precision": round(tp / max(1, tp + fp), 3), "recall": round(tp / max(1, tp + fn), 3), "impossible_tasks": len(impossible), "wrong_orders_on_impossible": fn, "wrong_declines_on_possible": fp},
        "policy_declines": sum(1 for r in results if r.outcome == "declined_by_policy"),
        "errors": sum(1 for r in results if r.outcome == "error"),
        "accuracy": round(sum(r.correct for r in results) / max(1, n), 3),
        "p50_latency_ms": round(sorted(r.latency_ms for r in results)[n // 2], 1) if n else 0,
        "p95_latency_ms": round(sorted(r.latency_ms for r in results)[min(n - 1, int(n * 0.95))], 1) if n else 0,
    }
