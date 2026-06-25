#!/usr/bin/env python3
"""
One-time CSV import tool for backfilling BofA statement data
before the Plaid Item connection date (2026-03-25).

BofA sign convention (BOTH file types):
  negative amount = money leaving account (expense/debit)
  positive amount = money entering account (income/credit)
  Both are negated here to match Plaid convention (positive = expense).

BofA CC CSV header (real, verified):
  Posted Date,Reference Number,Payee,Address,Amount

BofA Checking CSV header (real, verified):
  Date,Description,Amount,Running Bal.

Dedup: uses BofA Reference Number for CC rows and a filepath+row-index
hash for checking rows (stored as transaction_id). This prevents same-day
same-merchant same-amount transactions from being wrongly collapsed
(e.g. two $163.20 Uniqlo purchases + one $163.20 refund on same day).
Re-importing the same file is safe — existing transaction_ids are stored
in the ledger and checked.

Usage:
    python src/csv_importer.py --type checking  statement.csv
    python src/csv_importer.py --type credit    card.csv
    python src/csv_importer.py --type checking  *.csv --dry-run
    python src/csv_importer.py --type credit    jan.csv feb.csv
"""

import argparse
import csv
import hashlib
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from filters import load_rules, categorize_batch
from ledger_writer import write_spending_ledger
from utils import clean_env

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spending_rules.json")


def _parse_amount(raw: str) -> float:
    """Strip quotes, commas, and dollar signs; return float."""
    return float(raw.strip().strip('"').replace(",", "").replace("$", ""))


def _stable_id(filepath: str, row_idx: int) -> str:
    """Deterministic per-row ID for file types that have no Reference Number."""
    stem = hashlib.md5(os.path.basename(filepath).encode()).hexdigest()[:8]
    return f"csv-chk-{stem}-{row_idx}"


def parse_checking_csv(filepath: str) -> list[dict]:
    """Parse BofA checking account CSV export.

    Real header (line 7 after 5 summary lines + 1 blank):
        Date,Description,Amount,Running Bal.

    BofA checking sign: negative = debit (money out), positive = credit (money in).
    Negated here to match Plaid: positive = expense, negative = income.

    No Reference Number column → dedup ID is filepath+row_index hash.
    """
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Date,Description,Amount"):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            f"Cannot find 'Date,Description,Amount' header in {filepath}.\n"
            "Expected BofA checking export with header starting at line 7."
        )

    reader = csv.DictReader(io.StringIO("".join(lines[header_idx:])))
    transactions = []
    row_idx = 0

    for row in reader:
        desc = row.get("Description", "").strip()
        if not desc or desc.lower().startswith("beginning balance"):
            continue

        raw_date = row.get("Date", "").strip()
        if not raw_date:
            continue
        try:
            tx_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
        except ValueError:
            continue

        raw_amount = row.get("Amount", "").strip()
        if not raw_amount:
            continue
        try:
            bofa_amount = _parse_amount(raw_amount)
        except ValueError:
            continue

        # BofA checking: -7110.24 credit (payroll in) → negate → +7110.24 Plaid income ... wait:
        # BofA checking sign: positive = credit (money IN), negative = debit (money OUT).
        # Plaid sign:          positive = expense (money OUT), negative = income (money IN).
        # So: bofa_checking positive credit → Plaid negative income. plaid = -bofa.
        plaid_amount = -bofa_amount

        transactions.append({
            "date": str(tx_date),
            "name": desc,
            "amount": plaid_amount,
            "pending": False,
            "account_id": "csv-checking",
            "personal_finance_category": {"primary": ""},
            "transaction_id": _stable_id(filepath, row_idx),
        })
        row_idx += 1

    return transactions


def parse_credit_card_csv(filepath: str) -> list[dict]:
    """Parse BofA credit card CSV export.

    Real header (line 1, verified from actual file):
        Posted Date,Reference Number,Payee,Address,Amount

    BofA CC sign: negative = charge (money out), positive = payment/credit (money in).
    Same direction as checking — negated here to match Plaid convention.

    Reference Number is globally unique per transaction → used as dedup ID.
    """
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Posted Date,"):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            f"Cannot find 'Posted Date,' header in {filepath}.\n"
            "Expected BofA credit card export with header on first line."
        )

    reader = csv.DictReader(io.StringIO("".join(lines[header_idx:])))
    transactions = []

    for row in reader:
        raw_date = row.get("Posted Date", "").strip()
        if not raw_date:
            continue
        try:
            tx_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
        except ValueError:
            continue

        payee = row.get("Payee", "").strip()
        if not payee:
            continue

        raw_amount = row.get("Amount", "").strip()
        if not raw_amount:
            continue
        try:
            bofa_amount = _parse_amount(raw_amount)
        except ValueError:
            continue

        # BofA CC: -163.20 charge → negate → +163.20 Plaid expense.
        # BofA CC: +54529.97 payment → negate → -54529.97 Plaid income (or internal transfer).
        plaid_amount = -bofa_amount

        ref_num = row.get("Reference Number", "").strip()
        transactions.append({
            "date": str(tx_date),
            "name": payee,
            "amount": plaid_amount,
            "pending": False,
            "account_id": "csv-credit",
            "personal_finance_category": {"primary": ""},
            "transaction_id": f"csv-cc-{ref_num}" if ref_num else None,
        })

    return transactions


