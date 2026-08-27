import csv

from bazaar.schemas.models import Vertical
from bazaar.synthetic import generate_corpus


def test_corpus_size_and_shape(merchants):
    assert len(merchants) >= 50
    assert all(18 <= len(m.products) <= 22 for m in merchants)
    assert {m.vertical for m in merchants} == set(Vertical)
    assert sum(len(m.products) for m in merchants) >= 900


def test_corpus_is_deterministic(tmp_path):
    a = generate_corpus(tmp_path / "a")
    b = generate_corpus(tmp_path / "b")
    assert [m.model_dump_json() for m in a] == [m.model_dump_json() for m in b]


def test_source_csv_is_messy_but_complete(corpus_dir, merchants):
    m = merchants[0]
    rows = list(csv.reader((corpus_dir / m.merchant_id / "source.csv").open(encoding="utf-8")))
    assert len(rows) == len(m.products) + 1
    prices = [r[1] for r in rows[1:]]
    assert any(p.startswith(("Rs", "₹")) for p in prices)  # messiness present


def test_some_merchants_have_poisoned_rows(merchants):
    poisoned = [p for m in merchants for p in m.products if "poisoned_source" in p.flags]
    assert len(poisoned) >= 10
    assert all(m.serviceability.serves(m.base_pincode) for m in merchants)
