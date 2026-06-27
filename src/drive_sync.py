import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2.service_account import Credentials

from utils import clean_env


def get_drive_service():
    raw = clean_env(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"), "GOOGLE_SERVICE_ACCOUNT_JSON")
    service_account_info = json.loads(raw)
    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def download_ledger(file_id: str, local_path: str):
    try:
        service = get_drive_service()
        request = service.files().get_media(fileId=file_id)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        print(f"✅ Downloaded ledger from Drive → {local_path}")
    except Exception as e:
        print(f"⚠️  Drive download failed: {e}")
        raise


def upload_ledger(file_id: str, local_path: str):
    try:
        service = get_drive_service()
        media = MediaFileUpload(
            local_path,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            resumable=True,
        )
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"✅ Uploaded ledger to Drive ← {local_path}")
    except Exception as e:
        print(f"⚠️  Drive upload failed: {e}")
        raise


def snapshot_ledger(local_path: str, parent_folder_id: str, snapshot_name: str) -> str:
    """Create a new, permanent audit copy in Drive using files().create().

    This is NOT an update to the existing live-ledger file — it creates a
    brand-new file in the same parent folder, so the live ledger's file ID
    is untouched.  Returns the new file's Drive ID.
    """
    service = get_drive_service()
    file_metadata = {
        "name": snapshot_name,
        "parents": [parent_folder_id],
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    media = MediaFileUpload(
        local_path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=True,
    )
    result = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id")
        .execute()
    )
    new_id = result.get("id", "")
    print(f"✅ Snapshot saved to Drive: {snapshot_name} (id={new_id})")
    return new_id


def snapshot_exists_for_month(parent_folder_id: str, year_month: str) -> bool:
    """Return True if a snapshot for this year-month already exists in the folder.

    Matching strategy: exact filename match on
    'cashflow-tracker-snapshot-{year_month}.xlsx' within the given parent folder,
    excluding trashed files.

    Exact-name matching (not 'contains') is intentional:
      - Prevents false positives from partial year-month overlap (e.g. "2026-0"
        matching both "2026-07" and "2026-08" is impossible with an exact query,
        but 'contains' could theoretically match if someone named a file oddly).
      - If a snapshot for this month was accidentally trashed, trashed=false means
        we'll create a fresh one — correct behaviour, since a trashed file is gone
        from the user's view.
      - A manually placed file with the exact expected name counts as "exists" and
        suppresses a duplicate — also correct.
    """
    expected_name = f"cashflow-tracker-snapshot-{year_month}.xlsx"
    service = get_drive_service()
    query = (
        f"name = '{expected_name}' "
        f"and '{parent_folder_id}' in parents "
        f"and trashed = false"
    )
    resp = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
    return len(resp.get("files", [])) > 0
