import json

PFC_CATEGORY_MAP = {
    "FOOD_AND_DRINK": "Food & Drink",
    "GENERAL_MERCHANDISE": "General Merchandise",
    "TRANSPORTATION": "Transportation",
    "ENTERTAINMENT": "Entertainment",
    "PERSONAL_CARE": "Personal Care",
    "HOME_IMPROVEMENT": "Home Improvement",
    "RENT_AND_UTILITIES": "Rent & Utilities",
    "TRAVEL": "Travel",
    "MEDICAL": "Medical",
    "GOVERNMENT_AND_NON_PROFIT": "Government & Non-Profit",
    "LOAN_PAYMENTS": "Loan Payments",
    "BANK_FEES": "Bank Fees",
    "TRANSFER_IN": "Transfer In",
    "TRANSFER_OUT": "Transfer Out",
    "INCOME": "Income",
    "GENERAL_SERVICES": "General Services",
    "OTHER": "Other",
}


def load_rules(path: str) -> list:
    with open(path) as f:
        rules = json.load(f)
    # Longest keyword wins (most specific match first)
    return sorted(rules, key=lambda r: len(r["keyword"]), reverse=True)


def categorize(transaction: dict, rules: list, account_label: str = "") -> dict:
    name = transaction.get("name", "")
    amount = transaction.get("amount", 0.0)

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
    category = PFC_CATEGORY_MAP.get(pfc_primary, "Uncategorized")

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
