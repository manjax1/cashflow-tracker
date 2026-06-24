import os
import sys
import argparse
from datetime import date, timedelta, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from utils import clean_env
from plaid_client import PlaidClient
from filters import load_rules, categorize_batch
from ledger_writer import write_spending_ledger
from email_notifier import send_sync_summary
from drive_sync import download_ledger, upload_ledger

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spending_rules.json")


def _resolve_ledger_path() -> tuple[str, bool]:
    """Returns (ledger_path, is_cloud)."""
    is_cloud = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    if is_cloud:
        return "/tmp/cashflow-tracker.xlsx", True
    path = clean_env(os.getenv("SPENDING_LEDGER_FILE_PATH"), "SPENDING_LEDGER_FILE_PATH")
    return path, False


def _load_rules_with_fallback() -> list:
    """Load categorization rules from RULES_JSON env var (Railway) or local file."""
    import json
    rules_json_env = clean_env(os.getenv("RULES_JSON"), "RULES_JSON")
    if rules_json_env:
        try:
            rules = json.loads(rules_json_env)
            print(f"✅ Loaded {len(rules)} rules from RULES_JSON env var")
            from filters import load_rules as _sort_rules
            return sorted(rules, key=lambda r: len(r["keyword"]), reverse=True)
        except Exception as e:
            print(f"⚠️  RULES_JSON parse failed: {e} — falling back to file")

    if os.path.exists(RULES_PATH):
        from filters import load_rules
        rules = load_rules(RULES_PATH)
        print(f"✅ Loaded {len(rules)} rules from {RULES_PATH}")
        return rules

    print("⚠️  No spending_rules.json and no RULES_JSON env var — using Plaid categories only.")
    return []


def run_sync(from_date: date = None, to_date: date = None) -> dict:
    is_cloud = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    plaid_env = clean_env(os.getenv("PLAID_ENV", "production"), "PLAID_ENV")
    resend_set = bool(clean_env(os.getenv("RESEND_API_KEY"), "RESEND_API_KEY"))
    token_set = bool(clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN"))
    drive_set = bool(clean_env(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"), "GOOGLE_SERVICE_ACCOUNT_JSON"))
    print(f"[spending-tracker] env={plaid_env} cloud={is_cloud} resend={resend_set} token={token_set} drive={drive_set}")

    ledger_path, is_cloud = _resolve_ledger_path()
    file_id = clean_env(os.getenv("GOOGLE_DRIVE_FILE_ID"), "GOOGLE_DRIVE_FILE_ID")

    if is_cloud and file_id:
        try:
            download_ledger(file_id, ledger_path)
        except Exception:
            print("⚠️  Could not download existing ledger — will create fresh file.")

    client = PlaidClient()
    access_token = clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN")

    if not access_token or not client.verify_access_token(access_token):
        if is_cloud:
            raise RuntimeError("Plaid access token invalid or missing. Run link flow locally first.")
        from link_flow import run_link_flow
        run_link_flow(client)
        access_token = clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN")

    end = to_date if to_date else date.today()
    start = from_date if from_date else end - timedelta(days=7)
    print(f"Fetching transactions {start} → {end}")

    raw_transactions = client.get_transactions(access_token, start, end)
    accounts = client.get_accounts(access_token)
    account_map = {a["account_id"]: _account_label(a) for a in accounts}

    # Exclude accounts by last-4 mask — account 4719 is managed by Prateek (son), not household cash flow
    excluded_account_ids = {a["account_id"] for a in accounts if a.get("mask") == "4719"}
    prateek_excluded = [tx for tx in raw_transactions if tx.get("account_id") in excluded_account_ids]
    raw_transactions  = [tx for tx in raw_transactions if tx.get("account_id") not in excluded_account_ids]

    rules = _load_rules_with_fallback()

    included, excluded = categorize_batch(raw_transactions, rules, account_map)

    # Count excluded rental specifically
    excluded_rental_count = sum(
        1 for tx in excluded
        if "rental" in (tx.get("reason") or "").lower()
        or tx.get("category") != "Credit Card Payment"
    )

    result = write_spending_ledger(ledger_path, included)
    added, skipped = result["added"], result["skipped"]

    # Build summary
    category_totals: dict[str, float] = defaultdict(float)
    for tx in included:
        if tx.get("type") == "Expense":
            category_totals[tx["category"]] += tx.get("amount", 0.0)

    sorted_cats = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    total_spend = sum(v for _, v in sorted_cats)
    top_category = sorted_cats[0][0] if sorted_cats else "N/A"

    summary = {
        "date": str(end),
        "added": added,
        "skipped": skipped,
        "tx_count": added,
        "total_spend": total_spend,
        "top_category": top_category,
        "top_categories": [{"category": c, "amount": a} for c, a in sorted_cats[:5]],
        "transactions": [
            {
                "date": tx.get("date"),
                "name": tx.get("name"),
                "account_label": tx.get("account_label"),
                "category": tx.get("category"),
                "amount": tx.get("amount"),
            }
            for tx in sorted(included, key=lambda t: t.get("date", ""), reverse=True)
        ],
        "excluded_rental_count": excluded_rental_count,
        "ledger_path": ledger_path,
        "plaid_env": plaid_env,
    }

    try:
        send_sync_summary(summary)
    except Exception as e:
        print(f"⚠️  Email failed: {e}")

    if is_cloud and file_id:
        try:
            upload_ledger(file_id, ledger_path)
        except Exception as e:
            print(f"⚠️  Drive upload failed: {e}")

    if prateek_excluded:
        print(f"Excluded {len(prateek_excluded)} transactions from account ending 4719 (managed by Prateek)")
    print(f"✅ Sync complete — {added} added, {skipped} skipped, ${total_spend:,.2f} total spend")
    return summary


def _account_label(account: dict) -> str:
    subtype = account.get("subtype", "").lower()
    if "checking" in subtype:
        return "Checking"
    if "credit" in subtype:
        return "Credit Card"
    return account.get("name", "Unknown")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spending Tracker sync")
    parser.add_argument(
        "--from-date",
        help="Start date for transaction fetch (YYYY-MM-DD). Defaults to 7 days ago.",
    )
    args = parser.parse_args()

    from_date = None
    if args.from_date:
        try:
            from_date = date.fromisoformat(args.from_date)
        except ValueError:
            parser.error("--from-date must be YYYY-MM-DD")
        if from_date > date.today():
            parser.error("--from-date cannot be in the future")
        if (date.today() - from_date).days > 730:
            print("⚠️  Warning: requesting more than 2 years of history — Plaid may limit results.")

    run_sync(from_date)
