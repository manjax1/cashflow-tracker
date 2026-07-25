"""Link a named Plaid item (a bank login) and save its access token.

Each "item" is one online-banking login. The primary item is your BofA
login; additional items (e.g. Shalini's BofA login) are linked once here and
then picked up automatically by the daily sync in main.py.

Usage:
    python src/link_item.py shalini     # link Shalini's BofA login
    python src/link_item.py primary     # re-link your own BofA login

The command opens Plaid Link in your browser. The account holder enters their
own online-banking credentials there — the credentials go straight to Plaid,
never to this app. On success the exchanged access token is written to .env
under the item's env key.

After linking Shalini's card locally, copy the new PLAID_ACCESS_TOKEN_SHALINI
value from .env into the Railway environment so the daily cloud sync includes it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from plaid_client import PlaidClient
from link_flow import run_link_flow

# item name → .env key that stores its access token
ITEMS = {
    "primary": "PLAID_ACCESS_TOKEN",
    "shalini": "PLAID_ACCESS_TOKEN_SHALINI",
}


def main():
    name = (sys.argv[1] if len(sys.argv) > 1 else "primary").lower()
    env_key = ITEMS.get(name)
    if not env_key:
        print(f"Unknown item '{name}'. Choices: {', '.join(ITEMS)}")
        sys.exit(1)

    print(f"🔗 Linking Plaid item '{name}' → will save the token to {env_key}")
    print("   A browser window will open. The account holder should enter their")
    print("   own online-banking credentials in Plaid Link (they go to Plaid, not here).")
    client = PlaidClient()
    run_link_flow(client, env_key=env_key)
    print(f"✅ Done. '{name}' is linked. For the cloud sync, add {env_key} to Railway's env.")


if __name__ == "__main__":
    main()
