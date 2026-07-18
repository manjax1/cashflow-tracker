"""Interactive CLI for the cashflow agent, with the human-in-the-loop
approval gate for action tools.

Usage:
    python -m src.agent.cli            # normal
    python -m src.agent.cli --verbose  # print tool calls live
"""

import json
import os
import sys

try:
    import readline  # enables backspace/arrows/Ctrl-R in input(); loads history
except ImportError:  # very rare (e.g. minimal Windows builds)
    readline = None

from dotenv import load_dotenv

load_dotenv()

HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "logs", "agent_cli_history")


def init_history():
    if not readline:
        return
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    try:
        readline.read_history_file(HISTORY_FILE)
    except (FileNotFoundError, OSError):
        pass
    readline.set_history_length(500)


def save_history():
    if readline:
        try:
            readline.write_history_file(HISTORY_FILE)
        except OSError:
            pass


def show_history(n=20):
    if not readline:
        print("history unavailable (readline not supported on this platform)")
        return
    total = readline.get_current_history_length()
    start = max(1, total - n + 1)
    for i in range(start, total + 1):
        print(f"  {i:>4}  {readline.get_history_item(i)}")
    print("Tip: '!N' pre-fills command N for editing; up/down arrows recall; Ctrl-R searches.")


def prefill_input(prompt, text):
    """Show input() with `text` already typed, ready to edit."""
    if not readline:
        return input(prompt)
    readline.set_startup_hook(lambda: readline.insert_text(text))
    try:
        return input(prompt)
    finally:
        readline.set_startup_hook(None)

from .agent import Agent, SYSTEM, _dynamic_context   # noqa: E402
from . import tools, ledger                          # noqa: E402


def refresh_ledger(agent=None):
    """Pull the latest ledger from Google Drive (source of truth — the daily
    cloud job updates it), backing up the local copy first. Re-grounds the
    agent's date context afterward."""
    import shutil
    from datetime import datetime

    file_id = os.environ.get("GOOGLE_DRIVE_FILE_ID")
    if not file_id:
        print("GOOGLE_DRIVE_FILE_ID not set; cannot refresh from Drive.")
        return
    from src.drive_sync import download_ledger  # reuses existing auth

    path = ledger.LEDGER_PATH
    if os.path.exists(path):
        backup = path.replace(".xlsx", f"_BACKUP_{datetime.now():%Y-%m-%d_%H%M%S}.xlsx")
        shutil.copy2(path, backup)
    download_ledger(file_id, path)
    ledger._cache["mtime"] = None            # force reload
    txns = ledger.load_transactions()
    dates = sorted(t["Date"] for t in txns)
    print(f"Ledger refreshed: {len(txns)} transactions, {dates[0]} .. {dates[-1]}")
    if agent:
        agent.system = SYSTEM.format(context=_dynamic_context())


EDITABLE_FIELDS = {
    "recategorize_transaction": ["new_category", "rule_keyword"],
    "draft_email": ["subject", "body", "recipient_hint"],
}


def _edit_args(tool, args):
    """Let the user override fields before execution. Enter keeps current value."""
    print("Editing — press Enter to keep the current value.")
    for field in EDITABLE_FIELDS.get(tool, []):
        current = args.get(field, "")
        new = input(f"  {field} [{current}]: ").strip()
        if new:
            args[field] = new
        elif new == "" and field == "rule_keyword" and not current:
            args.pop(field, None)  # optional field left empty
    return args


def _describe_item(it):
    tx = _lookup_tx(it["source_ref"])
    if tx:
        return (f"{tx['Date']}  ${tx['Amount']:>9.2f}  {tx['Description'][:48]:<48} "
                f"[{tx['Category']}] → {it['new_category']}")
    return f"{it['source_ref']} → {it['new_category']} (transaction not found)"


def _lookup_tx(source_ref):
    for t in ledger.load_transactions():
        if str(t.get("SourceRef")) == source_ref:
            return t
    return None


def review_batch(p):
    """Per-item review of a recategorize_batch proposal: approve / edit /
    discard / approve-all-remaining / stop. Approved items execute in one
    pass (single backup, single save)."""
    items = p["args"]["items"]
    approved = []
    print(f"\nBATCH RECATEGORIZATION — {len(items)} items. "
          f"[a]pprove / [e]dit / [d]iscard / [A]pprove all remaining / [s]top")
    i = 0
    approve_rest = False
    while i < len(items):
        it = items[i]
        i += 1
        if approve_rest:
            approved.append(it)
            continue
        print(f"\n  {i}/{len(items)}  {_describe_item(it)}")
        if it.get("rule_keyword"):
            print(f"        + rule: '{it['rule_keyword']}' → {it['new_category']}")
        c = input("  [a/e/d/A/s]? ").strip()
        if c == "a":
            approved.append(it)
        elif c == "e":
            items[i - 1] = _edit_args("recategorize_transaction", it)
            approved.append(items[i - 1])
        elif c == "A":
            approve_rest = True
            approved.append(it)
        elif c == "s":
            print(f"  Stopped. {len(items) - i} remaining items discarded.")
            break
        else:
            tools.audit("batch_item_discarded", it)
    if approved:
        result = tools.EXECUTORS["recategorize_batch"]({"items": approved})
        print(f"\n✔ {result}")
    else:
        print("\n✘ No items approved; ledger unchanged.")


