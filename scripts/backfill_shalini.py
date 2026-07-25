#!/usr/bin/env python3
"""One-time backfill of Shalini's BofA VISA (x3070) itemized transactions.

Pulls ONLY account x3070 from Shalini's Plaid item (PLAID_ACCESS_TOKEN_SHALINI),
categorizes each purchase with the same rules as the daily sync, and writes them
into the ledger exactly like the other card (Account = "Shalini BoA VISA").

Idempotent: write_spending_ledger dedups by Plaid transaction_id, so re-running
adds nothing new. Safe to run more than once.

Run this ON YOUR MACHINE (needs the Plaid package + network + the token in .env),
not in the sandbox.

Usage:
    python scripts/backfill_shalini.py --dry-run                 # preview counts, no write
    python scripts/backfill_shalini.py                           # write, default from 2025-06-25
    python scripts/backfill_shalini.py --from-date 2025-06-25    # explicit start
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv

load_dotenv()

from utils import clean_env, resolve_ledger_path
from plaid_client import PlaidClient
from filters import categorize_batch
from ledger_writer import write_spending_ledger

# Must match the daily sync (main.py) so backfilled rows are identical to
# what the ongoing sync produces.
SHALINI_MASK = "3070"
SHALINI_LABEL = "Shalini BoA VISA"
DEFAULT_FROM = date(2025, 6, 25)   # ledger start


def main():
    dry = "--dry-run" in sys.argv
    from_date = DEFAULT_FROM
    if "--from-date" in sys.argv:
        from_date = date.fromisoformat(sys.argv[sys.argv.index("--from-date") + 1])
    end = date.today()

    token = clean_env(os.getenv("PLAID_ACCESS_TOKEN_SHALINI"), "PLAID_ACCESS_TOKEN_SHALINI")
    if not token:
        sys.exit("PLAID_ACCESS_TOKEN_SHALINI is not set in .env. Run: python src/link_item.py shalini")

    client = PlaidClient()
    if not client.verify_access_token(token):
        sys.exit("PLAID_ACCESS_TOKEN_SHALINI is invalid / needs re-auth. Run: python src/link_item.py shalini")

    ledger_path, _ = resolve_ledger_path()
    print(f"Ledger: {ledger_path}")
    print(f"Pulling x{SHALINI_MASK} transactions {from_date} → {end} ...")

    raw = client.get_transactions(token, from_date, end)
    accounts = client.get_accounts(token)

    keep_ids = {a["account_id"] for a in accounts if a.get("mask") == SHALINI_MASK}
    if not keep_ids:
        masks = ", ".join(sorted(a.get("mask", "?") for a in accounts))
        sys.exit(f"No account with mask {SHALINI_MASK} found under this item. Masks seen: {masks}")
    account_map = {aid: SHALINI_LABEL for aid in keep_ids}

    card_txns = [t for t in raw if t.get("account_id") in keep_ids]
    print(f"  {len(card_txns)} transactions on x{SHALINI_MASK} "
          f"(of {len(raw)} across all accounts in this item)")

    # Load rules exactly like the daily sync (Drive-first, then env, then local file).
    from main import _load_rules_with_fallback
    rules = _load_rules_with_fallback()

    included, excluded = categorize_batch(card_txns, rules, account_map)

    # Summary preview
    from collections import defaultdict
    by_cat = defaultdict(lambda: [0, 0.0])
    inc_total = exp_total = 0.0
    for tx in included:
        by_cat[tx["category"]][0] += 1
        by_cat[tx["category"]][1] += tx["amount"] * (1 if tx["type"] == "Expense" else -1)
        if tx["type"] == "Expense":
            exp_total += tx["amount"]
        else:
            inc_total += tx["amount"]

    print(f"\n  Categorized: {len(included)} included, {len(excluded)} excluded (transfers)")
    print(f"  Expense ${exp_total:,.2f} | Income/refund ${inc_total:,.2f} | "
          f"Net purchases ${exp_total - inc_total:,.2f}")
    print("  By category:")
    for cat, (n, net) in sorted(by_cat.items(), key=lambda kv: -abs(kv[1][1])):
        print(f"    {cat:34} {n:4}  ${net:>12,.2f}")

    if dry:
        print("\nDRY RUN — nothing written.")
        return

    result = write_spending_ledger(ledger_path, included)
    print(f"\nWrote to ledger: {result['added']} added, {result['skipped']} skipped (already present).")
    print("Next: run scripts/exclude_shalini_cc_payments.py, then 'push' from the agent CLI to sync Drive.")
    print("      Then verify with: python scripts/reconcile_shalini_cc.py")


if __name__ == "__main__":
    main()
