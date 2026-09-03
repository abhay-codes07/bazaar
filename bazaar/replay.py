"""``python -m bazaar.replay <gateway_url> <session_id>`` — render a session's audit timeline
from the hash chain and verify the chain while doing it."""

from __future__ import annotations

import argparse
import sys

import httpx


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bazaar-replay", description="Replay one session from the audit chain")
    p.add_argument("url", help="gateway base URL, e.g. http://localhost:8000")
    p.add_argument("session_id")
    a = p.parse_args(argv)

    with httpx.Client(base_url=a.url.rstrip("/"), timeout=30.0) as http:
        r = http.get(f"/bazaar/v1/sessions/{a.session_id}/replay")
    if r.status_code != 200:
        print(f"error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 1
    d = r.json()
    chain = "INTACT" if d["chain_ok"] else f"BROKEN at seq {d.get('first_bad_seq')}"
    print(f"session {a.session_id} · chain {chain}")
    for e in d["timeline"]:
        mark = "✗" if (e.get("outcome") == "declined" or e.get("checks_failed")) else "·"
        money = " ".join(f"{k}={v}" for k, v in (e.get("money") or {}).items())
        line = f"{mark} #{e['seq']:>4} {e['at'][11:19]}  {e['kind']:<10} {e['action']:<24} {e.get('outcome', ''):<10}"
        if e.get("checks_failed"):
            line += f" failed: {', '.join(e['checks_failed'])}"
        if money:
            line += f"  {money}"
        print(line)
        if e.get("note"):
            print(f"        {e['note']}")
    print(f"{len(d['timeline'])} entries · every hash verified against its predecessor")
    return 0 if d["chain_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
