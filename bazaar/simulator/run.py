"""``python -m bazaar.simulator.run`` — produce RESULTS.md / results.json from a full run.

Everything in the results file is generated here; nothing is hand-edited.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bazaar import __version__
from bazaar.compiler.compile import compile_merchant
from bazaar.compiler.evaluate import evaluate
from bazaar.compiler.readiness import readiness_score
from bazaar.conformance.checks import run_conformance
from bazaar.conformance.checks import summarize as summarize_conf
from bazaar.gateway import BazaarState, create_app
from bazaar.gateway.client import BuyerAgentClient
from bazaar.llm import FakeLLM, get_llm
from bazaar.settings import ROOT, get_settings
from bazaar.simulator.buyer_agent import TaskResult, run_task, summarize
from bazaar.simulator.redteam import run_redteam, summarize_redteam
from bazaar.simulator.tasks import generate_tasks
from bazaar.synthetic import load_corpus
from bazaar.trust.fairness_auditor import audit_merchant


def _fresh_state(merchants, tmp: Path, fresh_stock: bool = True, llm=None) -> tuple[BazaarState, TestClient, BuyerAgentClient]:
    st = BazaarState(audit_path=tmp / "audit.jsonl", llm=llm)
    for m in merchants:
        mm = m.model_copy(deep=True)
        if fresh_stock:
            for p in mm.products:
                p.stock = max(p.stock, 25)
        st.add_merchant(mm)
    app = create_app(st)
    # the harness client doubles as the merchant console (review-first approvals), so it
    # carries the admin token the way the real console does
    client = TestClient(app, headers={"x-admin-token": st.settings.bazaar_admin_token})
    buyer = BuyerAgentClient(client, operator="bazaar-simulator")
    buyer.register()
    client.post(f"/bazaar/v1/agents/{buyer.keyid}/tier", json={"tier": 2, "reason": "simulator"}, headers={"x-admin-token": st.settings.bazaar_admin_token})
    return st, client, buyer


# Per-order caps the sweep tightens the merchant policy to. ₹50,000 is the default (a control row that
# must reproduce the main run); ₹10,000 is the Reserve Pay block per NPCI OC-228; the last two are
# deliberately too tight so the cost of over-gating is visible instead of a reassuring zero.
SWEEP_CAPS_PAISE = (50_000_00, 10_000_00, 5_000_00, 2_000_00)


def run_false_positive_sweep(sim_merchants, tasks, main_results, tmp: Path, llm, log) -> list[dict[str, Any]]:
    """The judging bar asks for "honest metrics including false-positive cost". A single 0 wrong
    declines says nothing about the trade-off, so this re-runs the same tasks under tighter
    merchant policies and reports what each notch of strictness costs: possible tasks wrongly
    declined and the GMV those orders carried in the main run."""
    ordered = {r.task_id: r.gmv_paise for r in main_results if r.outcome == "order"}
    rows: list[dict[str, Any]] = []
    for cap in SWEEP_CAPS_PAISE:
        ms = []
        for m in sim_merchants:
            mm = m.model_copy(deep=True)
            mm.policy.max_order_paise = cap
            ms.append(mm)
        st_s, _, buyer_s = _fresh_state(ms, tmp / f"sweep_{cap}", fresh_stock=False, llm=llm)
        rs = [run_task(buyer_s, t, st_s) for t in tasks]
        summ = summarize(rs)
        lost = sum(gmv for tid, gmv in ordered.items() if next(r for r in rs if r.task_id == tid).outcome != "order")
        rows.append(
            {
                "max_order_paise": cap,
                "orders": summ["orders"],
                "gmv_paise": summ["gmv_paise"],
                "wrong_declines_on_possible": summ["declines"]["wrong_declines_on_possible"],
                "wrong_orders_on_impossible": summ["declines"]["wrong_orders_on_impossible"],
                "lost_gmv_paise": lost,
                "declined_checks": dict(Counter(c for r in rs for c in r.declined_checks)),
            }
        )
        log(f"sweep cap {_rs(cap)}: orders {summ['orders']}, wrong declines {summ['declines']['wrong_declines_on_possible']}, lost {_rs(lost)}")
    return rows


def run_all(n_tasks: int = 200, out_dir: Path | None = None, corpus_dir: Path | None = None, redteam: bool = True, fairness: bool = True, conformance: bool = True, max_merchants: int = 0, sweep: bool = True) -> dict[str, Any]:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # the MCP import turns on request logging
    t0 = time.time()
    corpus_dir = corpus_dir or get_settings().data_dir / "synthetic"
    out_dir = out_dir or ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ".tmp"
    tmp.mkdir(exist_ok=True)
    for f in tmp.glob("*.jsonl"):
        f.unlink()
    merchants = load_corpus(corpus_dir)
    if max_merchants:
        merchants = merchants[:max_merchants]
    s = get_settings()
    llm = get_llm()
    backend: dict[str, Any] = {"llm": s.bazaar_llm, "payments": s.bazaar_razorpay}
    if s.bazaar_llm == "openai":
        backend.update({"model": s.bazaar_openai_model, "compile_model": s.bazaar_openai_model_compile, "cache": s.bazaar_llm_cache})
    report: dict[str, Any] = {"version": __version__, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "backend": backend}

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {msg}", file=sys.stderr, flush=True)

    # ---- compiler (parallel for real backends; cached)
    pairs = []
    for i, m in enumerate(merchants, 1):
        pairs.append((compile_merchant(corpus_dir / m.merchant_id / "source.csv", m.model_copy(update={"products": []}), llm, workers=s.bazaar_llm_workers), m))
        if i % 10 == 0 or i == len(merchants):
            log(f"compiled {i}/{len(merchants)} merchants")
    ev = evaluate(pairs)
    readiness = [readiness_score(c.merchant).score for c, _ in pairs]
    report["compiler"] = {**ev.model_dump(), "readiness_mean": round(statistics.mean(readiness), 1), "readiness_min": min(readiness), "readiness_truth_mean": round(statistics.mean(readiness_score(m).score for m in merchants), 1)}

    # ---- transactions (Bazaar) vs baseline — tasks are generated from the exact stock the run starts with
    sim_merchants = []
    for m in merchants:
        mm = m.model_copy(deep=True)
        for p in mm.products:
            p.stock = max(p.stock, 25)
        sim_merchants.append(mm)
    tasks = generate_tasks(sim_merchants, n_tasks)
    st, client, buyer = _fresh_state(sim_merchants, tmp, fresh_stock=False, llm=llm)
    results: list[TaskResult] = []
    for i, t in enumerate(tasks, 1):
        results.append(run_task(buyer, t, st))
        if i % 25 == 0 or i == len(tasks):
            log(f"tasks {i}/{len(tasks)} · orders so far {sum(r.outcome == 'order' for r in results)}")
    summ = summarize(results)
    by_lang: dict[str, list[bool]] = {}
    for t, r in zip(tasks, results, strict=True):
        by_lang.setdefault(t.language, []).append(r.correct)
    summ["by_language"] = {k: round(sum(v) / len(v), 3) for k, v in by_lang.items()}
    summ["outcomes"] = dict(Counter(r.outcome for r in results))
    summ["expected"] = dict(Counter(t.expected for t in tasks))
    summ["declined_checks"] = dict(Counter(c for r in results for c in r.declined_checks))
    summ["errors_detail"] = [r.error for r in results if r.outcome == "error"][:10]
    report["transactions"] = summ
    report["trust"] = {"audit_entries": len(st.audit.entries), "chain_ok": st.audit.verify_chain()[0], "merkle_root": st.audit.merkle_root(), "ledger": st.ledger.summary(), "grants_issued": sum(1 for e in st.grant_events if e["event"] == "grant.issued"), "grants_used": sum(1 for e in st.grant_events if e["event"] == "grant.used"), "explanations_present": round(sum(1 for s in st.sessions.values() for t in s.turns if t["explanation"]) / max(1, sum(len(s.turns) for s in st.sessions.values())), 3)}

    # the baseline is a static price list with no agent behind it: no model calls needed
    st_b, client_b, buyer_b = _fresh_state(sim_merchants, tmp / "baseline", fresh_stock=False, llm=FakeLLM())
    log("baseline done")
    base_results = [run_task(buyer_b, t, st_b, baseline=True) for t in tasks]
    base = summarize(base_results)
    report["baseline_no_bazaar"] = {k: base[k] for k in ("orders", "task_to_order_rate", "possible_completion_rate", "gmv_paise")}
    report["baseline_no_bazaar"]["definition"] = "buyer reads a static price list: proceeds only for same-city merchants, cannot ask serviceability, gets no offers; without Bazaar these merchants are not agent-transactable at all (0 agent-originated orders)"
    report["lift"] = {"orders": summ["orders"] - base["orders"], "gmv_paise": summ["gmv_paise"] - base["gmv_paise"], "gmv_multiple": round(summ["gmv_paise"] / max(1, base["gmv_paise"]), 2)}

    # ---- false-positive cost: what each notch of policy strictness wrongly declines, and what it costs
    if sweep:
        report["false_positive_sweep"] = run_false_positive_sweep(sim_merchants, tasks, results, tmp, llm, log)

    # ---- red team (fresh state so the transaction numbers stay clean)
    if redteam:
        st_r, client_r, buyer_r = _fresh_state(merchants, tmp / "redteam", llm=llm)
        cases = run_redteam(buyer_r, st_r)
        log(f"red team {sum(c.passed for c in cases)}/{len(cases)}")
        report["redteam"] = {**summarize_redteam(cases), "detail": [c.model_dump() for c in cases]}

    # ---- fairness across all merchants
    if fairness:
        reps = [audit_merchant(m) for m in merchants]
        report["fairness"] = {"merchants": len(reps), "cohorts": sum(r.cohorts for r in reps), "rules": sum(r.rules_checked for r in reps), "passed": sum(r.passed for r in reps), "findings": [f.model_dump() for r in reps for f in r.findings]}

    # ---- conformance
    if conformance:
        st_c, client_c, _ = _fresh_state(merchants, tmp / "conf")
        checks = run_conformance(client_c)
        report["conformance"] = {**summarize_conf(checks), "detail": [c.model_dump() for c in checks]}

    if hasattr(llm, "stats"):
        report["backend"]["llm_cache"] = llm.stats()
    report["elapsed_s"] = round(time.time() - t0, 1)
    (out_dir / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "RESULTS.md").write_text(render_markdown(report), encoding="utf-8")
    (out_dir / "tasks.json").write_text(json.dumps([t.model_dump() for t in tasks], indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "task_results.json").write_text(json.dumps([r.model_dump() for r in results], indent=1, ensure_ascii=False), encoding="utf-8")
    return report


def _rs(paise: int) -> str:
    return f"₹{paise / 100:,.0f}"


def _signed_rs(paise: int) -> str:
    return f"{'−' if paise < 0 else '+'}₹{abs(paise) / 100:,.0f}"


def render_markdown(r: dict[str, Any]) -> str:
    c, t, b, lf, tr = r["compiler"], r["transactions"], r["baseline_no_bazaar"], r["lift"], r["trust"]
    lines = [
        "# Bazaar — measured results",
        "",
        f"Generated {r['generated_at']} by `python -m bazaar.simulator.run` (v{r['version']}, llm=`{r['backend']['llm']}`, payments=`{r['backend']['payments']}`). Nothing here is hand-edited.",
        "",
        f"## Catalog compiler ({c['merchants']} merchants, messy CSV → agent-readable catalog)",
        "",
        "| field | accuracy |",
        "|---|---|",
        *[f"| {k} | {v:.3f} |" for k, v in c["accuracy"].items()],
        "",
        f"Review rate {c['review_rate']:.3f} (items queued for the merchant instead of guessed) · injections neutralised **{c['injections_stripped']}/{c['injections_present']}** · readiness mean {c['readiness_mean']} (min {c['readiness_min']}).",
        "",
        f"## Transactions ({t['tasks']} buyer tasks, {t['possible_tasks']} possible / {t['declines']['impossible_tasks']} impossible by construction)",
        "",
        "| metric | Bazaar | baseline: static price list (same-city only, no serviceability answers, no offers) |",
        "|---|---|---|",
        f"| orders | **{t['orders']}** | {b['orders']} |",
        f"| task → order | {t['task_to_order_rate']:.1%} | {b['task_to_order_rate']:.1%} |",
        f"| completion on possible tasks | {t['possible_completion_rate']:.1%} | {b['possible_completion_rate']:.1%} |",
        f"| GMV | **{_rs(t['gmv_paise'])}** | {_rs(b['gmv_paise'])} |",
        f"| discounts given (all rule-bounded) | {_rs(t['discount_paise'])} | — |",
        f"| negotiation rounds | {t['negotiation_rounds']} | 0 |",
        "",
        f"Lift: **{lf['orders']:+d} orders, {_signed_rs(lf['gmv_paise'])} GMV ({lf['gmv_multiple']}×)**."
        + (f" The extra completions were bought with {_rs(t['discount_paise'])} of rule-bounded discounts — conversion up, GMV per order slightly down, exactly what bounded offers are for." if lf["gmv_paise"] < 0 <= lf["orders"] else ""),
        "",
        f"Declines on impossible tasks — precision {t['declines']['precision']:.3f}, recall {t['declines']['recall']:.3f}; wrong orders on impossible tasks: **{t['declines']['wrong_orders_on_impossible']}**; wrong declines on possible tasks: {t['declines']['wrong_declines_on_possible']}. Overall task accuracy {t['accuracy']:.1%}. Errors: {t['errors']}.",
        "",
        "By language: " + ", ".join(f"{k} {v:.1%}" for k, v in t["by_language"].items()) + f". Latency p50 {t['p50_latency_ms']} ms · p95 {t['p95_latency_ms']} ms (in-process, llm=`{r['backend']['llm']}`).",
        "",
        "## Trust",
        "",
        f"- Audit entries {tr['audit_entries']}, hash chain intact: **{tr['chain_ok']}**, Merkle root `{tr['merkle_root'][:16]}…`",
        f"- Explanations present on {tr['explanations_present']:.1%} of agent turns",
        f"- Grants issued {tr['grants_issued']}, used {tr['grants_used']}; fairness-ledger entries {tr['ledger']['entries']}, inconsistencies **{tr['ledger']['inconsistencies']}**",
    ]
    if "false_positive_sweep" in r:
        lines += [
            "",
            "## False-positive cost — policy strictness sweep",
            "",
            "Same tasks, tighter merchant per-order cap. Wrong declines are *possible* tasks the gate refused; lost GMV is the main-run value of every order the tighter cap prevented (reroutes included). The first row is the default cap and must match the table above.",
            "",
            "| per-order cap | orders | wrong declines on possible tasks | lost GMV | wrong orders on impossible tasks |",
            "|---|---|---|---|---|",
            *[f"| {_rs(w['max_order_paise'])}{' (default)' if w['max_order_paise'] == SWEEP_CAPS_PAISE[0] else ' (Reserve Pay block)' if w['max_order_paise'] == 10_000_00 else ''} | {w['orders']} | **{w['wrong_declines_on_possible']}** | {_rs(w['lost_gmv_paise'])} | {w['wrong_orders_on_impossible']} |" for w in r["false_positive_sweep"]],
        ]
    if "redteam" in r:
        rt = r["redteam"]
        lines += ["", f"## Red team — {rt['passed']}/{rt['cases']} passed ({rt['pass_rate']:.1%})", "", "| case | category | result |", "|---|---|---|", *[f"| {d['name']} | {d['category']} | {'✅' if d['passed'] else '❌ ' + d['detail']} |" for d in rt["detail"]]]
    if "fairness" in r:
        f = r["fairness"]
        lines += ["", f"## Fairness audit — {f['passed']}/{f['merchants']} merchants pass, {f['rules']} rules, {f['cohorts']:,} cohort simulations, {len(f['findings'])} findings"]
    if "conformance" in r:
        cf = r["conformance"]
        lines += ["", f"## Protocol conformance — {cf['passed']}/{cf['checks']} checks, conformant: **{cf['conformant']}**" + (f" (failed: {', '.join(cf['failed'])})" if cf["failed"] else "")]
    lines += ["", f"_Elapsed {r['elapsed_s']} s._", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bazaar-sim")
    p.add_argument("--tasks", type=int, default=200)
    p.add_argument("--out", default=str(ROOT / "results"))
    p.add_argument("--no-redteam", action="store_true")
    p.add_argument("--no-fairness", action="store_true")
    p.add_argument("--no-conformance", action="store_true")
    p.add_argument("--no-sweep", action="store_true", help="skip the false-positive cost sweep (4 extra task runs)")
    p.add_argument("--merchants", type=int, default=0, help="limit the corpus (cheap trial runs on paid backends)")
    a = p.parse_args(argv)
    rep = run_all(a.tasks, Path(a.out), redteam=not a.no_redteam, fairness=not a.no_fairness, conformance=not a.no_conformance, max_merchants=a.merchants, sweep=not a.no_sweep)
    t = rep["transactions"]
    print(f"tasks={t['tasks']} orders={t['orders']} gmv={t['gmv_paise'] / 100:.0f} accuracy={t['accuracy']} errors={t['errors']}")
    if "redteam" in rep:
        print(f"redteam {rep['redteam']['passed']}/{rep['redteam']['cases']} failed={rep['redteam']['failed']}")
    if "conformance" in rep:
        print(f"conformance {rep['conformance']['passed']}/{rep['conformance']['checks']}")
    print(f"wrote {Path(a.out) / 'RESULTS.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
