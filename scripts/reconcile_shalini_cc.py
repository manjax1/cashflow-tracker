#!/usr/bin/env python3
"""Reconcile Shalini's itemized x3070 purchases (from the Plaid backfill) against
the actual BofA bill-payments pulled off her checking statement.

Logic: a credit-card bill payment settles the purchases accrued since the prior
payment. So for each payment we sum the itemized x3070 purchases posted in the
window (previous_payment_date, this_payment_date] and compare. Big mismatches or
a window with $0 of itemized purchases against a real payment flag a coverage gap
(e.g. Plaid didn't return that far back, or a category is being excluded wrongly).

The payment reference below is transcribed from the checking-account statement
(BoA - Credit card payments.pdf). Before May 2026 BofA labeled both cards
identically ("BANK OF AMERICA - CREDIT CARD Bill Payment"), so the x3070 amount
is the SMALLER of each month's pair (the larger is your x0605 Visa). From May 2026
the statement names the account (ACCT# 3070), so those three are certain.

Usage:
    python scripts/reconcile_shalini_cc.py
    python scripts/reconcile_shalini_cc.py --label "Shalini BoA VISA"
"""
import os
import sys
from datetime import date, datetime

import openpyxl
from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.environ.get("SPENDING_LEDGER_FILE_PATH", os.path.join(ROOT, "cashflow-tracker.xlsx"))
LABEL = "Shalini BoA VISA"

# One row per BofA statement cycle: (closing_date, payment_amount).
# Purchases posted in (previous_close, this_close] are settled by payment_amount
# (paid ~4 weeks later). Bucketing by CLOSING date — not payment date — is what
# makes each cycle's itemized purchases line up with its payment; the BofA cycle
# closes on the 24th. Amounts are each statement's net (verified to the penny
# against the June/July 2025 PDFs and the Aug 2025–Apr 2026 CSVs).
STATEMENTS = [
    ("2025-06-24", 304.65),
    ("2025-07-24", 6.36),
    ("2025-08-24", 506.64),
    ("2025-09-24", 23.09),
    ("2025-10-24", 733.65),
    ("2025-11-24", 2142.86),
    ("2025-12-24", 71.49),
    ("2026-01-24", 100.39),
    ("2026-02-24", 126.64),
    ("2026-03-24", 809.33),
    ("2026-04-24", 88.33),
    ("2026-05-24", 1646.80),
    ("2026-06-24", 373.12),
]


def _pd(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def main():
    label = LABEL
    if "--label" in sys.argv:
        label = sys.argv[sys.argv.index("--label") + 1]

    wb = openpyxl.load_workbook(LEDGER, read_only=True)
    ws = wb["Transactions"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    hdr = [str(c) for c in rows[0]]
    ix = {h: i for i, h in enumerate(hdr)}
    A, T, AM, CA, DT, INC = ix["Account"], ix["Type"], ix["Amount"], ix["Category"], ix["Date"], ix["IncludeInNet"]

    # Itemized purchases on the card (net of refunds), only IncludeInNet rows.
    purch = []
    for r in rows[1:]:
        if str(r[A]) != label:
            continue
        if r[INC] is False:
            continue
        amt = float(r[AM] or 0)
        signed = amt if str(r[T]) == "Expense" else -amt
        purch.append((_pd(r[DT]), signed, str(r[CA])))

    print(f"Ledger: {LEDGER}")
    print(f"Account label: '{label}'  —  {len(purch)} itemized rows found\n")
    if not purch:
        print("No itemized rows yet. Run scripts/backfill_shalini.py first, then re-run this.")
        return

    stmts = sorted([(_pd(d), amt) for d, amt in STATEMENTS])
    last_purch = max(d for d, _, _ in purch)

    print(f"{'Statement cycle (by close date)':34} {'Itemized':>11} {'Payment':>11} {'Diff':>10}")
    print("-" * 72)
    prev = None
    tot_item = tot_pay = 0.0
    flags = []
    for close, pamt in stmts:
        lo = prev
        window = [s for d, s, _ in purch if (lo is None or d > lo) and d <= close]
        item_sum = sum(window)
        diff = item_sum - pamt
        tot_item += item_sum
        tot_pay += pamt
        lo_str = lo.isoformat() if lo else "start"
        flag = ""
        if item_sum == 0:
            flag = "  ← no itemized purchases"
            flags.append((lo_str, close.isoformat(), pamt))
        elif abs(diff) > max(1.0, 0.02 * pamt):
            flag = "  ← check"
            flags.append((lo_str, close.isoformat(), pamt))
        print(f"{lo_str} → {close.isoformat():14} {item_sum:>11,.2f} {pamt:>11,.2f} {diff:>10,.2f}{flag}")
        prev = close

    # Purchases posted after the last statement close have not been billed/paid yet.
    trailing = sum(s for d, s, _ in purch if d > stmts[-1][0])
    print("-" * 72)
    print(f"{'TOTALS (billed cycles)':34} {tot_item:>11,.2f} {tot_pay:>11,.2f} {tot_item - tot_pay:>10,.2f}")
    if last_purch > stmts[-1][0]:
        print(f"\nUnbilled (posted after {stmts[-1][0].isoformat()}, not yet on a paid statement): "
              f"${trailing:,.2f}")
    if flags:
        print(f"\n{len(flags)} cycle(s) to review (diff > 2% or no purchases):")
        for lo, hi, amt in flags:
            print(f"    {lo} → {hi}  (payment ${amt:,.2f})")
    else:
        print("\nAll billed cycles reconcile to their payment. ✓")


if __name__ == "__main__":
    main()
