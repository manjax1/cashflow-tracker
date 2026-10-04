import os
import sys
import json
import time

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
        d = os.path.dirname(local_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(local_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        print(f"✅ Downloaded ledger from Drive → {local_path}")
    except Exception as e:
        print(f"⚠️  Drive download failed: {e}")
        raise


def ensure_file_from_drive(local_path: str, env_name: str, ttl: int = 300) -> str:
    """Make a config file available locally, fetching it from Google Drive. Used
    for files kept OUT of git for privacy (rent_roll.json, mortgage_pi.json).

    - Always downloads when the local file is **missing** (e.g. Railway's
      ephemeral disk on a fresh container).
    - On a **deployed host** it also re-downloads when the local copy is older
      than `ttl` seconds, so edits pushed to Drive take effect without a restart.
      "Deployed" = any `RAILWAY_*` env var is present, or `CONFIG_REFRESH_TTL` is
      set (which also overrides the interval). On a **local dev machine** (neither
      signal) the time-based refresh is OFF, so your edited copy is never
      overwritten — you push changes up with scripts/push_config_to_drive.py.

    Returns local_path regardless (callers fall back to whatever is on disk)."""
    file_id = clean_env(os.getenv(env_name), env_name)
    if not file_id:
        return local_path

    refresh_env = clean_env(os.getenv("CONFIG_REFRESH_TTL"), "CONFIG_REFRESH_TTL")
    on_deployed_host = bool(refresh_env) or any(k.startswith("RAILWAY_") for k in os.environ)
    try:
        ttl_s = int(refresh_env) if refresh_env else ttl
    except ValueError:
        ttl_s = ttl

    need = not os.path.exists(local_path)
    if not need and on_deployed_host and ttl_s > 0:
        try:
            need = (time.time() - os.path.getmtime(local_path)) > ttl_s
        except OSError:
            need = True
    if need:
        try:
            download_ledger(file_id, local_path)
        except Exception as e:
            print(f"⚠️  Could not fetch {os.path.basename(local_path)} from Drive "
                  f"({env_name}): {e}")
    return local_path


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


