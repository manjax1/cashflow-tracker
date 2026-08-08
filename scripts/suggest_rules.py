#!/usr/bin/env python3
"""Propose keyword→category rules from the ledger's uncategorized transactions —
a classification baseline. Distinct merchants among the 'Other - Uncategorized'
rows are sent to an LLM that maps each to an EXISTING category and a robust
keyword. Rules both clean up existing rows (via recategorize_ledger) AND
auto-classify future transactions (during the daily sync).

Dry-run by default (writes nothing). Review, then --apply to append accepted
rules to spending_rules.json. Full baseline workflow:

    python scripts/suggest_rules.py                 # dry-run: proposed rules + coverage
    python scripts/suggest_rules.py --min-count 2   # only merchants seen >= 2x
    python scripts/suggest_rules.py --apply         # append rules to spending_rules.json
    python scripts/push_rules_to_drive.py           # publish rules (no redeploy)
    python src/recategorize_ledger.py --apply       # re-classify existing rows
    # then 'push' from the agent CLI to sync the ledger to Drive

Idempotent: keywords already present in spending_rules.json are skipped.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv

load_dotenv()

from agent import ledger
from agent import invoices

RULES_PATH = os.path.join(ledger.REPO_ROOT, "spending_rules.json")
UNCATEGORIZED = "Other - Uncategorized"
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")
CHUNK = 90

PROPOSE_TOOL = {
    "name": "propose_rules",
    "description": "Propose keyword→category classification rules for the merchants shown.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "case-insensitive SUBSTRING that reliably identifies this merchant across its variants (strip store #, city, dates), e.g. 'TESLA SUPERCHARGER', 'MENDOCINO FARMS', 'WALGREENS', 'NYTIMES'"},
                        "category": {"type": "string", "description": "EXACT existing category name from the provided list"},
                        "note": {"type": "string"},
                    },
                    "required": ["keyword", "category"],
                },
            },
        },
        "required": ["rules"],
    },
}

SYSTEM = """You build keyword→category rules for a household finance ledger.
You are given distinct bank-transaction descriptions that are currently
uncategorized. Map each recognizable merchant to the single best EXISTING category.

Rules:
- Use ONLY the exact category names provided. Never invent categories.
- keyword = a distinctive SUBSTRING that identifies the merchant across variants
  (ignore store numbers, cities, dates). Specific enough to avoid false matches.
- SKIP ambiguous person-to-person transfers ('Zelle payment to <person>',
  bare 'VENMO', 'PAYPAL ... XFER') — leave them out entirely.
