#!/usr/bin/env python3
"""
Aggregate dry-run: categorize all 18 BofA CSVs (9 checking + 9 credit,
July 2025 – March 2026) and write ONE consolidated report.

Read-only — never writes to the real ledger.
Per-file .dryrun.txt files are also generated as a side-effect of calling
_write_dryrun_file (the existing report builder we import from csv_importer).
"""

import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from csv_importer import (
    parse_checking_csv,
    parse_credit_card_csv,
    _write_dryrun_file,
    RULES_PATH,
)
from filters import load_rules, categorize_batch

# ── File lists (chronological) ────────────────────────────────────────────────
CHECKING_FILES = [
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-7-28-25.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-8-26-25.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-9-25-25.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-10-28-25.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-11-24-25.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-12-26-25.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-1-27-26.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-2-24-26.csv",
    "/Users/manjax/Downloads/BoA/BoA-Checking-account-5799-period-ending-3-26-26.csv",
]
CREDIT_FILES = [
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-July-2025.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Aug-2025.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Sep-2025.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Oct-2025.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Nov-2025.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Dec-2025.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Jan-2026.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Feb-2026.csv",
    "/Users/manjax/Downloads/BoA/BoA-Credit-card-account-0605-Mar-2026.csv",
]

OUTPUT_PATH = "/Users/manjax/Downloads/BoA/CONSOLIDATED_DRYRUN_2025-07_to_2026-03_FINAL.txt"

W = 115  # line width — matches _write_dryrun_file exactly


def _write_section(out, idx: int, acct_label: str, fp: str, timestamp: str,
                   included: list, excluded: list) -> None:
    """Write one file's section into the combined output file `out`."""
    basename = os.path.basename(fp)

    dates = sorted(tx.get("date", "") for tx in included if tx.get("date"))
    period = f"{dates[0]} → {dates[-1]}" if dates else "no transactions"

    # ── Section header ────────────────────────────────────────────────────────
    out.write("=" * W + "\n")
    out.write(
        f"FILE {idx:02d} of 18  │  {acct_label:<12}  │  {period}\n"
    )
    out.write(f"Source:    {fp}\n")
    out.write(f"Generated: {timestamp}\n")
    out.write(f"Rows:      {len(included)} included, {len(excluded)} excluded\n")
    out.write("=" * W + "\n\n")

    # ── Category breakdown (same format as _write_dryrun_file) ───────────────
    cat_counts: dict[str, int]   = defaultdict(int)
    cat_totals: dict[str, float] = defaultdict(float)
    type_totals: dict[str, float] = {"Income": 0.0, "Expense": 0.0}

    for tx in included:
        cat   = tx.get("category", "Uncategorized")
        amt   = tx.get("amount", 0.0)
        ttype = tx.get("type", "Expense")
        cat_counts[cat] += 1
        cat_totals[cat] += amt
        type_totals[ttype] += amt

    out.write("─" * W + "\n")
    out.write("Category Breakdown\n")
    out.write("─" * W + "\n")
    out.write(f"{'Category':<38} {'Count':>6}   {'Total':>12}\n")
    out.write("─" * W + "\n")
    for cat, total in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
        out.write(f"{cat:<38} {cat_counts[cat]:>6}   ${total:>11,.2f}\n")

    # ── Type / Net summary ────────────────────────────────────────────────────
    income  = type_totals["Income"]
    expense = type_totals["Expense"]
    net     = income - expense
    out.write("\n")
    out.write("─" * W + "\n")
    out.write("Type Summary\n")
    out.write("─" * W + "\n")
    out.write(f"  Income:   ${income:>12,.2f}\n")
    out.write(f"  Expense:  ${expense:>12,.2f}\n")
    out.write(f"  Net:      ${net:>12,.2f}\n")

    # ── Full transaction table (same columns as _write_dryrun_file) ───────────
    out.write("\n")
    out.write("─" * W + "\n")
    out.write("Transactions (all rows, sorted by date)\n")
    out.write("─" * W + "\n")
    out.write(f"{'Date':<12} {'Description':<46} {'Category':<32} {'Type':<8} {'Amount':>10}\n")
    out.write("─" * W + "\n")
    for tx in sorted(included, key=lambda t: t.get("date", "")):
        out.write(
            f"{tx['date']:<12} "
            f"{tx['name'][:45]:<46} "
            f"{tx.get('category', '')[:31]:<32} "
            f"{tx.get('type', ''):<8} "
            f"${tx.get('amount', 0):>9,.2f}\n"
        )
    out.write("\n")


