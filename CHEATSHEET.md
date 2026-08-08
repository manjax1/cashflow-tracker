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
| `python src/main.py` | Run a sync: pull recent Plaid transactions (all linked items), categorize, process any new Adriana files in Drive, write ledger, upload to Drive. |
| `python src/main.py --from-date 2025-06-01` | Backfill from a specific date (YYYY-MM-DD; can't be in the future). |
| `python src/link_item.py shalini` | One-time: link Shalini's BofA login (opens Plaid Link in the browser; she enters her own credentials). Syncs ONLY her Premium Rewards Visa x3070, labeled "Shalini BoA VISA". |
| `python src/link_item.py primary` | Re-link your own BofA login (e.g. after Plaid asks for re-auth). |
| `python scripts/backfill_shalini.py --dry-run` | Preview the one-time historical pull of x3070 purchases (2025-06-25 → today), by category. |
| `python scripts/backfill_shalini.py` | Write x3070 purchases into the ledger like the other card (idempotent; dedups by transaction_id). |
| `python scripts/reconcile_shalini_cc.py` | Reconcile itemized x3070 purchases against the BofA bill-payments per cycle; flags coverage gaps. |
| `python src/api.py` | Run the sync API server locally. |
| `curl -X POST https://<sync-service>.railway.app/sync` | Trigger the deployed sync on Railway. |
| `curl -X POST https://<sync-service>.railway.app/sync/test` | Test connections (Plaid, Drive, email) without a full sync. |

*Adriana ledgers auto-process when a file named `Adriana Managed Properties Ledger - <Month> <Year>` lands in the Adriana Drive folder. Idempotent per month.*

*Multiple Plaid logins ("items"): the **primary** item (your BofA login) keeps everything except an exclude-list of masks; **additional** items use an include-list so only named accounts are pulled — this is how Shalini's card is added without double-counting the joint checking x5799. To activate on Railway, add the `PLAID_ACCESS_TOKEN_SHALINI` value (written to `.env` by `link_item.py shalini`) to the Railway environment, then the daily cloud sync includes it automatically.*

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

*Web upload accepts a PDF **or a phone photo** (JPEG/PNG/HEIC). If the matching card charge hasn't synced yet, the receipt is saved to a `PendingReceipts` sheet in the ledger and **auto-splits on the next daily sync** once the charge posts — so you can upload in the store and let it reconcile itself. The upload drawer lists queued receipts with a **Clear** button, and any still unmatched after `COSTCO_PENDING_EXPIRY_DAYS` (default **100**) are auto-expired. Reconciliations are noted in the daily sync email.*

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
| `python -m src.agent.evals invariants` | Data-integrity checks (no agent): asserts no transfer/card-payment row is counted as income. Also runs automatically inside `evals run`. |

---

## Rules management

| Command | Purpose |
|---|---|
| `python scripts/suggest_rules.py` | Propose keyword→category rules from the uncategorized transactions (dry-run + coverage preview + lint warnings). |
| `python scripts/suggest_rules.py --out rules.json` | Write proposals to an editable file; hand-fix, then `--apply-file rules.json`. |
| `python scripts/suggest_rules.py --apply-file rules.json` | Append your reviewed rules to `spending_rules.json`. |
| `python src/recategorize_ledger.py` / `--apply` | Re-apply all rules to existing rows (dry-run diff, then write). Second review gate. |
| `python scripts/push_rules_to_drive.py` | Upload `spending_rules.json` to Drive (the sync loads it — no redeploy). |
| `python scripts/compact_rules.py` | Generate minified `spending_rules.compact.json` (legacy `RULES_JSON` fallback). |
| `python scripts/compact_rules.py --print` | Same, also print to stdout. |
| `python scripts/rename_category.py "Old" "New"` | Rename a category across the ledger (backs up first). |
| `python scripts/rename_category.py "Old" "New" --keyword GFLP` | Rename only rows whose description contains the keyword. |
| `python scripts/exclude_shalini_cc_payments.py --dry-run` | Preview neutralizing internal-transfer legs that leaked into totals: the x3070 lump bill-payments (checking side) AND `FROM CHK 5799` card-side payment credits (both x0605 and x3070, wrongly booked as income). |
| `python scripts/exclude_shalini_cc_payments.py` | Apply it: sets those rows to `Credit Card Payment` + `IncludeInNet=False` (backs up first). |
| `python src/csv_importer.py --type credit --account-label "Shalini BoA VISA" --before 2026-04-27 <statements.csv>` | Backfill x3070 history Plaid can't reach (pre-April-2026) from BofA statement CSVs; `--before` prevents overlap with Plaid. Add `--dry-run` to preview. |

---

## Rental margin (principal vs cash-flow)

`mortgage_pi.json` maps each rental mortgage debit **amount** → `{property, principal}` (nearest $10). Two analytics views are derived from it:

- **Cash-flow** (default): the full mortgage payment is an expense.
- **Margin**: the principal portion is treated as equity (excluded); interest, tax, insurance, mgmt, and maintenance stay as costs.

Where it shows up: the web dashboard's **Category × month** view has a Cash-flow ⇄ Margin toggle; the agent's `get_cashflow_summary` returns `rental_rollup.margin` + `principal_excluded` alongside the cash net (ask it for "rental margin excluding principal"); and the spreadsheet **Monthly Summary** shows a principal memo row + a `NET — MARGIN` row beside `NET INCOME (Cash-flow)`.

*When a mortgage payment amount changes (escrow re-analysis) or a property is added, add the new amount to `mortgage_pi.json` and commit. It's deployed with the code (not on Drive).*

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
- **Add Shalini's x3070 card end-to-end** (run locally — needs Plaid pkg + network):
  1. `python src/link_item.py shalini` — Shalini completes Plaid Link with her BofA credentials. *(done)*
  2. `python scripts/push_rules_to_drive.py` — publish the updated rule (x3070 bill-pay → excluded transfer).
  3. `python scripts/backfill_shalini.py --dry-run` then `python scripts/backfill_shalini.py` — pull x3070 purchases from 2025-06-25 and write them in.
  4. `python scripts/exclude_shalini_cc_payments.py` — neutralize the 3 historical lump payments (~$2,108). Run right after step 3 so there's no gap.
  5. `python scripts/reconcile_shalini_cc.py` — verify coverage per billing cycle; supply statement CSVs for any flagged gap.
  6. (CLI) `push` to sync Drive. `PLAID_ACCESS_TOKEN_SHALINI` is already on Railway, so the daily cloud sync keeps x3070 current.

  *Note: Plaid returns only ~90 days for BofA, so the initial backfill covers ~late-April 2026 on. For the June 2025–April 2026 gap, import the x3070 statement CSVs:*
  `python src/csv_importer.py --type credit --account-label "Shalini BoA VISA" --before 2026-04-27 --dry-run <csvs>` *then without `--dry-run`.*
  *Also re-run `push_rules_to_drive.py` + `exclude_shalini_cc_payments.py` after adding the `FROM CHK 5799` rule (fixes card-payment credits that were leaking as income on both cards).*
