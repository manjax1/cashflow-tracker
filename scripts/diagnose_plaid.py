#!/usr/bin/env python3
"""Read-only Plaid diagnostic — prints the exact error code behind a failed
sync/re-link, without exposing secrets. Does NOT write anything.

    python scripts/diagnose_plaid.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

import plaid  # noqa: E402
from plaid.api import plaid_api  # noqa: E402
from plaid.model.accounts_get_request import AccountsGetRequest  # noqa: E402
from plaid.exceptions import ApiException  # noqa: E402
from utils import clean_env  # noqa: E402

ENV_MAP = {"sandbox": plaid.Environment.Sandbox, "production": plaid.Environment.Production}


def mask(s):
    if not s:
        return "(empty)"
    return f"{s[:4]}…{s[-4:]} (len {len(s)})"


def main():
    client_id = clean_env(os.getenv("PLAID_CLIENT_ID"), "PLAID_CLIENT_ID")
    secret = clean_env(os.getenv("PLAID_SECRET"), "PLAID_SECRET")
    env_key = clean_env(os.getenv("PLAID_ENV", "sandbox"), "PLAID_ENV").lower()
    print("── Plaid config ─────────────────────────────")
    print(f"PLAID_ENV      = {env_key!r}")
    print(f"PLAID_CLIENT_ID= {mask(client_id)}")
    print(f"PLAID_SECRET   = {mask(secret)}")

    host = ENV_MAP.get(env_key)
    if host is None:
        print(f"\n❌ PLAID_ENV={env_key!r} is not 'sandbox' or 'production'. Fix this first.")
        return
    if env_key != "production":
        print("\n⚠️  PLAID_ENV is not 'production'. A production access token (real BofA) will NOT "
              "verify here, which forces the re-link flow and then fails on the real bank. "
              "If your accounts are live, set PLAID_ENV=production and use the PRODUCTION secret.")

    cfg = plaid.Configuration(host=host, api_key={"clientId": client_id, "secret": secret})
    client = plaid_api.PlaidApi(plaid.ApiClient(cfg))

    tokens = [("primary", clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN")),
              ("shalini", clean_env(os.getenv("PLAID_ACCESS_TOKEN_SHALINI"), "PLAID_ACCESS_TOKEN_SHALINI"))]

    for name, tok in tokens:
        print(f"\n── item: {name} ─────────────────────────────")
        if not tok:
            print("  (no token set — skipping)")
            continue
        print(f"  access_token = {mask(tok)}")
        try:
            resp = client.accounts_get(AccountsGetRequest(access_token=tok))
            accts = resp["accounts"]
            print(f"  ✅ OK — {len(accts)} accounts reachable:")
            for a in accts:
                print(f"       {a.get('mask','----')}  {a.get('name','')}")
        except ApiException as e:
            body = json.loads(e.body) if isinstance(e.body, str) else (e.body or {})
            code = body.get("error_code", "?")
            print(f"  ❌ {code}: {body.get('error_message','')}")
            hint = {
                "INVALID_API_KEYS": "The secret/client_id is wrong for this PLAID_ENV. Re-copy the "
                                    "PRODUCTION secret from the Plaid dashboard (no trailing spaces).",
                "INVALID_ACCESS_TOKEN": "Token doesn't match these credentials — usually a prod token "
                                        "with sandbox keys (or vice-versa). Check PLAID_ENV + secret.",
                "ITEM_LOGIN_REQUIRED": "The bank item needs re-authentication (expected after a reset). "
                                       "A genuine re-link is required — see notes on BofA OAuth re-link.",
            }.get(code)
            if hint:
                print(f"     → {hint}")


if __name__ == "__main__":
    main()
