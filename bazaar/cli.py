"""``bazaar`` command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bazaar.settings import ROOT


def cmd_synth(args: argparse.Namespace) -> int:
    from bazaar.synthetic import generate_corpus

    out = Path(args.out)
    merchants = generate_corpus(out, seed=args.seed)
    skus = sum(len(m.products) for m in merchants)
    print(f"wrote {len(merchants)} merchants / {skus} SKUs -> {out}")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from bazaar.schemas.models import Merchant, Product

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, model in {"merchant": Merchant, "product": Product}.items():
        (out / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
    print(f"wrote JSON schemas -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bazaar")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="generate the synthetic merchant corpus")
    s.add_argument("--out", default=str(ROOT / "data" / "synthetic"))
    s.add_argument("--seed", type=int, default=20260828)
    s.set_defaults(fn=cmd_synth)

    s = sub.add_parser("schema", help="export JSON schemas for the data model")
    s.add_argument("--out", default=str(ROOT / "bazaar" / "schemas" / "json"))
    s.set_defaults(fn=cmd_schema)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
