#!/usr/bin/env python3
"""Create an empty Drive file via the service account and print its ID.

⚠️  LIMITATION: service accounts have NO storage quota on personal Google
accounts, so a file they OWN cannot hold uploaded content (uploads fail with
'storageQuotaExceeded'). This script is only useful with a Google Workspace
Shared Drive.

PREFERRED for personal Gmail: create the file yourself (upload it in the Drive
web UI so YOU own it), share it with the service-account email as Editor, and
use that file's ID — exactly how the ledger file works. Then push_rules /
chat-log flush (which UPDATE the file, using your quota) succeed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv

load_dotenv()

from drive_sync import get_drive_service  # noqa: E402
from googleapiclient.http import MediaInMemoryUpload  # noqa: E402


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "cashflow-chatlog.jsonl"
    svc = get_drive_service()
    meta = {"name": name, "mimeType": "text/plain"}
    folder = os.environ.get("CHATLOG_DRIVE_FOLDER_ID")
    if folder:
        meta["parents"] = [folder]
    f = svc.files().create(
        body=meta, media_body=MediaInMemoryUpload(b"", mimetype="text/plain"),
        fields="id").execute()
    # Suggest an env-var name based on the file, or take one as arg 2.
    var = sys.argv[2] if len(sys.argv) > 2 else (
        "RULES_DRIVE_FILE_ID" if "rule" in name.lower()
        else "CHATLOG_DRIVE_FILE_ID" if ("chat" in name.lower() or "log" in name.lower())
        else "DRIVE_FILE_ID")
    print("Created Drive file:", name)
    print("File ID:", f["id"])
    print(f"\nSet it as an env var on Railway (and in local .env):\n  {var} = {f['id']}")


if __name__ == "__main__":
    main()
