#!/usr/bin/env python3
"""Manage web-app users: password + TOTP two-factor enrollment.

The user store is users.json (gitignored — contains password hashes and TOTP
secrets). For Railway, export it as the USERS_JSON env var: Railway's
filesystem is ephemeral, env vars are the durable store.

Usage:
    python scripts/manage_users.py add <username>     # prompts for password, shows QR
    python scripts/manage_users.py list
    python scripts/manage_users.py remove <username>
    python scripts/manage_users.py export             # prints compact USERS_JSON value
"""

import getpass
import json
import os
import sys

import pyotp
from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_PATH = os.path.join(ROOT, "users.json")
ISSUER = "Cashflow Agent"


def load():
    if os.path.exists(USERS_PATH):
        with open(USERS_PATH) as f:
            return json.load(f)
    return {}


def save(users):
    with open(USERS_PATH, "w") as f:
        json.dump(users, f, indent=2)
    os.chmod(USERS_PATH, 0o600)


def show_qr(uri):
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.print_ascii(invert=True)
    except ImportError:
        print("(pip install qrcode for a scannable terminal QR code)")


def add(username):
    users = load()
    if username in users:
        sys.exit(f"User '{username}' already exists. Remove first to re-enroll.")
    pw = getpass.getpass(f"Password for {username}: ")
    pw2 = getpass.getpass("Confirm: ")
    if pw != pw2:
        sys.exit("Passwords do not match.")
    if len(pw) < 8:
        sys.exit("Use at least 8 characters.")
    secret = pyotp.random_base32()
    users[username] = {
        "password_hash": generate_password_hash(pw),  # scrypt
        "totp_secret": secret,
    }
    save(users)
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)
    print(f"\n✅ User '{username}' created.")
    print("\nEnroll their authenticator app (Google Authenticator, Authy, 1Password...):")
    print(f"  Setup URI: {uri}\n")
    show_qr(uri)
    print("\nVerify now — enter the 6-digit code from the app (blank to skip):")
    code = input("Code: ").strip()
    if code:
        if pyotp.TOTP(secret).verify(code, valid_window=1):
            print("✅ Code verified — enrollment complete.")
        else:
            print("⚠️  Code did NOT verify. Check the device clock, or remove and re-add.")
    print("\nRemember: run 'export' and update USERS_JSON on Railway.")


def remove(username):
    users = load()
    if users.pop(username, None) is None:
        sys.exit(f"No such user '{username}'.")
    save(users)
    print(f"Removed '{username}'. Re-export USERS_JSON to Railway.")


def list_users():
    users = load()
    if not users:
        print("No users. Add one with: python scripts/manage_users.py add <username>")
    for u in users:
        print(f"  {u}")


def export():
    users = load()
    if not users:
        sys.exit("No users to export.")
    print(json.dumps(users, separators=(",", ":")))


if __name__ == "__main__":
    cmds = {"add": add, "remove": remove}
    if len(sys.argv) >= 3 and sys.argv[1] in cmds:
        cmds[sys.argv[1]](sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "list":
        list_users()
    elif len(sys.argv) == 2 and sys.argv[1] == "export":
        export()
    else:
        print(__doc__)
