#!/usr/bin/env python3
"""Share a service-account-owned Drive file with your Google account (so you
can view it), and optionally move it into one of your folders.

Usage:
    python scripts/share_drive_file.py --email you@gmail.com
    python scripts/share_drive_file.py --email you@gmail.com --folder <FOLDER_ID>

File id defaults to CHATLOG_DRIVE_FILE_ID from the environment; pass --file to override.
Prints the service-account email so you can grant it Editor on the folder if the
move fails (the folder must be shared with the service account to move into it).
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv

load_dotenv()

from drive_sync import get_drive_service  # noqa: E402
from utils import clean_env  # noqa: E402


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    file_id = arg("--file", os.environ.get("CHATLOG_DRIVE_FILE_ID"))
    email = arg("--email")
    folder = arg("--folder")
    if not file_id or not email:
        sys.exit("Need --email (and CHATLOG_DRIVE_FILE_ID set, or --file <id>)")

    sa_email = json.loads(clean_env(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
                                    "GOOGLE_SERVICE_ACCOUNT_JSON")).get("client_email")
    print("Service account:", sa_email)
    svc = get_drive_service()

    svc.permissions().create(
        fileId=file_id, sendNotificationEmail=False,
        body={"type": "user", "role": "writer", "emailAddress": email}).execute()
    print(f"✅ Shared file {file_id} with {email} (Editor). Look in Drive → 'Shared with me'.")

    if folder:
        try:
            cur = svc.files().get(fileId=file_id, fields="parents").execute()
            prev = ",".join(cur.get("parents", []))
            svc.files().update(fileId=file_id, addParents=folder,
                               removeParents=prev, fields="id,parents").execute()
            print(f"✅ Moved into folder {folder}.")
        except Exception as e:
            print(f"⚠️  Could not move into folder: {e}")
            print(f"   Share that folder with {sa_email} (Editor) in Drive, then retry.")


if __name__ == "__main__":
    main()
