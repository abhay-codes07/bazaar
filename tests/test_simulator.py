from collections import Counter

from bazaar.simulator.run import run_all
from bazaar.simulator.tasks import generate_tasks


def test_task_generator_mix(merchants):
    tasks = generate_tasks(merchants, 200)
    assert len(tasks) == 200 and len({t.task_id for t in tasks}) == 200
    exp = Counter(t.expected for t in tasks)
    assert 0.25 <= (200 - exp["order"]) / 200 <= 0.5
    assert {t.language for t in tasks} == {"en", "hi", "hi-Latn"}
    assert generate_tasks(merchants, 200)[17].message == tasks[17].message  # deterministic


def test_full_run_meets_bars(corpus_dir, tmp_path):
    rep = run_all(n_tasks=60, out_dir=tmp_path / "res", corpus_dir=corpus_dir)
    t = rep["transactions"]
    assert t["errors"] == 0, t["errors_detail"]
    assert t["declines"]["wrong_orders_on_impossible"] == 0
    assert t["possible_completion_rate"] >= 0.75  # some "possible" tasks have budgets below list price by design
    assert t["accuracy"] >= 0.85
    assert rep["lift"]["orders"] > 0
    assert rep["trust"]["chain_ok"] and rep["trust"]["ledger"]["inconsistencies"] == 0
    assert rep["redteam"]["pass_rate"] == 1.0, rep["redteam"]["failed"]
    assert rep["fairness"]["passed"] == rep["fairness"]["merchants"]
    assert rep["conformance"]["conformant"], rep["conformance"]["failed"]
    md = (tmp_path / "res" / "RESULTS.md").read_text(encoding="utf-8")
    assert md.startswith("# Bazaar") and "Red team" in md and "Protocol conformance" in md
