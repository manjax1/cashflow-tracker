"""Invoice Intelligence Phase 2 — Gmail ingestion agent.

Finds Amazon order-confirmation emails via the Gmail API (read-only),
extracts orders with the same LLM pipeline as file-drop invoices, and saves
them to invoices_data/ where `invoices apply` imports them into the ledger.

One-time setup (Google Cloud Console):
  1. console.cloud.google.com -> create/select a project
  2. APIs & Services -> Enable "Gmail API"
  3. OAuth consent screen -> External -> add your Gmail as a Test User
  4. Credentials -> Create Credentials -> OAuth client ID -> Desktop app
  5. Download JSON -> save as gmail_credentials.json in the repo root

Usage:
    python -m src.agent.gmail_ingest auth              # one-time browser consent
    python -m src.agent.gmail_ingest sync --days 30    # fetch + extract
    python -m src.agent.gmail_ingest sync --days 30 --dry-run
"""

import base64
import json
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from . import ledger                      # noqa: E402
from .tools import audit                  # noqa: E402
from . import invoices                    # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CRED_PATH = os.path.join(ledger.REPO_ROOT, "gmail_credentials.json")
TOKEN_PATH = os.path.join(ledger.REPO_ROOT, "gmail_token.json")
QUEUE_PATH = os.path.join(invoices.OUTDIR, "needs_invoice.json")

# Amazon order-confirmation senders
QUERY = ('from:(auto-confirm@amazon.com OR digital-no-reply@amazon.com) '
         'subject:(order OR ordered) ')


def get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    if not creds or not creds.valid:
        sys.exit("Not authorized. Run: python -m src.agent.gmail_ingest auth")
    return build("gmail", "v1", credentials=creds)


def do_auth():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CRED_PATH):
        sys.exit(f"Missing {CRED_PATH} — follow the setup steps in this file's docstring.")
    flow = InstalledAppFlow.from_client_secrets_file(CRED_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)
    print("✅ Gmail authorized (read-only). Token saved to gmail_token.json")


def _walk_parts(part, out):
    if part.get("mimeType", "").startswith("text/") and part.get("body", {}).get("data"):
        out.append((part["mimeType"],
                    base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="replace")))
    for p in part.get("parts", []):
        _walk_parts(p, out)


def message_text(msg):
    out = []
    _walk_parts(msg["payload"], out)
    html = next((t for m, t in out if m == "text/html"), None)
    text = html or next((t for m, t in out if m == "text/plain"), "")
    text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def queue_gap(order_id, reason):
    q = []
    if os.path.exists(QUEUE_PATH):
        with open(QUEUE_PATH) as f:
            q = json.load(f)
    if not any(e["order_id"] == order_id for e in q):
        q.append({"order_id": order_id, "reason": reason,
                  "queued_at": datetime.now().isoformat(timespec="seconds")})
        with open(QUEUE_PATH, "w") as f:
            json.dump(q, f, indent=2)


def sync(days=30, dry_run=False):
    import anthropic
    service = get_service()
    query = QUERY + f"newer_than:{days}d"
    resp = service.users().messages().list(userId="me", q=query, maxResults=100).execute()
    msgs = resp.get("messages", [])
    print(f"{len(msgs)} Amazon order emails in the last {days} days")
    os.makedirs(invoices.OUTDIR, exist_ok=True)
    client = anthropic.Anthropic()
    saved = skipped = queued = 0
    for m in msgs:
        full = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        text = message_text(full)
        oid_m = re.search(r"(\d{3}-\d{7}-\d{7})", text)
        if not oid_m:
            continue
        oid = oid_m.group(1)
        if os.path.exists(invoices.order_path(oid)):
            skipped += 1
            continue
        print(f"  ⚙ extracting order {oid} from email ...")
        order = invoices.allocate(invoices.extract_order(text[:14000], client))
        order["order_id"] = oid  # trust the regex over the model for the id
        order["_source_file"] = f"gmail:{m['id']}"
        order["_extracted_at"] = datetime.now().isoformat(timespec="seconds")
        # Amazon emails sometimes omit item details — detect and queue
        items_ok = order.get("items") and all(i.get("price", 0) > 0 for i in order["items"])
        total_ok = order.get("grand_total", 0) > 0
        if not items_ok:
            queue_gap(oid, "email lacked item detail — save the invoice PDF "
                           "to invoices_inbox/ or wait for next data export")
            queued += 1
            if not total_ok:
                continue
            order["needs_invoice"] = True
        invoices.print_order(order)
        if not dry_run:
            with open(invoices.order_path(oid), "w") as f:
                json.dump(order, f, indent=2)
            audit("gmail_order_extracted", {"order_id": oid, "email": m["id"]})
        saved += 1
    print(f"\n{saved} extracted, {skipped} already known, {queued} queued for invoice"
          + (" (dry run — nothing saved)" if dry_run else ""))
    if not dry_run and saved:
        print("Next: python -m src.agent.invoices apply   (imports new orders to the ledger)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "auth":
        do_auth()
    elif args and args[0] == "sync":
        days = int(args[args.index("--days") + 1]) if "--days" in args else 30
        sync(days=days, dry_run="--dry-run" in args)
    else:
        print(__doc__)
