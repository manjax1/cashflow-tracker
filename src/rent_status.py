"""Rent status engine: pending rent + arrears per rental property.

Reads the rent roll (rent_roll.json — expected monthly rent + how to attribute
payments to each property) and the ledger's Rental - Income rows, and computes,
per property: what's paid this month, what's still pending this month, the
cumulative arrears (or credit) since the tenant's start_month, the last month
that was fully paid, and the most recent payments (for the drill-down).

Design:
- Attribution is FIRST-MATCH-WINS in rent_roll order, so a payment is counted for
  exactly one property. Rental-Income rows that match no property are returned as
  'unmatched' (leakage / miscategorization to review) — never silently dropped.
- Payments are attributed to the calendar month of the transaction date. Split
  or partial payments within a month are summed. Timing mismatches (a July rent
  paid in June) net out in the running balance.
- Amounts come entirely from the ledger; expectations entirely from rent_roll.json.
"""
import json
import os
from collections import defaultdict
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENT_ROLL_PATH = os.path.join(REPO_ROOT, "rent_roll.json")
_TOL = 0.01


def load_rent_roll(path=RENT_ROLL_PATH):
    with open(path) as f:
        data = json.load(f)
    return [p for p in data.get("properties", []) if p.get("active", True)]


def load_ignore(path=RENT_ROLL_PATH):
    """Optional top-level 'ignore' list in rent_roll.json: description substrings
    for Rental-Income rows that are NOT current-tenant rent (historical
    pre-manager deposits, refunds, personal transfers). They're excluded from the
    'unmatched' review bucket so it only surfaces genuinely-missing tenants."""
    try:
        with open(path) as f:
            return [str(s).lower() for s in json.load(f).get("ignore", [])]
    except (OSError, ValueError):
        return []


def _ym(d):
    return str(d)[:7]


def _tenant_str(t):
    """tenant may be a string or a list of names — return a single display string."""
    return ", ".join(str(x) for x in t) if isinstance(t, (list, tuple)) else str(t or "")


