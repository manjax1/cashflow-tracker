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


