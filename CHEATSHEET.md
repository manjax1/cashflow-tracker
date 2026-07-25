# Cashflow Tracker — Command Cheatsheet

All commands run from the repo root with the venv active:
```bash
cd /Users/manjax/Documents/Code/AI/cashflow-tracker
source .venv/bin/activate
```

---

## Data sync (Plaid + Adriana rental ledgers)

| Command | Purpose |
|---|---|
| `python src/main.py` | Run a sync: pull recent Plaid transactions, categorize, process any new Adriana files in Drive, write ledger, upload to Drive. |
| `python src/main.py --from-date 2025-06-01` | Backfill from a specific date (YYYY-MM-DD; can't be in the future). |
| `python src/api.py` | Run the sync API server locally. |
| `curl -X POST https://<sync-service>.railway.app/sync` | Trigger the deployed sync on Railway. |
| `curl -X POST https://<sync-service>.railway.app/sync/test` | Test connections (Plaid, Drive, email) without a full sync. |

*Adriana ledgers auto-process when a file named `Adriana Managed Properties Ledger - <Month> <Year>` lands in the Adriana Drive folder. Idempotent per month.*

---

## Agent — CLI (interactive financial analyst)

| Command | Purpose |
|---|---|
| `python -m src.agent.cli` | Start the interactive agent. |
| `python -m src.agent.cli --verbose` | Same, printing each tool call live. |
| `python -m src.agent.cli --refresh` | Pull the latest ledger from Drive, then start. |

**Inside the CLI** (type at the `you>` prompt): `refresh` (Drive→local), `push` (local→Drive), `history`, `!N` (edit & rerun command N), `quit`.

---

## Agent — Web app & MCP server

| Command | Purpose |
|---|---|
| `python -m src.agent.web` | Run the 2FA web app locally (dashboard, chat, search, admin tools). |
| `python -m src.agent.mcp_server` | Run the MCP server (normally Claude Desktop launches this itself via config). |

---

## Amazon invoices

| Command | Purpose |
|---|---|
| `python -m src.agent.invoices ingest --dry-run` | Parse + classify invoice PDFs/emails in `invoices_inbox/` (preview only). |
| `python -m src.agent.invoices ingest` | Same, saving order JSON to `invoices_data/`. |
| `python -m src.agent.invoices import-export --dry-run` | Preview the Amazon data-export → order-JSON backfill. |
| `python -m src.agent.invoices import-export` | Run the export backfill (classifies all product names, cached). |
| `python -m src.agent.invoices reclassify` | Re-run classification (e.g. after adding a category); rewrites saved orders. |
| `python -m src.agent.invoices apply --dry-run` | Preview importing orders as ledger rows + which autopay rows flip. |
| `python -m src.agent.invoices apply` | Import Amazon orders as "Amazon Visa" spend rows (item-level). |
| `python -m src.agent.invoices list` | List extracted orders. |

---

## Gmail ingestion (Amazon order emails)

| Command | Purpose |
|---|---|
| `python -m src.agent.gmail_ingest auth` | One-time OAuth consent (read-only Gmail). |
| `python -m src.agent.gmail_ingest sync --days 30` | Find + extract Amazon order emails from the last N days. |
| `python -m src.agent.gmail_ingest sync --days 30 --dry-run` | Same, preview only. |

---

## Costco receipts (splits)

| Command | Purpose |
|---|---|
| `python -m src.agent.costco extract --dry-run` | Parse + classify receipts in `Costco-Purchases/` (preview). |
| `python -m src.agent.costco extract` | Same, saving to `costco_data/`. |
| `python -m src.agent.costco reconcile` | Match receipts to ledger charges; show proposed splits (dry-run). |
| `python -m src.agent.costco reconcile apply` | Write the item-level splits to the Splits sheet. |

---

## Eval harness

| Command | Purpose |
|---|---|
| `python -m src.agent.evals run` | Run all eval cases against the live agent. |
| `python -m src.agent.evals run --tag rental` | Run only cases with a given tag. |
| `python -m src.agent.evals run --baseline last` | Run + diff against the previous run (regression). |
| `python -m src.agent.evals run --verbose` | Print the agent's answer/tools for any failing case. |
| `python -m src.agent.evals list` | List all cases. |
| `python -m src.agent.evals harvest` | Pull the Drive chat log → candidate cases in `evals/candidates.jsonl`. |

---

## Rules management

| Command | Purpose |
|---|---|
| `python scripts/push_rules_to_drive.py` | Upload `spending_rules.json` to Drive (the sync loads it — no redeploy). |
| `python scripts/compact_rules.py` | Generate minified `spending_rules.compact.json` (legacy `RULES_JSON` fallback). |
| `python scripts/compact_rules.py --print` | Same, also print to stdout. |
| `python scripts/rename_category.py "Old" "New"` | Rename a category across the ledger (backs up first). |
| `python scripts/rename_category.py "Old" "New" --keyword GFLP` | Rename only rows whose description contains the keyword. |

---

## Users (web 2FA)

| Command | Purpose |
|---|---|
| `python scripts/manage_users.py add <username>` | Create a web user (prompts password, prints 2FA QR). |
| `python scripts/manage_users.py list` | List users. |
| `python scripts/manage_users.py remove <username>` | Remove a user. |
| `python scripts/manage_users.py export` | Print the compact `USERS_JSON` value for Railway. |

---

## Google Drive helpers

| Command | Purpose |
|---|---|
| `python scripts/share_drive_file.py --file <ID> --email you@gmail.com --folder <FOLDER_ID>` | Share/move a service-account-related Drive file to your account. |
| `python scripts/create_drive_file.py "name"` | Create an empty Drive file (⚠ SA-owned files can't hold content — prefer uploading a file yourself). |

*Reminder: Drive files used by the app (ledger, `spending_rules.json`, chat log) must be plain files you own (Type "Unknown"/"Binary"), not Google Docs — and the env var must point at the **file** ID, not the folder ID.*

---

## Local scheduler (macOS launchd)

| Command | Purpose |
|---|---|
| `./scripts/install_scheduler.sh` | Install the weekly sync (Mondays 7:00 AM). |
| `./scripts/uninstall_scheduler.sh` | Remove it. |

---

## Deploy

```bash
git add -A && git commit -m "..." && git push        # Railway auto-redeploys on push
pip install -r requirements.txt                      # after new dependencies
```

---

## Typical workflows

- **After a rule change:** edit `spending_rules.json` → `python scripts/push_rules_to_drive.py` → commit.
- **New Amazon orders:** `gmail_ingest sync --days 30` → `invoices apply` → (CLI) `push`.
- **New Costco receipts:** drop PDFs in `Costco-Purchases/` → `costco extract` → `costco reconcile apply` → (CLI) `push`.
- **Fix a miscategorization fast:** ask the admin web agent, or `rename_category.py` for a bulk/typo fix.
- **Before any prompt/model change:** `python -m src.agent.evals run --baseline last`.
