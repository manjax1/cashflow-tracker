#!/usr/bin/env python3
"""Re-authenticate an EXISTING Plaid item via Plaid HOSTED LINK (update mode).

Use this when the diagnostic shows ITEM_LOGIN_REQUIRED (e.g. after a Plaid key
reset / breach). Bank of America is an OAuth institution, so the local Flask
Link flow can't complete its OAuth redirect — Hosted Link lets Plaid host the
whole flow on its own domain and handle the redirect. The existing access_token
is preserved, so you do NOT change PLAID_ACCESS_TOKEN in .env or on Railway.

    python scripts/relink_item.py primary     # repair your BofA login
    python scripts/relink_item.py shalini      # repair Shalini's BofA login

Steps: the script prints a plaid.com URL. Open it, complete the BofA login
(credentials go to Plaid, never this app), then come back — the script polls
until the item is healthy and confirms.
"""
import os
import sys
import time
import webbrowser

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv, set_key  # noqa: E402
load_dotenv()

from plaid_client import PlaidClient  # noqa: E402
from utils import clean_env  # noqa: E402

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

ITEMS = {
    "primary": "PLAID_ACCESS_TOKEN",
    "shalini": "PLAID_ACCESS_TOKEN_SHALINI",
}


def new_item_flow(client, name, env_key):
    """Create a BRAND-NEW item via Hosted Link and save its new access token.
    Use when update mode can't repair the item (the bank revoked access)."""
    print(f"🆕 Creating a FRESH Plaid item for '{name}' via Hosted Link.")
    link_token, url = client.get_hosted_new_link()
    print("\n  Open this URL and complete the Bank of America login:\n")
    print(f"    {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("  Waiting for you to finish… (polling every 10s; Ctrl-C to stop)")
    deadline = time.time() + 15 * 60
    public_token = None
    while time.time() < deadline:
        time.sleep(10)
        try:
            public_token = client.poll_hosted_public_token(link_token)
        except Exception:
            public_token = None
        if public_token:
            break
        print("   …still waiting (finish the login in the browser tab).")
    if not public_token:
        print("\n⏱️  Timed out — re-run once you've completed the browser login.")
        sys.exit(2)

    access_token = client.exchange_public_token(public_token)
    set_key(ENV_PATH, env_key, access_token)
    print(f"\n✅ New item linked. Saved to .env as {env_key}.")
    if client.verify_access_token(access_token):
        print("   Verified healthy.")
    print(f"\n⚠️  IMPORTANT: copy the new {env_key} value from .env into Railway's "
          "environment (BOTH services) so the cloud sync uses it, then redeploy.")


def main():
    args = [a for a in sys.argv[1:]]
    want_new = "--new" in args
    positional = [a for a in args if not a.startswith("-")]
    name = (positional[0] if positional else "primary").lower()
    env_key = ITEMS.get(name)
    if not env_key:
        print(f"Unknown item '{name}'. Choices: {', '.join(ITEMS)}")
        sys.exit(1)

    client = PlaidClient()

    if want_new:
        new_item_flow(client, name, env_key)
        return

    access_token = clean_env(os.getenv(env_key), env_key)
    if not access_token:
        print(f"No existing token in {env_key}. This item has never been linked — "
              f"use `python scripts/relink_item.py {name} --new` to create it.")
        sys.exit(1)

    # Already healthy? Then nothing to do.
    try:
        if client.verify_access_token(access_token):
            print(f"✅ '{name}' is already healthy — no re-auth needed.")
            return
    except Exception:
        pass

    print(f"🔧 Repairing Plaid item '{name}' ({env_key}) via Hosted Link (update mode).")
    url = client.get_hosted_update_link(access_token)
    print("\n  Open this URL in your browser and complete the Bank of America login:\n")
    print(f"    {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("  Waiting for you to finish… (polling every 10s; Ctrl-C to stop)")
    deadline = time.time() + 15 * 60  # 15 minutes
    while time.time() < deadline:
        time.sleep(10)
        try:
            if client.verify_access_token(access_token):
                print(f"\n✅ '{name}' repaired and healthy. Same token — no env change needed.")
                return
        except Exception:
            pass
        print("   …still waiting (finish the login in the browser tab).")
    print("\n⏱️  Timed out waiting. Re-run this script once you've completed the browser login.")
    sys.exit(2)


if __name__ == "__main__":
    main()
