#!/usr/bin/env python3
"""Generate a compact spending_rules JSON for Railway's RULES_JSON env var.

- strips 'note' fields (documentation only; matching uses keyword+category)
- dedupes keywords case-insensitively (last occurrence wins — newest rule)
- minifies to a single line, ready to paste into Railway

Usage:  python scripts/compact_rules.py            # writes spending_rules.compact.json
        python scripts/compact_rules.py --print    # also prints to stdout
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "spending_rules.json")
OUT = os.path.join(ROOT, "spending_rules.compact.json")


def main():
    with open(SRC) as f:
        rules = json.load(f)

    deduped = {}
    for r in rules:  # later entries overwrite earlier ones
        deduped[r["keyword"].lower()] = {"keyword": r["keyword"], "category": r["category"]}
    compact = list(deduped.values())

    with open(OUT, "w") as f:
        json.dump(compact, f, separators=(",", ":"))

    full_size = os.path.getsize(SRC)
    compact_size = os.path.getsize(OUT)
    print(f"{len(rules)} rules -> {len(compact)} after dedupe")
    print(f"{full_size:,} bytes -> {compact_size:,} bytes ({100*compact_size//full_size}%)")
    print(f"Written to {OUT}")
    print("\nPaste the file contents into Railway -> Variables -> RULES_JSON")
    if "--print" in sys.argv:
        print()
        with open(OUT) as f:
            print(f.read())


if __name__ == "__main__":
    main()
