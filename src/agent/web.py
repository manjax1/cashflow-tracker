"""Web frontend for the cashflow agent — Claude-style chat + dashboard.

READ-ONLY by design: action tools (recategorize, email drafts) are stripped;
family members can analyze, search, and view dashboards. Mutations stay in
the CLI.

Run:    python -m src.agent.web            # http://<your-mac-ip>:5555
Auth:   set AGENT_WEB_PASSWORD in .env to require a shared password.
        Without it the app is open — fine on a trusted home network only.
"""

import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

import pyotp                                                    # noqa: E402
from flask import (Flask, jsonify, redirect, request,          # noqa: E402
                   send_from_directory, session)
from werkzeug.security import check_password_hash               # noqa: E402

from . import ledger                                            # noqa: E402
from .agent import Agent                                        # noqa: E402
from .tools import ACTION_TOOLS, TOOLS, audit                   # noqa: E402

READ_TOOLS = [t for t in TOOLS if t["name"] not in ACTION_TOOLS]
PASSWORD = os.environ.get("AGENT_WEB_PASSWORD", "")  # legacy LAN-only fallback
CHAT_LOG = os.path.join(ledger.REPO_ROOT, "logs", "web_chat.jsonl")
IS_CLOUD = bool(os.environ.get("RAILWAY_ENVIRONMENT"))

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("AGENT_WEB_SECRET", secrets.token_hex(16))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_CLOUD,          # HTTPS-only cookies on Railway
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


# --------------------------- users & 2FA ---------------------------

def load_users():
    """USERS_JSON env var (Railway) or users.json file (local)."""
    raw = os.environ.get("USERS_JSON", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print("⚠️  USERS_JSON parse failed")
    path = os.path.join(ledger.REPO_ROOT, "users.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


USERS = load_users()

# naive in-memory login rate limiter: ip -> [timestamps of failures]
_fails = {}
MAX_FAILS, FAIL_WINDOW = 5, 900  # 5 failures / 15 min


def _rate_limited(ip):
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    return len(_fails[ip]) >= MAX_FAILS


def _record_fail(ip):
    _fails.setdefault(ip, []).append(time.time())


def check_login(username, password, code):
    u = USERS.get(username)
    if not u:
        return False
    if not check_password_hash(u["password_hash"], password):
        return False
    return pyotp.TOTP(u["totp_secret"]).verify(code, valid_window=1)


# ------------------------ cloud ledger bootstrap ------------------------

_ledger_fetched_at = 0
LEDGER_MAX_AGE = int(os.environ.get("LEDGER_MAX_AGE_SECONDS", "21600"))  # 6h


def ensure_ledger():
    """On Railway there is no local xlsx — download from Drive at boot and
    re-download when stale. No-op locally when the file already exists."""
    global _ledger_fetched_at
    if os.path.exists(ledger.LEDGER_PATH) and (
            not IS_CLOUD or time.time() - _ledger_fetched_at < LEDGER_MAX_AGE):
        return
    file_id = os.environ.get("GOOGLE_DRIVE_FILE_ID")
    if not file_id:
        return
    from src.drive_sync import download_ledger
    download_ledger(file_id, ledger.LEDGER_PATH)
    ledger._cache["mtime"] = None
    _ledger_fetched_at = time.time()

_agents = {}          # session id -> Agent (read-only)
_agents_lock = threading.Lock()
MAX_HISTORY_MSGS = 40  # trim old turns to bound cost/context


def _get_agent():
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_hex(8)
        session["sid"] = sid
    with _agents_lock:
        if sid not in _agents:
            _agents[sid] = Agent(tools=READ_TOOLS, read_only=True)
        return _agents[sid]


def _authed():
    if USERS:                      # multi-user + 2FA mode
        return bool(session.get("user"))
    if PASSWORD:                   # legacy shared-password mode (LAN)
        return bool(session.get("authed"))
    return not IS_CLOUD            # open mode: never allowed in the cloud


def _log_chat(sid, question, answer, tool_calls, stats):
    os.makedirs(os.path.dirname(CHAT_LOG), exist_ok=True)
    with open(CHAT_LOG, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now().isoformat(timespec="seconds"), "sid": sid,
            "q": question, "a": answer,
            "tools": [t["name"] for t in tool_calls], "stats": stats,
        }) + "\n")


# ------------------------------ routes ------------------------------

@app.before_request
def _bootstrap_ledger():
    if request.path.startswith("/api/"):
        try:
            ensure_ledger()
        except Exception as e:
            audit("ledger_bootstrap_failed", {"error": str(e)})


@app.get("/")
def index():
    if not _authed():
        return send_from_directory(app.static_folder, "login.html")
    return send_from_directory(app.static_folder, "index.html")


