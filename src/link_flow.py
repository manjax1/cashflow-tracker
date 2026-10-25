import os
import webbrowser
import threading
from flask import Flask, request, jsonify
from dotenv import load_dotenv, set_key

load_dotenv()

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

LINK_HTML = """<!DOCTYPE html>
<html>
<head><title>Spending Tracker — Connect Bank</title>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
</head>
<body style="font-family:Arial,sans-serif;text-align:center;padding-top:80px;">
<h2>Spending Tracker</h2>
<p>Connect your Bank of America account to get started.</p>
<button id="link-btn" style="padding:12px 28px;font-size:16px;cursor:pointer;">
  Connect Bank Account
</button>
<p id="status"></p>
<script>
async function openLink() {
  document.getElementById("status").textContent = "Initializing...";
  const res = await fetch("/get_link_token");
  const { link_token } = await res.json();
  const handler = Plaid.create({
    token: link_token,
    onSuccess: async (public_token, metadata) => {
      document.getElementById("status").textContent = "Exchanging token...";
      const r = await fetch("/exchange_token", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({public_token})
      });
      const html = await r.text();
      document.body.innerHTML = html;
    },
    onExit: (err) => {
      if (err) document.getElementById("status").textContent = "Error: " + err.display_message;
    }
  });
  handler.open();
}
document.getElementById("link-btn").onclick = openLink;
</script>
</body>
</html>"""

SUCCESS_HTML = """<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;text-align:center;padding-top:80px;">
<h2>&#x2705; Bank account connected successfully!</h2>
<p>You can close this window.</p>
</body></html>"""


UPDATE_HTML = """<!DOCTYPE html>
<html>
<head><title>Spending Tracker — Reconnect Bank</title>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
</head>
<body style="font-family:Arial,sans-serif;text-align:center;padding-top:80px;">
<h2>Spending Tracker</h2>
<p>Re-authenticate your existing Bank of America connection.<br>
Your existing link is kept — this only refreshes the login.</p>
<button id="link-btn" style="padding:12px 28px;font-size:16px;cursor:pointer;">
  Reconnect Bank Account
</button>
<p id="status"></p>
<script>
async function openLink() {
  document.getElementById("status").textContent = "Initializing...";
  const res = await fetch("/get_link_token");
  const { link_token } = await res.json();
  const handler = Plaid.create({
    token: link_token,
    onSuccess: async () => {
      // Update mode: no public_token to exchange — the existing item is repaired.
      const r = await fetch("/update_done", {method: "POST"});
      document.body.innerHTML = await r.text();
    },
    onExit: (err) => {
      if (err) document.getElementById("status").textContent =
        "Error: " + (err.display_message || err.error_message || err.error_code);
    }
  });
  handler.open();
}
document.getElementById("link-btn").onclick = openLink;
</script>
</body>
</html>"""


def run_update_link_flow(client, access_token: str, label: str = "primary"):
    """Open Plaid Link in UPDATE MODE to repair an existing item whose login
    broke (ITEM_LOGIN_REQUIRED). The access_token is unchanged, so there's
    nothing to write back to .env — the item is simply restored to good state."""
    app = Flask(__name__)
    _shutdown = threading.Event()

    @app.route("/")
    def index():
        return UPDATE_HTML

    @app.route("/get_link_token")
    def get_link_token():
        return jsonify({"link_token": client.get_update_link_token(access_token)})

    @app.route("/update_done", methods=["POST"])
    def update_done():
        ok = False
        try:
            ok = client.verify_access_token(access_token)
        except Exception:
            ok = False
        print(f"✅ Update-mode re-auth complete for '{label}'. "
              f"Token still valid: {ok} (unchanged — no .env edit needed).")
        _shutdown.set()
        return SUCCESS_HTML

    def open_browser():
        import time
        time.sleep(1)
        webbrowser.open("http://localhost:5050")

    threading.Thread(target=open_browser, daemon=True).start()
    print(f"🔗 Opening Plaid Link (update mode) for '{label}' at http://localhost:5050 — "
          "complete the BofA login in your browser.")

    from werkzeug.serving import make_server
    server = make_server("0.0.0.0", 5050, app)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    _shutdown.wait()
    server.shutdown()
    print("✅ Update-mode flow complete.")


def run_link_flow(client, env_key: str = "PLAID_ACCESS_TOKEN"):
    app = Flask(__name__)
    _shutdown = threading.Event()

    @app.route("/")
    def index():
        return LINK_HTML

    @app.route("/get_link_token")
    def get_link_token():
        token = client.get_link_token()
        return jsonify({"link_token": token})

    @app.route("/exchange_token", methods=["POST"])
    def exchange_token():
        public_token = request.json["public_token"]
        access_token = client.exchange_public_token(public_token)
        set_key(ENV_PATH, env_key, access_token)
        os.environ[env_key] = access_token
        print(f"✅ Access token saved to {ENV_PATH} as {env_key}")
        _shutdown.set()
        return SUCCESS_HTML

    def open_browser():
        import time
        time.sleep(1)
        webbrowser.open("http://localhost:5050")

    threading.Thread(target=open_browser, daemon=True).start()
    print("🔗 Opening Plaid Link at http://localhost:5050 — complete the flow in your browser.")

    from werkzeug.serving import make_server
    server = make_server("0.0.0.0", 5050, app)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    _shutdown.wait()
    server.shutdown()
    print("✅ Link flow complete.")
