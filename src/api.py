import os
import threading
from datetime import datetime
from flask import Flask, jsonify, request

from src.utils import clean_env

app = Flask(__name__)

_sync_lock = threading.Lock()
_sync_running = False
_last_run: dict = {}


def _get_local_time() -> str:
    tz_str = clean_env(os.getenv("TZ", "America/Los_Angeles"), "TZ")
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_str))
    except Exception:
        now = datetime.utcnow()
    return now.isoformat()


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "spending-tracker",
        "environment": clean_env(os.getenv("PLAID_ENV", "production"), "PLAID_ENV"),
        "timestamp": _get_local_time(),
        "sync_running": _sync_running,
        "last_run": _last_run,
    })


@app.route("/sync", methods=["POST"])
def sync():
    global _sync_running, _last_run

    if not _sync_lock.acquire(blocking=False):
        return jsonify({"status": "busy", "message": "A sync is already running"}), 409

    body = request.get_json(silent=True) or {}
    from_date_str = body.get("from_date")

    def _run():
        global _sync_running, _last_run
        _sync_running = True
        try:
            from datetime import date
            from src.main import run_sync
            from_date = date.fromisoformat(from_date_str) if from_date_str else None
            result = run_sync(from_date)
            _last_run = result
        except Exception as e:
            _last_run = {"error": str(e), "timestamp": _get_local_time()}
            print(f"Sync error: {e}")
        finally:
            _sync_running = False
            _sync_lock.release()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/sync/test", methods=["POST"])
def sync_test():
    results = {}

    # Test Plaid
    try:
        from src.plaid_client import PlaidClient
        client = PlaidClient()
        access_token = clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN")
        if access_token and client.verify_access_token(access_token):
            results["plaid"] = "connected"
        else:
            results["plaid"] = "error: invalid or missing access token"
    except Exception as e:
        results["plaid"] = f"error: {e}"

    # Test Drive
    try:
        from src.drive_sync import get_drive_service
        svc = get_drive_service()
        svc.files().list(pageSize=1, fields="files(id)").execute()
        results["drive"] = "connected"
    except Exception as e:
        results["drive"] = f"error: {e}"

    all_ok = all(v == "connected" for v in results.values())
    results["status"] = "ok" if all_ok else "degraded"
    return jsonify(results)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