def main() -> None:
    rules = load_rules(RULES_PATH) if os.path.exists(RULES_PATH) else []
    if not rules:
        print("⚠️  No spending_rules.json — using Plaid PFC categories only.")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Interleave: Checking July, CC July, Checking Aug, CC Aug, …
    ordered: list[tuple[str, str]] = []
    for chk, cc in zip(CHECKING_FILES, CREDIT_FILES):
        ordered.append(("checking", chk))
        ordered.append(("credit",   cc))

    # ── Process all 18 files ──────────────────────────────────────────────────
    grand_included: list = []
    grand_cat_counts: dict[str, int]   = defaultdict(int)
    grand_cat_totals: dict[str, float] = defaultdict(float)
    grand_type_totals: dict[str, float] = {"Income": 0.0, "Expense": 0.0}
    per_file_cats: list[set] = []   # one set of category names per file

    file_results: list[tuple] = []  # (acct_type, fp, included, excluded, error)

    for acct_type, fp in ordered:
        parse_fn   = parse_checking_csv if acct_type == "checking" else parse_credit_card_csv
        acct_label = "Checking" if acct_type == "checking" else "Credit Card"
        print(f"\n📄 [{acct_label}] {os.path.basename(fp)}")

        try:
            txns = parse_fn(fp)
        except Exception as e:
            print(f"  ❌ Parse failed: {e}")
            file_results.append((acct_type, fp, [], [], str(e)))
            per_file_cats.append(set())
            continue

        print(f"  Rows parsed: {len(txns)}")
        account_map = {tx["account_id"]: acct_label for tx in txns}
        included, excluded = categorize_batch(txns, rules, account_map)
        print(f"  Included: {len(included)}  │  Excluded: {len(excluded)}")

        # Generate the per-file .dryrun.txt (existing behaviour, reused as-is)
        per_path = _write_dryrun_file(fp, included, timestamp)
        print(f"  Per-file report: {os.path.basename(per_path)}")

        file_results.append((acct_type, fp, included, excluded, None))

        # Accumulate for grand totals
        file_cats: set = set()
        for tx in included:
            cat   = tx.get("category", "Uncategorized")
            amt   = tx.get("amount", 0.0)
            ttype = tx.get("type", "Expense")
            grand_cat_counts[cat] += 1
            grand_cat_totals[cat] += amt
            grand_type_totals[ttype] += amt
            file_cats.add(cat)
        grand_included.extend(included)
        per_file_cats.append(file_cats)

    # ── Write consolidated report ─────────────────────────────────────────────
    print(f"\n📝 Writing consolidated report …")
    with open(OUTPUT_PATH, "w", encoding="utf-8") as out:

        # Top-level header
        out.write("=" * W + "\n")
        out.write("CONSOLIDATED DRY-RUN REPORT\n")
        out.write("BofA Checking (5799) + Credit Card (0605)  │  July 2025 – March 2026\n")
        out.write(f"9 checking files + 9 credit files = 18 files total\n")
        out.write(f"Generated: {generated_at}\n")
        out.write("=" * W + "\n\n")

        # Per-file sections
        for idx, (acct_type, fp, included, excluded, error) in enumerate(file_results, start=1):
            acct_label = "Checking" if acct_type == "checking" else "Credit Card"
            if error:
                out.write("=" * W + "\n")
                out.write(f"FILE {idx:02d} of 18  │  {acct_label}  │  {os.path.basename(fp)}\n")
                out.write(f"Generated: {timestamp}\n")
                out.write("=" * W + "\n\n")
                out.write(f"❌ PARSE ERROR: {error}\n\n")
                continue

            _write_section(out, idx, acct_label, fp, generated_at, included, excluded)

        # ── Grand total section ───────────────────────────────────────────────
        grand_income  = grand_type_totals["Income"]
        grand_expense = grand_type_totals["Expense"]
        grand_net     = grand_income - grand_expense

        out.write("=" * W + "\n")
        out.write("GRAND TOTAL — July 2025 through March 2026  (all 18 files combined)\n")
        out.write("=" * W + "\n\n")

        total_files   = sum(1 for _, _, inc, _, err in file_results if err is None)
        out.write(f"  Files processed:        {total_files} / 18\n")
        out.write(f"  Total transactions:     {len(grand_included)}\n\n")

        # How many files each category appeared in
        cat_file_count: dict[str, int] = defaultdict(int)
        for fset in per_file_cats:
            for cat in fset:
                cat_file_count[cat] += 1

        # Combined category breakdown (+ file-count column)
        out.write("─" * W + "\n")
        out.write("Combined Category Breakdown (sorted by total $ amount)\n")
        out.write("─" * W + "\n")
        out.write(f"{'Category':<38} {'Count':>6}   {'Total':>12}   {'Files':>5}\n")
        out.write("─" * W + "\n")
        for cat, total in sorted(grand_cat_totals.items(), key=lambda x: x[1], reverse=True):
            out.write(
                f"{cat:<38} {grand_cat_counts[cat]:>6}   "
                f"${total:>11,.2f}   {cat_file_count[cat]:>4}/18\n"
            )

        # Combined type / net summary
        out.write("\n")
        out.write("─" * W + "\n")
        out.write("Combined Type Summary\n")
        out.write("─" * W + "\n")
        out.write(f"  Income:   ${grand_income:>12,.2f}\n")
        out.write(f"  Expense:  ${grand_expense:>12,.2f}\n")
        out.write(f"  Net:      ${grand_net:>12,.2f}\n")

        # Category file-coverage table (sanity check)
        out.write("\n")
        out.write("─" * W + "\n")
        out.write("Category File-Coverage  (how many of 18 files each category appeared in)\n")
        out.write("─" * W + "\n")
        for cat, count in sorted(cat_file_count.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * count + "░" * (18 - count)
            out.write(f"  {count:>2}/18  {bar}  {cat}\n")

        out.write("\n" + "=" * W + "\n")
        out.write("END OF REPORT\n")
        out.write("=" * W + "\n")

    # ── Terminal summary ──────────────────────────────────────────────────────
    size = os.path.getsize(OUTPUT_PATH)
    ok   = sum(1 for _, _, _, _, err in file_results if err is None)
    print(f"\n{'─'*60}")
    print(f"✅  Files processed:     {ok} / 18")
    print(f"    Total transactions:  {len(grand_included)}")
    print(f"    Output:              {OUTPUT_PATH}")
    print(f"    File size:           {size:,} bytes  ({size / 1024:.1f} KB)")
    print(f"{'─'*60}")


if __name__ == "__main__":
    main()
