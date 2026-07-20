#!/usr/bin/env python3
"""Create an empty file in Google Drive via the service account and print its
ID — for the durable web chat log (CHATLOG_DRIVE_FILE_ID).

Usage:
    python scripts/create_drive_file.py "cashflow-chatlog.jsonl"

Then set the printed ID as CHATLOG_DRIVE_FILE_ID on Railway. The file is owned
by the service account; to view it yourself, share it or move it into a folder
the service account can write to.
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
    print("Created Drive file:", name)
    print("CHATLOG_DRIVE_FILE_ID =", f["id"])
    print("\nSet that as an env var on Railway (and locally in .env if you want).")


if __name__ == "__main__":
    main()