def _month_range(start_ym, end_ym):
    y, m = int(start_ym[:4]), int(start_ym[5:7])
    ey, em = int(end_ym[:4]), int(end_ym[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _matches(tx, prop):
    """A transaction belongs to a property if EITHER its Account equals the
    property's account_label (Adriana statement) OR its Description contains any
    name in name_any (direct payer). Supporting both lets one property collect an
    Adriana statement AND direct Zelle payments (e.g. an Adriana-managed unit that
    also receives a tenant's Zelle)."""
    m = prop.get("match", {})
    if "account_label" in m and str(tx.get("Account", "")) == m["account_label"]:
        return True
    if "name_any" in m:
        if m.get("account") and str(tx.get("Account", "")).lower() != m["account"].lower():
            return False
        desc = str(tx.get("Description", "")).lower()
        names = m["name_any"]
        if isinstance(names, str):
            names = [names]                 # tolerate a bare string instead of a list
        return any(_name_in(desc, n) for n in names)
    return False


def _name_in(desc, token):
    """A name token matches if EVERY word in it appears in the description
    (order-independent). So 'Almitra Perry' matches 'ALMITRA Y PERRY' and
    'Ruby Roberts' matches 'RUBY ANNETTE ROBERTS' — a bank-inserted middle
    name/initial no longer breaks the match. Single-word tokens (a surname) still
    work as a simple contains-check."""
    words = [w for w in str(token).lower().split() if w]
    return bool(words) and all(w in desc for w in words)


def _rental_income_rows(rows):
    return [t for t in rows
            if t.get("Type") == "Income"
            and "rental" in str(t.get("Category", "")).lower()
            and t.get("IncludeInNet", True)]


def compute_status(rows, as_of=None, roll=None, recent_n=6):
    """Return {'as_of', 'as_of_month', 'properties': [...], 'totals': {...},
    'unmatched': {...}}. `rows` is the full effective ledger (or any list with
    Date/Account/Description/Category/Type/Amount)."""
    roll = roll if roll is not None else load_rent_roll()
    as_of = as_of or date.today().isoformat()
    as_of_month = _ym(as_of)
    income = _rental_income_rows(rows)

    # First-match-wins attribution.
    matched_by_label = defaultdict(list)
    used = set()
    for prop in roll:
        for i, tx in enumerate(income):
            if i in used:
                continue
            if _matches(tx, prop):
                matched_by_label[prop["label"]].append(tx)
                used.add(i)
    ignore = load_ignore()
    def _ignored(tx):
        d = str(tx.get("Description", "")).lower()
        return any(s in d for s in ignore)
    unmatched = [tx for i, tx in enumerate(income) if i not in used and not _ignored(tx)]

    props_out = []
    tot_pending = tot_arrears = tot_credit = tot_rent = 0.0
    for prop in roll:
        rent = float(prop.get("monthly_rent") or 0)
        txs = matched_by_label.get(prop["label"], [])
        by_month = defaultdict(float)
        for tx in txs:
            by_month[_ym(tx["Date"])] += float(tx.get("Amount") or 0)

        start = prop.get("start_month") or as_of_month
        months = _month_range(start, as_of_month) if start <= as_of_month else [as_of_month]
        expected_total = rent * len(months)
        paid_in_scope = sum(by_month.get(m, 0.0) for m in months)
        balance = expected_total - paid_in_scope        # + = owed, - = credit
        arrears = max(0.0, round(balance, 2))
        credit = max(0.0, round(-balance, 2))

        paid_this = round(by_month.get(as_of_month, 0.0), 2)
        pending_this = max(0.0, round(rent - paid_this, 2))

        # A vacant unit isn't in arrears — it's a vacancy, not an unpaid tenant.
        vacant = bool(prop.get("vacant"))
        if vacant:
            arrears = 0.0
            pending_this = 0.0

        last_paid = ""
        for m in reversed(months):
            if by_month.get(m, 0.0) + _TOL >= rent and rent > 0:
                last_paid = m
                break

        # Most recent month with ANY payment, flagged partial if under a full month's rent.
        paid_months = sorted(m for m, v in by_month.items() if v > 0)
        last_paid_month = paid_months[-1] if paid_months else ""
        last_paid_partial = bool(last_paid_month) and rent > 0 and \
            by_month.get(last_paid_month, 0.0) + _TOL < rent
        months_behind = round(arrears / rent, 1) if rent > 0 else 0.0

        recent = sorted(txs, key=lambda t: t["Date"], reverse=True)[:recent_n]
        recent_out = [{"date": t["Date"], "amount": round(float(t.get("Amount") or 0), 2),
                       "month": _ym(t["Date"]),
                       "description": str(t.get("Description", ""))[:80]} for t in recent]

        props_out.append({
            "label": prop["label"], "property": prop.get("property", prop["label"]),
            "tenant": _tenant_str(prop.get("tenant", "")), "monthly_rent": round(rent, 2),
            "managed_by": prop.get("managed_by", ""),
            "paid_this_month": paid_this, "pending_this_month": pending_this,
            "arrears": arrears, "credit": credit,
            "months_behind": months_behind,
            "last_fully_paid_month": last_paid,
            "last_paid_month": last_paid_month, "last_paid_partial": last_paid_partial,
            "last_payment_date": max((t["Date"] for t in txs), default=""),
            "months_tracked": len(months), "start_month": start,
            "recent_payments": recent_out, "vacant": vacant,
            "verify": bool(prop.get("verify")), "note": prop.get("note", ""),
        })
        tot_pending += pending_this
        tot_arrears += arrears
        tot_credit += credit
        tot_rent += rent

    # Worst-first: most months behind, then largest dollar arrears, then pending.
    props_out.sort(key=lambda p: (p["months_behind"], p["arrears"], p["pending_this_month"]),
                   reverse=True)
    unmatched_total = round(sum(float(t.get("Amount") or 0) for t in unmatched), 2)
    return {
        "as_of": as_of, "as_of_month": as_of_month,
        "properties": props_out,
        "totals": {
            "monthly_rent_roll": round(tot_rent, 2),
            "pending_this_month": round(tot_pending, 2),
            "total_arrears": round(tot_arrears, 2),
            "total_credit": round(tot_credit, 2),
            "properties": len(props_out),
        },
        "unmatched": {
            "count": len(unmatched), "total": unmatched_total,
            "transactions": [{"date": t["Date"], "amount": round(float(t.get("Amount") or 0), 2),
                              "account": t.get("Account", ""),
                              "description": str(t.get("Description", ""))[:80]}
                             for t in sorted(unmatched, key=lambda t: t["Date"], reverse=True)[:25]],
            "note": "Rental-Income rows matching no rent-roll property — review: a new "
                    "tenant to add, or a miscategorized row.",
        },
    }


def property_detail(rows, label, as_of=None, roll=None, months_back=12):
    """Per-property drill-down: month-by-month expected vs paid (with fully-paid
    flag) plus every payment in the window."""
    roll = roll if roll is not None else load_rent_roll()
    prop = next((p for p in roll if p["label"] == label), None)
    if not prop:
        return {"error": f"unknown property label: {label}"}
    as_of = as_of or date.today().isoformat()
    as_of_month = _ym(as_of)
    rent = float(prop.get("monthly_rent") or 0)
    income = _rental_income_rows(rows)
    txs = [t for t in income if _matches(t, prop)]

    by_month_txs = defaultdict(list)
    for t in txs:
        by_month_txs[_ym(t["Date"])].append(t)

    start = prop.get("start_month") or as_of_month
    all_months = _month_range(start, as_of_month) if start <= as_of_month else [as_of_month]
    months = all_months[-months_back:]
    month_rows = []
    for m in months:
        paid = round(sum(float(t.get("Amount") or 0) for t in by_month_txs.get(m, [])), 2)
        month_rows.append({"month": m, "expected": round(rent, 2), "paid": paid,
                           "shortfall": max(0.0, round(rent - paid, 2)),
                           "fully_paid": paid + _TOL >= rent and rent > 0})
    payments = sorted(txs, key=lambda t: t["Date"], reverse=True)
    payments = [t for t in payments if _ym(t["Date"]) >= months[0]] if months else payments
    return {
        "label": label, "property": prop.get("property", label),
        "tenant": _tenant_str(prop.get("tenant", "")), "monthly_rent": round(rent, 2),
        "months": month_rows,
        "payments": [{"date": t["Date"], "amount": round(float(t.get("Amount") or 0), 2),
                      "description": str(t.get("Description", ""))[:100]} for t in payments],
    }
