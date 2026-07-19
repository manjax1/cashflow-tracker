"""Invoice Intelligence — Session 1: extraction & classification.

Parses invoices dropped into invoices_inbox/ (Amazon order PDFs, .eml, .txt),
uses the LLM to extract structured order data and classify each item against
YOUR existing category taxonomy, then deterministically allocates tax/
promotions so item amounts sum exactly to the grand total.

Design rule (same as the agent): the LLM extracts and proposes; deterministic
code validates, allocates, and stores. Nothing touches the ledger here —
matching & splits are Session 2.

Usage:
    python -m src.agent.invoices ingest --dry-run   # parse+classify, print only
    python -m src.agent.invoices ingest             # also save JSON to invoices_data/
    python -m src.agent.invoices list               # show extracted orders
"""

import json
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

import anthropic  # noqa: E402

from . import ledger  # noqa: E402
from .tools import audit  # noqa: E402

INBOX = os.path.join(ledger.REPO_ROOT, "invoices_inbox")
OUTDIR = os.path.join(ledger.REPO_ROOT, "invoices_data")
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")

EXTRACT_TOOL = {
    "name": "record_order",
    "description": "Record the structured contents of one retail invoice/order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "order_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
            "merchant": {"type": "string", "description": "e.g. Amazon"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "price": {"type": "number", "description": "unit price"},
                        "quantity": {"type": "integer", "default": 1},
                        "category": {"type": "string",
                                     "description": "MUST be one of the provided taxonomy categories"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["name", "price", "category", "confidence"],
                },
            },
            "subtotal": {"type": "number"},
            "shipping": {"type": "number", "default": 0},
            "promotion": {"type": "number", "description": "discount as NEGATIVE number, 0 if none"},
            "tax": {"type": "number", "default": 0},
            "grand_total": {"type": "number", "description": "the amount actually charged"},
            "payment_hint": {"type": "string", "description": "e.g. 'Visa ending 3810'"},
            "notes": {"type": "string", "description": "anything odd: multiple shipments, gift cards, ambiguity"},
        },
        "required": ["order_id", "order_date", "merchant", "items", "grand_total"],
    },
}

SYSTEM = """You extract structured data from retail invoices and classify items.
Classification rules:
- Use ONLY the category names provided in the taxonomy list. Never invent one.
- Household consumables/food -> Groceries; plants/garden supplies -> Gardening;
  apparel/shoes -> Clothing; devices/accessories/electronics -> the best-fitting
  existing category; if truly unclear use 'Other - Uncategorized' with low confidence.
- Mark confidence honestly: 'high' only when the item name is unambiguous.
- Report money fields exactly as printed. Do not compute or adjust totals."""


# ----------------------------- file parsing -----------------------------

def read_pdf(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def read_eml(path):
    import email
    from email import policy
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    body = msg.get_body(preferencelist=("html", "plain"))
    text = body.get_content() if body else ""
    text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)          # strip tags
    return re.sub(r"\s+", " ", text)


