import json

PFC_CATEGORY_MAP = {
    "FOOD_AND_DRINK": "Plaid: Food & Drink",
    "GENERAL_MERCHANDISE": "Plaid: General Merchandise",
    "TRANSPORTATION": "Plaid: Transportation",
    "ENTERTAINMENT": "Plaid: Entertainment",
    "PERSONAL_CARE": "Plaid: Personal Care",
    "HOME_IMPROVEMENT": "Plaid: Home Improvement",
    "RENT_AND_UTILITIES": "Plaid: Rent & Utilities",
    "TRAVEL": "Plaid: Travel",
    "MEDICAL": "Plaid: Medical",
    "GOVERNMENT_AND_NON_PROFIT": "Plaid: Government & Non-Profit",
    "LOAN_PAYMENTS": "Plaid: Loan Payments",
    "BANK_FEES": "Plaid: Bank Fees",
    "TRANSFER_IN": "Plaid: Transfer In",
    "TRANSFER_OUT": "Plaid: Transfer Out",
    "INCOME": "Plaid: Income",
    "GENERAL_SERVICES": "Plaid: General Services",
    "OTHER": "Plaid: Other",
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

            if cat in ("EXCLUDE_RENTAL", "EXCLUDE_ZERO"):
                return {"excluded": True, "reason": note or cat, "category": None}

            if cat == "Credit Card Payment":
                return {
                    "excluded": True,
                    "reason": "Credit card payment - internal transfer",
                    "category": "Credit Card Payment",
                }

            tx_type = "Income" if amount < 0 else "Expense"
            return {
                "excluded": False,
                "category": cat,
                "account_label": account_label,
                "amount": abs(amount),
                "type": tx_type,
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