- SKIP anything you can't confidently classify. Omit rather than guess.
- One rule per merchant."""


def load_uncategorized():
    rows = [t for t in ledger.load_transactions()
            if str(t.get("Category")) == UNCATEGORIZED]
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0.0])
    for t in rows:
        d = str(t.get("Description", ""))[:80]
        agg[d][0] += 1
        agg[d][1] += abs(float(t.get("Amount") or 0))
    merchants = [{"desc": d, "count": n, "total": round(s, 2)} for d, (n, s) in agg.items()]
    merchants.sort(key=lambda m: (-m["count"], -m["total"]))
    return rows, merchants


def _propose_rules_llm(merchants, categories, client):
    """One LLM call per chunk of merchants → list of {keyword, category, note}."""
    out = []
    cat_block = "\n".join(f"- {c}" for c in categories)
    for i in range(0, len(merchants), CHUNK):
        chunk = merchants[i:i + CHUNK]
        listing = "\n".join(f"[{m['count']:>3}x  ${m['total']:>9,.2f}]  {m['desc']}" for m in chunk)
        prompt = (f"Existing categories (use EXACT names):\n{cat_block}\n\n"
                  f"Uncategorized merchants (count, total, description):\n{listing}\n\n"
                  "Call propose_rules with one rule per merchant you can confidently classify.")
        resp = client.messages.create(
            model=MODEL, max_tokens=8000, system=SYSTEM,
            tools=[PROPOSE_TOOL], tool_choice={"type": "tool", "name": "propose_rules"},
            messages=[{"role": "user", "content": prompt}])
        out.extend(next(b.input for b in resp.content if b.type == "tool_use")["rules"])
    return out


def validate_and_dedup(proposed, categories, existing_keywords):
    """Keep rules with a valid category, a substantive keyword, and no clash with
    an existing rule or another proposed keyword."""
    cats = set(categories)
    seen = set(k.lower() for k in existing_keywords)
    clean = []
    for r in proposed:
        kw = str(r.get("keyword", "")).strip()
        cat = str(r.get("category", "")).strip()
        if len(kw) < 4 or cat not in cats or kw.lower() in seen:
            continue
        seen.add(kw.lower())
        clean.append({"keyword": kw, "category": cat, "note": r.get("note", "baseline rule (suggest_rules)")})
    return clean


def simulate(rules, unc_rows):
    """How many uncategorized rows / $ each rule would newly classify."""
    remaining = list(unc_rows)
    covered_rows = covered_amt = 0
    report = []
    for r in rules:
        kw = r["keyword"].lower()
        hit = [t for t in remaining if kw in str(t.get("Description", "")).lower()]
        if not hit:
            continue
        amt = sum(abs(float(t.get("Amount") or 0)) for t in hit)
        report.append({**r, "rows": len(hit), "amount": round(amt, 2)})
        covered_rows += len(hit)
        covered_amt += amt
        remaining = [t for t in remaining if t not in hit]   # longest-first would refine; fine for preview
    report.sort(key=lambda x: -x["rows"])
    return report, covered_rows, round(covered_amt, 2), len(remaining)


# Owner category conventions — force these over the LLM's guess so recurring
# merchants land consistently (and future runs stay stable). Applied to the
# generated proposals; hand-edited --apply-file rules are honored as-is.
MERCHANT_OVERRIDES = [
    ("TJ MAXX", "Clothing"), ("TJMAXX", "Clothing"),
    ("DD'S DISCOUNTS", "Clothing"), ("DDS DISCOUNTS", "Clothing"),
    ("ROSS DRESS", "Clothing"), ("ROSS STORES", "Clothing"), ("TARGET", "Clothing"),
    ("AQUI", "Dining"), ("BITES", "Dining"),
    ("ARCO", "Transportation"), ("76 ", "Transportation"),
    ("CITI CARD ONLINE", "Credit Card Payment"),  # old Citi (Costco), now paid via BofA VISA
]


def _apply_overrides(rules):
    for r in rules:
        ku = str(r["keyword"]).upper()
        for sub, cat in MERCHANT_OVERRIDES:
            if sub in ku:
                r["category"] = cat
                break
    return rules


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def lint(rules):
    """Clean the proposed rules: drop keywords made redundant by a shorter one
    of the same category; flag same-merchant category conflicts and short/risky
    keywords. Returns (kept_rules, warnings)."""
    warnings = []
    kept = []
    for r in sorted(rules, key=lambda r: len(_norm(r["keyword"]))):
        nl = _norm(r["keyword"])
        redundant = False
        for k in kept:
            nk = _norm(k["keyword"])
            if nk and nk in nl:
                if k["category"] == r["category"]:
                    redundant = True
                    warnings.append(f"merged: '{r['keyword']}' ⊂ '{k['keyword']}' (same category, {r['category']})")
                    break
                warnings.append(f"CONFLICT: '{r['keyword']}'→{r['category']} vs "
                                f"'{k['keyword']}'→{k['category']} — same merchant, different category; pick one")
        if redundant:
            continue
        if len(r["keyword"]) < 6:
            warnings.append(f"SHORT keyword '{r['keyword']}'→{r['category']} — risk of false matches; lengthen or drop")
        kept.append(r)
    return kept, warnings


def apply_rules(rules):
    with open(RULES_PATH) as f:
        existing = json.load(f)
    existing.extend({"keyword": r["keyword"], "category": r["category"], "note": r["note"]} for r in rules)
    with open(RULES_PATH, "w") as f:
        json.dump(existing, f, indent=2)
    return len(existing)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=1, help="only merchants seen at least N times")
    ap.add_argument("--apply", action="store_true", help="append accepted rules to spending_rules.json")
    ap.add_argument("--out", metavar="FILE", help="write the proposed rules to an editable JSON file")
    ap.add_argument("--apply-file", metavar="FILE",
                    help="skip the LLM; append hand-edited rules from FILE to spending_rules.json")
    args = ap.parse_args()

    categories = [c for c in invoices.taxonomy() if c != UNCATEGORIZED]
    existing_keywords = [r["keyword"] for r in json.load(open(RULES_PATH))]

    # Apply-from-file path: read edited rules, re-validate, append. No LLM call.
    if args.apply_file:
        edited = json.load(open(args.apply_file))
        rules = validate_and_dedup(edited, categories, existing_keywords)
        n = apply_rules(rules)
        print(f"Applied {len(rules)} rules from {args.apply_file} → spending_rules.json now has {n} rules.")
        print("Next: python scripts/push_rules_to_drive.py  &&  python src/recategorize_ledger.py --apply  (then 'push').")
        return

    unc_rows, merchants = load_uncategorized()
    merchants = [m for m in merchants if m["count"] >= args.min_count]
    total_amt = round(sum(abs(float(t.get("Amount") or 0)) for t in unc_rows), 2)
    print(f"Uncategorized: {len(unc_rows)} rows, ${total_amt:,.2f}, {len(merchants)} "
          f"distinct merchants (min-count {args.min_count}).")
    if not merchants:
        return

    import anthropic
    proposed = _propose_rules_llm(merchants, categories, anthropic.Anthropic())
    rules = _apply_overrides(validate_and_dedup(proposed, categories, existing_keywords))
    rules, warnings = lint(rules)
    report, cov_rows, cov_amt, still = simulate(rules, unc_rows)

    print(f"\nProposed {len(rules)} new rules — would classify {cov_rows}/{len(unc_rows)} "
          f"rows (${cov_amt:,.2f} of ${total_amt:,.2f}); {still} rows still uncategorized.\n")
    print(f"{'keyword':32} {'category':26} {'rows':>4} {'amount':>11}")
    print("-" * 78)
    for r in report:
        print(f"{r['keyword'][:31]:32} {r['category'][:25]:26} {r['rows']:>4} ${r['amount']:>10,.2f}")

    if warnings:
        conflicts = [w for w in warnings if w.startswith("CONFLICT")]
        shorts = [w for w in warnings if w.startswith("SHORT")]
        merged = [w for w in warnings if w.startswith("merged")]
        print(f"\n⚠️  Review ({len(conflicts)} conflicts, {len(shorts)} short keywords, "
              f"{len(merged)} auto-merged):")
        for w in conflicts + shorts:
            print(f"   {w}")
        if merged:
            print(f"   …and {len(merged)} redundant variants auto-merged.")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(rules, f, indent=2)
        print(f"\nWrote {len(rules)} rules to {args.out}. Edit it (fix conflicts, drop risky "
              f"keywords), then:\n   python scripts/suggest_rules.py --apply-file {args.out}")
        return
    if not args.apply:
        print("\nDRY RUN — nothing written. Use --out FILE to hand-edit, or --apply to add directly.")
        return
    n = apply_rules(rules)
    print(f"\nAppended {len(rules)} rules → spending_rules.json now has {n} rules.")
    print("Next: python scripts/push_rules_to_drive.py  &&  python src/recategorize_ledger.py --apply  (then 'push').")


if __name__ == "__main__":
    main()
