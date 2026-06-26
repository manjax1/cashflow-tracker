import json

PFC_CATEGORY_MAP = {
    "FOOD_AND_DRINK": "Dining",
    "TRANSPORTATION": "Transportation",
    "ENTERTAINMENT": "Entertainment",
    "TRAVEL": "Travel",
    "MEDICAL": "Health and Fitness",
    "INCOME": "Income - Other",
    "TRANSFER_IN": "Income - Other",
    # Empty string: CSV-imported rows have no Plaid PFC data. Treat the same as
    # GENERAL_MERCHANDISE below — prevents refunds (negative amount) from hitting
    # the sign-based fallback and landing in "Income - Other".
    "": "Other - Uncategorized",
    # Explicitly mapped so refunds (negative amount) don't hit the sign-based fallback
    # and get miscategorized as Income - Other. TRANSFER_OUT and OTHER still fall through.
    "GENERAL_MERCHANDISE": "Other - Uncategorized",
    "BANK_FEES": "Other - Uncategorized",
    "PERSONAL_CARE": "Other - Uncategorized",
    "HOME_IMPROVEMENT": "Other - Uncategorized",
    "RENT_AND_UTILITIES": "Other - Uncategorized",
    "GOVERNMENT_AND_NON_PROFIT": "Other - Uncategorized",
    "LOAN_PAYMENTS": "Other - Uncategorized",
    "GENERAL_SERVICES": "Other - Uncategorized",
}


def load_rules(path: str) -> list:
    with open(path) as f:
        rules = json.load(f)
    # Longest keyword wins (most specific match first)
    return sorted(rules, key=lambda r: len(r["keyword"]), reverse=True)


# ── Special-case categorizers ─────────────────────────────────────────────────
# Each returns {category, note} if it claims the transaction, else None.
# They run before the keyword-rule loop in categorize().

def categorize_hippo_insurance(transaction: dict) -> dict | None:
    """Date-window routing for Hippo Insurance charges.

    Aug 16 – Sep 5 of any year = primary residence renewal (HCA-3456835-05).
    All other dates = one of the 6 rental property policies.
    """
    if "HIPPO INSURANCE" not in transaction.get("name", "").upper():
        return None
    from datetime import date as date_cls
    try:
        tx_date = date_cls.fromisoformat(transaction["date"])
    except (KeyError, ValueError):
        return None
    if date_cls(tx_date.year, 8, 16) <= tx_date <= date_cls(tx_date.year, 9, 5):
        return {
            "category": "Primary - Insurance-Home",
            "note": "Hippo - primary residence (HCA-3456835-05) - date-window match",
        }
    return {
        "category": "Rental - Insurance",
        "note": "Hippo - one of 6 rental property policies - date-window match",
    }


def categorize_retail_with_refunds(transaction: dict) -> dict | None:
    """Sign-aware routing for specific retail/restaurant merchants.

    Uses Plaid sign convention: positive amount = expense/purchase,
    negative amount = income/credit direction (refund).

    Each merchant maps to its own purchase-side category; all share
    "Income - Retail Refunds" on the refund side.
    """
    RETAILER_CATEGORIES = {
        "ROSS STORES":  "Shopping",
        "UNIQLO":       "Shopping",
        "MARSHALLS":    "Shopping",
        "NORDSTROM":    "Shopping",
        "NIKE":         "Shopping",
        "COSTCO WHSE":  "Other Credit Card Expenses",
        "CHIPOTLE":     "Dining",
        "TARGET":       "Other - Uncategorized",
    }
    name_upper = transaction.get("name", "").upper()
    purchase_category = next(
        (cat for kw, cat in RETAILER_CATEGORIES.items() if kw in name_upper),
        None,
    )
    if purchase_category is None:
        return None
    amount = transaction.get("amount", 0.0)
    if amount < 0:
        return {
            "category": "Income - Retail Refunds",
            "note": "Merchandise return - offsets prior purchase",
        }
    return {
        "category": purchase_category,
        "note": "Retail purchase",
    }


_SPECIAL_CASE_FNS = (categorize_hippo_insurance, categorize_retail_with_refunds)

# ─────────────────────────────────────────────────────────────────────────────


def categorize(transaction: dict, rules: list, account_label: str = "") -> dict:
    name = transaction.get("name", "")
    amount = transaction.get("amount", 0.0)

    # Special-case functions run first, before keyword rules.
    for special_fn in _SPECIAL_CASE_FNS:
        special = special_fn(transaction)
        if special is not None:
            tx_type = "Income" if amount < 0 else "Expense"
            return {
                "excluded": False,
                "category": special["category"],
                "account_label": account_label,
                "amount": abs(amount),
                "type": tx_type,
                "exclude_from_net": False,
            }

    for rule in rules:
        if rule["keyword"].lower() in name.lower():
            cat = rule["category"]
            note = rule.get("note", "")

            if cat == "EXCLUDE_ZERO":
                return {"excluded": True, "reason": note or "zero-value notification", "category": None}

            # Fully excluded categories: appear in no totals and no sheets
            if cat in ("Credit Card Payment", "Internal Transfer"):
                return {
                    "excluded": True,
                    "reason": f"{cat} - internal transfer",
                    "category": cat,
                }

            tx_type = "Income" if amount < 0 else "Expense"
            # Visible in Transactions sheet but excluded from Income/Expense/Net totals
            exclude_from_net = (cat == "One-Off - Non-Recurring (excluded from Net)")
            return {
                "excluded": False,
                "category": cat,
                "account_label": account_label,
                "amount": abs(amount),
                "type": tx_type,
                "exclude_from_net": exclude_from_net,
            }

    # Fall back to Plaid personal_finance_category
    pfc_primary = transaction.get("personal_finance_category", {}).get("primary", "")
    category = PFC_CATEGORY_MAP.get(pfc_primary)
    if category is None:
        category = "Income - Other" if amount < 0 else "Other - Uncategorized"

    tx_type = "Income" if amount < 0 else "Expense"
    return {
        "excluded": False,
        "category": category,
        "account_label": account_label,
        "amount": abs(amount),
        "type": tx_type,
        "exclude_from_net": False,
    }


def categorize_batch(transactions: list, rules: list, account_map: dict = None) -> tuple:
    account_map = account_map or {}
    included, excluded = [], []
    for tx in transactions:
        if tx.get("pending"):
            continue
        label = account_map.get(tx.get("account_id", ""), "Unknown")
        result = categorize(tx, rules, account_label=label)
        enriched = {**tx, **result}
        if result["excluded"]:
            excluded.append(enriched)
        else:
            included.append(enriched)
    return included, excluded
