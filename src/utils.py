import os
from datetime import datetime


def clean_env(value, name=""):
    """Strip surrounding quotes that Railway sometimes auto-adds to env vars."""
    if value:
        value = value.strip()
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]
    return value


def resolve_ledger_path() -> tuple[str, bool]:
    """Return (ledger_path, is_cloud) using the same logic as the sync pipeline.

    On Railway (RAILWAY_ENVIRONMENT set) the ledger lives at /tmp/cashflow-tracker.xlsx
    regardless of what SPENDING_LEDGER_FILE_PATH says — the env var there is a stale
    local Mac path that was never updated for the container.  Locally the env var wins.
    """
    is_cloud = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    if is_cloud:
        return "/tmp/cashflow-tracker.xlsx", True
    path = clean_env(os.getenv("SPENDING_LEDGER_FILE_PATH"), "SPENDING_LEDGER_FILE_PATH")
    return path, False


def to_pacific_str(utc_iso_str: str) -> str:
    """Convert a UTC ISO timestamp string to a readable Pacific Time string.

    Intended for ad-hoc debug scripts inspecting Drive metadata
    (e.g. service.files().get(...).execute()["modifiedTime"]).
    Output example: "2026-06-27 06:16:09 PM PDT"
    """
    try:
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
    except Exception:
        return utc_iso_str  # fallback: return raw string unchanged

    # Drive returns e.g. "2026-06-27T13:16:09.000Z"; fromisoformat needs no trailing Z
    dt_utc = datetime.fromisoformat(utc_iso_str.replace("Z", "+00:00"))
    dt_pacific = dt_utc.astimezone(pacific)
    return dt_pacific.strftime("%Y-%m-%d %I:%M:%S %p %Z")
