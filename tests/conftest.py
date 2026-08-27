from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("BAZAAR_LLM", "fake")
os.environ.setdefault("BAZAAR_RAZORPAY", "fake")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_fakes():
    from bazaar.razorpay_client.fake import FakeRazorpay

    FakeRazorpay.reset_shared()
    yield
    FakeRazorpay.reset_shared()


@pytest.fixture(scope="session")
def corpus_dir(tmp_path_factory) -> Path:
    from bazaar.synthetic import generate_corpus

    d = tmp_path_factory.mktemp("corpus")
    generate_corpus(d)
    return d


@pytest.fixture(scope="session")
def merchants(corpus_dir):
    from bazaar.synthetic import load_corpus

    return load_corpus(corpus_dir)
