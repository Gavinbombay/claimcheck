"""CLI: python -m claimcheck CLAIMS.json [--base DIR] [--json]

Exit 0 if every claim is SUPPORTED, 1 if any is CONTRADICTED, 2 if any is
only UNVERIFIED.
"""
from __future__ import annotations

import argparse
import json
import sys

from .core import CONTRADICTED, SUPPORTED, check_all, load_claims


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="claimcheck", description=__doc__.splitlines()[0])
    ap.add_argument("claims", help="JSON file of claims")
    ap.add_argument("--base", default=".", help="directory relative paths resolve against")
    ap.add_argument("--json", action="store_true", help="print results as JSON")
    a = ap.parse_args(argv)
    results = check_all(load_claims(a.claims), a.base)
    if a.json:
        print(json.dumps([{"kind": r.claim.kind, "text": r.claim.text, "verdict": r.verdict,
                           "detail": r.detail} for r in results], indent=2))
    else:
        w = max((len(r.claim.text or r.claim.kind) for r in results), default=5)
        for r in results:
            print(f"{r.verdict:<12} {(r.claim.text or r.claim.kind):<{w}}  {r.detail}")
        counts = {v: sum(r.verdict == v for r in results) for v in (SUPPORTED, CONTRADICTED, "UNVERIFIED")}
        print(" ".join(f"{k.lower()}={v}" for k, v in counts.items()))
    if any(r.verdict == CONTRADICTED for r in results):
        return 1
    if any(r.verdict != SUPPORTED for r in results):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
