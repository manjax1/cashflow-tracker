# spending-tracker

Tracks personal spending across Bank of America checking and credit card accounts via Plaid. Generates a categorized Excel ledger with monthly/YTD/YoY dashboards, syncs to Google Drive, and sends a weekly email summary.

---

## Features

- **Plaid Link** browser flow to connect your bank account (one-time setup)
- **Custom keyword rules** with fallback to Plaid's Personal Finance Categories (PFC)
- **EXCLUDE_RENTAL** pattern — silently drops transactions that belong to a separate rental property tracker running on the same bank account
- **Credit card payment deduplication** — internal transfers never count as spend
- **Excel dashboard**: Transactions, Monthly Summary (stacked bar chart), YTD Summary, YoY Trends
- **Google Drive sync** — ledger auto-uploads after each run (cloud mode)
- **Email summary** with top categories and full transaction list (Resend → Gmail → SendGrid fallback)
- **Railway-ready** — Dockerfile, railway.json, health check endpoint, `/sync` API

---

## Setup

### 1. Clone and create venv

```bash
cd /Users/manjax/Documents/Code/AI/spending-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.template .env
# Fill in: PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV, RESEND_API_KEY,
#          EMAIL_RECIPIENT, GOOGLE_DRIVE_FILE_ID, GOOGLE_SERVICE_ACCOUNT_JSON,
#          SPENDING_LEDGER_FILE_PATH
```

You can reuse the same `PLAID_CLIENT_ID`/`PLAID_SECRET` from any other project on your Plaid developer account. `PLAID_ACCESS_TOKEN` is left blank — it's filled in automatically by the Link flow.

### 3. Configure spending rules

```bash
cp spending_rules.template.json spending_rules.json
# Edit spending_rules.json to match your actual merchant names
```

Rules are matched case-insensitively. Longest keyword wins (most specific match first).

| Category value | Effect |
|---|---|
| Any string | Tags the transaction with that category |
| `EXCLUDE_RENTAL` | Silently excludes — rental/property transactions |
| `Credit Card Payment` | Excluded from spend totals (internal transfer) |

### 4. First run — connect your bank account

```bash
python src/main.py
```

A browser window opens at `http://localhost:5050`. Complete the Plaid Link flow. Your `PLAID_ACCESS_TOKEN` is saved to `.env` automatically.

### 5. Backfill historical data

```bash
python src/main.py --from-date 2025-06-01
```

---

## Usage

```bash
# Weekly sync (last 7 days)
python src/main.py

# Custom date range
python src/main.py --from-date 2025-01-01

# Run API server locally
python src/api.py

# Test connections
curl -X POST http://localhost:8080/sync/test
```

---

## How categorization works

1. **Custom rules** (`spending_rules.json`) are checked first — longest keyword match wins
2. If no rule matches, **Plaid's PFC** `primary` category is used (displayed as "Plaid: Food & Drink" etc.)
3. If neither matches, the transaction is tagged `Uncategorized`

Pending transactions are always skipped — they're picked up on the next sync once settled.

---

## EXCLUDE_RENTAL pattern

If you have a rental property tracked on the same Bank of America account, add rules like:

```json
{"keyword": "PROPERTY MGMT CO NAME", "category": "EXCLUDE_RENTAL", "note": "Property management fee"}
```

The sync email shows an "excluded rental" count so you can verify the filter is working correctly.

---

## Railway deploy

```bash
# Set all env vars in Railway dashboard, then:
railway up
```

Trigger a manual sync:
```bash
curl -X POST https://your-service.railway.app/sync
```

---

## Local scheduler (macOS)

```bash
chmod +x scripts/*.sh
./scripts/install_scheduler.sh   # runs every Monday 7:00 AM via launchd
./scripts/uninstall_scheduler.sh
```
