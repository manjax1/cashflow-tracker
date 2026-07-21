#!/usr/bin/env python3
"""Upload the local spending_rules.json to Google Drive so the Railway sync job
picks it up on its next run — no compaction, no env var, no redeploy.

One-time setup:
    python scripts/create_drive_file.py "spending_rules.json"   # prints an ID
    # set that ID as RULES_DRIVE_FILE_ID on Railway AND in local .env

Then, after any rule change:
    python scripts/push_rules_to_drive.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv

load_dotenv()

from drive_sync import get_drive_service  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "spending_rules.json")


def main():
    file_id = os.environ.get("RULES_DRIVE_FILE_ID")
    if not file_id:
        sys.exit("RULES_DRIVE_FILE_ID not set. Create the Drive file first:\n"
                 '  python scripts/create_drive_file.py "spending_rules.json"\n'
                 "then add its ID to .env and Railway as RULES_DRIVE_FILE_ID.")
    if not os.path.exists(RULES):
        sys.exit(f"No rules file at {RULES}")
    import json
    n = len(json.load(open(RULES)))
    svc = get_drive_service()
    svc.files().update(fileId=file_id,
                       media_body=MediaFileUpload(RULES, mimetype="application/json")).execute()
    print(f"✅ Uploaded {n} rules to Drive. The next daily sync will use them "
          "(no redeploy needed).")


if __name__ == "__main__":
    main()
