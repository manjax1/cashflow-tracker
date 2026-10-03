# Deployment & configuration

How the Cashflow Tracker is deployed, what each piece needs, and the gotchas that
have actually bitten us. Keep this current when the deployment changes.

> This file is committed to git — **never put secret values or personal data in
> it.** Only variable *names* and structure.

---

## Topology: two Railway services

The app runs as **two separate Railway services**. They share the same codebase
(this repo) but have **independent environments** — a variable set on one is NOT
visible to the other. This is the single biggest source of confusion, so it's
first.

| Role | What it does | Public URL |
| --- | --- | --- |
| **Web front-end** | Serves the website + `/api/*` (dashboard, search, 🏠 Rent tab, agent chat, receipt upload, recategorization). Reads the ledger + config files from Google Drive. | `cashflow.tikitansy.com` |
| **Sync / backend** | Runs the scheduled sync (`src/main.py`): Plaid pull, categorize, write ledger, Adriana import, rent sheets, reconciliation, email. | `web-production-0a569.up.railway.app` |

> **Tip:** rename the services in Railway to `cashflow-web` and `cashflow-sync`
> so it's obvious which is which. If you ever move both into a *single* Railway
> project, use **Shared Variables** (`${{shared.NAME}}`) so each var is defined
> once instead of per-service.

---

## The golden rule

**Shared config must be set on BOTH services.** When you add or change a variable
that both need (anything Drive/Google/Anthropic related), set it in *both*
environments, then redeploy *both*. The classic failure mode (we hit it): the
Rent tab 404'd / showed "not found" because `RENT_ROLL_DRIVE_FILE_ID` was set on
the sync service but not the web service.

---

## Environment variables

Legend: ✅ required · ◻️ optional · — not used.

| Variable | Web | Sync | Purpose |
| --- | :--: | :--: | --- |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ | ✅ | Service-account creds for all Drive access |
| `GOOGLE_DRIVE_FILE_ID` | ✅ | ✅ | The master ledger `.xlsx` on Drive |
| `RULES_DRIVE_FILE_ID` | ✅ | ✅ | `spending_rules.json` on Drive |
| `RENT_ROLL_DRIVE_FILE_ID` | ✅ | ✅ | `rent_roll.json` on Drive (Rent tab + rent sheets) |
| `MORTGAGE_PI_DRIVE_FILE_ID` | ✅ | ✅ | `mortgage_pi.json` on Drive (margin view) |
| `CHATLOG_DRIVE_FILE_ID` | ◻️ | ◻️ | Agent chat-log sink, if used |
| `ANTHROPIC_API_KEY` | ✅ | ✅ | Agent chat (web) + Costco vision / classification (sync) |
| `PLAID_CLIENT_ID` / `PLAID_SECRET` / `PLAID_ENV` | — | ✅ | Bank connection |
| `PLAID_ACCESS_TOKEN` (+ `_SHALINI`) | — | ✅ | Linked bank items |
| `RESEND_API_KEY` / `SENDGRID_API_KEY` / `EMAIL_PASS` | — | ✅ | Daily-summary email providers (fallback chain) |
| `EMAIL_SENDER` / `EMAIL_RECIPIENT` | — | ✅ | Email from/to |
| `SPENDING_LEDGER_FILE_PATH` | ◻️ | ◻️ | Local ledger path override (mainly local/MCP) |
| `TZ` | ◻️ | ✅ | Timezone for the scheduled run |

If unsure where a new variable belongs: anything the **web** reads at request
time (config, Drive, Anthropic) goes on **both**; Plaid/email are **sync-only**.

---

## Config files delivered via Google Drive (NOT in git)

These hold personal data / tenant PII and are gitignored. They reach the deployed
services at runtime by downloading from Drive when the local file is absent
(`drive_sync.ensure_file_from_drive`), so Railway's ephemeral disk always gets a
fresh copy. Local dev keeps its own copy and is never overwritten.

| File | Drive id var | Updated by |
| --- | --- | --- |
| `cashflow-tracker.xlsx` (ledger) | `GOOGLE_DRIVE_FILE_ID` | the sync itself (upload after each run) |
| `spending_rules.json` | `RULES_DRIVE_FILE_ID` | `scripts/push_rules_to_drive.py` |
| `rent_roll.json` | `RENT_ROLL_DRIVE_FILE_ID` | `scripts/push_config_to_drive.py` |
| `mortgage_pi.json` | `MORTGAGE_PI_DRIVE_FILE_ID` | `scripts/push_config_to_drive.py` |

### First-time setup for a Drive config file

Service accounts have **no storage quota** on personal Gmail, so they can't
*create* Drive files — you create/own the file and share it:

1. In Google Drive, **drag the local file in** (you become the owner).
2. Right-click → **Share** → add the **service-account email** (the
   `client_email` in `GOOGLE_SERVICE_ACCOUNT_JSON`) as **Editor**.
3. Copy the **file** id from the URL — `drive.google.com/file/d/<ID>/view`
   (the `<ID>`, *not* the `/folders/<…>` id).
4. Set the matching `*_DRIVE_FILE_ID` on **both** Railway services (and in local
   `.env`), then redeploy.

### After editing one later

```
python scripts/push_config_to_drive.py        # rent_roll.json + mortgage_pi.json
python scripts/push_rules_to_drive.py          # spending_rules.json
```

---

## Deploy gotchas

- **A `git push --force` (history rewrite) does not reliably auto-deploy.** After
  one, manually **Redeploy** each service and confirm the active deployment's
  commit SHA matches `git rev-parse origin/main`.
- **Env-var changes need a redeploy** to reach the running container.
- **Diagnose from the server's own view:** `GET /api/rent_source` (authed)
  reports whether the web service sees `RENT_ROLL_DRIVE_FILE_ID`, whether a local
  file exists, and whether a live Drive download succeeds — no secrets. Use it
  whenever the Rent tab is empty.

---

## Privacy

Personal data (ledger, rent roll, mortgage, Costco receipts, personal runbook)
is kept out of git and delivered via Drive. The repo is **private**. No
credentials are committed (`.env`, tokens, service-account JSON, `users.json`
are all gitignored). If the repo is ever made public, first move the
remaining identifiers in code (card masks, property names, family name in
`scripts/*shalini*.py`, `src/adriana_parser.py`, `src/main.py`) into
Drive-delivered config too.
