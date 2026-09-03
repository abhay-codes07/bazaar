"""The README quotes numbers from generated results. This test fails the build if they drift
apart — which is exactly what happened once: results were regenerated, the README kept the old
lift. Both files are committed, so this runs offline in CI."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def _signed_rs(paise: int) -> str:
    return f"{'−' if paise < 0 else '+'}₹{abs(paise) / 100:,.0f}"


def _rs(paise: int) -> str:
    return f"₹{paise / 100:,.0f}"


@pytest.mark.parametrize("results_dir", ["results", "results/gpt4o"])
def test_results_md_matches_results_json(results_dir):
    d = ROOT / results_dir
    if not (d / "results.json").exists():
        pytest.skip(f"{results_dir} not generated")
    rep = json.loads((d / "results.json").read_text(encoding="utf-8"))
    md = (d / "RESULTS.md").read_text(encoding="utf-8")
    lf = rep["lift"]
    assert f"Lift: **{lf['orders']:+d} orders, {_signed_rs(lf['gmv_paise'])} GMV ({lf['gmv_multiple']}×)**" in md
    assert f"| orders | **{rep['transactions']['orders']}** |" in md
    if "false_positive_sweep" in rep:
        assert "False-positive cost" in md
        first = rep["false_positive_sweep"][0]
        assert first["orders"] == rep["transactions"]["orders"], "the default-cap sweep row must reproduce the main run"


def test_readme_quotes_the_generated_lift():
    rep = json.loads((ROOT / "results" / "results.json").read_text(encoding="utf-8"))
    readme = README.read_text(encoding="utf-8")
    t, b, lf = rep["transactions"], rep["baseline_no_bazaar"], rep["lift"]
    expected = f"{t['orders']} / {_rs(t['gmv_paise'])} vs {b['orders']} / {_rs(b['gmv_paise'])} → **{lf['orders']:+d} orders, {_signed_rs(lf['gmv_paise'])} ({lf['gmv_multiple']}×)**"
    assert expected in readme, f"README lift row is stale; expected: {expected}"
    # headline counts the README states as bold numbers
    assert f"**{t['declines']['wrong_orders_on_impossible']}**" in readme
    rt = rep["redteam"]
    assert f"**{rt['passed']}/{rt['cases']}**" in readme
    cf = rep["conformance"]
    assert f"**{cf['passed']}/{cf['checks']}**" in readme


def test_readme_quotes_the_sweep_row():
    rep = json.loads((ROOT / "results" / "results.json").read_text(encoding="utf-8"))
    if "false_positive_sweep" not in rep:
        pytest.skip("no sweep in results")
    readme = README.read_text(encoding="utf-8")
    tight = rep["false_positive_sweep"][-1]
    # the README must show the *cost* row, not only the comfortable zero
    pat = re.compile(rf"{re.escape(_rs(tight['max_order_paise']))}.*?\*\*{tight['wrong_declines_on_possible']}\*\*.*?{re.escape(_rs(tight['lost_gmv_paise']))}")
    assert pat.search(readme), f"README false-positive row stale; expected cap {_rs(tight['max_order_paise'])} → {tight['wrong_declines_on_possible']} wrong declines, {_rs(tight['lost_gmv_paise'])} lost"