def _write_dryrun_file(filepath: str, included: list, timestamp: str) -> str:
    """Write a complete per-file dry-run report. Returns the output path."""
    from collections import defaultdict

    stem = os.path.splitext(os.path.abspath(filepath))[0]
    out_path = f"{stem}.{timestamp}.dryrun.txt"

    cat_counts: dict[str, int]   = defaultdict(int)
    cat_totals: dict[str, float] = defaultdict(float)
    type_totals: dict[str, float] = {"Income": 0.0, "Expense": 0.0}

    for tx in included:
        cat    = tx.get("category", "Uncategorized")
        amount = tx.get("amount", 0.0)
        ttype  = tx.get("type", "Expense")
        cat_counts[cat] += 1
        cat_totals[cat] += amount
        type_totals[ttype] += amount

    income  = type_totals["Income"]
    expense = type_totals["Expense"]
    net     = income - expense

    sorted_txns = sorted(included, key=lambda t: t.get("date", ""))

    W = 115  # total line width

    with open(out_path, "w", encoding="utf-8") as f:
        # ── Header ──────────────────────────────────────────────────────────
        f.write(f"Source:    {os.path.abspath(filepath)}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Rows:      {len(included)}\n")
        f.write("\n")

        # ── Category breakdown ───────────────────────────────────────────────
        f.write("─" * W + "\n")
        f.write("Category Breakdown\n")
        f.write("─" * W + "\n")
        f.write(f"{'Category':<38} {'Count':>6}   {'Total':>12}\n")
        f.write("─" * W + "\n")
        for cat, total in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
            f.write(f"{cat:<38} {cat_counts[cat]:>6}   ${total:>11,.2f}\n")

        # ── Type / Net summary ───────────────────────────────────────────────
        f.write("\n")
        f.write("─" * W + "\n")
        f.write("Type Summary\n")
        f.write("─" * W + "\n")
        f.write(f"  Income:   ${income:>12,.2f}\n")
        f.write(f"  Expense:  ${expense:>12,.2f}\n")
        f.write(f"  Net:      ${net:>12,.2f}\n")

        # ── Full transaction table ───────────────────────────────────────────
        f.write("\n")
        f.write("─" * W + "\n")
        f.write("Transactions (all rows, sorted by date)\n")
        f.write("─" * W + "\n")
        f.write(f"{'Date':<12} {'Description':<46} {'Category':<32} {'Type':<8} {'Amount':>10}\n")
        f.write("─" * W + "\n")
        for tx in sorted_txns:
            f.write(
                f"{tx['date']:<12} "
                f"{tx['name'][:45]:<46} "
                f"{tx.get('category', '')[:31]:<32} "
                f"{tx.get('type', ''):<8} "
                f"${tx.get('amount', 0):>9,.2f}\n"
            )

    return out_path


def import_csvs(
    filepaths: list[str],
    csv_type: str,
    ledger_path: str,
    dry_run: bool = False,
    account_label: str = None,
) -> None:
    parser_fn = parse_checking_csv if csv_type == "checking" else parse_credit_card_csv
    default_label = "Checking" if csv_type == "checking" else "Credit Card"
    label = account_label or default_label

    rules = load_rules(RULES_PATH) if os.path.exists(RULES_PATH) else []
    if not rules:
        print("⚠️  No spending_rules.json — using Plaid PFC categories only.")

    timestamp    = datetime.now().strftime("%Y%m%d-%H%M%S")
    all_included: list = []
    dryrun_paths: list[str] = []

    for fp in filepaths:
        print(f"\n📄 {fp}")
        try:
            txns = parser_fn(fp)
        except Exception as e:
            print(f"  ❌ Parse failed: {e}")
            continue

        print(f"  Rows parsed: {len(txns)}")

        account_map = {tx["account_id"]: label for tx in txns}
        included, excluded = categorize_batch(txns, rules, account_map)
        print(f"  Included: {len(included)}  |  Excluded: {len(excluded)}")

        if dry_run:
            out_path = _write_dryrun_file(fp, included, timestamp)
            dryrun_paths.append(out_path)

        all_included.extend(included)

    if not all_included:
        print("\nNothing to write.")
        return

    total = len(all_included)
    print(f"\nTotal transactions to import: {total}")

    if dry_run:
        # Terminal preview — quick glance, not the full record
        print("\n── First 25 rows (terminal preview) ────────────────────────────────────────────")
        print(f"{'Date':<12} {'Description':<42} {'Category':<30} {'Type':<8} {'Amount':>9}")
        print("─" * 107)
        for tx in sorted(all_included, key=lambda t: t.get("date", ""))[:25]:
            print(
                f"{tx['date']:<12} "
                f"{tx['name'][:41]:<42} "
                f"{tx.get('category','')[:29]:<30} "
                f"{tx.get('type',''):<8} "
                f"${tx.get('amount', 0):>8,.2f}"
            )
        if total > 25:
            print(f"  ... and {total - 25} more")
        print("\n[dry-run] No changes written to ledger.")
        print("\nDry-run reports written:")
        for p in dryrun_paths:
            print(f"  {p}")
        return

    result = write_spending_ledger(ledger_path, all_included)
    print(f"✅ Done — {result['added']} added, {result['skipped']} skipped (duplicates)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Import BofA CSV statement(s) into the cashflow ledger"
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=["checking", "credit"],
        help="Statement type: 'checking' or 'credit'",
    )
    parser.add_argument("files", nargs="+", help="CSV file(s) to import")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and categorize but do not write to ledger",
    )
    parser.add_argument(
        "--account-label",
        metavar="LABEL",
        help='Override account label written to ledger (default: Checking or Credit Card)',
    )
    args = parser.parse_args()

    ledger_path = clean_env(os.getenv("SPENDING_LEDGER_FILE_PATH"), "SPENDING_LEDGER_FILE_PATH")
    if not ledger_path:
        print("❌ SPENDING_LEDGER_FILE_PATH not set in environment")
        sys.exit(1)

    import_csvs(
        args.files,
        csv_type=args.type,
        ledger_path=ledger_path,
        dry_run=args.dry_run,
        account_label=args.account_label,
    )