def read_invoice(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return read_pdf(path)
    if ext == ".eml":
        return read_eml(path)
    with open(path, errors="replace") as f:
        return f.read()


# --------------------------- LLM extraction ---------------------------

def taxonomy():
    cats = ledger.list_categories()["categories"]
    return sorted(c["category"] for c in cats)


def extract_order(text, client=None):
    client = client or anthropic.Anthropic()
    cats = taxonomy()
    prompt = (f"Taxonomy categories (use these EXACT names):\n"
              + "\n".join(f"- {c}" for c in cats)
              + f"\n\nInvoice text:\n---\n{text[:12000]}\n---\n"
              "Extract this order with record_order.")
    resp = client.messages.create(
        model=MODEL, max_tokens=4000, system=SYSTEM,
        tools=[EXTRACT_TOOL], tool_choice={"type": "tool", "name": "record_order"},
        messages=[{"role": "user", "content": prompt}])
    order = next(b.input for b in resp.content if b.type == "tool_use")
    order["_tokens"] = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
    return order


# ----------------------- deterministic allocation -----------------------

def allocate(order):
    """Scale item lines so allocated amounts sum EXACTLY to grand_total
    (tax, shipping, promotions spread proportionally; remainder pennies to
    the largest item). Flags a validation warning if inputs look off."""
    items = order.get("items", [])
    gross = [round(i["price"] * i.get("quantity", 1), 2) for i in items]
    base = sum(gross)
    total = order["grand_total"]
    warnings = []
    if not items or base <= 0:
        order["allocation_warning"] = "no items or zero subtotal"
        return order
    printed = order.get("subtotal")
    if printed is not None and abs(printed - base) > 0.02:
        warnings.append(f"item sum {base:.2f} != printed subtotal {printed:.2f}")
    alloc = [round(g * total / base, 2) for g in gross]
    drift = round(total - sum(alloc), 2)
    if drift:
        alloc[gross.index(max(gross))] = round(alloc[gross.index(max(gross))] + drift, 2)
    for item, g, a in zip(items, gross, alloc):
        item["gross"] = g
        item["allocated_amount"] = a
    if warnings:
        order["allocation_warning"] = "; ".join(warnings)
    return order


# ------------------------------ pipeline ------------------------------

def order_path(order_id):
    return os.path.join(OUTDIR, f"{order_id}.json")


def ingest(dry_run=False):
    if not os.path.isdir(INBOX):
        sys.exit(f"No inbox folder at {INBOX}")
    files = sorted(f for f in os.listdir(INBOX)
                   if os.path.splitext(f)[1].lower() in (".pdf", ".eml", ".txt", ".html"))
    if not files:
        sys.exit(f"No invoice files (.pdf/.eml/.txt/.html) in {INBOX}")
    os.makedirs(OUTDIR, exist_ok=True)
    client = anthropic.Anthropic()
    done = skipped = 0
    for fname in files:
        path = os.path.join(INBOX, fname)
        text = read_invoice(path)
        m = re.search(r"Order\s*#?\s*([\d-]{15,25})", text) or re.search(r"([\d]{3}-[\d]{7}-[\d]{7})", fname)
        oid_hint = m.group(1) if m else None
        if oid_hint and os.path.exists(order_path(oid_hint)):
            print(f"  ↷ {fname}: already extracted ({oid_hint}), skipping")
            skipped += 1
            continue
        print(f"  ⚙ extracting {fname} ...")
        order = allocate(extract_order(text, client))
        order["_source_file"] = fname
        order["_extracted_at"] = datetime.now().isoformat(timespec="seconds")
        print_order(order)
        if not dry_run:
            with open(order_path(order["order_id"]), "w") as f:
                json.dump(order, f, indent=2)
            audit("invoice_extracted", {"order_id": order["order_id"],
                                        "file": fname, "total": order["grand_total"]})
        done += 1
    print(f"\n{done} extracted, {skipped} skipped"
          + (" (dry run — nothing saved)" if dry_run else f" → {OUTDIR}"))


def print_order(o):
    print(f"    {o['merchant']} order {o['order_id']}  ({o['order_date']})  "
          f"total ${o['grand_total']:.2f}  [{o.get('payment_hint', '?')}]")
    for i in o["items"]:
        conf = {"high": "", "medium": " (?)", "low": " (??)"}[i["confidence"]]
        print(f"      ${i.get('allocated_amount', i['price']):>8.2f}  "
              f"{i['category']}{conf}  —  {i['name'][:58]}")
    if o.get("allocation_warning"):
        print(f"      ⚠ {o['allocation_warning']}")
    if o.get("notes"):
        print(f"      note: {o['notes']}")


def list_orders():
    if not os.path.isdir(OUTDIR):
        sys.exit("Nothing extracted yet.")
    for f in sorted(os.listdir(OUTDIR)):
        if f.endswith(".json"):
            with open(os.path.join(OUTDIR, f)) as fh:
                print_order(json.load(fh))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "ingest":
        ingest(dry_run="--dry-run" in args)
    elif args and args[0] == "list":
        list_orders()
    else:
        print(__doc__)