@app.post("/login")
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0]
    if _rate_limited(ip):
        audit("web_login_rate_limited", {"ip": ip})
        return "Too many failed attempts. Try again in 15 minutes.", 429
    if USERS:
        username = request.form.get("username", "").strip()
        ok = check_login(username, request.form.get("password", ""),
                         request.form.get("code", "").strip())
        if ok:
            session.permanent = True
            session["user"] = username
            audit("web_login_ok", {"user": username, "ip": ip})
            return redirect("/")
        _record_fail(ip)
        audit("web_login_failed", {"user": username, "ip": ip})
    elif PASSWORD and request.form.get("password", "") == PASSWORD:
        session.permanent = True
        session["authed"] = True
        return redirect("/")
    else:
        _record_fail(ip)
    return send_from_directory(app.static_folder, "login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.post("/api/chat")
def chat():
    if not _authed():
        return jsonify({"error": "unauthorized"}), 401
    message = (request.json or {}).get("message", "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400
    agent = _get_agent()
    # bound conversation length (keep system-grounding; trim oldest turns)
    if len(agent.history) > MAX_HISTORY_MSGS:
        agent.history = agent.history[-MAX_HISTORY_MSGS:]
        # history must start with a plain user message, not tool_results
        while agent.history and not (
                agent.history[0]["role"] == "user"
                and isinstance(agent.history[0]["content"], str)):
            agent.history.pop(0)
    try:
        answer = agent.ask(message)
    except Exception as e:
        audit("web_chat_error", {"error": str(e)})
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    _log_chat(session.get("user", session["sid"]), message, answer,
              agent.last_tool_calls, agent.stats)
    return jsonify({"answer": answer,
                    "tool_calls": agent.last_tool_calls,
                    "stats": agent.stats})


@app.get("/api/meta")
def meta():
    """Categories and available months, for the search dropdowns."""
    if not _authed():
        return jsonify({"error": "unauthorized"}), 401
    txns = ledger.load_transactions()
    months = sorted({t["Date"][:7] for t in txns}, reverse=True)
    cats = ledger.list_categories()["categories"]
    income_cats = sorted(c["category"] for c in cats if c["income"] > c["expense"])
    expense_cats = sorted(c["category"] for c in cats if c["expense"] >= c["income"])
    return jsonify({"months": months, "income_categories": income_cats,
                    "expense_categories": expense_cats})


@app.get("/api/search")
def search():
    """Deterministic ledger search — no LLM involved.
    Params: q (text), category, tx_type (Income|Expense), month (YYYY-MM)."""
    if not _authed():
        return jsonify({"error": "unauthorized"}), 401
    q = request.args.get("q", "").strip() or None
    category = request.args.get("category", "").strip() or None
    tx_type = request.args.get("tx_type", "").strip() or None
    month = request.args.get("month", "").strip() or None

    txns = ledger.load_transactions()
    dates = sorted(t["Date"] for t in txns)
    if month:  # YYYY-MM -> full month window
        start, end = month + "-01", month + "-31"
    else:
        start, end = dates[0], dates[-1]
    result = ledger.query_transactions(
        start, end, category=category, tx_type=tx_type, search=q, limit=200)
    return jsonify(result)


@app.get("/api/monthly")
def monthly_summary():
    """12-month rolling income/expense/net per month (Sheet-style summary)."""
    if not _authed():
        return jsonify({"error": "unauthorized"}), 401
    series = {m: ledger.get_trends(metric=m, lookback_periods=13)["series"]
              for m in ("income", "expenses", "net")}
    by_period = {}
    for m, pts in series.items():
        for p in pts:
            row = by_period.setdefault(p["period"], {"partial": False})
            row[m] = p["value"]
            row["partial"] = row["partial"] or p.get("partial", False)
    months = sorted(by_period.keys(), reverse=True)[:13]
    rows = [{"month": p, **by_period[p]} for p in months]
    complete = [r for r in rows if not r["partial"]][:12]
    totals = {
        "income": round(sum(r["income"] for r in complete), 2),
        "expenses": round(sum(r["expenses"] for r in complete), 2),
        "net": round(sum(r["net"] for r in complete), 2),
        "months": len(complete),
    }
    return jsonify({"rows": rows, "totals": totals})


@app.get("/api/dashboard")
def dashboard():
    if not _authed():
        return jsonify({"error": "unauthorized"}), 401
    txns = ledger.load_transactions()
    dates = sorted(t["Date"] for t in txns)
    latest = dates[-1]
    year_start = latest[:4] + "-01-01"
    month_start = latest[:7] + "-01"

    ytd = ledger.get_cashflow_summary(year_start, latest, group_by="category")
    month = ledger.get_cashflow_summary(month_start, latest, group_by="category")
    trends = {m: ledger.get_trends(metric=m, lookback_periods=12)
              for m in ("income", "expenses", "net")}
    anomalies = ledger.find_anomalies(30)

    top_expenses = [g for g in month["groups"] if g["expense"] > 0][:8]
    return jsonify({
        "coverage": {"start": dates[0], "end": latest, "transactions": len(txns)},
        "ytd": {"income": ytd["total_income"], "expense": ytd["total_expense"],
                "net": ytd["net"], "rental": ytd["rental_rollup"]},
        "month": {"label": latest[:7], "income": month["total_income"],
                  "expense": month["total_expense"], "net": month["net"],
                  "top_expenses": top_expenses},
        "trends": {m: [p for p in trends[m]["series"] if not p.get("partial")]
                   for m in trends},
        "anomaly_count": anomalies["finding_count"],
    })


def main():
    host = os.environ.get("AGENT_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("AGENT_WEB_PORT", "5555")))
    if USERS:
        print(f"🔐 Multi-user 2FA mode: {len(USERS)} user(s) configured.")
    elif PASSWORD:
        print("🔑 Shared-password mode (LAN). For per-user 2FA, run "
              "scripts/manage_users.py.")
    elif IS_CLOUD:
        print("❌ No USERS_JSON configured — refusing all access in cloud mode.")
    else:
        print("⚠️  Open mode (no auth) — trusted home network only. "
              "Run scripts/manage_users.py to enable per-user 2FA.")
    print(f"Cashflow Agent web UI: http://localhost:{port} "
          f"(family: http://<your-mac-ip>:{port})")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
