#!/usr/bin/env python3
"""Keep the private config files (rent_roll.json, mortgage_pi.json) on Google
Drive so they stay OUT of git but still reach Railway at runtime — same pattern
as spending_rules.json. The app downloads them at startup via
RENT_ROLL_DRIVE_FILE_ID / MORTGAGE_PI_DRIVE_FILE_ID when the local copy is absent
(see drive_sync.ensure_file_from_drive).

FIRST-TIME SETUP (do this once per file, in the Drive web UI):
  1. Drag the local file (rent_roll.json / mortgage_pi.json) into your Google
     Drive so YOU own it (service accounts have no storage quota and cannot own
     uploaded content).
  2. Right-click it -> Share -> add the service-account email (printed below)
     as **Editor**.
  3. Open the file, copy its ID from the URL
     (drive.google.com/file/d/<THIS_IS_THE_ID>/view), and set it in BOTH .env and
     Railway:  RENT_ROLL_DRIVE_FILE_ID=...   MORTGAGE_PI_DRIVE_FILE_ID=...
  The uploaded copy is already current, so you're done. Use this script only to
  PUSH later local edits:

      python scripts/push_config_to_drive.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from drive_sync import get_drive_service  # noqa: E402
from utils import clean_env  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = [("rent_roll.json", "RENT_ROLL_DRIVE_FILE_ID"),
           ("mortgage_pi.json", "MORTGAGE_PI_DRIVE_FILE_ID")]


def _service_account_email():
    try:
        return json.loads(clean_env(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
                                    "GOOGLE_SERVICE_ACCOUNT_JSON")).get("client_email", "(unknown)")
    except Exception:
        return "(unknown)"


def main():
    svc = get_drive_service()
    sa = _service_account_email()
    missing = []
    for fname, env in CONFIGS:
        path = os.path.join(ROOT, fname)
        if not os.path.exists(path):
            print(f"⏭️  {fname}: not found locally — skipping")
            continue
        fid = clean_env(os.getenv(env), env)
        if not fid:
            missing.append((fname, env))
            continue
        svc.files().update(fileId=fid, media_body=MediaFileUpload(
            path, mimetype="application/json", resumable=True)).execute()
        print(f"✅ {fname}: updated on Drive ({env}={fid})")

    if missing:
        print("\n⚠️  First-time setup needed for:", ", ".join(f for f, _ in missing))
        print("    Service accounts can't create files on personal Gmail, so create each one yourself:")
        print("    1. Drag the file into Google Drive (you become the owner).")
        print(f"    2. Share it with  {sa}  as Editor.")
        print("    3. Copy its file ID from the URL and set the env var in .env AND Railway:")
        for f, env in missing:
            print(f"         {env}=<id of {f}>")
        print("    The uploaded copy is already current — re-run this script only to push later edits.")


if __name__ == "__main__":
    main()
