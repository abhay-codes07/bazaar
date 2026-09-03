"""``python -m bazaar.conformance <gateway_url>`` — run the 24 protocol checks against a live
gateway and print pass/fail plus a badge JSON (``--badge out.json``)."""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from bazaar import __version__
from bazaar.conformance.checks import run_conformance, summarize


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bazaar-conformance", description="Bazaar protocol conformance runner")
    p.add_argument("url", help="gateway base URL, e.g. http://localhost:8000")
    p.add_argument("--merchant", default=None, help="merchant_id to test (default: first listed)")
    p.add_argument("--badge", default=None, help="write the badge JSON here")
    a = p.parse_args(argv)

    from urllib.parse import urlparse

    authority = urlparse(a.url).netloc or "localhost"
    with httpx.Client(base_url=a.url.rstrip("/"), timeout=30.0, follow_redirects=True) as http:
        checks = run_conformance(http, merchant_id=a.merchant, authority=authority)
    summ = summarize(checks)
    for c in checks:
        print(f"{'PASS' if c.passed else 'FAIL'}  {c.name}" + (f"  — {c.detail}" if c.detail and not c.passed else ""))
    print(f"\n{summ['passed']}/{summ['checks']} checks · conformant: {summ['conformant']}")
    if a.badge:
        badge = {"schema": "bazaar-conformance/v1", "runner_version": __version__, "url": a.url, **summ, "detail": [c.model_dump() for c in checks]}
        with open(a.badge, "w", encoding="utf-8") as f:
            json.dump(badge, f, indent=2)
        print(f"badge → {a.badge}")
    return 0 if summ["conformant"] else 1


if __name__ == "__main__":
    sys.exit(main())
