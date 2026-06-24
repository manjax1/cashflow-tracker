import os
import sys
import threading
from datetime import date, datetime
from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import clean_env

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


def _wants_html() -> bool:
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "text/html"


def _html_response(status_code: int, data: dict) -> tuple:
    if status_code == 409:
        body = f"<p>⏳ {data.get('message', 'A sync is already in progress, please wait')}</p>"
    elif status_code >= 400:
        body = f"<p>❌ {data.get('message', 'Error')}</p>"
    else:
        from_d = data.get("from_date") or "default (7 days ago)"
        to_d = data.get("to_date", "today")
        body = f"<p>✅ Sync started for {from_d} to {to_d}</p>"
        note = data.get("note")
        if note:
            body += f"<p><em>Note: {note}</em></p>"
    html = f"<!doctype html><html><body>{body}</body></html>"
    return html, status_code, {"Content-Type": "text/html"}


def _respond(status_code: int, data: dict):
    if _wants_html():
        return _html_response(status_code, data)
    return jsonify(data), status_code


def _parse_date_param(value: str, name: str):
    """Returns (date_obj, error_response) — exactly one will be non-None."""
    try:
        return date.fromisoformat(value), None
    except ValueError:
        err = {"status": "error", "message": f"Invalid {name} '{value}' — expected YYYY-MM-DD"}
        return None, _respond(400, err)


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
    to_date_str = body.get("to_date")

    def _run():
        global _sync_running, _last_run
        _sync_running = True
        try:
            from main import run_sync
            from_date = date.fromisoformat(from_date_str) if from_date_str else None
            to_date = date.fromisoformat(to_date_str) if to_date_str else None
            result = run_sync(from_date, to_date)
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


@app.route("/sync/run", methods=["GET"])
def sync_run():
    global _sync_running, _last_run

    today = date.today()

    from_date_str = request.args.get("from_date")
    to_date_str = request.args.get("to_date")

    from_date = None
    if from_date_str:
        from_date, err = _parse_date_param(from_date_str, "from_date")
        if err:
            return err

    to_date = today
    to_date_clamped = False
    if to_date_str:
        to_date, err = _parse_date_param(to_date_str, "to_date")
        if err:
            return err
        if to_date > today:
            to_date = today
            to_date_clamped = True

    if from_date and to_date < from_date:
        return _respond(400, {
            "status": "error",
            "message": f"to_date ({to_date}) must not be before from_date ({from_date})",
        })

    if not _sync_lock.acquire(blocking=False):
        return _respond(409, {"status": "busy", "message": "A sync is already in progress, please wait"})

    # Capture resolved dates for the thread closure
    resolved_from = from_date
    resolved_to = to_date

    def _run():
        global _sync_running, _last_run
        _sync_running = True
        try:
            from main import run_sync
            result = run_sync(resolved_from, resolved_to)
            _last_run = result
        except Exception as e:
            _last_run = {"error": str(e), "timestamp": _get_local_time()}
            print(f"Sync error: {e}")
        finally:
            _sync_running = False
            _sync_lock.release()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    resp = {
        "status": "started",
        "from_date": str(from_date) if from_date else None,
        "to_date": str(to_date),
    }
    if to_date_clamped:
        resp["note"] = f"to_date was in the future and has been clamped to today ({today})"
    return _respond(200, resp)


@app.route("/sync/test", methods=["POST"])
def sync_test():
    results = {}

    # Test Plaid
    try:
        from plaid_client import PlaidClient
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
        from drive_sync import get_drive_service
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