def review_proposals():
    """The approval gate. Enforced in code — no prompt can bypass it.
    Each proposal is reviewed individually: approve, edit-then-approve,
    or discard — so a batch of proposals can be handled selectively."""
    total = len(tools.PENDING)
    n = 0
    while tools.PENDING:
        p = tools.PENDING.pop(0)
        n += 1
        if p["tool"] == "recategorize_batch":
            review_batch(p)
            continue
        print("\n" + "=" * 60)
        print(f"PROPOSED ACTION {n}/{total}: {p['tool']}  (id {p['id']})")
        print(json.dumps(p["args"], indent=2))
        print("=" * 60)
        choice = input("[a]pprove / [e]dit then approve / [d]iscard? ").strip().lower()
        if choice == "e":
            p["args"] = _edit_args(p["tool"], p["args"])
            tools.audit("proposal_edited", p)
            choice = "a"
        if choice == "a":
            result = tools.EXECUTORS[p["tool"]](p["args"])
            print(f"✔ {result}")
        else:
            tools.audit("proposal_discarded", p)
            print("✘ Discarded. Nothing was modified or sent.")


def push_ledger():
    """Upload the local ledger to Google Drive (after local recategorizations)."""
    file_id = os.environ.get("GOOGLE_DRIVE_FILE_ID")
    if not file_id:
        print("GOOGLE_DRIVE_FILE_ID not set; cannot push to Drive.")
        return
    from src.drive_sync import upload_ledger
    txns = ledger.load_transactions()
    dates = sorted(t["Date"] for t in txns)
    print(f"Pushing local ledger ({len(txns)} transactions, {dates[0]} .. {dates[-1]}) to Drive...")
    upload_ledger(file_id, ledger.LEDGER_PATH)
    tools.audit("ledger_pushed_to_drive", {"transactions": len(txns), "latest": dates[-1]})


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    if "--refresh" in sys.argv:
        refresh_ledger()
    init_history()
    agent = Agent(verbose=verbose)
    print("Cashflow Agent — financial analyst for your ledger.")
    print("Ask about income, expenses, trends, anomalies. Commands: 'history', "
          "'!N' (edit & rerun), 'refresh' (pull ledger from Drive), "
          "'push' (upload ledger to Drive), 'quit'.\n")
    pending_prefill = None
    while True:
        try:
            if pending_prefill is not None:
                q = prefill_input("you> ", pending_prefill).strip()
                pending_prefill = None
            else:
                q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q.lower() in ("quit", "exit", "q"):
            break
        if q.lower() == "history":
            show_history()
            continue
        if q.startswith("!") and readline:
            ref = q[1:].strip()
            if ref == "!":
                idx = readline.get_current_history_length() - 1  # before this '!' entry
            elif ref.isdigit():
                idx = int(ref)
            else:
                print("usage: 'history' to list, then '!N' or '!!'")
                continue
            item = readline.get_history_item(idx)
            if not item:
                print(f"no history entry {idx}")
                continue
            pending_prefill = item
            continue
        if q.lower() == "refresh":
            try:
                refresh_ledger(agent)
            except Exception as e:
                print(f"refresh failed: {type(e).__name__}: {e}")
            continue
        if q.lower() == "push":
            confirm = input("Push local ledger to Drive, overwriting the cloud copy? [y/N] ").strip().lower()
            if confirm == "y":
                try:
                    push_ledger()
                except Exception as e:
                    print(f"push failed: {type(e).__name__}: {e}")
            else:
                print("Cancelled.")
            continue
        try:
            answer = agent.ask(q)
        except Exception as e:
            print(f"error: {type(e).__name__}: {e}")
            continue
        print(f"\nagent> {answer}\n")
        review_proposals()
    save_history()
    s = agent.stats
    print(f"\nsession: {s['turns']} API turns, {s['tool_calls']} tool calls, "
          f"{s['input_tokens']}+{s['output_tokens']} tokens")


if __name__ == "__main__":
    main()
